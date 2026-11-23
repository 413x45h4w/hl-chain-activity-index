"""Solana JSON-RPC. Vote transactions are the consensus exclusion.

getRecentPerformanceSamples is the free public source that already splits
total transactions from non-vote transactions over ~60s windows. It does not
include fees. Fees are the sum of meta.fee on recent blocks, over the measured
span of those blocks, and that span is stored separately from the 60s
transaction window.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..http_util import HttpError, first_success, json_rpc
from .common import blank, failure


def collect_solana(chain):
    endpoints = chain.get("endpoints") or []
    if not endpoints:
        return [failure(chain, chain.get("unavailable_reason") or "no public RPC configured")]
    timeout = int(chain.get("timeout") or 20)

    def pull(url):
        return json_rpc(url, "getRecentPerformanceSamples", [30], timeout=timeout)

    try:
        samples, source = first_success(endpoints, pull)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    if not samples:
        return [failure(chain, f"empty performance samples from {source}")]
    # The first element is the most recent sample. Timestamps are not provided.
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rows = []
    cursor = now
    for index, sample in enumerate(samples):
        period = float(sample.get("samplePeriodSecs") or 60)
        gross = sample.get("numTransactions")
        net = sample.get("numNonVoteTransactions")
        excluded = None
        if gross is not None and net is not None:
            excluded = gross - net
        slot = sample.get("slot")
        observed = cursor
        cursor = cursor - timedelta(seconds=period)
        reasons = {
            "native_transfer_count": "getRecentPerformanceSamples has no transfer breakdown",
            "native_transfer_value": "getRecentPerformanceSamples has no transfer values",
            "token_transfer_count": "a single slot can be decoded, but its transfers are not scaled up to the 60s sample",
            "active_addresses": "not in performance samples",
            "staking_rewards": "inflation and staking rewards are not user transactions in this sample; they are outside both counts",
            "time_basis": "RPC sample has no timestamp; observed_at is the collector clock walking backward by samplePeriodSecs",
        }
        if gross is not None and net is not None and gross == net:
            reasons["vote_transactions"] = (
                "numNonVoteTransactions equals numTransactions. On Solana mainnet a full block "
                "also contains no Vote111111111111111111111111111111111111111 instructions, so "
                "consensus votes are not in this feed and excluded_tx_count is 0. On chains where "
                "the two fields differ, the difference is treated as vote transactions."
            )
        rows.append(
            blank(
                chain,
                sample_kind="window",
                sample_key=f"{chain['id']}:perf:{slot}",
                observed_at=observed.isoformat().replace("+00:00", "Z"),
                window_seconds=period,
                head_height=str(slot) if slot is not None else None,
                source=source + " getRecentPerformanceSamples",
                gross_tx_count=gross,
                excluded_tx_count=excluded,
                net_tx_count=net,
                distribution_count=excluded,
                tx_types={"vote": excluded, "non_vote": net} if excluded is not None else {},
                null_reasons=reasons,
                extra={
                    "num_slots": sample.get("numSlots"),
                    "excluded_rule": "Solana vote transactions",
                    "minute_note": chain.get("minute_note"),
                    "clock_aligned": True,
                },
            )
        )
    if rows:
        rows[0]["lag_seconds"] = float(samples[0].get("samplePeriodSecs") or 60)
        fee_native, fee_window = _recent_fees(source, timeout)
        if fee_native is None:
            rows[0]["null_reasons"]["fee_native"] = "getBlock fee sum was not available from this RPC"
        else:
            rows[0]["fee_native"] = fee_native
            rows[0]["extra"]["fee_window_seconds"] = fee_window
            rows[0]["null_reasons"].pop("fee_native", None)
    return rows


def _recent_fees(url, timeout):
    """Sum user-paid fees over a short run of blocks.

    The oldest block is only a timestamp anchor. meta.fee is lamports paid by
    the signer, which is larger than the leader's Fee reward because part of
    each fee is burned.
    """
    try:
        slot = json_rpc(url, "getSlot", [{"commitment": "confirmed"}], timeout=timeout)
    except HttpError:
        return None, None
    found = []
    cursor = int(slot)
    scanned = 0
    while scanned < 90 and cursor > 1:
        scanned += 1
        cursor -= 1
        try:
            block = json_rpc(
                url,
                "getBlock",
                [cursor, {
                    "encoding": "json",
                    "transactionDetails": "accounts",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 1,
                }],
                timeout=timeout,
            )
        except HttpError:
            continue
        if not block or block.get("blockTime") is None:
            continue
        lamports = 0
        for tx in block.get("transactions") or []:
            lamports += int((tx.get("meta") or {}).get("fee") or 0)
        found.append((int(block["blockTime"]), lamports))
        if len(found) >= 2 and found[0][0] - found[-1][0] >= 15:
            break
        if len(found) >= 70:
            break
    if len(found) < 2:
        return None, None
    window = found[0][0] - found[-1][0]
    if window <= 0:
        return None, None
    lamports = sum(item[1] for item in found[:-1])
    return lamports / 1_000_000_000, float(window)
