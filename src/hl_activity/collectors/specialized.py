"""Non-EVM collectors. Each one records only fields the endpoint actually returns."""

from __future__ import annotations

from datetime import datetime, timezone

from ..emissions import attach_emissions
from ..http_util import HttpError, json_rpc, request_json
from .common import blank, failure, hex_int, iso_from_unix, now_iso

SPECIALIZED = {}


def _reg(name):
    def wrap(fn):
        SPECIALIZED[name] = fn
        return fn

    return wrap


def _endpoint(chain):
    endpoints = chain.get("endpoints") or []
    return endpoints[0] if endpoints else None


@_reg("xrp")
def collect_xrp(chain):
    url = _endpoint(chain)
    if not url:
        return [failure(chain, "no XRPL endpoint")]
    try:
        payload = request_json(
            url,
            method="POST",
            body={"method": "ledger", "params": [{"ledger_index": "validated", "transactions": True, "expand": True}]},
            timeout=25,
        )
    except HttpError as exc:
        return [failure(chain, str(exc))]
    result = (payload or {}).get("result") or {}
    if result.get("error"):
        return [failure(chain, str(result.get("error")))]
    ledger = result.get("ledger") or {}
    txs = ledger.get("transactions") or []
    types = {}
    native = 0
    native_value = 0.0
    token = 0
    addresses = set()
    fees = 0.0
    for tx in txs:
        if isinstance(tx, str):
            return [
                blank(
                    chain,
                    sample_kind="window",
                    sample_key=f"xrpl:ledger:{ledger.get('ledger_index')}",
                    observed_at=_ripple_time(ledger.get("close_time")),
                    head_height=str(ledger.get("ledger_index")),
                    source=url,
                    gross_tx_count=len(txs),
                    null_reasons={"net_tx_count": "ledger transactions were not expanded"},
                )
            ]
        ttype = tx.get("TransactionType") or "unknown"
        types[ttype] = types.get(ttype, 0) + 1
        if tx.get("Account"):
            addresses.add(tx["Account"])
        if tx.get("Destination"):
            addresses.add(tx["Destination"])
        fee = tx.get("Fee")
        if fee is not None:
            fees += int(fee) / 1_000_000
        if ttype == "Payment":
            amount = tx.get("Amount")
            self_payment = tx.get("Account") and tx.get("Account") == tx.get("Destination")
            if isinstance(amount, str):
                if self_payment:
                    types["self_payment"] = types.get("self_payment", 0) + 1
                else:
                    native += 1
                    native_value += int(amount) / 1_000_000
            else:
                token += 1
    close = _ripple_time(ledger.get("close_time"))
    parent = _ripple_time(ledger.get("parent_close_time"))
    window = None
    if close and parent:
        a = datetime.fromisoformat(close.replace("Z", "+00:00"))
        b = datetime.fromisoformat(parent.replace("Z", "+00:00"))
        delta = (a - b).total_seconds()
        window = delta if delta > 0 else None
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:ledger:{ledger.get('ledger_index')}",
            observed_at=close,
            window_seconds=window,
            head_height=str(ledger.get("ledger_index")),
            source=url,
            gross_tx_count=len(txs),
            excluded_tx_count=0,
            net_tx_count=len(txs),
            native_transfer_count=native,
            native_transfer_value=native_value,
            native_transfer_is_net=1,
            token_transfer_count=token,
            distribution_count=0,
            active_addresses=len(addresses),
            fee_native=fees,
            tx_types=types,
            null_reasons={
                "staking_rewards": "XRPL has no staking-reward transaction class in the ledger tx set",
            "distributions": "no protocol reward tx type was identified in this ledger",
            "self_payment": "XRP payments from an account to itself are counted as transactions but not as peer-to-peer volume",
        },
            extra={"excluded_rule": "none of the validated ledger tx types are consensus votes or block rewards"},
        )
    ]


def _ripple_time(value):
    if value is None:
        return None
    # Ripple epoch 2000-01-01
    return iso_from_unix(946684800 + int(value))


@_reg("cardano")
def collect_cardano(chain):
    base = (_endpoint(chain) or "").rstrip("/")
    if not base:
        return [failure(chain, "no Koios endpoint")]
    try:
        blocks = request_json(f"{base}/blocks?order=block_height.desc&limit=5", timeout=40)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    if not isinstance(blocks, list) or not blocks:
        return [failure(chain, "Koios returned no blocks")]
    rows = []
    for index, block in enumerate(blocks):
        ts = block.get("block_time")
        observed = ts if isinstance(ts, str) else None
        if observed and not observed.endswith("Z"):
            observed = observed.replace("+00:00", "Z")
            if not observed.endswith("Z"):
                observed = observed + "Z"
        gross = block.get("tx_count")
        fees = block.get("fees")
        window = None
        if index + 1 < len(blocks):
            window = _delta(observed, _cardano_time(blocks[index + 1].get("block_time")))
        rows.append(
            blank(
                chain,
                sample_kind="window" if index == 0 else "block",
                sample_key=f"{chain['id']}:block:{block.get('block_height')}",
                observed_at=observed,
                window_seconds=window,
                head_height=str(block.get("block_height")),
                source=base,
                gross_tx_count=gross,
                excluded_tx_count=0,
                net_tx_count=gross,
                fee_native=(int(fees) / 1_000_000) if fees not in (None, "") else None,
                tx_types={"transactions": gross},
                null_reasons={
                    "tx_types": "Koios block row has a tx count and fees, not a script/governance/transfer split",
                    "native_transfer_value": "out_sum includes change and is not used as peer-to-peer volume",
                    "token_transfer_count": "not in the blocks endpoint",
                    "active_addresses": "not in the blocks endpoint",
                    "distributions": "stake rewards are epoch-level and are not in the block tx count as a separable field here",
                },
            )
        )
    return rows


def _cardano_time(value):
    if not isinstance(value, str):
        return None
    text = value.replace("+00:00", "Z")
    if not text.endswith("Z"):
        text += "Z"
    return text


def _tron_root(url):
    marker = "/wallet/"
    if marker in url:
        return url.split(marker, 1)[0]
    return url.rstrip("/")


def _tron_fee_sun(info):
    """User-paid TRX fees. Energy and bandwidth totals are units, not sun."""
    total = 0
    for item in info or []:
        receipt = item.get("receipt") or {}
        total += int(receipt.get("net_fee") or 0)
        total += int(receipt.get("energy_fee") or 0)
    return total


