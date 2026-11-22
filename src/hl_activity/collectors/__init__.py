"""Chain collectors. Each returns samples or a single failure sample with null metrics."""

from .cosmos import collect_cosmos
from .evm import collect_evm
from .solana import collect_solana
from .specialized import SPECIALIZED
from .utxo import collect_utxo_blockchair, collect_utxo_blockcypher, collect_utxo_mempool

def collect_unavailable(chain):
    from .common import failure

    return [failure(chain, chain.get("unavailable_reason") or "no public source configured")]


COLLECTORS = {
    "unavailable": collect_unavailable,
    "evm": collect_evm,
    "solana": collect_solana,
    "utxo_mempool": collect_utxo_mempool,
    "utxo_blockcypher": collect_utxo_blockcypher,
    "utxo_blockchair": collect_utxo_blockchair,
    "cosmos": collect_cosmos,
    **SPECIALIZED,
}


def collect_chain(chain):
    kind = chain.get("collector")
    fn = COLLECTORS.get(kind)
    if fn is None:
        from .common import failure

        return [failure(chain, f"no collector registered for {kind}")]
    try:
        return fn(chain)
    except Exception as exc:
        from .common import failure

        return [failure(chain, f"{type(exc).__name__}: {exc}")]
