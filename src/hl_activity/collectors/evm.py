"""EVM execution-layer collector.

Consensus-layer issuance (beacon rewards, priority-fee recipient income that
is not itself a transaction) is not in the tx list. Net excludes OP-stack
deposit transactions (type 0x7e), which are protocol bridge messages.
Airdrops that look like ordinary contract calls cannot be separated and stay
inside net; that limit is recorded on the sample.
"""

from __future__ import annotations

from ..http_util import HttpError, first_success, json_rpc
from .common import blank, failure, hex_int, iso_from_unix

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SYSTEM_TYPES = {"0x7e"}
WEI = 10**18


def _rpc(url, method, params, timeout):
    return json_rpc(url, method, params, timeout=timeout)


def collect_evm(chain):
    endpoints = chain.get("endpoints") or []
    if not endpoints:
        return [failure(chain, chain.get("unavailable_reason") or "no public RPC configured")]
    timeout = int(chain.get("timeout") or 18)
    detail_blocks = int(chain.get("detail_blocks") or 1)
    header_blocks = int(chain.get("header_blocks") or 8)

    def head(url):
        return _rpc(url, "eth_blockNumber", [], timeout)

    try:
        head_hex, source = first_success(endpoints, head)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    head_height = hex_int(head_hex)
    headers = []
    for offset in range(header_blocks):
        height = head_height - offset
        if height < 0:
            break
        try:
            block = _rpc(source, "eth_getBlockByNumber", [hex(height), False], timeout)
            count = _rpc(source, "eth_getBlockTransactionCountByNumber", [hex(height)], timeout)
        except HttpError:
            break
        if not block:
            break
        headers.append((height, block, hex_int(count)))
    if not headers:
        return [failure(chain, f"block header unavailable at {source}")]

    def block_time(block):
        return hex_int(block.get("timestamp"))

    header_samples = []
    for index, (height, block, count) in enumerate(headers):
        ts = block_time(block)
        if index + 1 < len(headers):
            window = ts - block_time(headers[index + 1][1])
        else:
            window = None
        if window is not None and window <= 0:
            window = None
        header_samples.append(
            blank(
                chain,
                sample_kind="block",
                sample_key=f"{chain['id']}:header:{height}",
                observed_at=iso_from_unix(ts),
                window_seconds=window,
                head_height=str(height),
                source=source,
                gross_tx_count=count,
                null_reasons={
                    "net_tx_count": "header-only backfill; transactions were not downloaded, so system txs were not separated",
                    "native_transfer_value": "header-only backfill",
                    "token_transfer_count": "header-only backfill",
                    "active_addresses": "header-only backfill",
                    "fee_native": "header-only backfill uses gasUsed but not the effective price",
                },
                extra={"gas_used": hex_int(block.get("gasUsed")), "tx_count_source": "eth_getBlockTransactionCountByNumber"},
            )
        )

    detailed = []
    for height, block, count in headers[:detail_blocks]:
        try:
            full = _rpc(source, "eth_getBlockByNumber", [hex(height), True], timeout)
        except HttpError as exc:
            detailed.append({"error": str(exc), "height": height})
            continue
        receipts = None
        receipt_error = None
        try:
            receipts = _rpc(source, "eth_getBlockReceipts", [hex(height)], timeout)
        except HttpError as exc:
            receipt_error = str(exc)
        detailed.append({"height": height, "full": full, "receipts": receipts, "receipt_error": receipt_error, "count": count})

    parsed = [item for item in detailed if item.get("full")]
    if not parsed:
        err = detailed[0]["error"] if detailed else "no full block"
        header_samples.append(
            failure(chain, f"full block fetch failed ({err}); header gross counts were still stored")
        )
        # failure() uses a unique key; keep headers
        return header_samples

    type_totals = {}
    gross = 0
    excluded = 0
    native_count = 0
    native_value = 0
    token_transfers = 0
    token_known = True
    addresses = set()
    fee_wei = 0
    fee_known = True
    fee_reason = None
    window = 0
    newest_ts = None
    oldest_parent_ts = None
    reasons = {
        "staking_rewards": "execution RPC does not expose consensus-layer issuance or validator rewards",
        "airdrops": "airdrop distributions that look like ordinary contract calls are not separable and remain inside net",
    }

    for item in parsed:
        full = item["full"]
        ts = hex_int(full.get("timestamp"))
        parent_ts = None
        # interval from the matching header pair when present
        for index, (height, block, _count) in enumerate(headers):
            if height == item["height"] and index + 1 < len(headers):
                parent_ts = block_time(headers[index + 1][1])
                break
        if parent_ts is not None and ts > parent_ts:
            window += ts - parent_ts
        elif parent_ts is not None and ts == parent_ts:
            # Many L2 timestamps only have one-second resolution, so two
            # distinct blocks can share a timestamp. Count that pair as one
            # second, which is the clock's resolution, and say so.
            window += 1
            reasons["window_seconds"] = (
                "adjacent blocks share a one-second timestamp; the window uses 1 second, the resolution of the clock"
            )
        newest_ts = ts if newest_ts is None else max(newest_ts, ts)
        if parent_ts is not None:
            oldest_parent_ts = parent_ts if oldest_parent_ts is None else min(oldest_parent_ts, parent_ts)
        txs = full.get("transactions") or []
        gross += len(txs)
        receipt_by_hash = {}
        if item["receipts"]:
            for receipt in item["receipts"]:
                receipt_by_hash[receipt.get("transactionHash")] = receipt
        elif item["receipt_error"]:
            fee_known = False
            token_known = False
            fee_reason = "eth_getBlockReceipts unavailable: " + item["receipt_error"]
        for tx in txs:
            primary = _primary_type(tx)
            type_totals[primary] = type_totals.get(primary, 0) + 1
            if primary == "system":
                excluded += 1
                continue
            frm = (tx.get("from") or "").lower()
            to = (tx.get("to") or "").lower()
            if frm:
                addresses.add(frm)
            if to:
                addresses.add(to)
            value = hex_int(tx.get("value"))
            if value > 0:
                native_count += 1
                native_value += value
            receipt = receipt_by_hash.get(tx.get("hash"))
            if receipt is None:
                continue
            gas_used = hex_int(receipt.get("gasUsed"))
            price = hex_int(receipt.get("effectiveGasPrice") or receipt.get("gasPrice"))
            fee_wei += gas_used * price
            for log in receipt.get("logs") or []:
                topics = [t.lower() for t in (log.get("topics") or [])]
                if topics and topics[0] == TRANSFER_TOPIC:
                    token_transfers += 1

    if not fee_known:
        reasons["fee_native"] = fee_reason
    if not token_known:
        reasons["token_transfer_count"] = fee_reason
    reasons["token_transfer_value_usd"] = (
        "token transfer amounts are heterogeneous; USD value is not summed from HL mids of unrelated tokens"
    )
    head = parsed[0]["height"]
    sample = blank(
        chain,
        sample_kind="window",
        sample_key=f"{chain['id']}:window:{head}",
        observed_at=iso_from_unix(newest_ts),
        window_seconds=window or None,
        head_height=str(head),
        source=source,
        gross_tx_count=gross,
        excluded_tx_count=excluded,
        net_tx_count=gross - excluded,
        native_transfer_count=native_count,
        native_transfer_value=native_value / WEI,
        native_transfer_is_net=1,
        token_transfer_count=token_transfers if token_known else None,
        distribution_count=0,
        distribution_value_native=0.0,
        active_addresses=len(addresses),
        fee_native=(fee_wei / WEI) if fee_known else None,
        tx_types=type_totals,
        null_reasons=reasons,
        extra={
            "detail_blocks": len(parsed),
            "excluded_rule": "OP-stack deposit txs (type 0x7e) only; consensus rewards are not execution txs",
            "minute_note": chain.get("minute_note"),
        },
    )
    if window is None or window <= 0:
        sample["null_reasons"]["window_seconds"] = "parent block timestamp missing, so a per-minute rate cannot be computed"
        sample["window_seconds"] = None
    return [sample] + header_samples


def _primary_type(tx):
    typ = str(tx.get("type") or "0x0").lower()
    if typ in SYSTEM_TYPES:
        return "system"
    if tx.get("to") in (None, "null"):
        return "contract_create"
    data = tx.get("input") or "0x"
    value = hex_int(tx.get("value"))
    if data in ("0x", "0x0"):
        return "native_transfer" if value > 0 else "empty"
    return "contract_call"
