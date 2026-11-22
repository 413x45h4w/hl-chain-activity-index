"""Shared sample shape. Missing measurements stay None and carry a reason."""

from __future__ import annotations

from datetime import datetime, timezone


def iso_from_unix(ts):
    if ts is None:
        return None
    moment = datetime.fromtimestamp(float(ts), timezone.utc)
    text = moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if text.endswith(".000Z"):
        return text[:-5] + "Z"
    return text


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lag_seconds(observed_at, now=None):
    if not observed_at:
        return None
    end = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - end).total_seconds())


def blank(chain, **overrides):
    row = {
        "chain_id": chain["id"],
        "sample_kind": "window",
        "sample_key": None,
        "observed_at": None,
        "window_seconds": None,
        "head_height": None,
        "lag_seconds": None,
        "source": None,
        "gross_tx_count": None,
        "excluded_tx_count": None,
        "net_tx_count": None,
        "native_transfer_count": None,
        "native_transfer_value": None,
        "native_transfer_is_net": 0,
        "token_transfer_count": None,
        "distribution_count": None,
        "distribution_value_native": None,
        "active_addresses": None,
        "fee_native": None,
        "tx_types": {},
        "null_reasons": {},
        "extra": {},
    }
    row.update(overrides)
    if row.get("observed_at") and row.get("lag_seconds") is None:
        row["lag_seconds"] = lag_seconds(row["observed_at"])
    return row


def failure(chain, reason):
    return blank(
        chain,
        sample_kind="window",
        sample_key=f"{chain['id']}:failure:{now_iso()}",
        observed_at=now_iso(),
        source=chain.get("collector"),
        null_reasons={"source": reason},
        extra={"minute_note": chain.get("minute_note")},
    )


def hex_int(value):
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return int(value, 16)