@_reg("tron")
def collect_tron(chain):
    url = _endpoint(chain) or "https://api.trongrid.io/wallet/getnowblock"
    root = _tron_root(url)
    try:
        head = request_json(f"{root}/wallet/getnowblock", method="POST", body={}, timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    head_num = ((head or {}).get("block_header") or {}).get("raw_data", {}).get("number")
    if head_num is None:
        return [failure(chain, "tron block had no number")]
    # About 30 seconds. The oldest block is a timestamp anchor only.
    fetched = []
    for offset in range(10):
        number = head_num - offset
        try:
            if offset == 0:
                block = head
            else:
                block = request_json(f"{root}/wallet/getblockbynum", method="POST", body={"num": number}, timeout=20)
            info = request_json(
                f"{root}/wallet/gettransactioninfobyblocknum",
                method="POST",
                body={"num": number},
                timeout=25,
            )
        except HttpError as exc:
            if offset == 0:
                return [failure(chain, str(exc))]
            break
        header = ((block or {}).get("block_header") or {}).get("raw_data", {})
        fetched.append({"number": number, "header": header, "txs": (block or {}).get("transactions") or [], "info": info or []})
        if len(fetched) >= 2:
            newest = fetched[0]["header"].get("timestamp")
            oldest = fetched[-1]["header"].get("timestamp")
            if newest and oldest and (newest - oldest) >= 24_000:
                break
    if len(fetched) < 2:
        included = fetched
        window = None
    else:
        included = fetched[:-1]
        window = (included[0]["header"]["timestamp"] - fetched[-1]["header"]["timestamp"]) / 1000
        if window <= 0:
            window = None
    types = {}
    excluded = 0
    native = 0
    native_value = 0.0
    token = 0
    addresses = set()
    gross = 0
    fee_sun = 0
    for item in included:
        fee_sun += _tron_fee_sun(item["info"])
        for tx in item["txs"]:
            gross += 1
            contracts = ((tx.get("raw_data") or {}).get("contract")) or []
            labels = []
            for contract in contracts:
                ctype = contract.get("type") or "unknown"
                labels.append(ctype)
                types[ctype] = types.get(ctype, 0) + 1
                value = ((contract.get("parameter") or {}).get("value") or {})
                if value.get("owner_address"):
                    addresses.add(value["owner_address"])
                if value.get("to_address"):
                    addresses.add(value["to_address"])
                if ctype == "TransferContract":
                    native += 1
                    native_value += int(value.get("amount") or 0) / 1_000_000
                elif ctype == "TransferAssetContract":
                    token += 1
            if labels and all(label == "WithdrawBalanceContract" for label in labels):
                excluded += 1
    ts = included[0]["header"].get("timestamp") if included else None
    observed = iso_from_unix(ts / 1000) if ts else None
    number = included[0]["number"] if included else head_num
    rows = [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{number}",
            observed_at=observed,
            window_seconds=window,
            head_height=str(number),
            source=root,
            gross_tx_count=gross,
            excluded_tx_count=excluded,
            net_tx_count=gross - excluded,
            native_transfer_count=native,
            native_transfer_value=native_value,
            native_transfer_is_net=1,
            token_transfer_count=token,
            distribution_count=excluded,
            active_addresses=len(addresses),
            fee_native=fee_sun / 1_000_000,
            tx_types=types,
            null_reasons={
                "token_transfer_count": "TRC-20 transfers inside TriggerSmartContract are not decoded; only TRC-10 TransferAssetContract is counted",
            },
            extra={
                "excluded_rule": "WithdrawBalanceContract reward claims",
                "blocks": len(included),
                "fee_window_seconds": window,
            },
        )
    ]
    _attach_tron_emissions(rows[0], root, len(included), window)
    return rows


def _attach_tron_emissions(sample, root, block_count, window):
    try:
        payload = request_json(f"{root}/wallet/getchainparameters", method="POST", body={}, timeout=20)
        params = {item.get("key"): item.get("value") for item in payload.get("chainParameter") or []}
        producer = params.get("getWitnessPayPerBlock")
        voters = params.get("getWitness127PayPerBlock")
        if producer is None or voters is None:
            raise ValueError("witness pay parameters missing")
        trx_per_block = (int(producer) + int(voters)) / 1_000_000
    except Exception as exc:
        sample.setdefault("null_reasons", {})["emissions_native"] = f"tron witness pay unavailable: {exc}"
        return
    attach_emissions(
        sample,
        trx_per_block * block_count,
        window,
        "getWitnessPayPerBlock plus getWitness127PayPerBlock, from getChainParameters",
    )


@_reg("aptos")
def collect_aptos(chain):
    base = (_endpoint(chain) or "").rstrip("/")
    try:
        info = request_json(base, timeout=20)
        height = int(info["block_height"])
        rows = []
        for offset in range(4):
            h = height - offset
            block = request_json(f"{base}/blocks/by_height/{h}?with_transactions=true", timeout=25)
            txs = block.get("transactions") or []
            types = {}
            excluded = 0
            native = 0
            addresses = set()
            for tx in txs:
                kind = tx.get("type") or "unknown"
                types[kind] = types.get(kind, 0) + 1
                if kind in (
                    "block_metadata_transaction",
                    "state_checkpoint_transaction",
                    "validator_transaction",
                    "block_epilogue_transaction",
                ):
                    excluded += 1
                    continue
                if tx.get("sender"):
                    addresses.add(tx["sender"])
                payload = tx.get("payload") or {}
                function = payload.get("function") or ""
                if function.endswith("::aptos_account::transfer") or function.endswith("::coin::transfer"):
                    native += 1
            ts = int(block["block_timestamp"]) / 1_000_000
            rows.append(
                blank(
                    chain,
                    sample_kind="block",
                    sample_key=f"{chain['id']}:block:{h}",
                    observed_at=iso_from_unix(ts),
                    head_height=str(h),
                    source=base,
                    gross_tx_count=len(txs),
                    excluded_tx_count=excluded,
                    net_tx_count=len(txs) - excluded,
                    native_transfer_count=native,
                    native_transfer_is_net=1,
                    active_addresses=len(addresses),
                    tx_types=types,
                    null_reasons={
                        "native_transfer_value": "transfer amounts are not summed; only aptos_account::transfer and coin::transfer calls are counted",
                        "token_transfer_count": "fungible-asset transfers are not fully decoded",
                        "fee_native": "gas units are present per tx but the price conversion was not applied",
                        "distributions": "staking rewards are not part of block_metadata; excluded types are metadata, checkpoint, and validator txs",
                    },
                    extra={"excluded_rule": "block_metadata, state_checkpoint, validator transactions"},
                )
            )
    except HttpError as exc:
        return [failure(chain, str(exc))]
    except Exception as exc:
        return [failure(chain, f"{type(exc).__name__}: {exc}")]
    _fill_windows(rows)
    return _with_window(chain, rows)


