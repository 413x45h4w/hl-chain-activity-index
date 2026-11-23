"""Cosmos SDK LCD collector.

The ranking window is about 60 seconds of blocks (the last 10 Terra Classic
blocks, at a ~6s block time), not a 2-block slice scaled up to a minute.
A short slice that happens to contain the oracle-vote burst reports several
times the sustained rate.

Excluded from net when every message in the transaction is protocol work:
staking-reward withdrawals, Terra oracle prevote/vote, and Babylon finality
signatures. Those are validator duties, the same class as a Solana vote
transaction. Governance votes and IBC messages stay in net and are labeled
on their own.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..http_util import HttpError, request_json
from .common import blank, failure

REWARD_TYPES = {
    "/cosmos.distribution.v1beta1.MsgWithdrawDelegatorReward",
    "/cosmos.distribution.v1beta1.MsgWithdrawValidatorCommission",
}
# Validator duties. A transaction made only of these is not organic usage.
PROTOCOL_MESSAGE_NAMES = {
    "MsgWithdrawDelegatorReward",
    "MsgWithdrawValidatorCommission",
    "MsgAggregateExchangeRatePrevote",
    "MsgAggregateExchangeRateVote",
    "MsgAddFinalitySig",
}
SEND_TYPES = {
    "/cosmos.bank.v1beta1.MsgSend",
    "/cosmos.bank.v1beta1.MsgMultiSend",
}
IBC_MARKERS = ("/ibc.", "MsgTransfer", "MsgRecvPacket", "MsgAcknowledgement")


def collect_cosmos(chain):
    endpoints = chain.get("endpoints") or []
    if not endpoints:
        return [failure(chain, chain.get("unavailable_reason") or "no LCD configured")]
    last_error = None
    for base in endpoints:
        base = base.rstrip("/")
        try:
            return _from_lcd(chain, base)
        except HttpError as exc:
            last_error = str(exc)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return [failure(chain, last_error or "cosmos LCD failed")]


def message_name(mtype):
    if not mtype:
        return ""
    return str(mtype).rsplit(".", 1)[-1].split("/")[-1]


def tx_is_protocol(messages):
    """True when the transaction is only validator or reward machinery."""
    names = [message_name(msg.get("@type")) for msg in messages or []]
    return bool(names) and all(name in PROTOCOL_MESSAGE_NAMES for name in names)


def choose_window(stamped, target_seconds, max_blocks):
    """Pick the blocks inside a minute-scale window.

    ``stamped`` is newest first and each item has ``t`` in seconds.
    The oldest fetched block is only a timestamp anchor: its transactions
    belong to the previous interval. Returned blocks are the ones whose
    production time is inside ``window_seconds``.
    """
    fetched = list(stamped[:max_blocks])
    if len(fetched) < 2 or fetched[0].get("t") is None:
        return fetched, None
    end = 2
    while end < len(fetched):
        older = fetched[end - 1].get("t")
        if older is None:
            break
        if fetched[0]["t"] - older >= target_seconds:
            break
        end += 1
    anchor = fetched[end - 1]
    if anchor.get("t") is None:
        return fetched[: end - 1], None
    window = fetched[0]["t"] - anchor["t"]
    if window <= 0:
        return fetched[: end - 1], None
    return fetched[: end - 1], window


def _from_lcd(chain, base):
    latest = request_json(f"{base}/cosmos/base/tendermint/v1beta1/blocks/latest", timeout=20)
    block = (latest or {}).get("block") or {}
    header = block.get("header") or {}
    height = int(header.get("height"))
    head_time = _parse_time(header.get("time"))
    target = float(chain.get("target_window_seconds") or 60)
    max_blocks = int(chain.get("max_blocks") or 16)
    fetched = []
    for offset in range(max_blocks):
        block_height = height - offset
        if block_height <= 0:
            break
        if offset == 0:
            body = block
            when = head_time
        else:
            payload = request_json(
                f"{base}/cosmos/base/tendermint/v1beta1/blocks/{block_height}",
                timeout=20,
            )
            body = (payload or {}).get("block") or {}
            when = _parse_time((body.get("header") or {}).get("time"))
        raw_txs = ((body.get("data") or {}).get("txs")) or []
        fetched.append(
            {
                "height": block_height,
                "time": when,
                "t": _iso_seconds(when),
                "raw": len(raw_txs),
            }
        )
        included, span = choose_window(fetched, target, max_blocks)
        if span is not None and span >= target:
            break
    included, window_seconds = choose_window(fetched, target, max_blocks)
    if not included:
        return [failure(chain, "no cosmos blocks")]
    rows = []
    for item in included:
        gross = item["raw"]
        decoded = _txs_at_height(base, item["height"]) if gross else []
        decoded_complete = decoded is not None and len(decoded) >= gross
        sample = _sample_from_block(
            chain,
            base,
            item["height"],
            item["time"],
            gross,
            decoded if decoded_complete else None,
        )
        if not decoded_complete and gross:
            sample["null_reasons"]["net_tx_count"] = (
                f"decoded {0 if decoded is None else len(decoded)} of {gross} txs; "
                "net is withheld rather than scaled from a partial decode"
            )
            sample["excluded_tx_count"] = None
            sample["net_tx_count"] = None
            sample["native_transfer_count"] = None
            sample["native_transfer_value"] = None
            sample["token_transfer_count"] = None
            sample["active_addresses"] = None
            sample["distribution_count"] = None
        rows.append(sample)
    window = _combine(chain, base, rows, window_seconds)
    for row in rows:
        row["sample_kind"] = "block"
    return [window] + rows


def _combine(chain, base, rows, window_seconds):
    head = rows[0]
    if any(r["net_tx_count"] is None and (r["gross_tx_count"] or 0) > 0 for r in rows):
        head = dict(head)
        head["sample_kind"] = "window"
        head["sample_key"] = f"{chain['id']}:window:{head['head_height']}"
        head["window_seconds"] = window_seconds
        return head
    gross = sum(r["gross_tx_count"] or 0 for r in rows)
    excluded = sum(r["excluded_tx_count"] or 0 for r in rows)
    types = {}
    for row in rows:
        for key, value in (row["tx_types"] or {}).items():
            types[key] = types.get(key, 0) + value
    addresses = None
    # Addresses are not deduped across blocks; do not sum unique counts.
    if len(rows) == 1:
        addresses = rows[0]["active_addresses"]
    return blank(
        chain,
        sample_kind="window",
        sample_key=f"{chain['id']}:window:{head['head_height']}",
        observed_at=head["observed_at"],
        window_seconds=window_seconds,
        head_height=head["head_height"],
        source=base,
        gross_tx_count=gross,
        excluded_tx_count=excluded,
        net_tx_count=gross - excluded,
        native_transfer_count=sum(r["native_transfer_count"] or 0 for r in rows),
        native_transfer_value=sum(r["native_transfer_value"] or 0 for r in rows),
        native_transfer_is_net=1,
        token_transfer_count=sum(r["token_transfer_count"] or 0 for r in rows),
        distribution_count=sum(r["distribution_count"] or 0 for r in rows),
        distribution_value_native=None,
        active_addresses=addresses,
        fee_native=sum(r["fee_native"] or 0 for r in rows) if all(r["fee_native"] is not None for r in rows) else None,
        tx_types=types,
        null_reasons={
            **(head.get("null_reasons") or {}),
            "active_addresses": "unique addresses are kept for a single block only; they are not summed across the window",
        }
        if len(rows) > 1
        else (head.get("null_reasons") or {}),
        extra={
            "blocks": len(rows),
            "gross_tx_in_window": gross,
            "excluded_rule": (
                "a transaction is removed from net only when every message is a reward withdrawal, "
                "a Terra oracle prevote/vote, or a Babylon finality signature"
            ),
        },
    )


def _sample_from_block(chain, base, height, when, gross, decoded):
    reasons = {
        "staking_rewards": "inflation is not a transaction; reward withdrawals are counted separately and removed from net when they are the whole tx",
        "distribution_value_native": "reward messages were counted; amounts were not converted because denom fields vary",
    }
    if decoded is None:
        return blank(
            chain,
            sample_kind="block",
            sample_key=f"{chain['id']}:block:{height}",
            observed_at=when,
            head_height=str(height),
            source=base,
            gross_tx_count=gross,
            null_reasons=reasons,
        )
    native_denom = chain.get("native_denom")
    exponent = int(chain.get("native_exponent") or 6)
    types = {}
    excluded = 0
    native_count = 0
    native_value = 0.0
    token_count = 0
    distribution_count = 0
    distribution_value = 0.0
    addresses = set()
    fee_native = 0.0
    fee_known = True
    for tx in decoded:
        body = ((tx.get("tx") or {}).get("body") or {})
        messages = body.get("messages") or []
        for msg in messages:
            mtype = msg.get("@type") or "unknown"
            label = _label(mtype)
            types[label] = types.get(label, 0) + 1
            if message_name(mtype) in REWARD_TYPES or message_name(mtype) in {
                "MsgWithdrawDelegatorReward",
                "MsgWithdrawValidatorCommission",
            }:
                distribution_count += 1
            if mtype in SEND_TYPES:
                amount, native_hits, other_hits = _bank_amounts(msg, native_denom, exponent)
                native_count += native_hits
                native_value += amount
                token_count += other_hits
            elif "MsgTransfer" in mtype:
                token_count += 1
        if tx_is_protocol(messages):
            excluded += 1
        fee, fee_ok = _fee(tx, native_denom, exponent)
        if fee_ok:
            fee_native += fee
        else:
            fee_known = False
        for event in ((tx.get("tx_response") or {}).get("events") or []):
            pass
        signers = ((tx.get("tx") or {}).get("auth_info") or {}).get("signer_infos") or []
        # addresses from messages
        for msg in messages:
            for key in ("from_address", "to_address", "sender", "receiver", "delegator_address", "validator_address"):
                if msg.get(key):
                    addresses.add(msg[key])
        if signers and not addresses:
            pass
    net = gross - excluded
    if not fee_known:
        reasons["fee_native"] = "a fee denom was not the chain native denom"
    return blank(
        chain,
        sample_kind="block",
        sample_key=f"{chain['id']}:block:{height}",
        observed_at=when,
        head_height=str(height),
        source=base,
        gross_tx_count=gross,
        excluded_tx_count=excluded,
        net_tx_count=net,
        native_transfer_count=native_count,
        native_transfer_value=native_value,
        native_transfer_is_net=1,
        token_transfer_count=token_count,
        distribution_count=distribution_count,
        distribution_value_native=None,
        active_addresses=len(addresses),
        fee_native=fee_native if fee_known else None,
        tx_types=types,
        null_reasons=reasons,
        extra={"message_count": sum(types.values())},
    )


def _txs_at_height(base, height):
    rows = []
    offset = 0
    while offset <= 400:
        url = (
            f"{base}/cosmos/tx/v1beta1/txs?query=tx.height%3D{height}"
            f"&pagination.limit=100&pagination.offset={offset}"
        )
        try:
            payload = request_json(url, timeout=20)
        except HttpError:
            # alternate query syntax used by some LCDs
            url = f"{base}/cosmos/tx/v1beta1/txs?events=tx.height%3D{height}&pagination.limit=100&pagination.offset={offset}"
            try:
                payload = request_json(url, timeout=20)
            except HttpError:
                return None if offset == 0 else rows
        txs = (payload or {}).get("txs") or []
        responses = (payload or {}).get("tx_responses") or []
        if not txs and not responses:
            break
        for index, tx in enumerate(txs):
            response = responses[index] if index < len(responses) else {}
            rows.append({"tx": tx, "tx_response": response})
        if len(txs) < 100:
            break
        offset += 100
    return rows


def _label(mtype):
    name = message_name(mtype)
    if name in {"MsgAggregateExchangeRatePrevote", "MsgAggregateExchangeRateVote"}:
        return "oracle_vote"
    if name == "MsgAddFinalitySig":
        return "finality_signature"
    if name in {"MsgWithdrawDelegatorReward", "MsgWithdrawValidatorCommission"} or mtype in REWARD_TYPES:
        return "distribution_withdrawal"
    if mtype in SEND_TYPES:
        return "bank_send"
    if "MsgVote" in mtype:
        return "governance_vote"
    if any(marker in mtype for marker in IBC_MARKERS):
        return "ibc"
    if "MsgDelegate" in mtype or "MsgUndelegate" in mtype or "MsgBeginRedelegate" in mtype:
        return "staking"
    if "MsgExecuteContract" in mtype or "MsgExecute" in mtype:
        return "contract"
    name = mtype.rsplit(".", 1)[-1]
    return name or "other"


def _bank_amounts(msg, native_denom, exponent):
    coins = []
    if msg.get("amount"):
        amount = msg["amount"]
        coins.extend(amount if isinstance(amount, list) else [amount])
    for output in msg.get("outputs") or []:
        coins.extend(output.get("coins") or [])
    native = 0.0
    native_hits = 0
    other_hits = 0
    for coin in coins:
        denom = coin.get("denom")
        try:
            qty = int(coin.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if native_denom and denom == native_denom:
            native += qty / (10**exponent)
            native_hits += 1
        else:
            other_hits += 1
    return native, native_hits, other_hits


def _fee(tx, native_denom, exponent):
    fees = ((((tx.get("tx") or {}).get("auth_info") or {}).get("fee") or {}).get("amount")) or []
    native = 0.0
    ok = True
    for coin in fees:
        denom = coin.get("denom")
        try:
            qty = int(coin.get("amount") or 0)
        except (TypeError, ValueError):
            ok = False
            continue
        if not native_denom or denom == native_denom:
            native += qty / (10**exponent)
        elif qty:
            ok = False
    return native, ok


def _iso_seconds(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _parse_time(value):
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
