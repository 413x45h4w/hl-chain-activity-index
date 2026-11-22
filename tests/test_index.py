import json
import tempfile
import unittest
from pathlib import Path

from hl_activity.mapping import (
    active_index_coins,
    chains_for_coins,
    is_k_prefix,
    load_chains,
    load_universe,
)
from hl_activity.rank import metric_bundle, percentile_ranks, rank_chains
from hl_activity.storage import connect, insert_activity


ROOT = Path(__file__).resolve().parents[1]


class MappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.universe = load_universe()
        cls.chains = load_chains()
        cls.meta = json.loads((ROOT / "mapping" / "hl_meta_snapshot.json").read_text())

    def test_covers_snapshot_universe(self):
        tickers = [row["name"] for row in self.meta["universe"]]
        mapped = [coin["ticker"] for coin in self.universe["coins"]]
        self.assertEqual(mapped, tickers)
        self.assertEqual(len(tickers), self.universe["hl_universe_size"])

    def test_every_mapped_chain_exists(self):
        ids = set(self.chains["by_id"])
        for coin in self.universe["coins"]:
            if coin["status"] == "mapped":
                self.assertIn(coin["chain_id"], ids, coin["ticker"])
                self.assertIn(coin["asset_role"], {"native", "hosted"})

    def test_unmapped_are_explicit(self):
        for coin in self.universe["coins"]:
            if coin["status"] == "unmapped":
                self.assertIsNone(coin["chain_id"])
                self.assertTrue(coin["notes"])

    def test_stablecoins_leave_the_index(self):
        active = active_index_coins(self.universe)
        self.assertNotIn("USTC", [c["ticker"] for c in active])
        stables = [c["ticker"] for c in self.universe["coins"] if c["is_stablecoin"]]
        self.assertEqual(stables, ["USTC"])
        self.assertIn("ENA", [c["ticker"] for c in active])
        self.assertIn("PAXG", [c["ticker"] for c in active])
        self.assertIn("STABLE", [c["ticker"] for c in active])

    def test_k_prefix_and_hype(self):
        by = self.universe["by_ticker"]
        for ticker, coin in by.items():
            if is_k_prefix(ticker) and coin["status"] == "mapped":
                self.assertEqual(coin["multiplier"], 1000, ticker)
                self.assertTrue(coin["underlying"], ticker)
        self.assertEqual(by["HYPE"]["chain_id"], "hyperliquid")
        self.assertEqual(by["HYPE"]["asset_role"], "native")
        self.assertEqual(by["kPEPE"]["chain_id"], "ethereum")
        self.assertEqual(by["kBONK"]["underlying"], "BONK")
        self.assertNotEqual(by["KAS"]["multiplier"], 1000)
        self.assertIsNone(by["W"]["chain_id"])

    def test_delist_flags_match_snapshot(self):
        flags = {row["name"]: bool(row.get("isDelisted")) for row in self.meta["universe"]}
        for coin in self.universe["coins"]:
            self.assertEqual(coin["is_delisted"], flags[coin["ticker"]])

    def test_active_chains_skip_delisted_only(self):
        active = active_index_coins(self.universe)
        chain_ids = set(chains_for_coins(active))
        self.assertIn("bitcoin", chain_ids)
        self.assertIn("solana", chain_ids)
        self.assertNotIn("fantom", chain_ids)
        self.assertNotIn("canto", chain_ids)


class RankTests(unittest.TestCase):
    def test_percentiles_and_missing_components(self):
        self.assertEqual(percentile_ranks({"a": 1, "b": 3, "c": None}), {"a": 0.0, "b": 100.0})
        rows = [
            {"chain_id": "high", "sample_kind": "window", "observed_at": "2026-11-22T00:00:00Z",
             "window_seconds": 60, "gross_tx_count": 100, "excluded_tx_count": 10, "net_tx_count": 90,
             "native_transfer_value_usd": 50, "native_transfer_is_net": 1, "active_addresses": 20},
            {"chain_id": "low", "sample_kind": "window", "observed_at": "2026-11-22T00:00:00Z",
             "window_seconds": 60, "gross_tx_count": 10, "excluded_tx_count": 0, "net_tx_count": 10,
             "native_transfer_value_usd": None, "native_transfer_is_net": 0, "active_addresses": None},
            {"chain_id": "empty", "sample_kind": "window", "observed_at": "2026-11-22T00:00:00Z",
             "window_seconds": None, "gross_tx_count": None, "net_tx_count": None,
             "native_transfer_value_usd": None, "native_transfer_is_net": 0, "active_addresses": None},
        ]
        result = rank_chains(rows)
        self.assertEqual([item["chain_id"] for item in result["ranked"]], ["high", "low"])
        self.assertEqual(result["unranked"][0]["chain_id"], "empty")
        high = result["ranked"][0]
        self.assertGreater(high["composite"], result["ranked"][1]["composite"])
        self.assertIn("net_native_transfer_usd_per_min", high["components_used"])
        self.assertNotIn("net_native_transfer_usd_per_min", result["ranked"][1]["components_used"])
        bundle = metric_bundle(rows[0])
        self.assertAlmostEqual(bundle["net_tx_per_min"], 90)
        self.assertAlmostEqual(bundle["excluded_tx_per_min"], 10)

    def test_append_only_ignores_duplicate_sample_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "activity.sqlite")
            row = {
                "collected_at": "2026-11-22T00:00:00Z",
                "observed_at": "2026-11-22T00:00:00Z",
                "chain_id": "bitcoin",
                "sample_kind": "window",
                "sample_key": "bitcoin:block:1",
                "gross_tx_count": 10,
                "net_tx_count": 9,
            }
            self.assertTrue(insert_activity(conn, row))
            row["gross_tx_count"] = 999
            row["collected_at"] = "2026-11-22T00:01:00Z"
            self.assertFalse(insert_activity(conn, row))
            conn.commit()
            stored = conn.execute("SELECT gross_tx_count FROM activity").fetchone()[0]
            self.assertEqual(stored, 10)
            conn.close()


if __name__ == "__main__":
    unittest.main()
