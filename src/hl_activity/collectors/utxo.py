"""UTXO collectors.

Coinbase (the miner/reward transaction) is the distribution excluded from net.
Output sums are not peer-to-peer transfer volume because they include change,
so native transfer value stays null.
"""

from __future__ import annotations

from ..emissions import attach_emissions
from ..http_util import HttpError, request_json
from .common import blank, failure, iso_from_unix, now_iso


def collect_utxo_mempool(chain):
    base = (chain.get("endpoints") or [None])[0]
    if not base:
        return [failure(chain, "no mempool API configured")]
    try:
        tip = int(request_json(f"{base}/blocks/tip/height", timeout=20))
        blocks = request_json(f"{base}/v1/blocks/{tip}", timeout=25)
        mempool = request_json(f"{base}/mempool", timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    if not isinstance(blocks, list) or not blocks:
        return [failure(chain, "mempool block list empty")]
    rows = []
    for index, block in enumerate(blocks):
        if block.get("stale"):
            continue
        extras = block.get("extras") or {}
        tx_count = block.get("tx_count")
        total_fees = extras.get("totalFees")
        reward = extras.get("reward")
        subsidy = None
        if reward is not None and total_fees is not None:
            subsidy = (reward - total_fees) / 1e8
        fee_native = (total_fees / 1e8) if total_fees is not None else None
        ts = block.get("timestamp")
        window = None
        if index + 1 < len(blocks):
            prev = blocks[index + 1].get("timestamp")
            if ts and prev and ts > prev:
                window = ts - prev
        gross = tx_count
        excluded = 1 if gross else None
        net = (gross - 1) if gross else None
        rows.append(
            blank(
                chain,
                sample_kind="window" if index == 0 else "block",
                sample_key=f"{chain['id']}:block:{block.get('height')}:sub",
                observed_at=iso_from_unix(ts),
                window_seconds=window,
                head_height=str(block.get("height")),
                source=base,
                gross_tx_count=gross,
                excluded_tx_count=excluded,
                net_tx_count=net,
                native_transfer_count=None,
                native_transfer_is_net=0,
                distribution_count=1 if gross else None,
                distribution_value_native=subsidy,
                fee_native=fee_native,
                tx_types={"coinbase": 1, "non_coinbase": net},
                null_reasons={
                    "native_transfer_value": "block output sum includes change and is not peer-to-peer volume",
                    "native_transfer_count": "non-coinbase transactions are not the same as peer-to-peer payments; count is withheld",
                    "token_transfer_count": "no free ordinals/runes indexer is queried",
                    "active_addresses": "mempool block summary has no unique-address field",
                    "minute_level": chain.get("minute_note"),
                },
                extra={
                    "mempool_count": (mempool or {}).get("count") if index == 0 else None,
                    "mempool_vsize": (mempool or {}).get("vsize") if index == 0 else None,
                    "pool": ((extras.get("pool") or {}).get("name")),
                    "total_output_sats": extras.get("totalOutputAmt"),
                },
            )
        )
        if subsidy is not None and window:
            attach_emissions(
                rows[-1],
                subsidy,
                window,
                "block subsidy = coinbase reward minus fees",
            )
    if not rows:
        return [failure(chain, "no non-stale blocks")]
    # The newest block is the ranking window. Older blocks stay as history.
    rows[0]["sample_kind"] = "window"
    return rows


def collect_utxo_blockcypher(chain):
    base = (chain.get("endpoints") or [None])[0]
    if not base:
        return [failure(chain, "no blockcypher endpoint")]
    try:
        chain_info = request_json(base, timeout=20)
        latest = request_json(f"{base}/blocks/{chain_info['hash']}", timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    tx_count = latest.get("n_tx")
    fees = latest.get("fees")
    # BlockCypher fees are satoshis.
    height = latest.get("height")
    ts = latest.get("time")
    observed = ts if isinstance(ts, str) else None
    if observed and observed.endswith("+00:00"):
        observed = observed.replace("+00:00", "Z")
    gross = tx_count
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{height}",
            observed_at=observed or now_iso(),
            window_seconds=None,
            head_height=str(height),
            source=base,
            gross_tx_count=gross,
            excluded_tx_count=1 if gross else None,
            net_tx_count=(gross - 1) if gross else None,
            distribution_count=1 if gross else None,
            fee_native=(fees / 1e8) if fees is not None else None,
            tx_types={"coinbase": 1, "non_coinbase": (gross - 1) if gross else None},
            null_reasons={
                "window_seconds": "blockcypher latest-block payload has no parent timestamp, so a per-minute rate is not computed",
                "native_transfer_value": "not a peer-to-peer volume",
                "token_transfer_count": "not available from this endpoint",
                "active_addresses": "not available from this endpoint",
                "distribution_value_native": "subsidy is not separated by this endpoint",
                "minute_level": chain.get("minute_note"),
            },
            extra={"chain_height": chain_info.get("height")},
        )
    ]