@_reg("sui")
def collect_sui(chain):
    url = _endpoint(chain)
    if url and "graphql" in url:
        return _sui_graphql(chain, url)
    try:
        latest = int(json_rpc(url, "sui_getLatestCheckpointSequenceNumber", [], timeout=20))
        rows = []
        for offset in range(6):
            seq = latest - offset
            checkpoint = json_rpc(url, "sui_getCheckpoint", [str(seq)], timeout=20)
            txs = checkpoint.get("transactions") or []
            ts = int(checkpoint.get("timestampMs")) / 1000
            rows.append(
                blank(
                    chain,
                    sample_kind="block",
                    sample_key=f"{chain['id']}:checkpoint:{seq}",
                    observed_at=iso_from_unix(ts),
                    head_height=str(seq),
                    source=url,
                    gross_tx_count=len(txs),
                    excluded_tx_count=None,
                    net_tx_count=None,
                    tx_types={"checkpoint_txs": len(txs)},
                    null_reasons={
                        "net_tx_count": "checkpoint digest list has no system-vs-user label; net is withheld",
                        "native_transfer_value": "checkpoint payload has digests only",
                        "token_transfer_count": "not in the checkpoint object",
                        "active_addresses": "not in the checkpoint object",
                        "fee_native": "not in the checkpoint object",
                        "distributions": "staking distributions are not identified from digests",
                    },
                    extra={"network_total_transactions": checkpoint.get("networkTotalTransactions")},
                )
            )
    except HttpError as exc:
        return [failure(chain, str(exc))]
    _fill_windows(rows)
    if not rows:
        return [failure(chain, "no sui checkpoints")]
    head = dict(rows[0])
    head["sample_kind"] = "window"
    head["sample_key"] = f"{chain['id']}:window:{head['head_height']}"
    gross = sum(r["gross_tx_count"] or 0 for r in rows)
    head["gross_tx_count"] = gross
    head["window_seconds"] = sum(r["window_seconds"] or 0 for r in rows) or head.get("window_seconds")
    head["net_tx_count"] = None
    return [head] + rows


@_reg("near")
def collect_near(chain):
    url = _endpoint(chain)
    try:
        block = json_rpc(url, "block", {"finality": "final"}, timeout=20)
        header = block["header"]
        chunks = block.get("chunks") or []
        types = {}
        gross = 0
        native = 0
        addresses = set()
        for chunk in chunks[:8]:
            detail = json_rpc(url, "chunk", {"chunk_id": chunk["chunk_hash"]}, timeout=20)
            txs = detail.get("transactions") or []
            gross += len(txs)
            for tx in txs:
                signer = tx.get("signer_id")
                receiver = tx.get("receiver_id")
                if signer:
                    addresses.add(signer)
                if receiver:
                    addresses.add(receiver)
                for action in tx.get("actions") or []:
                    if isinstance(action, dict):
                        kind = next(iter(action.keys()), "unknown")
                    elif isinstance(action, str):
                        kind = action
                    else:
                        kind = "unknown"
                    types[kind] = types.get(kind, 0) + 1
                    if kind == "Transfer":
                        native += 1
        ts = int(header["timestamp"]) / 1_000_000_000
    except HttpError as exc:
        return [failure(chain, str(exc))]
    except Exception as exc:
        return [failure(chain, f"{type(exc).__name__}: {exc}")]
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{header.get('height')}",
            observed_at=iso_from_unix(ts),
            window_seconds=None,
            head_height=str(header.get("height")),
            source=url,
            gross_tx_count=gross,
            excluded_tx_count=0,
            net_tx_count=gross,
            native_transfer_count=native,
            native_transfer_is_net=1,
            active_addresses=len(addresses),
            tx_types=types,
            null_reasons={
                "window_seconds": "a single NEAR block was read; the parent interval was not fetched",
                "native_transfer_value": "transfer deposit amounts were not summed",
                "token_transfer_count": "FT transfers inside FunctionCall are not decoded",
                "fee_native": "not summed",
                "distributions": "protocol rewards are not user transactions in the chunk tx list",
            },
            extra={"chunks_fetched": min(len(chunks), 8), "chunks_in_block": len(chunks)},
        )
    ]


@_reg("algorand")
def collect_algorand(chain):
    base = (_endpoint(chain) or "").rstrip("/")
    try:
        status = request_json(f"{base}/v2/status", timeout=20, headers={"Accept": "application/json"})
        rnd = status["last-round"]
        block = request_json(f"{base}/v2/blocks/{rnd}", timeout=25, headers={"Accept": "application/json"})
    except HttpError as exc:
        return [failure(chain, str(exc))]
    txns = ((block.get("block") or block).get("txns")) or block.get("transactions") or []
    # indexer and algod shapes differ. algod block uses "txns" with "txn" inside.
    types = {}
    excluded = 0
    native = 0
    native_value = 0.0
    token = 0
    addresses = set()
    for wrapper in txns:
        txn = wrapper.get("txn") or wrapper
        ttype = txn.get("type") or "unknown"
        types[ttype] = types.get(ttype, 0) + 1
        if ttype in ("keyreg", "hb"):
            excluded += 1
        snd = txn.get("snd")
        rcv = txn.get("rcv")
        if isinstance(snd, str):
            addresses.add(snd)
        if isinstance(rcv, str):
            addresses.add(rcv)
        if ttype == "pay":
            native += 1
            native_value += int(txn.get("amt") or 0) / 1_000_000
        elif ttype == "axfer":
            token += 1
    ts = (block.get("block") or block).get("ts") or status.get("last-round")
    observed = iso_from_unix(ts) if isinstance(ts, int) and ts > 1_000_000_000 else now_iso()
    gross = len(txns)
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:round:{rnd}",
            observed_at=observed,
            window_seconds=None,
            head_height=str(rnd),
            source=base,
            gross_tx_count=gross,
            excluded_tx_count=excluded,
            net_tx_count=gross - excluded,
            native_transfer_count=native,
            native_transfer_value=native_value,
            native_transfer_is_net=1,
            token_transfer_count=token,
            active_addresses=len(addresses) or None,
            tx_types=types,
            null_reasons={
                "window_seconds": "single round; parent round time was not fetched",
                "active_addresses": "algod often returns addresses as raw bytes, which were not counted",
                "fee_native": "not summed",
                "distributions": "consensus rewards inside the block header are not part of the tx list; keyreg and heartbeat txs are excluded",
            },
            extra={"excluded_rule": "keyreg and heartbeat"},
        )
    ]


