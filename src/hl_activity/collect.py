"""Run one collection pass and append it to the history store."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from .collectors import collect_chain
from .hl_client import fetch_mids, fetch_perp_meta
from .mapping import active_index_coins, chains_for_coins, load_chains, load_universe
from .storage import connect, insert_activity, insert_price, insert_run, utc_now

# USDC is the Hyperliquid quote asset. Mids are already USDC prices. When a
# chain's gas token is that numeraire and allMids has no USDC row, the price
# is 1 by definition of the quote, not an external market.
NUMERAIRE = "USDC"


def k_prefix_prices(universe, mids):
    """Price a gas asset from its k-prefix Hyperliquid market, which is 1000 units."""
    out = {}
    for coin in universe.get("coins") or []:
        underlying = coin.get("underlying")
        multiplier = coin.get("multiplier")
        ticker = coin.get("ticker")
        if not underlying or not multiplier or ticker not in mids:
            continue
        out[underlying] = (mids[ticker] / float(multiplier), ticker)
    return out


def priced(chain, mids, delisted, wrapped=None):
    ticker = chain.get("gas_ticker")
    if not ticker:
        return None, None, None
    if ticker in mids:
        source = "hyperliquid_allMids"
        if ticker in delisted:
            source = "hyperliquid_allMids_delisted_market"
        return mids[ticker], ticker, source
    if ticker == NUMERAIRE:
        return 1.0, ticker, "hl_quote_numeraire"
    if wrapped and ticker in wrapped:
        mid, source_ticker = wrapped[ticker]
        return mid, source_ticker, "hyperliquid_allMids_k_prefix"
    return None, ticker, None


def apply_prices(sample, chain, mids, delisted, wrapped=None):
    price, ticker, source = priced(chain, mids, delisted, wrapped)
    reasons = dict(sample.get("null_reasons") or {})
    sample["price_ticker"] = ticker
    sample["price_usd"] = price
    sample["price_source"] = source
    if price is None:
        if ticker:
            reasons["fee_usd"] = f"no Hyperliquid mid for gas ticker {ticker}"
            reasons["native_transfer_value_usd"] = reasons["fee_usd"]
            reasons["distribution_value_usd"] = reasons["fee_usd"]
            if sample.get("emissions_native") not in (None, 0, 0.0):
                reasons["emissions_usd"] = reasons["fee_usd"]
            elif sample.get("emissions_native") in (0, 0.0):
                sample["emissions_usd"] = 0.0
        else:
            reasons["fee_usd"] = "chain has no gas ticker mapped to a Hyperliquid market"
            reasons["native_transfer_value_usd"] = reasons["fee_usd"]
            reasons["emissions_usd"] = reasons["fee_usd"]
    else:
        if sample.get("fee_native") is not None:
            sample["fee_usd"] = sample["fee_native"] * price
        elif "fee_native" not in reasons and sample.get("fee_native") is None:
            reasons.setdefault("fee_usd", reasons.get("fee_native", "fee in native units was not collected"))
        if sample.get("native_transfer_value") is not None:
            sample["native_transfer_value_usd"] = sample["native_transfer_value"] * price
        else:
            reasons.setdefault(
                "native_transfer_value_usd",
                reasons.get("native_transfer_value", "native transfer value was not collected"),
            )
        if sample.get("distribution_value_native") is not None:
            sample["distribution_value_usd"] = sample["distribution_value_native"] * price
        else:
            reasons.setdefault(
                "distribution_value_usd",
                reasons.get("distribution_value_native", "distribution value was not collected"),
            )
        if sample.get("emissions_native") is not None:
            sample["emissions_usd"] = sample["emissions_native"] * price
        else:
            reasons.setdefault(
                "emissions_usd",
                reasons.get("emissions_native", "protocol emissions were not collected"),
            )
    if not sample.get("native_transfer_is_net"):
        reasons.setdefault(
            "net_native_transfer_usd",
            "transfer value is missing or is not known to exclude distributions, so it is left out of the net ranking",
        )
    sample["null_reasons"] = reasons
    sample["tx_types_json"] = sample.get("tx_types") or {}
    sample["null_reasons_json"] = reasons
    sample["extra_json"] = sample.get("extra") or {}
    return sample


def _targets(universe, chains, meta):
    coins = active_index_coins(universe, meta["coins"])
    wanted = chains_for_coins(coins)
    return [chains["by_id"][chain_id] for chain_id in wanted if chain_id in chains["by_id"]]


def collect_once(workers=12):
    collected_at = utc_now()
    universe = load_universe()
    chains = load_chains()
    meta = fetch_perp_meta()
    mids = fetch_mids()
    known = {c["ticker"] for c in universe["coins"]}
    live = {c["name"] for c in meta["coins"]}
    new_tickers = sorted(live - known)
    delisted = {c["name"] for c in meta["coins"] if c["is_delisted"]}
    wrapped = k_prefix_prices(universe, mids)
    targets = _targets(universe, chains, meta)
    missing_chain_cfg = [
        c["chain_id"]
        for c in active_index_coins(universe, meta["coins"])
        if c.get("status") == "mapped" and c.get("chain_id") not in chains["by_id"]
    ]

    samples = []
    errors = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(collect_chain, chain): chain for chain in targets}
        for future in as_completed(futures):
            chain = futures[future]
            try:
                rows = future.result()
            except Exception as exc:
                errors.append({"chain_id": chain["id"], "error": str(exc)})
                continue
            for row in rows:
                samples.append(apply_prices(row, chain, mids, delisted, wrapped))

    conn = connect()
    inserted = 0
    skipped = 0
    try:
        for ticker, mid in mids.items():
            if ticker in known or ticker == NUMERAIRE:
                insert_price(conn, collected_at, ticker, mid, ticker in delisted)
        for sample in samples:
            sample["collected_at"] = collected_at
            if insert_activity(conn, sample):
                inserted += 1
            else:
                skipped += 1
        net_chains = {
            s["chain_id"]
            for s in samples
            if s.get("sample_kind") == "window" and s.get("net_tx_count") is not None and s.get("window_seconds")
        }
        summary = {
            "collected_at": collected_at,
            "chains_targeted": len(targets),
            "chains_with_net": len(net_chains),
            "samples_inserted": inserted,
            "samples_skipped": skipped,
            "new_unmapped_tickers": new_tickers,
            "missing_chain_config": missing_chain_cfg,
            "collector_errors": errors,
            "chains_with_net_ids": sorted(net_chains),
        }
        insert_run(conn, collected_at, summary)
        conn.commit()
    finally:
        conn.close()
    return summary


def coverage_report():
    universe = load_universe()
    meta_names = None
    try:
        meta = fetch_perp_meta()
        meta_names = meta["coins"]
    except Exception:
        meta_names = None
    coins = universe["coins"]
    if meta_names is not None:
        active = active_index_coins(universe, meta_names)
    else:
        active = [c for c in coins if not c.get("is_delisted") and not c.get("is_stablecoin")]
    mapped = [c for c in active if c.get("status") == "mapped"]
    unmapped = [c for c in active if c.get("status") == "unmapped"]
    stables = [c for c in coins if c.get("is_stablecoin")]
    return {
        "hl_universe_size": len(coins),
        "active_non_stable": len(active),
        "active_mapped": len(mapped),
        "active_unmapped": [c["ticker"] for c in unmapped],
        "stable_excluded": [c["ticker"] for c in stables],
        "active_chains": chains_for_coins(mapped),
    }


def dumps(summary):
    return json.dumps(summary, indent=2, sort_keys=True)
