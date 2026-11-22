"""Authoring source for mapping/chains.yaml and mapping/universe.yaml.

The committed YAML/CSV is the reviewable artifact. Re-run this only to
rebuild those files from the notes below; runtime code reads the YAML.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hl_activity.mapping import utc_now, write_universe  # noqa: E402

KRAKEN = "https://support.kraken.com/articles/360000678446-cryptocurrencies-available-on-kraken"
META = json.loads((ROOT / "mapping" / "hl_meta_snapshot.json").read_text())

POLICY = {
    "stablecoins": (
        "A perp is excluded only when the token itself is a stablecoin. "
        "Governance tokens of stablecoin issuers (ENA, SKY, USUAL, and similar) stay in. "
        "Failed pegs that were issued as stablecoins (USTC) stay out. "
        "Price near $1 is not enough on its own, and a ticker that merely contains STABLE is not enough."
    ),
    "k_prefix": (
        "A ticker matching k + Uppercase (kPEPE, kBONK) is Hyperliquid's 1000-unit contract on the "
        "underlying token. The chain is the underlying token's chain. KAS is not a k-prefix."
    ),
    "native_vs_hosted": (
        "asset_role native means the coin is the chain's gas or staking asset. "
        "hosted means it was issued on that chain (ERC-20, SPL, jetton, BRC-20, HIP-1) while another asset pays gas. "
        "A bridged representation on a second chain does not move the mapping when a home chain is documented "
        "(lock-and-release home, gas token, or a single CoinGecko asset_platform_id)."
    ),
    "unmapped": (
        "If the home chain is unknown, or two chains are co-equal official issuances, the coin is status unmapped. "
        "sync never fills that in from a name or a market-cap guess."
    ),
    "prices": "USD conversions use Hyperliquid allMids only. USDC, the HL quote asset, is 1 when a chain's gas token is USDC and allMids has no USDC row.",
    "kraken_list_as_of": "2026-10-30",
}


def chain(**kwargs):
    return kwargs


CHAINS = [
    chain(id="bitcoin", name="Bitcoin", gas_ticker="BTC", collector="utxo_mempool", minute_feasible=False,
          minute_note="Confirmed activity is one block every ~10 minutes. The collector polls mempool size every run and records each new block; the per-minute rate is the block's tx count divided by the measured block interval.",
          endpoints=["https://mempool.space/api"]),
    chain(id="litecoin", name="Litecoin", gas_ticker="LTC", collector="utxo_mempool", minute_feasible=False,
          minute_note="Same constraint as Bitcoin: block interval is a few minutes, not one minute.",
          endpoints=["https://litecoinspace.org/api"]),
    chain(id="dogecoin", name="Dogecoin", gas_ticker="DOGE", collector="utxo_blockcypher", minute_feasible=False,
          minute_note="BlockCypher's free latest-block endpoint has no parent timestamp, so a per-minute rate is not computed. Block time is about one minute, but this source does not support a rate.",
          endpoints=["https://api.blockcypher.com/v1/doge/main"]),
    chain(id="bitcoincash", name="Bitcoin Cash", gas_ticker="BCH", collector="utxo_blockchair", minute_feasible=False,
          minute_note="Free Blockchair block list. Block interval is about 10 minutes.",
          blockchair="bitcoin-cash"),
    chain(id="bitcoinsv", name="Bitcoin SV", gas_ticker="BSV", collector="utxo_blockchair", minute_feasible=False,
          minute_note="Free Blockchair block list. Block interval is about 10 minutes.",
          blockchair="bitcoin-sv"),
    chain(id="zcash", name="Zcash", gas_ticker="ZEC", collector="utxo_blockchair", minute_feasible=False,
          minute_note="Free Blockchair block list. Block interval is about 75 seconds, but a public rate is only as fresh as the latest block the API returns.",
          blockchair="zcash"),
    chain(id="dash", name="Dash", gas_ticker="DASH", collector="utxo_blockchair", minute_feasible=False,
          minute_note="Free Blockchair block list. Block interval is about 2.5 minutes.",
          blockchair="dash"),
    chain(id="ethereum", name="Ethereum", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Public execution RPC. Beacon-chain attestation and issuance are outside this feed.",
          endpoints=["https://ethereum-rpc.publicnode.com", "https://rpc.ankr.com/eth"]),
    chain(id="ethereumclassic", name="Ethereum Classic", gas_ticker="ETC", collector="evm", minute_feasible=True,
          minute_note="Public EVM RPC. Block interval is about 13 seconds.",
          endpoints=["https://etc.etcdesktop.com"]),
    chain(id="bnb", name="BNB Smart Chain", gas_ticker="BNB", collector="evm", minute_feasible=True,
          minute_note="Public BSC RPC. Block interval is about 3 seconds.",
          endpoints=["https://bsc-rpc.publicnode.com", "https://bsc-dataseed.binance.org"]),
    chain(id="avalanche", name="Avalanche C-Chain", gas_ticker="AVAX", collector="evm", minute_feasible=True,
          minute_note="C-Chain only. X-Chain and P-Chain activity is not included.",
          endpoints=["https://avalanche-c-chain-rpc.publicnode.com", "https://api.avax.network/ext/bc/C/rpc"]),
    chain(id="polygon", name="Polygon PoS", gas_ticker="POL", collector="evm", minute_feasible=True,
          minute_note="Bor RPC. POL is the gas token after the MATIC migration.",
          endpoints=["https://polygon-bor-rpc.publicnode.com", "https://polygon-rpc.com"]),
    chain(id="arbitrum", name="Arbitrum One", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Gas is ETH. ARB is a hosted governance token.",
          endpoints=["https://arbitrum-one-rpc.publicnode.com", "https://arb1.arbitrum.io/rpc"]),
    chain(id="optimism", name="Optimism", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="OP-stack deposit transactions (type 0x7e) are removed from net.",
          endpoints=["https://optimism-rpc.publicnode.com", "https://mainnet.optimism.io"]),
    chain(id="base", name="Base", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="OP-stack. Deposit transactions are removed from net. Gas is ETH.",
          endpoints=["https://base-rpc.publicnode.com", "https://mainnet.base.org"]),
    chain(id="zksync", name="ZKsync Era", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Gas is ETH. ZK is a hosted token.",
          endpoints=["https://mainnet.era.zksync.io", "https://zksync.drpc.org"]),
    chain(id="linea", name="Linea", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Gas is ETH. LINEA is hosted.",
          endpoints=["https://rpc.linea.build", "https://linea-rpc.publicnode.com"]),
    chain(id="scroll", name="Scroll", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Gas is ETH. Only delisted SCR maps here.",
          endpoints=["https://rpc.scroll.io", "https://scroll-rpc.publicnode.com"]),
    chain(id="blast", name="Blast", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Gas is ETH. Only delisted BLAST maps here.",
          endpoints=["https://rpc.blast.io", "https://blast-rpc.publicnode.com"]),
    chain(id="mantle", name="Mantle", gas_ticker="MNT", collector="evm", minute_feasible=True,
          minute_note="MNT is the gas token. Kraken also deposits the Ethereum representation of MNT.",
          endpoints=["https://rpc.mantle.xyz", "https://mantle-rpc.publicnode.com"]),
    chain(id="celo", name="Celo", gas_ticker="CELO", collector="evm", minute_feasible=True,
          minute_note="Celo has been an Ethereum L2 since 2025. CELO remains the gas token.",
          endpoints=["https://forno.celo.org", "https://celo-rpc.publicnode.com"]),
    chain(id="sonic", name="Sonic", gas_ticker="S", collector="evm", minute_feasible=True,
          minute_note="Sonic mainnet. The legacy FTM perp is mapped to Fantom Opera, not here.",
          endpoints=["https://rpc.soniclabs.com", "https://sonic-rpc.publicnode.com"]),
    chain(id="fantom", name="Fantom Opera", gas_ticker="FTM", collector="evm", minute_feasible=True,
          minute_note="Legacy FTM chain. The HL FTM perp is delisted, so this chain is not in the active ranking.",
          endpoints=["https://fantom-rpc.publicnode.com", "https://rpcapi.fantom.network"]),
    chain(id="berachain", name="Berachain", gas_ticker="BERA", collector="evm", minute_feasible=True,
          minute_note="Public Berachain RPC.",
          endpoints=["https://rpc.berachain.com", "https://berachain-rpc.publicnode.com"]),
    chain(id="sei", name="Sei", gas_ticker="SEI", collector="evm", minute_feasible=True,
          minute_note="Sei v2 EVM RPC. Cosmos-side messages are not a second feed.",
          endpoints=["https://evm-rpc.sei-apis.com", "https://sei-evm-rpc.publicnode.com"]),
    chain(id="monad", name="Monad", gas_ticker="MON", collector="evm", minute_feasible=True,
          minute_note="400ms blocks. The free RPC sample is a short window of recent blocks, scaled to a per-minute rate.",
          endpoints=["https://rpc.monad.xyz"]),
    chain(id="megaeth", name="MegaETH", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Gas is ETH. MEGA is hosted. Public RPC is rate limited.",
          endpoints=["https://mainnet.megaeth.com/rpc"]),
    chain(id="plasma", name="Plasma", gas_ticker="XPL", collector="evm", minute_feasible=True,
          minute_note="Public Plasma RPC. Gas is XPL.",
          endpoints=["https://rpc.plasma.to"]),
    chain(id="zerog", name="0G", gas_ticker="0G", collector="evm", minute_feasible=True,
          minute_note="0G mainnet EVM RPC. Gas ticker is 0G.",
          endpoints=["https://evmrpc.0g.ai"]),
    chain(id="robinhood", name="Robinhood Chain", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Arbitrum Orbit chain 4663. Gas is ETH.",
          endpoints=["https://rpc.mainnet.chain.robinhood.com", "https://robinhood-rpc.publicnode.com"]),
    chain(id="hyperliquid", name="Hyperliquid", gas_ticker="HYPE", collector="evm", minute_feasible=True,
          minute_note="Figures are HyperEVM only. The validator L1 order book is not exposed as a typed transaction feed on the public info API, so L1 matching activity is not included and is not estimated.",
          endpoints=["https://rpc.hyperliquid.xyz/evm", "https://hyperliquid.drpc.org"]),
    chain(id="zetachain", name="ZetaChain", gas_ticker="ZETA", collector="evm", minute_feasible=True,
          minute_note="ZETA is the gas token. An ERC-20 representation also exists on Ethereum.",
          endpoints=["https://zetachain-evm.blockpi.network/v1/rpc/public", "https://zeta-chain.drpc.org"]),
    chain(id="manta", name="Manta Pacific", gas_ticker="ETH", collector="evm", minute_feasible=True,
          minute_note="Gas is ETH. MANTA is hosted. Confidence on the token role is medium.",
          endpoints=["https://pacific-rpc.manta.network/http"]),
    chain(id="story", name="Story", gas_ticker="IP", collector="evm", minute_feasible=True,
          minute_note="IP perp is delisted. Collected for mapping completeness, not the active ranking.",
          endpoints=["https://mainnet.storyrpc.io"]),
    chain(id="canto", name="Canto", gas_ticker="CANTO", collector="evm", minute_feasible=False,
          minute_note="Delisted perp. Public RPCs for Canto have been unreliable.",
          endpoints=["https://canto.slingshot.finance", "https://jsonrpc.canto.nodestake.top"]),
    chain(id="arc", name="Arc", gas_ticker="USDC", collector="evm", minute_feasible=False,
          minute_note="XTEN's documented genesis network. A free no-key RPC was not confirmed; the endpoints below are attempted and a failure is stored as null rather than a guess.",
          endpoints=["https://rpc.arc.network", "https://arc-rpc.publicnode.com"]),
    chain(id="cashcat", name="CashCat Chain", gas_ticker="CASHCAT", collector="unavailable", minute_feasible=False,
          minute_note="CASHCAT is the native gas coin. No free public RPC was confirmed, so activity is null.",
          endpoints=[], unavailable_reason="No free public CashCat RPC was confirmed. Docs describe the chain but do not publish a stable unauthenticated endpoint in the sources used here."),
    chain(id="movement", name="Movement", gas_ticker="MOVE", collector="unavailable", minute_feasible=False,
          minute_note="MOVE is native on Movement (CoinGecko platform address 0xa). No free public fullnode URL was confirmed.",
          endpoints=[], unavailable_reason="No free public Movement RPC was confirmed."),
    chain(id="canton", name="Canton", gas_ticker="CC", collector="unavailable", minute_feasible=False,
          minute_note="Canton is a permissioned network. No public transaction feed was confirmed.",
          endpoints=[], unavailable_reason="Canton does not publish a public unauthenticated transaction feed in the sources checked."),
    chain(id="solana", name="Solana", gas_ticker="SOL", collector="solana", minute_feasible=True,
          minute_note="getRecentPerformanceSamples is about 60 seconds and already splits vote transactions. That is the net series.",
          endpoints=["https://solana-rpc.publicnode.com", "https://api.mainnet-beta.solana.com"]),
    chain(id="fogo", name="Fogo", gas_ticker="FOGO", collector="solana", minute_feasible=True,
          minute_note="SVM chain. The Solana performance-sample method is attempted on the public Fogo RPC.",
          endpoints=["https://mainnet.fogo.io"]),
    chain(id="cosmoshub", name="Cosmos Hub", gas_ticker="ATOM", collector="cosmos", minute_feasible=True,
          minute_note="LCD block plus decoded txs. Reward-withdrawal transactions are removed from net.",
          endpoints=["https://rest.cosmos.directory/cosmoshub"], native_denom="uatom", native_exponent=6),
    chain(id="injective", name="Injective", gas_ticker="INJ", collector="cosmos", minute_feasible=True,
          minute_note="Injective LCD. inEVM is not a second activity feed.",
          endpoints=["https://rest.cosmos.directory/injective"], native_denom="inj", native_exponent=18),
    chain(id="celestia", name="Celestia", gas_ticker="TIA", collector="cosmos", minute_feasible=True,
          minute_note="Celestia LCD.",
          endpoints=["https://rest.cosmos.directory/celestia"], native_denom="utia", native_exponent=6),
    chain(id="dydx", name="dYdX Chain", gas_ticker="DYDX", collector="cosmos", minute_feasible=True,
          minute_note="dYdX Chain LCD. The old Ethereum ERC-20 is not the mapped home.",
          endpoints=["https://rest.cosmos.directory/dydx"], native_denom="adydx", native_exponent=18),
    chain(id="dymension", name="Dymension", gas_ticker="DYM", collector="cosmos", minute_feasible=True,
          minute_note="Dymension hub LCD. Rollapp activity is not included.",
          endpoints=["https://rest.cosmos.directory/dymension"], native_denom="adym", native_exponent=18),
    chain(id="saga", name="Saga", gas_ticker="SAGA", collector="cosmos", minute_feasible=True,
          minute_note="Saga chainlet hub LCD.",
          endpoints=["https://rest.cosmos.directory/saga"], native_denom="usaga", native_exponent=6),
    chain(id="initia", name="Initia", gas_ticker="INIT", collector="cosmos", minute_feasible=True,
          minute_note="Initia LCD.",
          endpoints=["https://rest.cosmos.directory/initia"], native_denom="uinit", native_exponent=6),
    chain(id="fetchai", name="Fetch.ai", gas_ticker="FET", collector="cosmos", minute_feasible=True,
          minute_note="Fetch hub LCD. Kraken's deposit network is Fetch.ai. The ASI merger kept the FET ticker.",
          endpoints=["https://rest.cosmos.directory/fetchhub"], native_denom="afet", native_exponent=18),
    chain(id="neutron", name="Neutron", gas_ticker="NTRN", collector="cosmos", minute_feasible=True,
          minute_note="Delisted perp. Not in the active ranking.",
          endpoints=["https://rest.cosmos.directory/neutron"], native_denom="untrn", native_exponent=6),
    chain(id="babylon", name="Babylon", gas_ticker="BABY", collector="cosmos", minute_feasible=True,
          minute_note="Babylon LCD. Bitcoin staking receipts are not a second chain in this index.",
          endpoints=["https://rest.cosmos.directory/babylon"], native_denom="ubbn", native_exponent=6),
    chain(id="terraclassic", name="Terra Classic", gas_ticker="LUNC", collector="cosmos", minute_feasible=True,
          minute_note="Columbus LCD. kLUNC is 1000 LUNC. USTC is excluded as a stablecoin.",
          endpoints=["https://terra-classic-lcd.publicnode.com"], native_denom="uluna", native_exponent=6),
    chain(id="mantra", name="MANTRA", gas_ticker="OM", collector="cosmos", minute_feasible=True,
          minute_note="Delisted OM perp. Not in the active ranking.",
          endpoints=["https://rest.cosmos.directory/mantra"], native_denom="uom", native_exponent=6),
    chain(id="thorchain", name="THORChain", gas_ticker="RUNE", collector="cosmos", minute_feasible=False,
          minute_note="THORChain's public thornode API is not a stock Cosmos LCD. If the cosmos tx query fails, the chain stays null instead of using height as a fake tx count.",
          endpoints=["https://thornode.ninerealms.com"], native_denom="rune", native_exponent=8),
    chain(id="xrpl", name="XRP Ledger", gas_ticker="XRP", collector="xrp", minute_feasible=True,
          minute_note="Validated ledger with expanded transactions. Ledgers close every few seconds.",
          endpoints=["https://s1.ripple.com:51234", "https://xrplcluster.com"]),
    chain(id="cardano", name="Cardano", gas_ticker="ADA", collector="cardano", minute_feasible=True,
          minute_note="Koios block rows. Epoch stake rewards are not inside the block tx count.",
          endpoints=["https://api.koios.rest/api/v1"]),
    chain(id="tron", name="Tron", gas_ticker="TRX", collector="tron", minute_feasible=True,
          minute_note="getnowblock. WithdrawBalanceContract reward claims are removed from net.",
          endpoints=["https://api.trongrid.io/wallet/getnowblock"]),
    chain(id="aptos", name="Aptos", gas_ticker="APT", collector="aptos", minute_feasible=True,
          minute_note="Block metadata, state checkpoint, and validator transactions are removed from net.",
          endpoints=["https://fullnode.mainnet.aptoslabs.com/v1"]),
    chain(id="sui", name="Sui", gas_ticker="SUI", collector="sui", minute_feasible=True,
          minute_note="Public JSON-RPC was deprecated. Checkpoint totals come from the Mysten GraphQL endpoint. Net is withheld because the total is not labeled system vs user.",
          endpoints=["https://graphql.mainnet.sui.io/graphql"]),
    chain(id="near", name="NEAR", gas_ticker="NEAR", collector="near", minute_feasible=True,
          minute_note="One final block and its chunks.",
          endpoints=["https://rpc.mainnet.near.org"]),
    chain(id="algorand", name="Algorand", gas_ticker="ALGO", collector="algorand", minute_feasible=True,
          minute_note="Key registration and heartbeat transactions are removed from net.",
          endpoints=["https://mainnet-api.algonode.cloud"]),
    chain(id="stellar", name="Stellar", gas_ticker="XLM", collector="stellar", minute_feasible=True,
          minute_note="Horizon ledger summaries. Inflation is disabled, so there is no reward tx class in the summary.",
          endpoints=["https://horizon.stellar.org"]),
    chain(id="hedera", name="Hedera", gas_ticker="HBAR", collector="hedera", minute_feasible=True,
          minute_note="Mirror node block count. The type mix is a 100-transaction sample and is not used to invent a net figure.",
          endpoints=["https://mainnet-public.mirrornode.hedera.com"]),
    chain(id="ton", name="TON", gas_ticker="GRAM", collector="unavailable", minute_feasible=False,
          minute_note="GRAM is the rebranded native coin (CoinGecko id the-open-network). The delisted TON perp is the same chain. No free keyed-less TON indexer was confirmed, so activity is null.",
          endpoints=[], unavailable_reason="No free TON indexer that returns a transaction breakdown without an API key was confirmed. Toncenter and TonAPI require a key for reliable use."),
    chain(id="stacks", name="Stacks", gas_ticker="STX", collector="stacks", minute_feasible=False,
          minute_note="Hiro block summaries. Coinbase and tenure txs are inside tx_count and were not separated, so net is withheld. Stacks blocks follow Bitcoin.",
          endpoints=["https://api.mainnet.hiro.so"]),
    chain(id="conflux", name="Conflux", gas_ticker="CFX", collector="conflux", minute_feasible=True,
          minute_note="Core space RPC, one confirmed epoch.",
          endpoints=["https://main.confluxrpc.com"]),
    chain(id="kaspa", name="Kaspa", gas_ticker="KAS", collector="kaspa", minute_feasible=True,
          minute_note="BlockDAG window from the public REST API. One coinbase per block is removed and that assumption is labeled.",
          endpoints=["https://api.kaspa.org"]),
    chain(id="polkadot", name="Polkadot", gas_ticker="DOT", collector="bittensor", minute_feasible=False,
          minute_note="The public sidecar host did not resolve. Substrate HTTP returns SCALE-encoded extrinsics, so only a gross count is stored and net is withheld.",
          endpoints=["https://rpc.polkadot.io"]),
    chain(id="polymesh", name="Polymesh", gas_ticker="POLYX", collector="bittensor", minute_feasible=False,
          minute_note="Substrate HTTP. Extrinsics stay SCALE-encoded, so only a gross count is attempted and net is withheld.",
          endpoints=["https://mainnet-rpc.polymesh.network"]),
    chain(id="bittensor", name="Bittensor", gas_ticker="TAO", collector="bittensor", minute_feasible=False,
          minute_note="Finney RPC. Extrinsics are not decoded, so net is withheld.",
          endpoints=["https://bittensor-finney.api.onfinality.io/rpc"]),
    chain(id="monero", name="Monero", gas_ticker="XMR", collector="monero", minute_feasible=False,
          minute_note="Public node block header. Amounts and addresses are private and stay null. Block interval is about 2 minutes.",
          endpoints=["https://xmr-node.cakewallet.com:18081/json_rpc"]),
    chain(id="filecoin", name="Filecoin", gas_ticker="FIL", collector="filecoin", minute_feasible=False,
          minute_note="Lotus ChainHead message count. Methods are not decoded, so net is withheld. Epochs are 30 seconds.",
          endpoints=["https://api.node.glif.io/rpc/v1"]),
    chain(id="arweave", name="Arweave", gas_ticker="AR", collector="arweave", minute_feasible=False,
          minute_note="Current block. Block interval is much longer than a minute.",
          endpoints=["https://arweave.net"]),
    chain(id="mina", name="Mina", gas_ticker="MINA", collector="mina", minute_feasible=False,
          minute_note="Mina explorer GraphQL block tx counts. Slot time is about 3 minutes.",
          endpoints=["https://api.minaexplorer.com/graphql"]),
    chain(id="starknet", name="Starknet", gas_ticker="STRK", collector="starknet", minute_feasible=True,
          minute_note="Transaction-hash list only. Net is withheld because invokes are not classified.",
          endpoints=["https://starknet.api.onfinality.io/public"]),
    chain(id="neo", name="Neo N3", gas_ticker="GAS", collector="neo", minute_feasible=False,
          minute_note="N3 JSON-RPC. Kraken deposits NEO and GAS via Neo X; the native assets are mapped to N3. Neo X activity is not added on top.",
          endpoints=["https://mainnet1.neo.coz.io:443"]),
    chain(id="icp", name="Internet Computer", gas_ticker="ICP", collector="icp", minute_feasible=False,
          minute_note="Public dashboard message-count metric. It is not a per-transaction breakdown, and net is withheld.",
          endpoints=["https://ic-api.internetcomputer.org/api/v3/metrics/message-execution-rate"]),
    chain(id="iota", name="IOTA", gas_ticker="IOTA", collector="iota", minute_feasible=False,
          minute_note="Rebased fullnode checkpoint API is attempted. A failure is stored as null.",
          endpoints=["https://api.mainnet.iota.cafe"]),
]

# Coin rows. status mapped | unmapped | stable_excluded
# Fields: chain, role, standard, multiplier, underlying, confidence, source, note

def M(chain, role="hosted", standard="erc20", mult=None, underlying=None, confidence="high", source=KRAKEN, note=""):
    return dict(status="mapped", chain_id=chain, asset_role=role, standard=standard, multiplier=mult,
                underlying=underlying, confidence=confidence, sources=[source] if isinstance(source, str) else source, notes=note)

def U(note, source=""):
    sources = [source] if source else []
    return dict(status="unmapped", chain_id=None, asset_role=None, standard=None, multiplier=None,
                underlying=None, confidence=None, sources=sources, notes=note)

def K(chain, standard, underlying, source=KRAKEN, note="", confidence="high"):
    text = "k-prefix is 1000 units of the underlying. " + note
    return M(chain, "hosted", standard, 1000, underlying, confidence, source, text.strip())

def S(chain, note):
    return dict(status="stable_excluded", chain_id=chain, asset_role="native", standard=None, multiplier=None,
                underlying=None, confidence="high", sources=[KRAKEN], notes=note, is_stablecoin=True)

COINS = {}

def add(ticker, row):
    COINS[ticker] = row

# Native L1/L2 gas assets
for ticker, chain, source, note in [
    ("BTC", "bitcoin", "https://bitcoin.org", "Native asset of Bitcoin."),
    ("ETH", "ethereum", KRAKEN, "Native asset of Ethereum. Kraken also lists bridged representations; the home chain is Ethereum."),
    ("ATOM", "cosmoshub", KRAKEN, "Native asset of Cosmos Hub."),
    ("DYDX", "dydx", KRAKEN, "Native asset of dYdX Chain after the Ethereum migration."),
    ("SOL", "solana", KRAKEN, "Native asset of Solana."),
    ("AVAX", "avalanche", KRAKEN, "Native asset of Avalanche. Activity collected on the C-Chain."),
    ("BNB", "bnb", KRAKEN, "Native asset of BNB Smart Chain."),
    ("LTC", "litecoin", KRAKEN, "Native asset of Litecoin."),
    ("DOGE", "dogecoin", KRAKEN, "Native asset of Dogecoin."),
    ("INJ", "injective", KRAKEN, "Native asset of Injective."),
    ("SUI", "sui", KRAKEN, "Native asset of Sui."),
    ("STX", "stacks", KRAKEN, "Native asset of Stacks."),
    ("CFX", "conflux", KRAKEN, "Native asset of Conflux."),
    ("XRP", "xrpl", KRAKEN, "Native asset of the XRP Ledger."),
    ("BCH", "bitcoincash", KRAKEN, "Native asset of Bitcoin Cash."),
    ("APT", "aptos", KRAKEN, "Native asset of Aptos."),
    ("TRX", "tron", KRAKEN, "Native asset of Tron."),
    ("SEI", "sei", KRAKEN, "Native gas asset of Sei."),
    ("RUNE", "thorchain", KRAKEN, "Native asset of THORChain."),
    ("TIA", "celestia", KRAKEN, "Native asset of Celestia."),
    ("BSV", "bitcoinsv", "https://bitcoinsv.com", "Native asset of Bitcoin SV."),
    ("ADA", "cardano", KRAKEN, "Native asset of Cardano."),
    ("MINA", "mina", KRAKEN, "Native asset of Mina."),
    ("POLYX", "polymesh", "https://polymesh.network", "Native asset of Polymesh."),
    ("GAS", "neo", KRAKEN, "GAS is the native fuel asset of Neo N3. Kraken's deposit rail is Neo X."),
    ("NEO", "neo", KRAKEN, "NEO is the native governance asset of Neo N3. Kraken's deposit rail is Neo X."),
    ("FET", "fetchai", KRAKEN, "Kraken's only deposit network is Fetch.ai. FET remains the ticker through the ASI merger."),
    ("NEAR", "near", KRAKEN, "Native asset of NEAR."),
    ("FIL", "filecoin", KRAKEN, "Native asset of Filecoin."),
    ("ETC", "ethereumclassic", KRAKEN, "Native asset of Ethereum Classic."),
    ("ZETA", "zetachain", "https://www.zetachain.com", "ZETA is the gas token of ZetaChain. Kraken deposits the Ethereum ERC-20 representation."),
    ("DYM", "dymension", KRAKEN, "Native asset of Dymension."),
    ("TAO", "bittensor", KRAKEN, "Native asset of Bittensor."),
    ("AR", "arweave", KRAKEN, "Native asset of Arweave."),
    ("MNT", "mantle", "https://www.mantle.xyz", "MNT is Mantle's gas token. Kraken deposits the Ethereum ERC-20 representation."),
    ("SAGA", "saga", KRAKEN, "Native asset of Saga."),
    ("HBAR", "hedera", KRAKEN, "Native asset of Hedera."),
    ("XLM", "stellar", KRAKEN, "Native asset of Stellar."),
    ("IOTA", "iota", "https://www.iota.org", "Native asset of IOTA."),
    ("ALGO", "algorand", KRAKEN, "Native asset of Algorand."),
    ("HYPE", "hyperliquid", "https://hyperliquid.xyz", "Native gas token of Hyperliquid L1 / HyperEVM."),
    ("S", "sonic", KRAKEN, "Native gas token of Sonic."),
    ("BERA", "berachain", KRAKEN, "Native gas token of Berachain."),
    ("INIT", "initia", KRAKEN, "Native asset of Initia."),
    ("BABY", "babylon", KRAKEN, "Native asset of Babylon."),
    ("XPL", "plasma", "https://www.plasma.org/docs/plasma-chain/network-information/connect-to-plasma", "Native gas token of Plasma."),
    ("0G", "zerog", "https://docs.0g.ai/developer-hub/mainnet/mainnet-overview", "Native gas token of 0G. Token symbol on the network is 0G."),
    ("ZEC", "zcash", KRAKEN, "Native asset of Zcash."),
    ("MON", "monad", "https://monad.xyz/about", "Native gas and staking token of Monad."),
    ("CC", "canton", KRAKEN, "Native asset of Canton Network."),
    ("ICP", "icp", KRAKEN, "Native asset of the Internet Computer."),
    ("FOGO", "fogo", "https://community-docs.fogo.io/", "Native gas and staking token of Fogo."),
    ("XMR", "monero", KRAKEN, "Native asset of Monero."),
    ("DASH", "dash", KRAKEN, "Native asset of Dash."),
    ("KAS", "kaspa", KRAKEN, "Native asset of Kaspa. Not a k-prefix contract."),
    ("DOT", "polkadot", KRAKEN, "Native asset of Polkadot. Kraken lists Asset Hub as the deposit location for the same native asset."),
    ("CELO", "celo", KRAKEN, "Gas token of Celo."),
    ("POL", "polygon", KRAKEN, "Gas token of Polygon PoS. An ERC-20 representation also exists on Ethereum."),
    ("STRK", "starknet", KRAKEN, "Fee token of Starknet."),
    ("FTM", "fantom", KRAKEN, "Delisted. Native asset of Fantom Opera, not the successor S token."),
    ("MATIC", "polygon", KRAKEN, "Delisted legacy ticker for the Polygon gas asset before POL."),
    ("CANTO", "canto", "https://canto.io", "Delisted. Native asset of Canto."),
    ("NTRN", "neutron", KRAKEN, "Delisted. Native asset of Neutron."),
    ("IP", "story", KRAKEN, "Delisted. Native asset of Story."),
    ("OM", "mantra", KRAKEN, "Delisted. Native asset of MANTRA chain."),
    ("TON", "ton", "https://www.coingecko.com/en/coins/gram", "Delisted ticker. Same network as GRAM after the Toncoin rebrand. The delisted book is not used as the gas price."),
    ("GRAM", "ton", "https://www.coingecko.com/en/coins/gram", "CoinGecko id the-open-network: Toncoin rebranded to GRAM. Native asset of TON. ERC-20 and BEP-20 representations also exist."),
    ("CASHCAT", "cashcat", "https://docs.cashcat.network/getting-started/get-cashcat", "Native gas coin of CashCat Chain. Also issued as an ERC-20 on Robinhood Chain and bridged."),
]:
    add(ticker, M(chain, "native", None, source=source, note=note))

# Hosted tokens with a single documented home
for ticker, chain, standard, note in [
    ("APE", "ethereum", "erc20", "ApeCoin ERC-20."),
    ("OP", "optimism", "erc20", "OP is the governance token. ETH is gas."),
    ("ARB", "arbitrum", "erc20", "ARB is the governance token on Arbitrum One. ETH is gas."),
    ("CRV", "ethereum", "erc20", ""),
    ("LDO", "ethereum", "erc20", ""),
    ("LINK", "ethereum", "erc20", "Canonical Chainlink token is the Ethereum ERC-20."),
    ("GMX", "arbitrum", "erc20", "Canonical GMX token is on Arbitrum. A bridged Avalanche copy exists."),
    ("SNX", "ethereum", "erc20", ""),
    ("AAVE", "ethereum", "erc20", ""),
    ("COMP", "ethereum", "erc20", ""),
    ("MKR", "ethereum", "erc20", "Delisted. Maker governance token, later rebranded toward SKY."),
    ("WLD", "optimism", "erc20", "Kraken's deposit network is Optimism."),
    ("FXS", "ethereum", "erc20", "Delisted. Frax Share, not the FRAX stablecoin."),
    ("HPOS", "ethereum", "erc20", "Delisted. HarryPotterObamaSonic10Inu ERC-20."),
    ("RLB", "ethereum", "erc20", "Delisted. Rollbit coin ERC-20."),
    ("UNIBOT", "ethereum", "erc20", "Delisted."),
    ("YGG", "ethereum", "erc20", ""),
    ("UNI", "ethereum", "erc20", ""),
    ("ZRO", "ethereum", "erc20", "Kraken lists only Ethereum. OFT copies exist on other chains; Ethereum is the deposit home used here."),
    ("BLZ", "ethereum", "erc20", "Delisted."),
    ("BANANA", "ethereum", "erc20", "Banana Gun. The HL mid matches that market, not BANANAS31."),
    ("TRB", "ethereum", "erc20", "Tellor."),
    ("FTT", "ethereum", "erc20", "Delisted. Original FTT is the Ethereum ERC-20."),
    ("LOOM", "ethereum", "erc20", "Delisted."),
    ("OGN", "ethereum", "erc20", "Delisted."),
    ("BNT", "ethereum", "erc20", "Delisted."),
    ("REQ", "ethereum", "erc20", "Delisted."),
    ("BIGTIME", "ethereum", "erc20", ""),
    ("ORBS", "ethereum", "erc20", "Delisted. The traded token is an Ethereum ERC-20; Orbs also runs a separate network."),
    ("BLUR", "ethereum", "erc20", ""),
    ("STG", "ethereum", "erc20", "Delisted. Kraken lists Ethereum."),
    ("MEME", "ethereum", "erc20", "Memeland memecoin."),
    ("BADGER", "ethereum", "erc20", "Delisted."),
    ("SUSHI", "ethereum", "erc20", ""),
    ("ILV", "ethereum", "erc20", "Delisted."),
    ("IMX", "ethereum", "erc20", "Kraken lists Ethereum, not Immutable zkEVM."),
    ("SUPER", "ethereum", "erc20", ""),
    ("RSR", "ethereum", "erc20", ""),
    ("GALA", "ethereum", "erc20", "Kraken lists Ethereum. GalaChain is a separate network and is not used as the home."),
    ("PEOPLE", "ethereum", "erc20", "ConstitutionDAO."),
    ("ENS", "ethereum", "erc20", ""),
    ("UMA", "ethereum", "erc20", ""),
    ("ONDO", "ethereum", "erc20", ""),
    ("ALT", "ethereum", "erc20", "AltLayer."),
    ("PENDLE", "ethereum", "erc20", ""),
    ("ETHFI", "ethereum", "erc20", ""),
    ("ENA", "ethereum", "erc20", "Ethena governance token. USDe is the stablecoin and is not this perp."),
    ("EIGEN", "ethereum", "erc20", ""),
    ("REZ", "ethereum", "erc20", ""),
    ("TURBO", "ethereum", "erc20", ""),
    ("SAND", "ethereum", "erc20", ""),
    ("SPX", "ethereum", "erc20", "SPX6900."),
    ("MORPHO", "ethereum", "erc20", ""),
    ("ANIME", "ethereum", "erc20", ""),
    ("NIL", "ethereum", "erc20", "Migrated from nilChain to Ethereum ERC-20 in March 2026. https://nillion.com/news/nil-token-migration-guide/"),
    ("PAXG", "ethereum", "erc20", "Pax Gold is a commodity token, not a dollar stablecoin."),
    ("HYPER", "ethereum", "erc20", "Hyperlane. Not the Hyperliquid gas token HYPE."),
    ("SOPH", "ethereum", "erc20", "Sophon docs: SOPH is an Ethereum-native ERC-20. The Sophon chain token is a bridged representation and that chain is being wound down. https://docs.sophon.xyz/tokenomics/soph"),
    ("SYRUP", "ethereum", "erc20", "Maple SYRUP."),
    ("PROVE", "ethereum", "erc20", "Succinct."),
    ("SKY", "ethereum", "erc20", "Sky governance token. USDS is the stablecoin and is not this perp."),
    ("AXS", "ethereum", "erc20", "Kraken lists Ethereum. Ronin holds a bridged game balance."),
    ("AZTEC", "ethereum", "erc20", "Kraken lists the Aztec token as an Ethereum ERC-20."),
    ("LIT", "ethereum", "erc20", "Lighter, not Litentry. HL mid matches CoinGecko lighter (about $2.37) and the Ethereum contract 0x232ce3bd40fcd6f80f3d55a522d03f25df784ee2. Kraken lists this asset as LIGHTER on Ethereum and a different LIT as Litentry."),
    ("APEX", "ethereum", "erc20", "ApeX Protocol. HL mid matches the Ethereum contract market. This is not ASTER, which is the APX rebrand."),
    ("USUAL", "ethereum", "erc20", "Usual governance token. USD0 is the stablecoin and is not this perp."),
    ("RDNT", "arbitrum", "erc20", "Delisted. Radiant Capital."),
    ("CHIP", "arbitrum", "erc20", "USD.AI token. HL mid is far from $1, so it is not treated as a stablecoin. Kraken ticker USD.AI, network Arbitrum."),
    ("XAI", "arbitrum", "erc20", "CoinGecko asset xai-blockchain lists a single Arbitrum contract. Xai is an L3; the indexed token contract is on Arbitrum One."),
    ("CYBER", "optimism", "erc20", "Delisted. Kraken lists Optimism."),
    ("WCT", "optimism", "erc20", "WalletConnect token. Kraken and Coinone list Optimism."),
    ("BRETT", "base", "erc20", "Base meme token."),
    ("AIXBT", "base", "erc20", "Kraken lists Base."),
    ("VVV", "base", "erc20", "Venice token. Kraken lists Base."),
    ("KAITO", "base", "erc20", "Kraken and Coinone list Base."),
    ("ZORA", "base", "erc20", "Kraken and Coinone list Base, not the Zora network as the token home."),
    ("AERO", "base", "erc20", "Aerodrome."),
    ("AVNT", "base", "erc20", "Avantis. Kraken lists Base."),
    ("ZEN", "base", "erc20", "CoinGecko asset_platform_id for Horizen (zencash) is base, category Base Native. A legacy Horizen-chain contract still exists."),
    ("ZK", "zksync", "erc20", "Kraken lists zkSync Era. ETH is gas."),
    ("LINEA", "linea", "erc20", "Kraken lists Linea. ETH is gas."),
    ("CAKE", "bnb", "bep20", ""),
    ("ASTER", "bnb", "bep20", "Aster docs: BEP-20 0x000Ae314E2A2172a039B26378814C252734f556A. APX was rebranded to ASTER. https://docs.asterdex.com/usdaster-token/overview"),
    ("MERL", "bnb", "bep20", "Kraken's deposit network is BNB Chain. Merlin Chain is the project's Bitcoin L2; the liquid token Kraken supports is the BEP-20."),
    ("ACE", "bnb", "bep20", "Delisted. CoinGecko endurance (Fusionist) lists a single BNB Chain contract."),
    ("LISTA", "bnb", "bep20", "Delisted. Lista DAO token, not the lisUSD stablecoin."),
    ("TST", "bnb", "bep20", "Delisted. Mapped as the BNB Chain TST meme associated with the 2025 Binance listing. Confidence medium."),
    ("STABLE", "bnb", "bep20", "Kraken lists STABLE on BNB Chain and does not mark it as a stablecoin. HL mid is about $0.02, so it is not a dollar peg. Project identity beyond that listing was not independently documented."),
    ("NXPC", "avalanche", "erc20", "NEXPACE. Coinone and Hinadex list Avalanche C-Chain."),
    ("WLD", "optimism", "erc20", ""),
]:
    add(ticker, M(chain, "hosted", standard, note=note, confidence="medium" if ticker in {"TST", "STABLE", "MERL", "ZEN", "XAI"} else "high"))

# The loop above adds WLD twice; the second write wins and is the same mapping. Fine.

add("ORDI", M("bitcoin", "hosted", "brc20", note="BRC-20 on Bitcoin, not the native BTC asset."))
add("RENDER", M("solana", "hosted", "spl", note="Post-migration Render token. Kraken lists Solana. The old RNDR ticker is the Ethereum token."))
add("RNDR", M("ethereum", "hosted", "erc20", note="Delisted pre-migration Render token."))
add("PYTH", M("solana", "hosted", "spl", note="Kraken lists Solana. Wormhole OFT copies exist."))
add("JUP", M("solana", "hosted", "spl", note=""))
add("JTO", M("solana", "hosted", "spl", note=""))
add("WIF", M("solana", "hosted", "spl", note=""))
add("BOME", M("solana", "hosted", "spl", note=""))
add("TNSR", M("solana", "hosted", "spl", note=""))
add("POPCAT", M("solana", "hosted", "spl", note=""))
add("IO", M("solana", "hosted", "spl", note="io.net."))
add("ME", M("solana", "hosted", "spl", note="Magic Eden. Kraken lists Solana."))
add("GMT", M("solana", "hosted", "spl", note="STEPN. Kraken lists Solana."))
add("GOAT", M("solana", "hosted", "spl", note=""))
add("MOODENG", M("solana", "hosted", "spl", note="Moo Deng. Kraken lists Solana."))
add("GRASS", M("solana", "hosted", "spl", note=""))
add("PNUT", M("solana", "hosted", "spl", note=""))
add("FARTCOIN", M("solana", "hosted", "spl", note=""))
add("PENGU", M("solana", "hosted", "spl", note=""))
add("PUMP", M("solana", "hosted", "spl", note="pump.fun token. Not the old Hyperliquid spot meme that reused the ticker."))
add("TRUMP", M("solana", "hosted", "spl", note="Official Trump. Kraken lists Solana. Distinct from the old Hyperliquid spot meme also called TRUMP."))
add("MELANIA", M("solana", "hosted", "spl", note=""))
add("LAYER", M("solana", "hosted", "spl", note="Solayer. Kraken lists Solana."))
add("MET", M("solana", "hosted", "spl", note="Meteora. Kraken lists Solana."))
add("2Z", M("solana", "hosted", "spl", note="DoubleZero. Kraken lists Solana."))
add("SKR", M("solana", "hosted", "spl", note="Seeker. Kraken lists Solana."))
add("BIO", M("solana", "hosted", "spl", note="Bio Protocol. Kraken lists Solana."))
add("GRIFFAIN", M("solana", "hosted", "spl", note=""))
add("USELESS", M("solana", "hosted", "spl", note=""))
add("MEW", M("solana", "hosted", "spl", note="Delisted. cat in a dogs world."))
add("MYRO", M("solana", "hosted", "spl", note="Delisted."))
add("CHILLGUY", M("solana", "hosted", "spl", note="Delisted."))
add("AI16Z", M("solana", "hosted", "spl", note="Delisted. Later associated with the ElizaOS rebrand, which Kraken lists on Solana."))
add("ZEREBRO", M("solana", "hosted", "spl", note="Delisted."))
add("VINE", M("solana", "hosted", "spl", note="Delisted."))
add("YZY", M("solana", "hosted", "spl", source="https://www.coingecko.com/en/coins/yzy", note="Delisted. CoinGecko yzy lists a single Solana mint."))
add("NOT", M("ton", "hosted", "jetton", note="Notcoin jetton."))
add("HMSTR", M("ton", "hosted", "jetton", note="Hamster Kombat jetton."))
add("kDOGS", K("ton", "jetton", "DOGS", note="Delisted. DOGS is a TON jetton."))
add("CATI", M("ton", "hosted", "jetton", note="Delisted. Catizen jetton."))
add("PURR", M("hyperliquid", "hosted", "hip-1", source="https://api.hyperliquid.xyz/info", note="HIP-1 token on Hyperliquid. HYPE is gas. Spot meta gives it a HyperEVM contract as well."))
add("FRIEND", M("base", "hosted", "erc20", note="Delisted. friend.tech FRIEND token on Base."))
add("BLAST", M("blast", "hosted", "erc20", note="Delisted. BLAST token on Blast. ETH is gas."))
add("SCR", M("scroll", "hosted", "erc20", note="Delisted. SCR token on Scroll. ETH is gas."))
add("NEIROETH", M("ethereum", "hosted", "erc20", note="Delisted. The ETH-qualified ticker is the Ethereum Neiro, which is why it is not treated as the same market as kNEIRO."))
add("PROMPT", M("ethereum", "hosted", "erc20", note="Delisted. Kraken lists Ethereum."))
add("OMNI", M("ethereum", "hosted", "erc20", note="Delisted. Omni Network token is an Ethereum ERC-20."))
add("PANDORA", M("ethereum", "hosted", "erc20", note="Delisted. Pandora ERC-404. The HL mid is a high unit price consistent with that token."))
add("WLFI", M("ethereum", "hosted", "erc20", source="https://docs.worldlibertyfinancial.com/wlfi-token/contract-addresses", note="Governance token. Ethereum is the lock-and-release CCIP home. BNB Chain and Solana are burn-and-mint copies. Not the USD1 stablecoin."))
add("MOVE", M("movement", "native", None, source="https://www.coingecko.com/en/coins/movement", note="CoinGecko lists a Movement platform address 0xa plus bridged ERC-20s on Ethereum and Base. Mapped as the native Movement asset."))
add("MEGA", M("megaeth", "hosted", "erc20", note="Kraken's network is MegaETH. The public chain metadata lists ETH as gas, so MEGA is hosted rather than the gas token."))
add("PONS", M("robinhood", "hosted", "erc20", source="https://www.coingecko.com/en/coins/pons", note="ERC-20 on Robinhood Chain (chain 4663), contract 0x39dbed3a2bd333467115de45665cc57f813c4571."))
add("XTEN", M("arc", "hosted", "erc20", source="https://docs.extended.exchange/extended-resources/xten/tokenomics", note="Extended's token. Docs name Arc as the genesis network, with CCTP to Ethereum, Arbitrum, and Base. Not treated as Arc's gas token."))
add("kPEPE", K("ethereum", "erc20", "PEPE", note="PEPE is an Ethereum ERC-20."))
add("kSHIB", K("ethereum", "erc20", "SHIB", note="SHIB is an Ethereum ERC-20."))
add("kBONK", K("solana", "spl", "BONK", note="BONK is an SPL token."))
add("kLUNC", K("terraclassic", "native", "LUNC", note="1000 LUNC. LUNC is Terra Classic's native asset, so the role of the underlying is native; the perp itself is a 1000-unit contract."))

# kLUNC role should stay hosted contract on a native underlying's chain. The helper sets hosted. Good.
# But standard native is wrong. Fix:
COINS["kLUNC"]["standard"] = None
COINS["kLUNC"]["notes"] += " Underlying role is native."

add("MANTA", M("manta", "hosted", "erc20", confidence="medium", source="https://manta.network", note="Mapped to Manta Pacific, where the network token lives. ETH is gas. Confidence medium."))

add("USTC", S("terraclassic", "TerraClassicUSD, issued as a stablecoin. Excluded even though the peg failed. Delisted on Hyperliquid."))

# Unmapped on purpose
add("W", U("Wormhole W is an NTT token launched as multichain. Kraken deposits only the Solana representation. That is an exchange choice, not a single home chain.", "https://wormhole.com"))
add("kFLOKI", U("FLOKI has official contracts on both Ethereum (0xcf0c122c6b73ff809c693db761e7baebe62b6a2e) and BNB Chain (0xfb5b838b6cfeedc2873ab27866079ac55363d37e). Neither the project nor a primary lock-and-release home picks one. Kraken lists only Ethereum.", "https://www.coingecko.com/en/coins/floki"))
COINS["kFLOKI"]["multiplier"] = 1000
COINS["kFLOKI"]["underlying"] = "FLOKI"
add("kNEIRO", U("Hyperliquid lists kNEIRO and NEIROETH separately. NEIROETH is the Ethereum Neiro. The underlying of kNEIRO was not confirmed (several Neiro tokens exist), so the chain is not guessed."))
COINS["kNEIRO"]["multiplier"] = 1000
COINS["kNEIRO"]["underlying"] = None
add("VIRTUAL", U("CoinGecko virtual-protocol lists Ethereum, Base, Solana, Robinhood, and Arc contracts. Kraken deposits only Solana. No single home chain is stated.", "https://www.coingecko.com/en/coins/virtual-protocol"))
add("RESOLV", U("Resolv governance token, not the USR stablecoin. CoinGecko lists Ethereum and BNB Chain contracts and the docs do not name a single home.", "https://docs.resolv.xyz/litepaper/resolv-token/resolv-token-overview"))
add("STBL", U("STBL is the governance token, not the USST stablecoin. Official contracts exist on Ethereum (0xB3116013C55D49f575ace3cb0d123f3dbF6caC35) and BNB Chain (0x8dEdf84656fa932157e27C060D8613824e7979e3). Terms say primarily Ethereum while Kraken lists only BNB Chain.", "https://docs.stbl.com/docs/introduction/developer-overview"))
add("HEMI", U("Hemi network gas is ETH (https://docs.hemi.xyz/discover/network-details). The HEMI token's issuance chain was not confirmed, so it is not assigned to Hemi or Ethereum."))
add("JELLY", U("Delisted. A Solana token jelly-my-jelly exists, and Hyperliquid also traded a distinct JELLY market. The delisted perp was not tied to one contract in the sources checked."))
add("DOOD", U("Delisted. Doodles token chain was not confirmed in the sources checked."))
add("LAUNCHCOIN", U("Delisted. Home chain was not confirmed in the sources checked."))
add("MAVIA", U("Delisted. Heroes of Mavia token chain was not confirmed in the sources checked."))
add("PIXEL", U("Delisted. CoinGecko pixels lists Ethereum and Ronin contracts with no single home.", "https://www.coingecko.com/en/coins/pixels"))
add("MAV", U("Delisted. Maverick lists Ethereum, Base, BNB Chain, and zkSync contracts with no single home.", "https://www.coingecko.com/en/coins/maverick-protocol"))
add("AI", U("Delisted. Ticker collision; the specific token was not confirmed."))
add("SHIA", U("Delisted. Home chain was not confirmed."))
add("OX", U("Delisted. Home chain was not confirmed."))
add("ARK", U("Delisted. Ticker collides across several assets; the Hyperliquid market was not tied to one chain."))
add("STRAX", U("Delisted. CoinGecko now lists this asset as Xertra with an empty platform field, so the post-rebrand chain is not confirmed.", "https://www.coingecko.com/en/coins/stratis"))
add("NFTI", U("Delisted. Home chain was not confirmed."))

def main():
    names = {row["name"]: row for row in META["universe"]}
    missing = sorted(set(names) - set(COINS))
    extra = sorted(set(COINS) - set(names))
    if missing or extra:
        raise SystemExit(f"ticker mismatch missing={missing} extra={extra}")
    chain_ids = {c["id"] for c in CHAINS}
    for ticker, row in COINS.items():
        if row["chain_id"] and row["chain_id"] not in chain_ids:
            raise SystemExit(f"{ticker} chain {row['chain_id']} missing")
    ordered = []
    for row in META["universe"]:
        ticker = row["name"]
        item = dict(COINS[ticker])
        item["ticker"] = ticker
        item["is_delisted"] = bool(row.get("isDelisted"))
        item["is_stablecoin"] = bool(item.get("is_stablecoin")) or item["status"] == "stable_excluded"
        chain = next((c for c in CHAINS if c["id"] == item["chain_id"]), None)
        item["chain_name"] = chain["name"] if chain else None
        ordered.append(item)
    payload = {
        "version": 1,
        "updated": utc_now(),
        "hl_universe_size": len(ordered),
        "policy": POLICY,
        "coins": ordered,
    }
    write_universe(payload)
    chain_doc = {"version": 1, "updated": payload["updated"], "chains": CHAINS}
    path = ROOT / "mapping" / "chains.yaml"
    path.write_text(
        "# Chain registry: collector, free endpoints, and what minute-level data can mean.\n"
        "# Activity categories that a run could not fill are null in the sample, with a reason.\n",
        encoding="utf-8",
    )
    with path.open("a", encoding="utf-8") as handle:
        yaml.safe_dump(chain_doc, handle, sort_keys=False, allow_unicode=True, width=110)
    print(f"coins {len(ordered)} chains {len(CHAINS)} unmapped {sum(1 for c in ordered if c['status']=='unmapped')}")

if __name__ == "__main__":
    main()