@_reg("stellar")
def collect_stellar(chain):
    base = (_endpoint(chain) or "").rstrip("/")
    try:
        payload = request_json(f"{base}/ledgers?order=desc&limit=5", timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    records = ((payload or {}).get("_embedded") or {}).get("records") or []
    if not records:
        return [failure(chain, "horizon returned no ledgers")]
    rows = []
    for index, ledger in enumerate(records):
        gross = ledger.get("successful_transaction_count")
        failed = ledger.get("failed_transaction_count") or 0
        total = (gross or 0) + failed
        window = None
        if index + 1 < len(records):
            window = _delta(ledger.get("closed_at"), records[index + 1].get("closed_at"))
        rows.append(
            blank(
                chain,
                sample_kind="window" if index == 0 else "block",
                sample_key=f"{chain['id']}:ledger:{ledger.get('sequence')}",
                observed_at=ledger.get("closed_at"),
                window_seconds=window,
                head_height=str(ledger.get("sequence")),
                source=base,
                gross_tx_count=total,
                excluded_tx_count=0,
                net_tx_count=total,
                fee_native=_stellar_fee(base, ledger.get("sequence")) if index == 0 else None,
                tx_types={
                    "successful": gross,
                    "failed": failed,
                    "operations": ledger.get("operation_count"),
                },
                null_reasons={
                    "native_transfer_count": "ledger summary has operation_count but not a payment split",
                    "token_transfer_count": "not in the ledger summary",
                    "active_addresses": "not in the ledger summary",
                    "distributions": "inflation is disabled on Stellar; no reward tx class is in this summary",
                    "fee_native": None if index == 0 else "fee_charged is summed for the newest ledger only",
                },
            )
        )
    if rows and rows[0].get("fee_native") is None:
        rows[0]["null_reasons"]["fee_native"] = "Horizon ledger has no total_fees field and the transaction list did not return fee_charged"
    elif rows:
        rows[0]["null_reasons"].pop("fee_native", None)
        rows[0].setdefault("extra", {})
        rows[0]["extra"]["fee_window_seconds"] = rows[0].get("window_seconds")
    return rows


def _stellar_fee(base, sequence):
    """Sum fee_charged. Horizon no longer puts total_fees on the ledger."""
    if sequence is None:
        return None
    total = 0
    url = f"{base}/ledgers/{sequence}/transactions?limit=200&order=asc"
    pages = 0
    while url and pages < 6:
        pages += 1
        try:
            payload = request_json(url, timeout=20)
        except HttpError:
            return None
        records = ((payload or {}).get("_embedded") or {}).get("records") or []
        for tx in records:
            if tx.get("fee_charged") is None:
                continue
            total += int(tx["fee_charged"])
        if len(records) < 200:
            break
        url = (((payload or {}).get("_links") or {}).get("next") or {}).get("href")
    return total / 10_000_000


@_reg("hedera")
def collect_hedera(chain):
    base = (_endpoint(chain) or "").rstrip("/")
    try:
        blocks = request_json(f"{base}/api/v1/blocks?limit=1&order=desc", timeout=20)
        txs = request_json(f"{base}/api/v1/transactions?limit=100&order=desc", timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    block = ((blocks or {}).get("blocks") or [{}])[0]
    transactions = (txs or {}).get("transactions") or []
    types = {}
    for tx in transactions:
        name = tx.get("name") or "unknown"
        types[name] = types.get(name, 0) + 1
    start = block.get("timestamp", {}).get("from")
    end = block.get("timestamp", {}).get("to")
    observed = iso_from_unix(float(end)) if end else None
    window = None
    if start and end:
        window = float(end) - float(start)
    count = block.get("count")
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{block.get('number')}",
            observed_at=observed,
            window_seconds=window if window and window > 0 else None,
            head_height=str(block.get("number")),
            source=base,
            gross_tx_count=count,
            excluded_tx_count=None,
            net_tx_count=None,
            tx_types=types,
            null_reasons={
                "net_tx_count": "block count is complete, but the 100 most recent transactions are only a type sample and are not used to scale exclusions",
                "tx_types": "type mix is the latest 100 transactions, not the whole block",
                "native_transfer_value": "not summed",
                "active_addresses": "not summed",
                "fee_native": "not summed",
                "distributions": "staking reward transfers from account 0.0.800 are inside CRYPTOTRANSFER and were not separated",
            },
            extra={"type_sample_size": len(transactions)},
        )
    ]