def collect_utxo_blockchair(chain):
    slug = chain.get("blockchair")
    if not slug:
        return [failure(chain, "blockchair slug missing")]
    url = f"https://api.blockchair.com/{slug}/blocks?limit=8"
    try:
        payload = request_json(url, timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    data = (payload or {}).get("data") or []
    if not data:
        return [failure(chain, f"blockchair returned no blocks for {slug}")]
    rows = []
    for index, block in enumerate(data):
        ts = block.get("time")
        observed = ts.replace(" ", "T") + "Z" if isinstance(ts, str) else None
        gross = block.get("transaction_count")
        fee = block.get("fee_total")
        # blockchair fee_total is sats for BTC-like chains
        divisor = float(chain.get("atomic_divisor") or 1e8)
        window = None
        if index + 1 < len(data):
            # list is newest first; time delta to the next row
            pass
        rows.append(
            blank(
                chain,
                sample_kind="window" if index == 0 else "block",
                sample_key=f"{chain['id']}:block:{block.get('id')}:gen",
                observed_at=observed,
                window_seconds=window,
                head_height=str(block.get("id")),
                source=url,
                gross_tx_count=gross,
                excluded_tx_count=1 if gross else None,
                net_tx_count=(gross - 1) if gross else None,
                distribution_count=1 if gross else None,
                fee_native=(fee / divisor) if fee is not None else None,
                emissions_native=(block["generation"] / divisor) if block.get("generation") is not None else None,
                tx_types={"coinbase": 1, "non_coinbase": (gross - 1) if gross else None},
                null_reasons={
                    "window_seconds": "blockchair blocks list does not include a reliable parent interval here; per-minute rate needs the next run to compare timestamps and is left null when only one block is the ranking window without a computed delta",
                    "native_transfer_value": "output totals include change",
                    "token_transfer_count": "not available",
                    "active_addresses": "not available",
                    "minute_level": chain.get("minute_note"),
                },
            )
        )
        if block.get("generation") is None:
            rows[-1]["null_reasons"]["emissions_native"] = "blockchair block had no generation field"
    # Fill windows from successive timestamps now that rows exist.
    times = []
    for block in data:
        ts = block.get("time")
        if isinstance(ts, str):
            times.append(datetime_from_blockchair(ts))
        else:
            times.append(None)
    for index, row in enumerate(rows):
        if index + 1 < len(times) and times[index] and times[index + 1]:
            delta = (times[index] - times[index + 1]).total_seconds()
            if delta > 0:
                row["window_seconds"] = delta
                row["null_reasons"].pop("window_seconds", None)
                if row.get("emissions_native") is not None:
                    row.setdefault("extra", {})["emissions_window_seconds"] = delta
                    row["extra"]["emissions_basis"] = "blockchair generation, new coins excluding fees"
                    row["null_reasons"].pop("emissions_native", None)
    return rows


def datetime_from_blockchair(value):
    from datetime import datetime, timezone

    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
