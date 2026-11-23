"""Append-only SQLite plus daily CSV partitions.

Derived ranking files live under data/derived and are rebuilt. They are not
the historical record. Inserts are ignore-on-sample-key, never updates.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "history" / "activity.sqlite"
ACTIVITY_DIR = ROOT / "data" / "history" / "activity"
PRICE_DIR = ROOT / "data" / "history" / "prices"

ACTIVITY_COLUMNS = [
    "collected_at",
    "observed_at",
    "chain_id",
    "sample_kind",
    "sample_key",
    "window_seconds",
    "head_height",
    "lag_seconds",
    "source",
    "gross_tx_count",
    "excluded_tx_count",
    "net_tx_count",
    "native_transfer_count",
    "native_transfer_value",
    "native_transfer_value_usd",
    "native_transfer_is_net",
    "token_transfer_count",
    "distribution_count",
    "distribution_value_native",
    "distribution_value_usd",
    "emissions_native",
    "emissions_usd",
    "active_addresses",
    "fee_native",
    "fee_usd",
    "price_ticker",
    "price_usd",
    "price_source",
    "tx_types_json",
    "null_reasons_json",
    "extra_json",
]


def connect(path=DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            observed_at TEXT,
            chain_id TEXT NOT NULL,
            sample_kind TEXT NOT NULL,
            sample_key TEXT NOT NULL UNIQUE,
            window_seconds REAL,
            head_height TEXT,
            lag_seconds REAL,
            source TEXT,
            gross_tx_count REAL,
            excluded_tx_count REAL,
            net_tx_count REAL,
            native_transfer_count REAL,
            native_transfer_value REAL,
            native_transfer_value_usd REAL,
            native_transfer_is_net INTEGER,
            token_transfer_count REAL,
            distribution_count REAL,
            distribution_value_native REAL,
            distribution_value_usd REAL,
            emissions_native REAL,
            emissions_usd REAL,
            active_addresses REAL,
            fee_native REAL,
            fee_usd REAL,
            price_ticker TEXT,
            price_usd REAL,
            price_source TEXT,
            tx_types_json TEXT,
            null_reasons_json TEXT,
            extra_json TEXT
        );
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            mid_px REAL NOT NULL,
            is_delisted INTEGER NOT NULL,
            sample_key TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            chains_targeted INTEGER,
            chains_with_net INTEGER,
            samples_inserted INTEGER,
            samples_skipped INTEGER,
            notes_json TEXT
        );
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(activity)")}
    for column in ("emissions_native", "emissions_usd"):
        if column not in existing:
            conn.execute(f"ALTER TABLE activity ADD COLUMN {column} REAL")
    return conn


def _day(iso_ts):
    return (iso_ts or "unknown")[:10]


def _history_dir(conn, kind):
    listed = conn.execute("PRAGMA database_list").fetchone()
    return Path(listed[2]).parent / kind


def _append_csv(directory, day, row, fieldnames):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day}.csv"
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            header = handle.readline().strip().split(",")
        if header != list(fieldnames):
            path = directory / f"{day}.v2.csv"
    new_file = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow({key: row.get(key) for key in fieldnames})


def insert_activity(conn, row):
    payload = {key: row.get(key) for key in ACTIVITY_COLUMNS}
    for key in ("tx_types_json", "null_reasons_json", "extra_json"):
        value = payload.get(key)
        if isinstance(value, (dict, list)):
            payload[key] = json.dumps(value, sort_keys=True)
    cur = conn.execute(
        f"""
        INSERT OR IGNORE INTO activity ({",".join(ACTIVITY_COLUMNS)})
        VALUES ({",".join(":" + c for c in ACTIVITY_COLUMNS)})
        """,
        payload,
    )
    inserted = cur.rowcount == 1
    if inserted:
        _append_csv(_history_dir(conn, "activity"), _day(payload["collected_at"]), payload, ACTIVITY_COLUMNS)
    return inserted


def insert_price(conn, collected_at, ticker, mid_px, is_delisted):
    minute = collected_at[:16]
    sample_key = f"{ticker}:{minute}"
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO prices (collected_at, ticker, mid_px, is_delisted, sample_key)
        VALUES (?, ?, ?, ?, ?)
        """,
        (collected_at, ticker, mid_px, 1 if is_delisted else 0, sample_key),
    )
    inserted = cur.rowcount == 1
    if inserted:
        _append_csv(
            _history_dir(conn, "prices"),
            _day(collected_at),
            {
                "collected_at": collected_at,
                "ticker": ticker,
                "mid_px": mid_px,
                "is_delisted": int(bool(is_delisted)),
                "sample_key": sample_key,
            },
            ["collected_at", "ticker", "mid_px", "is_delisted", "sample_key"],
        )
    return inserted


def insert_run(conn, collected_at, summary):
    conn.execute(
        """
        INSERT INTO runs (collected_at, chains_targeted, chains_with_net, samples_inserted, samples_skipped, notes_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            collected_at,
            summary.get("chains_targeted"),
            summary.get("chains_with_net"),
            summary.get("samples_inserted"),
            summary.get("samples_skipped"),
            json.dumps(summary, sort_keys=True),
        ),
    )


def load_activity(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM activity ORDER BY observed_at")]


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