@_reg("stacks")
def collect_stacks(chain):
    base = (_endpoint(chain) or "").rstrip("/")
    try:
        payload = request_json(f"{base}/extended/v2/blocks?limit=5", timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    results = (payload or {}).get("results") or []
    if not results:
        return [failure(chain, "hiro returned no blocks")]
    rows = []
    for index, block in enumerate(results):
        gross = block.get("tx_count")
        ts = block.get("block_time")
        window = None
        if index + 1 < len(results):
            prev = results[index + 1].get("block_time")
            if ts and prev:
                window = ts - prev
        rows.append(
            blank(
                chain,
                sample_kind="window" if index == 0 else "block",
                sample_key=f"{chain['id']}:block:{block.get('height')}",
                observed_at=iso_from_unix(ts),
                window_seconds=window if window and window > 0 else None,
                head_height=str(block.get("height")),
                source=base,
                gross_tx_count=gross,
                null_reasons={
                    "net_tx_count": "block summary tx_count includes coinbase and tenure transactions; they were not fetched individually, so net is withheld",
                    "fee_native": "not in the v2 block summary used here",
                    "native_transfer_count": "not in the block summary",
                    "active_addresses": "not in the block summary",
                },
            )
        )
    return rows


@_reg("conflux")
def collect_conflux(chain):
    url = _endpoint(chain)
    try:
        epoch = json_rpc(url, "cfx_epochNumber", ["latest_confirmed"], timeout=20)
        block = json_rpc(url, "cfx_getBlockByEpochNumber", [epoch, True], timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    txs = block.get("transactions") or []
    types = {}
    native = 0
    native_value = 0.0
    addresses = set()
    for tx in txs:
        if isinstance(tx, str):
            txs = []
            break
        data = tx.get("data") or "0x"
        value = hex_int(tx.get("value"))
        if data in ("0x", "0x0") and value > 0:
            kind = "native_transfer"
            native += 1
            native_value += value / 10**18
        elif tx.get("to") in (None, "null"):
            kind = "contract_create"
        else:
            kind = "contract_call"
        types[kind] = types.get(kind, 0) + 1
        if tx.get("from"):
            addresses.add(tx["from"].lower())
        if tx.get("to"):
            addresses.add(tx["to"].lower())
    ts = hex_int(block.get("timestamp"))
    tx_list = block.get("transactions")
    gross = len(tx_list) if isinstance(tx_list, list) else 0
    decoded = isinstance(tx_list, list) and (gross == 0 or isinstance(tx_list[0], dict))
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:epoch:{hex_int(epoch)}",
            observed_at=iso_from_unix(ts),
            window_seconds=None,
            head_height=str(hex_int(epoch)),
            source=url,
            gross_tx_count=gross if isinstance(tx_list, list) else None,
            excluded_tx_count=0 if decoded else None,
            net_tx_count=gross if decoded else None,
            native_transfer_count=native,
            native_transfer_value=native_value,
            native_transfer_is_net=1,
            active_addresses=len(addresses) or None,
            tx_types=types,
            null_reasons={
                "window_seconds": "parent epoch time was not fetched",
                "fee_native": "gas used is on the receipt, which was not requested",
                "token_transfer_count": "logs were not requested",
                "distributions": "PoW/PoS rewards are not user transactions in the epoch tx list",
            },
        )
    ]


@_reg("kaspa")
def collect_kaspa(chain):
    base = (_endpoint(chain) or "https://api.kaspa.org").rstrip("/")
    try:
        score_payload = request_json(f"{base}/info/virtual-chain-blue-score", timeout=20)
        score = score_payload.get("blueScore") if isinstance(score_payload, dict) else score_payload
        blocks = request_json(
            f"{base}/blocks-from-bluescore?blueScore={int(score) - 30}&includeTransactions=true",
            timeout=25,
        )
    except HttpError as exc:
        return [failure(chain, str(exc))]
    except Exception as exc:
        return [failure(chain, f"{type(exc).__name__}: {exc}")]
    if isinstance(blocks, dict):
        blocks = blocks.get("blocks") or blocks.get("data") or []
    if not isinstance(blocks, list) or not blocks:
        return [failure(chain, f"kaspa block list empty at score {score}")]
    # One DAG sample: sum tx counts over the returned blocks and the time span.
    total = 0
    times = []
    for block in blocks:
        header = block.get("header") or block
        txs = block.get("transactions") or []
        count = block.get("txCount") or block.get("transactionCount") or (len(txs) if txs else None)
        if count is None:
            # verbose header-only responses often include tx ids elsewhere
            count = len(block.get("verboseData", {}).get("transactionIds") or []) or None
        if count is not None:
            total += count
        ts = header.get("timestamp") or (block.get("verboseData") or {}).get("timestamp")
        if ts:
            times.append(int(ts))
    if not times:
        return [failure(chain, "kaspa blocks had no timestamps")]
    # kaspa timestamps are milliseconds
    start, end = min(times), max(times)
    window = (end - start) / 1000 if end > start else None
    blue = (blocks[-1].get("verboseData") or {}).get("blueScore") or score
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:bluescore:{blue}",
            observed_at=iso_from_unix(end / 1000),
            window_seconds=window,
            head_height=str(blue),
            source=base,
            gross_tx_count=total if total else None,
            excluded_tx_count=len(blocks) if total else None,
            net_tx_count=(total - len(blocks)) if total else None,
            distribution_count=len(blocks) if total else None,
            tx_types={"blocks": len(blocks), "coinbase_assumed": len(blocks), "txs": total},
            null_reasons={
                "excluded_tx_count": "one coinbase per block is assumed and removed; merged blocks can contain extra coinbase txs, so this exclusion can under-remove",
                "native_transfer_value": "not in the non-verbose block list",
                "active_addresses": "not available",
                "fee_native": "not available",
                "token_transfer_count": "not available",
            },
            extra={"blocks_in_window": len(blocks)},
        )
    ]


@_reg("polkadot")
def collect_polkadot(chain):
    base = (_endpoint(chain) or "").rstrip("/")
    try:
        head = request_json(f"{base}/blocks/head", timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    extrinsics = head.get("extrinsics") or []
    types = {}
    excluded = 0
    native = 0
    addresses = set()
    for ext in extrinsics:
        method = ext.get("method") or {}
        pallet = ((method.get("pallet") or "") + "." + (method.get("method") or "")).strip(".")
        if not pallet:
            pallet = ext.get("method") if isinstance(ext.get("method"), str) else "unknown"
        types[pallet] = types.get(pallet, 0) + 1
        if pallet.lower() in {"timestamp.set", "parasinherent.enter", "parainherent.enter"} or pallet.startswith("timestamp"):
            excluded += 1
        if "payoutstakers" in pallet.lower() or "payout_stakers" in pallet.lower():
            excluded += 1
        if "transfer" in pallet.lower() and "balances" in pallet.lower():
            native += 1
        signer = (ext.get("signature") or {}).get("signer") or ext.get("signer")
        if isinstance(signer, str):
            addresses.add(signer)
        elif isinstance(signer, dict):
            ident = signer.get("id") or signer.get("Id")
            if ident:
                addresses.add(str(ident))
    number = head.get("number")
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{number}",
            observed_at=now_iso(),
            window_seconds=6,
            head_height=str(number),
            source=base,
            gross_tx_count=len(extrinsics),
            excluded_tx_count=excluded,
            net_tx_count=len(extrinsics) - excluded,
            native_transfer_count=native,
            native_transfer_is_net=1,
            active_addresses=len(addresses) or None,
            tx_types=types,
            null_reasons={
                "observed_at": "sidecar block payload used here has no unix timestamp; observed_at is the collection time and lag is not a chain-head age",
                "window_seconds": "Polkadot slot target is 6s; parent delta was not read",
                "native_transfer_value": "balances transfer amounts were not decoded from sidecar args",
                "fee_native": "not summed",
                "lag_seconds": "not a chain-head lag because the block time was not in the payload",
            },
            extra={"excluded_rule": "timestamp inherent, para inherent, staking payoutStakers"},
        )
    ]


