# Hyperliquid chain activity index

On-chain activity for the home chain of every non-stablecoin Hyperliquid perp, ranked by activity **net of distributions**.

The coin list and every USD price come from Hyperliquid's public info API (`POST https://api.hyperliquid.xyz/info`). Nothing in the ranking is filled in from another price source.

## Run it

```bash
pip install -r requirements.txt
export PYTHONPATH=src
python -m hl_activity sync       # refresh delist flags; new tickers become unmapped
python -m hl_activity collect    # one pass, append-only
python -m hl_activity dashboard  # write dashboard/index.html
```

`python -m hl_activity all` does those three in order.

Minute collection, where the source can support it:

```bash
bash scripts/run_minutely.sh
```

GitHub Actions cannot run every minute. `.github/workflows/collect.yml` runs on a 15-minute cron and on manual dispatch, and uploads the history as an artifact instead of committing over it. The schedule is a backup. The minute loop is the runner that builds a price-comparable series.

Open `dashboard/index.html` in a browser. It is a single file with the latest ranking and the stored history embedded.

## Universe

`mapping/universe.yaml` (and `mapping/universe.csv`) is the reviewed map. `scripts/seed_mapping.py` is the authoring source that regenerates it. `python -m hl_activity sync` re-reads `meta` and:

- updates `is_delisted`
- appends any new ticker as `status: unmapped`

It does not guess a chain.

A perp is in the activity index when it is not a stablecoin and not delisted. Delisted markets stay in the mapping so the file still covers the full `meta` universe. Chains that only appear through delisted markets (Fantom, Canto, Blast, Scroll, Neutron, MANTRA, Story) are not in the active ranking.

### Stablecoins

Excluded only when the token itself is a stablecoin. In the current `meta` that is **USTC** (TerraClassicUSD), including though the peg failed. Governance tokens of stablecoin issuers stay in: ENA, SKY, USUAL, and the RESOLV / STBL tickers (those two are unmapped for a different reason). PAXG is gold, CHIP trades far from $1, and STABLE's Hyperliquid mid is about $0.02, so none of those are treated as stablecoins.

### k-prefix and HYPE

`kPEPE`, `kSHIB`, `kBONK`, `kLUNC`, `kDOGS` are 1000-unit contracts on the underlying token. The chain is the underlying's chain. `KAS` is Kaspa, not a k-prefix. `kFLOKI` and `kNEIRO` are unmapped because the underlying's home chain is ambiguous; the 1000 multiplier is still recorded for `kFLOKI`.

`HYPE` is the native gas token of Hyperliquid L1 / HyperEVM. `PURR` is a HIP-1 token on that chain, so it is hosted. `HYPER` is Hyperlane's token on Ethereum, not HYPE.

### Native vs hosted

`asset_role: native` means the coin is that chain's gas or staking asset. `hosted` means it was issued there (ERC-20, BEP-20, SPL, jetton, BRC-20, HIP-1) while another asset pays gas. A bridged copy does not move the mapping when a home is documented: ZETA, MNT, and POL are the gas tokens of their own chains even though Kraken also deposits an Ethereum representation; WLFI's lock-and-release contract is on Ethereum; SOPH's canonical contract is on Ethereum while the Sophon chain copy is bridged and that chain is winding down.

### Unmapped on purpose

These active perps have no chain in the index:

| Ticker | Why |
| --- | --- |
| W | Wormhole NTT, no single home. Kraken's Solana deposit is an exchange choice. |
| kFLOKI | Official contracts on both Ethereum and BNB Chain. |
| kNEIRO | Distinct from NEIROETH, underlying not confirmed. |
| VIRTUAL | Contracts on Ethereum, Base, Solana, Robinhood, and Arc. |
| RESOLV | Governance token, not USR. Ethereum and BNB contracts, no stated home. |
| STBL | Governance token, not USST. Ethereum and BNB contracts disagree with the Kraken rail. |
| HEMI | Hemi's gas is ETH. The HEMI token's issuance chain was not confirmed. |

Delisted and also unmapped: JELLY, DOOD, LAUNCHCOIN, MAVIA, PIXEL, MAV, AI, SHIA, OX, ARK, STRAX, NFTI.

