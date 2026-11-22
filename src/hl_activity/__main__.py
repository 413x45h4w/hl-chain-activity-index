"""CLI: sync the mapping, collect activity, rank, and write the dashboard."""

from __future__ import annotations

import argparse
import json
import time

from .collect import collect_once, coverage_report, dumps
from .dashboard import write_dashboard
from .hl_client import fetch_perp_meta
from .mapping import load_chains, load_universe, sync_with_meta, write_universe


def cmd_sync(_args):
    universe = load_universe()
    chains = load_chains()
    meta = fetch_perp_meta()
    updated = sync_with_meta(universe, meta, chains)
    added = updated.pop("added")
    missing = updated.pop("missing_from_meta")
    write_universe(updated)
    print(json.dumps({"version": updated["version"], "added_unmapped": added, "missing_from_meta": missing}, indent=2))


def cmd_collect(args):
    summary = collect_once(workers=args.workers)
    print(dumps(summary))


def cmd_run(args):
    while True:
        started = time.time()
        try:
            summary = collect_once(workers=args.workers)
            print(dumps(summary), flush=True)
        except Exception as exc:
            print(json.dumps({"error": str(exc)}), flush=True)
        elapsed = time.time() - started
        time.sleep(max(1.0, args.interval - elapsed))


def cmd_dashboard(_args):
    path = write_dashboard()
    print(path)


def cmd_all(args):
    cmd_sync(args)
    cmd_collect(args)
    path = write_dashboard()
    print(json.dumps({"dashboard": str(path), "coverage": coverage_report()}, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid native-chain activity index")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync")
    collect = sub.add_parser("collect")
    collect.add_argument("--workers", type=int, default=12)
    run = sub.add_parser("run")
    run.add_argument("--interval", type=int, default=60)
    run.add_argument("--workers", type=int, default=8)
    sub.add_parser("dashboard")
    all_cmd = sub.add_parser("all")
    all_cmd.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    {"sync": cmd_sync, "collect": cmd_collect, "run": cmd_run, "dashboard": cmd_dashboard, "all": cmd_all}[args.cmd](args)


if __name__ == "__main__":
    main()