@_reg("monero")
def collect_monero(chain):
    url = _endpoint(chain)
    try:
        header = request_json(
            url,
            method="POST",
            body={"jsonrpc": "2.0", "id": "0", "method": "get_last_block_header"},
            timeout=20,
        )
        info = (header or {}).get("result", {}).get("block_header") or {}
        height = info.get("height")
        block = request_json(
            url,
            method="POST",
            body={"jsonrpc": "2.0", "id": "0", "method": "get_block", "params": {"height": height}},
            timeout=20,
        )
    except HttpError as exc:
        return [failure(chain, str(exc))]
    body = (block or {}).get("result") or {}
    blob = body.get("block_header") or info
    tx_hashes = body.get("tx_hashes") or []
    # num_txes on the header excludes the miner tx on some nodes and includes it on others.
    gross = blob.get("num_txes")
    if gross is None:
        gross = len(tx_hashes) + 1
    ts = blob.get("timestamp")
    reward = blob.get("reward")
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{height}",
            observed_at=iso_from_unix(ts),
            window_seconds=None,
            head_height=str(height),
            source=url,
            gross_tx_count=gross,
            excluded_tx_count=1,
            net_tx_count=(gross - 1) if gross is not None else len(tx_hashes),
            distribution_count=1,
            distribution_value_native=(reward / 1e12) if reward is not None else None,
            tx_types={"miner_tx": 1, "non_miner": len(tx_hashes)},
            null_reasons={
                "window_seconds": "parent block time was not fetched",
                "native_transfer_value": "Monero amounts are not public",
                "active_addresses": "Monero addresses are not public",
                "token_transfer_count": "no token layer is read",
                "fee_native": "fees are mixed into the miner reward on this header and were not separated",
                "minute_level": chain.get("minute_note"),
            },
            extra={"tx_hash_count": len(tx_hashes)},
        )
    ]


@_reg("filecoin")
def collect_filecoin(chain):
    url = _endpoint(chain)
    try:
        head = json_rpc(url, "Filecoin.ChainHead", [], timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    height = (head or {}).get("Height")
    cids = (head or {}).get("Cids") or []
    messages = 0
    errors = []
    for cid in cids[:4]:
        try:
            result = json_rpc(url, "Filecoin.ChainGetBlockMessages", [cid], timeout=25)
        except HttpError as exc:
            errors.append(str(exc))
            continue
        bls = (result or {}).get("BlsMessages") or []
        secpk = (result or {}).get("SecpkMessages") or []
        messages += len(bls) + len(secpk)
    if messages == 0 and errors:
        return [failure(chain, "; ".join(errors[:2]))]
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:tipset:{height}",
            observed_at=now_iso(),
            window_seconds=30,
            head_height=str(height),
            source=url,
            gross_tx_count=messages,
            excluded_tx_count=None,
            net_tx_count=None,
            tx_types={"messages": messages, "blocks_in_tipset": len(cids)},
            null_reasons={
                "net_tx_count": "message method numbers were not decoded, so miner and system messages were not removed",
                "observed_at": "tipset blocks were not decoded for timestamp; observed_at is collection time",
                "window_seconds": "Filecoin epoch target is 30s and was not measured from parent epochs",
                "native_transfer_value": "not decoded",
                "active_addresses": "not decoded",
                "fee_native": "not decoded",
            },
        )
    ]


@_reg("arweave")
def collect_arweave(chain):
    base = (_endpoint(chain) or "https://arweave.net").rstrip("/")
    try:
        block = request_json(f"{base}/block/current", timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    txs = block.get("txs") or []
    gross = len(txs)
    ts = block.get("timestamp")
    reward = block.get("reward")
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{block.get('height')}",
            observed_at=iso_from_unix(ts),
            window_seconds=None,
            head_height=str(block.get("height")),
            source=base,
            gross_tx_count=gross,
            excluded_tx_count=0,
            net_tx_count=gross,
            distribution_value_native=(int(reward) / 10**12) if reward not in (None, "") else None,
            fee_native=(int(block.get("tx_fee") or 0) / 10**12) if block.get("tx_fee") not in (None, "") else None,
            tx_types={"transactions": gross},
            null_reasons={
                "window_seconds": "parent block time was not fetched",
                "native_transfer_value": "transaction values are not in the block header list of tx ids",
                "active_addresses": "not in the block header",
                "token_transfer_count": "not available",
                "distributions": "block reward is recorded as distribution value; it is not a transaction in gross, so net equals gross",
            },
            extra={"weave_size": block.get("weave_size")},
        )
    ]


