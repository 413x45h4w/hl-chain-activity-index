"""Load and refresh the versioned coin-to-chain mapping."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAPPING_PATH = ROOT / "mapping" / "universe.yaml"
CHAINS_PATH = ROOT / "mapping" / "chains.yaml"
CSV_PATH = ROOT / "mapping" / "universe.csv"

K_PREFIX_NOTE = (
    "Hyperliquid k-prefixed perps are 1000 units of the underlying token "
    "(kPEPE is 1000 PEPE). The chain is the underlying token's chain."
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_yaml(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_universe(path=MAPPING_PATH):
    data = load_yaml(path)
    coins = data.get("coins") or []
    by_ticker = {}
    for coin in coins:
        ticker = coin["ticker"]
        if ticker in by_ticker:
            raise ValueError(f"duplicate mapping row {ticker}")
        by_ticker[ticker] = coin
    data["by_ticker"] = by_ticker
    return data


def load_chains(path=CHAINS_PATH):
    data = load_yaml(path)
    chains = data.get("chains") or []
    by_id = {}
    for chain in chains:
        if chain["id"] in by_id:
            raise ValueError(f"duplicate chain {chain['id']}")
        by_id[chain["id"]] = chain
    data["by_id"] = by_id
    return data


def is_k_prefix(ticker):
    return len(ticker) > 2 and ticker.startswith("k") and ticker[1].isupper()


def index_coins(universe):
    """Non-stable coins that enter the activity index (mapped or unmapped)."""
    rows = []
    for coin in universe["coins"]:
        if coin.get("is_stablecoin") or coin.get("status") == "stable_excluded":
            continue
        rows.append(coin)
    return rows


def active_index_coins(universe, meta_coins=None):
    delisted = {}
    if meta_coins is not None:
        delisted = {c["name"]: c["is_delisted"] for c in meta_coins}
    out = []
    for coin in index_coins(universe):
        flag = delisted.get(coin["ticker"], coin.get("is_delisted"))
        if flag:
            continue
        out.append(coin)
    return out


def chains_for_coins(coins):
    ids = []
    seen = set()
    for coin in coins:
        chain_id = coin.get("chain_id")
        if not chain_id or chain_id in seen:
            continue
        if coin.get("status") != "mapped":
            continue
        seen.add(chain_id)
        ids.append(chain_id)
    return ids


def write_universe(data, path=MAPPING_PATH, csv_path=CSV_PATH):
    payload = {
        "version": data.get("version", 1),
        "updated": data.get("updated"),
        "hl_universe_size": data.get("hl_universe_size"),
        "policy": data.get("policy"),
        "coins": data.get("coins"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "# Hyperliquid perp -> native chain mapping.\n"
            "# Review this file when a new perp appears. sync never guesses a chain;\n"
            "# unknown tickers are appended with status: unmapped.\n"
        )
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True, width=100)
    fields = [
        "ticker",
        "status",
        "is_delisted",
        "is_stablecoin",
        "chain_id",
        "chain_name",
        "asset_role",
        "standard",
        "multiplier",
        "underlying",
        "confidence",
        "sources",
        "notes",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for coin in payload["coins"]:
            sources = coin.get("sources") or []
            if isinstance(sources, list):
                sources = " | ".join(sources)
            writer.writerow(
                {
                    "ticker": coin.get("ticker"),
                    "status": coin.get("status"),
                    "is_delisted": coin.get("is_delisted"),
                    "is_stablecoin": coin.get("is_stablecoin"),
                    "chain_id": coin.get("chain_id") or "",
                    "chain_name": coin.get("chain_name") or "",
                    "asset_role": coin.get("asset_role") or "",
                    "standard": coin.get("standard") or "",
                    "multiplier": coin.get("multiplier") if coin.get("multiplier") is not None else "",
                    "underlying": coin.get("underlying") or "",
                    "confidence": coin.get("confidence") or "",
                    "sources": sources,
                    "notes": (coin.get("notes") or "").replace("\n", " "),
                }
            )


def sync_with_meta(universe, meta, chains):
    """Refresh delist flags. New tickers become unmapped. Never assigns a chain."""
    by_ticker = {c["ticker"]: dict(c) for c in universe["coins"]}
    chain_names = {c["id"]: c["name"] for c in chains["chains"]}
    known = set(by_ticker)
    live = {c["name"]: c for c in meta["coins"]}
    added = []
    for name, row in live.items():
        if name not in by_ticker:
            by_ticker[name] = {
                "ticker": name,
                "status": "unmapped",
                "is_delisted": row["is_delisted"],
                "is_stablecoin": False,
                "chain_id": None,
                "chain_name": None,
                "asset_role": None,
                "standard": None,
                "multiplier": 1000 if is_k_prefix(name) else None,
                "underlying": None,
                "confidence": None,
                "sources": [],
                "notes": (
                    "New Hyperliquid perp since the reviewed mapping. "
                    "Left unmapped on purpose. k-prefix multiplier is recorded "
                    "when the ticker matches the kTOKEN convention, but the "
                    "underlying chain is not guessed."
                    if is_k_prefix(name)
                    else "New Hyperliquid perp since the reviewed mapping. Left unmapped on purpose."
                ),
            }
            added.append(name)
        else:
            by_ticker[name]["is_delisted"] = row["is_delisted"]
            chain_id = by_ticker[name].get("chain_id")
            if chain_id:
                by_ticker[name]["chain_name"] = chain_names.get(chain_id, by_ticker[name].get("chain_name"))
    missing = sorted(known - set(live))
    for ticker in missing:
        note = by_ticker[ticker].get("notes") or ""
        flag = "Absent from the latest meta response."
        if flag not in note:
            by_ticker[ticker]["notes"] = (note + " " + flag).strip()
    ordered = []
    for row in meta["coins"]:
        ordered.append(by_ticker.pop(row["name"]))
    ordered.extend(by_ticker[t] for t in sorted(by_ticker))
    version = int(universe.get("version") or 1)
    if added:
        version += 1
    return {
        "version": version,
        "updated": utc_now(),
        "hl_universe_size": len(meta["coins"]),
        "policy": universe.get("policy"),
        "coins": ordered,
        "added": added,
        "missing_from_meta": missing,
    }
