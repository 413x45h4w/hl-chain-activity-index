"""Hyperliquid public info API. Universe and prices come from here only."""

from __future__ import annotations

from .http_util import request_json

INFO_URL = "https://api.hyperliquid.xyz/info"


def info(body, timeout=30):
    return request_json(INFO_URL, method="POST", body=body, timeout=timeout)


def fetch_perp_meta(timeout=30):
    payload = info({"type": "meta"}, timeout=timeout)
    universe = payload.get("universe") or []
    coins = []
    for row in universe:
        coins.append(
            {
                "name": row.get("name"),
                "is_delisted": bool(row.get("isDelisted")),
                "max_leverage": row.get("maxLeverage"),
                "sz_decimals": row.get("szDecimals"),
                "margin_mode": row.get("marginMode"),
            }
        )
    return {"collateral_token": payload.get("collateralToken"), "coins": coins}


def fetch_mids(timeout=30):
    payload = info({"type": "allMids"}, timeout=timeout)
    out = {}
    for ticker, px in (payload or {}).items():
        try:
            out[ticker] = float(px)
        except (TypeError, ValueError):
            continue
    return out