@_reg("mina")
def collect_mina(chain):
    url = _endpoint(chain) or "https://api.minaexplorer.com/graphql"
    query = {
        "query": "query { blocks(limit: 5, sortBy: DATETIME_DESC) { blockHeight txCount dateTime snarkFees canonical } }"
    }
    try:
        payload = request_json(url, method="POST", body=query, timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    blocks = (((payload or {}).get("data") or {}).get("blocks")) or []
    canonical = [b for b in blocks if b.get("canonical") is not False]
    if not canonical:
        return [failure(chain, f"mina graphql returned no blocks: {str(payload)[:240]}")]
    rows = []
    for index, block in enumerate(canonical):
        observed = block.get("dateTime")
        if observed and not str(observed).endswith("Z"):
            observed = str(observed).replace("+00:00", "Z")
        window = None
        if index + 1 < len(canonical):
            window = _delta(observed, canonical[index + 1].get("dateTime"))
        gross = block.get("txCount")
        rows.append(
            blank(
                chain,
                sample_kind="window" if index == 0 else "block",
                sample_key=f"{chain['id']}:block:{block.get('blockHeight')}",
                observed_at=observed if isinstance(observed, str) else None,
                window_seconds=window,
                head_height=str(block.get("blockHeight")),
                source=url,
                gross_tx_count=gross,
                excluded_tx_count=0,
                net_tx_count=gross,
                fee_native=(int(block["snarkFees"]) / 1e9) if block.get("snarkFees") not in (None, "") else None,
                tx_types={"user_commands": gross},
                null_reasons={
                    "native_transfer_value": "not in this block query",
                    "active_addresses": "not in this block query",
                    "token_transfer_count": "not in this block query",
                    "distributions": "coinbase is not included in txCount on Mina explorer; net equals the user-command count",
                },
            )
        )
    return rows


@_reg("starknet")
def collect_starknet(chain):
    url = _endpoint(chain)
    try:
        number = json_rpc(url, "starknet_blockNumber", [], timeout=20)
        block = json_rpc(url, "starknet_getBlockWithTxHashes", [{"block_number": number}], timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    txs = block.get("transactions") or []
    ts = block.get("timestamp")
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{number}",
            observed_at=iso_from_unix(ts),
            window_seconds=None,
            head_height=str(number),
            source=url,
            gross_tx_count=len(txs),
            null_reasons={
                "net_tx_count": "only transaction hashes were fetched; declare/deploy/invoke and fee-account system txs were not classified",
                "window_seconds": "parent block time was not fetched",
                "fee_native": "receipts were not fetched",
                "native_transfer_value": "not decoded",
                "active_addresses": "not decoded",
                "token_transfer_count": "not decoded",
            },
        )
    ]


@_reg("thorchain")
def collect_thorchain(chain):
    base = (_endpoint(chain) or "").rstrip("/")
    try:
        last = request_json(f"{base}/thorchain/lastblock", timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    thor = None
    if isinstance(last, list):
        for row in last:
            if str(row.get("chain", "")).upper() in {"THOR", "THORCHAIN"}:
                thor = row
                break
        thor = thor or (last[0] if last else None)
    else:
        thor = last
    height = (thor or {}).get("thorchain") or (thor or {}).get("last_observed_in")
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:height:{height}:{now_iso()[:16]}",
            observed_at=now_iso(),
            head_height=str(height),
            source=base,
            null_reasons={
                "gross_tx_count": "thornode lastblock reports chain height, not a transaction count; tx count was not invented from height",
                "net_tx_count": "no transaction payload at this endpoint",
                "native_transfer_value": "not available from lastblock",
                "active_addresses": "not available from lastblock",
                "fee_native": "not available from lastblock",
            },
            extra={"lastblock": thor},
        )
    ]


@_reg("neo")
def collect_neo(chain):
    url = _endpoint(chain)
    try:
        count = json_rpc(url, "getblockcount", [], timeout=20)
        block = json_rpc(url, "getblock", [count - 1, True], timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    txs = block.get("tx") or []
    types = {}
    excluded = 0
    native = 0
    for tx in txs:
        ttype = tx.get("type") or "unknown"
        types[ttype] = types.get(ttype, 0) + 1
        if ttype in {"MinerTransaction", "Transaction"} and tx.get("sender") in (None, ""):
            pass
        if ttype == "MinerTransaction":
            excluded += 1
        if ttype == "ContractTransaction":
            native += 1
    ts = block.get("time")
    if isinstance(ts, (int, float)) and ts > 10_000_000_000:
        ts = ts / 1000
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{block.get('index')}",
            observed_at=iso_from_unix(ts),
            window_seconds=None,
            head_height=str(block.get("index")),
            source=url,
            gross_tx_count=len(txs),
            excluded_tx_count=excluded,
            net_tx_count=len(txs) - excluded,
            native_transfer_count=native or None,
            tx_types=types,
            null_reasons={
                "window_seconds": "parent block time was not fetched",
                "native_transfer_value": "NEP-17 transfers are not decoded",
                "token_transfer_count": "not decoded",
                "active_addresses": "not decoded",
                "fee_native": "sysfee and netfee were not summed",
                "native_transfer_count": "only legacy ContractTransaction types are counted; most N3 activity is InvocationTransaction",
            },
        )
    ]


@_reg("bittensor")
def collect_bittensor(chain):
    url = _endpoint(chain)
    try:
        header = json_rpc(url, "chain_getHeader", [], timeout=20)
        block_hash = json_rpc(url, "chain_getBlockHash", [], timeout=20)
        block = json_rpc(url, "chain_getBlock", [block_hash], timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    extrinsics = ((block or {}).get("block") or {}).get("extrinsics") or []
    number = hex_int((header or {}).get("number"))
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:block:{number}",
            observed_at=now_iso(),
            head_height=str(number),
            source=url,
            gross_tx_count=len(extrinsics),
            null_reasons={
                "net_tx_count": "extrinsics are SCALE-encoded; timestamp and consensus inherents were not removed",
                "observed_at": "block timestamp was not decoded; observed_at is collection time",
                "native_transfer_value": "not decoded",
                "active_addresses": "not decoded",
                "fee_native": "not decoded",
                "token_transfer_count": "not decoded",
            },
        )
    ]


@_reg("icp")
def collect_icp(chain):
    url = _endpoint(chain)
    try:
        payload = request_json(url, timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    if isinstance(payload, dict) and payload.get("message_execution_rate"):
        series = payload["message_execution_rate"]
        ts, value = series[-1]
        return [
            blank(
                chain,
                sample_kind="window",
                sample_key=f"{chain['id']}:message-rate:{int(float(ts))}",
                observed_at=iso_from_unix(ts),
                window_seconds=None,
                source=url,
                null_reasons={
                    "gross_tx_count": (
                        f"Internet Computer public metric message_execution_rate latest point is {value} "
                        f"at unix {ts}. The unit is not a documented transaction count, so it is not stored as txs."
                    ),
                    "net_tx_count": "no transaction split is published on this endpoint",
                },
                extra={"message_execution_rate": value, "metric_unix": ts},
            )
        ]
    # The public dashboard API returns a list of {datetime, value} or a bare value.
    message_rate = _icp_latest(payload)
    if message_rate is None:
        return [failure(chain, f"ICP payload had no numeric series: {str(payload)[:240]}")]
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:messages:{now_iso()[:16]}",
            observed_at=now_iso(),
            window_seconds=60,
            source=url,
            gross_tx_count=message_rate,
            excluded_tx_count=None,
            net_tx_count=None,
            null_reasons={
                "net_tx_count": "the public message-count metric does not split update calls, queries, or protocol messages",
                "native_transfer_value": "not in this metric",
                "active_addresses": "not in this metric",
                "fee_native": "not in this metric",
                "token_transfer_count": "not in this metric",
                "metric": "value is the dashboard message count for the latest bucket, stored as gross_tx_count; it is not claimed to be a per-minute user tx count unless window_seconds matches the bucket",
            },
            extra={"raw_latest": message_rate},
        )
    ]