## What is measured

Each sample is one chain window: a block, a ledger, a checkpoint span, or a ~60s RPC sample. Counts are divided by the **measured** window to get per-minute rates. Windows shorter than one second are kept in the history and are not scaled into the ranking, because a tenth of a second would otherwise dominate the index. If the source does not provide a window, the rate is null. It is not filled with a guessed block time except where a row says so (Tron's 3s target is labeled in `null_reasons` and is the protocol interval, not a measured parent delta).

**Gross** is every transaction the source returned.

**Excluded**, and therefore removed from **net**, only when the source can name them:

- Solana vote transactions, when `numNonVoteTransactions` is lower than `numTransactions` (this is what Fogo's SVM feed does). On Solana mainnet the public RPC currently reports the two fields equal, and a full block contains no `Vote111...` instructions, so votes are not in that feed and excluded is 0. That is recorded on the sample, not assumed in silence.
- Bitcoin-like coinbase / miner transactions, one per block.
- Cosmos reward withdrawals, Terra oracle prevote/vote transactions, and Babylon finality signatures, when those messages are the whole transaction. IBC transfers and governance votes stay in net. Oracle votes are validator duties, the same class as a Solana vote. The Cosmos rate uses about 60 seconds of blocks, so a single oracle-vote block is not scaled up as if it repeated every few seconds.
- OP-stack deposit transactions (`type 0x7e`).
- Aptos block metadata, state checkpoint, and validator transactions.
- Algorand key registration and heartbeat.
- Tron `WithdrawBalanceContract` (claiming block rewards).
- Monero's miner transaction.

Airdrops that look like ordinary contract calls are not separable. They stay inside net, and the EVM sample says so. Consensus-layer issuance (Ethereum beacon rewards, Solana inflation) is not a transaction in these feeds. Where a block subsidy is a separate field, it is stored as `distribution_value_native` and is not added back into net.

**Native transfer value** is peer-to-peer value of the gas asset only when the source exposes it (EVM `msg.value` excluding system txs, XRPL XRP payments, Cosmos `MsgSend` of the native denom, Algorand payments, Tron `TransferContract`). UTXO output sums include change, so they are not used. XRP payments from an account to itself are not counted as volume. `native_transfer_is_net` is true only when that value is known not to include reward payouts. Otherwise the USD figure is left out of the ranking.

**Token transfer counts** are ERC-20 `Transfer` logs when receipts are available, TRC-10 transfers, Algorand asset transfers, and XRPL token payments. Heterogeneous token amounts are not converted to USD.

**Fees** are the execution fees the source reports (EVM effective gas price, mempool.space `totalFees`, Solana `meta.fee`, Tron `net_fee` plus `energy_fee`, XRPL fees, and so on). Solana's 60-second transaction sample does not include fees, so the fee column uses the measured span of the blocks whose fees were summed. USD uses the Hyperliquid mid of the gas token. If that mid does not exist, USD is null. USDC is the Hyperliquid quote: when gas is USDC and `allMids` has no USDC row, the price is 1 because mids are already in USDC. A blank fee means the source did not provide one. A zero is a measured zero.

**Active addresses** are unique senders and receivers in the inspected window. The ranking uses address intensity (addresses in the window, per minute). That is not a deduped daily active user count.

Missing fields are JSON `null` plus a `null_reasons` entry. They are not zero.

## Ranking

The ranking observation is the newest `sample_kind: window` row for each active chain.

Components, each converted to a percentile among the chains where that component is present (100 = highest):

1. net transactions per minute
2. net native-transfer USD per minute
3. address intensity per minute

The **composite index** is the unweighted mean of the percentiles that exist. A missing component is omitted, not treated as zero. `component_count` shows how many of the three were available. A z-score mean of the same components is stored beside it. Chains with none of the three are listed as unranked with the reason.

Percentiles are recomputed from the latest windows whenever the dashboard is built. History rows are not rewritten.

## History

`data/history/activity.sqlite` is append-only. The primary key of a sample is `sample_key` (chain, block or slot or checkpoint). Inserts use `INSERT OR IGNORE`. A second pass does not overwrite a block that was already stored. Daily CSV partitions live next to the database:

- `data/history/activity/YYYY-MM-DD.csv`
- `data/history/prices/YYYY-MM-DD.csv`

`data/derived/ranking.json` is a rebuilt view for the dashboard, not the historical record.

Hyperliquid mids for mapped tickers are appended once per collection minute (`prices.sample_key` is ticker plus minute).

## Sources and gaps

Collectors and endpoints are in `mapping/chains.yaml`, including whether minute-level data is realistic.

Chains where a free public source does not support a one-minute confirmed series:

- Bitcoin, Litecoin, Bitcoin Cash, Bitcoin SV, Dogecoin, Zcash, Dash, Monero: activity arrives per block (minutes, not one minute). The collector still polls every run and records lag as time since the block.
- Stacks follows Bitcoin. Net is withheld because coinbase and tenure txs sit inside the Hiro tx count.
- Arweave and Mina block times are minutes. Mina's previous GraphQL host no longer resolves; that chain is null until a free replacement is confirmed.
- TON / GRAM: Toncenter and TonAPI need an API key. No keyless breakdown was confirmed, so the chain is null.
- Canton has no public transaction feed.
- CashCat and Movement have no confirmed free RPC in the sources used here.
- Internet Computer's public `message_execution_rate` is stored in `extra` and not as a transaction count, because the unit is not a documented tx tally.
- Sui JSON-RPC was deprecated. GraphQL checkpoint totals give a gross delta only.
- Polkadot's sidecar host did not resolve. The substrate RPC returns encoded extrinsics, so net is withheld.
- Filecoin message methods are not decoded, so net is withheld.
- Hyperliquid figures are **HyperEVM only**. The L1 order book is not a public typed transaction feed and is not estimated.
- THORChain's thornode endpoint is not a stock Cosmos LCD; if the tx query fails, the chain stays null.

A category that a run could not fill is null on that chain's latest window. The dashboard lists those reasons on the chain. Re-running `collect` appends a new window when the head moves; it does not backfill a category the source does not have.

## Latest run

Collected 22 Nov 2026 from the public endpoints in `mapping/chains.yaml`. The `meta` snapshot has 235 perp markets. 179 are active and not stablecoins. 172 of those are mapped. 7 stay unmapped (W, kFLOKI, kNEIRO, VIRTUAL, RESOLV, STBL, HEMI). USTC is the only stablecoin exclusion. That is 71 active chains.

61 of 71 chains returned a gross transaction count. 42 have a usable net rate and are ranked. History rows are append-only. Fogo still shows the Solana-style vote split (about 8,900 gross transactions per minute, about 105 after votes). Terra Classic is measured over about 60 seconds of blocks: roughly 185 gross transactions per minute, of which about 179 are oracle prevote/vote transactions, leaving about 6 net. Babylon finality signatures are removed the same way. Solana's public feed currently contains no vote instructions, so its net equals its gross and the sample says why. On Ethereum and the other EVM chains, consensus votes and block rewards are not transactions in the execution block, so net and gross stay close. Bitcoin subtracts the one coinbase transaction in the block.

28 chains are unranked because the free source has no measured window, no separable net, or no public feed. That includes TON, Canton, CashCat, Movement, Sui (gross checkpoint delta only), Aptos (windows shorter than a second), Cardano (Koios timed out), and Starknet (HTTP 429).

## Dashboard

`dashboard/index.html` shows:

- the ranked table with net, gross, excluded, native USD, address intensity, fees, lag, and how many index components were present
- the chain's active Hyperliquid perps, and delisted perps on the same chain
- per-chain tx/min over the stored samples (gross in amber, net in mint)
- the latest type mix
- null reasons and the minute-level note
- active unmapped tickers

Lag is hidden when the sample's clock is the collector's clock rather than a chain timestamp (`lag_unknown`).

## Tests

```bash
PYTHONPATH=src python3 -m unittest tests.test_index
```

The tests check that the mapping covers the saved `meta` snapshot, that unmapped coins have no chain, that USTC is the stablecoin exclusion, that k-prefixes and HYPE are handled, that the composite omits missing components, and that a duplicate `sample_key` does not overwrite.
