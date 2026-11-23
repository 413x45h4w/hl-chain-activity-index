"""Organic activity ranking.

Net activity removes protocol-generated work: staking-reward and inflation
payouts, coinbase/block-reward transactions, validator/system/consensus votes
(Solana vote transactions, Algorand key registration, Aptos block metadata,
OP-stack deposit transactions, Cosmos withdraw-reward messages, Tron reward
withdrawals, Stacks coinbase/tenure). Governance votes stay in net. They are
user actions, unlike Solana's consensus vote transactions.

The ranking observation for a chain is the newest fully measured window.
Per-minute rates divide counts by that window. Unique addresses are reported
as an intensity (addresses in the window per minute). That is not a deduped
daily active-user count.

Composite index: for each of net tx/min, net native-transfer USD/min, and
address intensity, percentile rank among chains where that component is
present (100 = highest). The composite is the unweighted mean of the
components that exist. A missing component is omitted, not treated as zero.
A z-score mean of the same components is reported beside it. Chains with no
usable component are listed as unranked.
"""

from __future__ import annotations

import json
import math
import statistics


def per_minute(count, window_seconds):
    # Sub-second windows scale a burst into a minute and dominate the index.
    # Those samples stay in history; they do not enter the rate.
    if count is None or window_seconds is None or window_seconds < 1:
        return None
    return count / (window_seconds / 60.0)


def percentile_ranks(values):
    """High value -> high percentile in [0, 100]. Ties share the average rank."""
    usable = [(key, value) for key, value in values.items() if value is not None]
    if not usable:
        return {}
    ordered = sorted(usable, key=lambda item: item[1])
    n = len(ordered)
    ranks = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        # average competition rank, then scale so the top is 100 and the bottom is 0
        avg_index = (i + j) / 2.0
        pct = 100.0 if n == 1 else 100.0 * avg_index / (n - 1)
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = pct
        i = j + 1
    return ranks


def zscores(values):
    usable = {key: value for key, value in values.items() if value is not None}
    if len(usable) < 2:
        return {key: 0.0 for key in usable}
    mean = statistics.fmean(usable.values())
    stdev = statistics.pstdev(usable.values())
    if stdev == 0:
        return {key: 0.0 for key in usable}
    return {key: (value - mean) / stdev for key, value in usable.items()}


def _num(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def select_latest_windows(rows):
    """Newest window-kind sample per chain. Block rows are chart history."""
    chosen = {}
    for row in rows:
        if row.get("sample_kind") not in ("window", "live"):
            continue
        chain_id = row["chain_id"]
        current = chosen.get(chain_id)
        if current is None or (row.get("observed_at") or "") >= (current.get("observed_at") or ""):
            if current is not None and row.get("observed_at") == current.get("observed_at"):
                if _num(row.get("window_seconds")) or 0 < _num(current.get("window_seconds")) or 0:
                    continue
            chosen[chain_id] = row
    return chosen


def _fee_window_seconds(row):
    extra = row.get("extra_json")
    if extra is None:
        extra = row.get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except json.JSONDecodeError:
            return None
    if isinstance(extra, dict):
        return _num(extra.get("fee_window_seconds"))
    return None


def metric_bundle(row):
    window = _num(row.get("window_seconds"))
    gross = _num(row.get("gross_tx_count"))
    excluded = _num(row.get("excluded_tx_count"))
    net = _num(row.get("net_tx_count"))
    if net is None and gross is not None and excluded is not None:
        net = gross - excluded
    transfer_usd = _num(row.get("native_transfer_value_usd"))
    transfer_is_net = bool(row.get("native_transfer_is_net"))
    net_transfer_usd = transfer_usd if transfer_is_net else None
    addresses = _num(row.get("active_addresses"))
    fees_usd = _num(row.get("fee_usd"))
    fee_window = _fee_window_seconds(row) or window
    return {
        "gross_tx_per_min": per_minute(gross, window),
        "excluded_tx_per_min": per_minute(excluded, window),
        "net_tx_per_min": per_minute(net, window),
        "gross_native_transfer_usd_per_min": per_minute(transfer_usd, window),
        "net_native_transfer_usd_per_min": per_minute(net_transfer_usd, window),
        "address_intensity_per_min": per_minute(addresses, window),
        "fee_usd_per_min": per_minute(fees_usd, fee_window),
        "distribution_per_min": per_minute(_num(row.get("distribution_count")), window),
        "distribution_usd_per_min": per_minute(_num(row.get("distribution_value_usd")), window),
        "token_transfer_per_min": per_minute(_num(row.get("token_transfer_count")), window),
    }


def rank_chains(window_rows):
    bundles = {row["chain_id"]: metric_bundle(row) for row in window_rows}
    components = {
        "net_tx_per_min": {cid: b["net_tx_per_min"] for cid, b in bundles.items()},
        "net_native_transfer_usd_per_min": {
            cid: b["net_native_transfer_usd_per_min"] for cid, b in bundles.items()
        },
        "address_intensity_per_min": {cid: b["address_intensity_per_min"] for cid, b in bundles.items()},
    }
    pct = {name: percentile_ranks(values) for name, values in components.items()}
    zed = {name: zscores(values) for name, values in components.items()}
    ranked = []
    unranked = []
    for row in window_rows:
        cid = row["chain_id"]
        present = [name for name, values in components.items() if cid in pct[name]]
        if not present:
            unranked.append(
                {
                    "chain_id": cid,
                    "reason": "no net tx rate, net native-transfer USD rate, or address intensity in the latest window",
                    "metrics": bundles[cid],
                    "row": row,
                }
            )
            continue
        composite = sum(pct[name][cid] for name in present) / len(present)
        z_vals = [zed[name][cid] for name in present if cid in zed[name]]
        composite_z = sum(z_vals) / len(z_vals) if z_vals else None
        ranked.append(
            {
                "chain_id": cid,
                "composite": composite,
                "composite_z": composite_z,
                "components_used": present,
                "component_count": len(present),
                "percentiles": {name: pct[name].get(cid) for name in components},
                "zscores": {name: zed[name].get(cid) for name in components},
                "metrics": bundles[cid],
                "row": row,
            }
        )
    ranked.sort(key=lambda item: (-item["composite"], -(item["metrics"]["net_tx_per_min"] or -1)))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return {"ranked": ranked, "unranked": unranked, "component_universe_sizes": {name: len(values) for name, values in pct.items()}}