@_reg("iota")
def collect_iota(chain):
    url = _endpoint(chain)
    try:
        info = json_rpc(url, "iota_getLatestCheckpointSequenceNumber", [], timeout=20)
        checkpoint = json_rpc(url, "iota_getCheckpoint", [str(info)], timeout=20)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    txs = checkpoint.get("transactions") or []
    ts = checkpoint.get("timestampMs")
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:checkpoint:{info}",
            observed_at=iso_from_unix(int(ts) / 1000) if ts else None,
            head_height=str(info),
            source=url,
            gross_tx_count=len(txs),
            null_reasons={
                "net_tx_count": "checkpoint lists transaction digests only",
                "native_transfer_value": "not in the checkpoint object",
                "active_addresses": "not in the checkpoint object",
                "fee_native": "not in the checkpoint object",
                "token_transfer_count": "not in the checkpoint object",
            },
        )
    ]


def _sui_graphql(chain, url):
    query = {
        "query": "query { checkpoints(last: 20) { nodes { sequenceNumber timestamp networkTotalTransactions } } }"
    }
    try:
        payload = request_json(url, method="POST", body=query, timeout=25)
    except HttpError as exc:
        return [failure(chain, str(exc))]
    nodes = (((payload or {}).get("data") or {}).get("checkpoints") or {}).get("nodes") or []
    if len(nodes) < 2:
        return [failure(chain, f"sui graphql returned {len(nodes)} checkpoints")]
    nodes = sorted(nodes, key=lambda node: int(node["sequenceNumber"]))
    first, last = nodes[0], nodes[-1]
    gross = int(last["networkTotalTransactions"]) - int(first["networkTotalTransactions"])
    window = _delta(last.get("timestamp"), first.get("timestamp"))
    return [
        blank(
            chain,
            sample_kind="window",
            sample_key=f"{chain['id']}:checkpoint:{last['sequenceNumber']}",
            observed_at=last.get("timestamp"),
            window_seconds=window,
            head_height=str(last["sequenceNumber"]),
            source=url,
            gross_tx_count=gross,
            null_reasons={
                "net_tx_count": "networkTotalTransactions delta has no system-vs-user split, so net is withheld",
                "native_transfer_value": "graphql checkpoint totals have no transfer values",
                "token_transfer_count": "not in the checkpoint total",
                "active_addresses": "not in the checkpoint total",
                "fee_native": "not in the checkpoint total",
            },
            extra={"checkpoints": len(nodes), "from_sequence": first["sequenceNumber"]},
        )
    ]


def _icp_latest(payload):
    if isinstance(payload, (int, float)):
        return float(payload)
    if isinstance(payload, dict):
        if "message_count" in payload and isinstance(payload["message_count"], list):
            return _icp_latest(payload["message_count"])
        for key in ("value", "count", "messages"):
            if isinstance(payload.get(key), (int, float)):
                return float(payload[key])
        for value in payload.values():
            found = _icp_latest(value)
            if found is not None:
                return found
    if isinstance(payload, list) and payload:
        last = payload[-1]
        if isinstance(last, dict) and "value" in last:
            try:
                return float(last["value"])
            except (TypeError, ValueError):
                return None
        return _icp_latest(last)
    return None


def _delta(later, earlier):
    if not later or not earlier:
        return None
    try:
        a = datetime.fromisoformat(str(later).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(earlier).replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = (a - b).total_seconds()
    return delta if delta > 0 else None


def _fill_windows(rows):
    for index, row in enumerate(rows):
        if index + 1 < len(rows):
            delta = _delta(row.get("observed_at"), rows[index + 1].get("observed_at"))
            if delta:
                row["window_seconds"] = delta


def _with_window(chain, rows):
    if not rows:
        return [failure(chain, "no rows")]
    _fill_windows(rows)
    complete = [r for r in rows if r.get("net_tx_count") is not None]
    if not complete:
        rows[0]["sample_kind"] = "window"
        return rows
    window_seconds = sum(r.get("window_seconds") or 0 for r in complete) or None
    types = {}
    for row in complete:
        for key, value in (row.get("tx_types") or {}).items():
            if isinstance(value, (int, float)):
                types[key] = types.get(key, 0) + value
    head = blank(
        chain,
        sample_kind="window",
        sample_key=f"{chain['id']}:window:{complete[0]['head_height']}",
        observed_at=complete[0]["observed_at"],
        window_seconds=window_seconds,
        head_height=complete[0]["head_height"],
        source=complete[0]["source"],
        gross_tx_count=sum(r["gross_tx_count"] or 0 for r in complete),
        excluded_tx_count=sum(r["excluded_tx_count"] or 0 for r in complete),
        net_tx_count=sum(r["net_tx_count"] or 0 for r in complete),
        native_transfer_count=sum(r["native_transfer_count"] or 0 for r in complete),
        native_transfer_value=sum(r["native_transfer_value"] or 0 for r in complete)
        if all(r["native_transfer_value"] is not None for r in complete)
        else None,
        native_transfer_is_net=1 if all(r.get("native_transfer_is_net") for r in complete) else 0,
        token_transfer_count=sum(r["token_transfer_count"] or 0 for r in complete)
        if any(r["token_transfer_count"] is not None for r in complete)
        else None,
        active_addresses=complete[0]["active_addresses"] if len(complete) == 1 else None,
        tx_types=types,
        null_reasons=complete[0].get("null_reasons") or {},
        extra={"blocks": len(complete)},
    )
    if len(complete) > 1:
        head["null_reasons"]["active_addresses"] = "unique addresses are not summed across blocks"
        head["active_addresses"] = None
    for row in rows:
        row["sample_kind"] = "block"
    return [head] + rows
