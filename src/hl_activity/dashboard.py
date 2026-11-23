"""Build a self-contained dashboard from the append-only history."""

from __future__ import annotations

import json
from pathlib import Path

from .mapping import active_index_coins, load_chains, load_universe
from .rank import metric_bundle, rank_chains, select_latest_windows
from .storage import ROOT, connect, load_activity

OUT = ROOT / "dashboard" / "index.html"
DERIVED = ROOT / "data" / "derived" / "ranking.json"


def _hydrate(row):
    for key in ("tx_types_json", "null_reasons_json", "extra_json"):
        value = row.get(key)
        if isinstance(value, str) and value:
            try:
                row[key] = json.loads(value)
            except json.JSONDecodeError:
                row[key] = {}
        elif value is None:
            row[key] = {}
    row["native_transfer_is_net"] = bool(row.get("native_transfer_is_net"))
    return row


def _history(rows):
    points = []
    for row in rows:
        metrics = metric_bundle(row)
        points.append(
            {
                "observed_at": row.get("observed_at"),
                "sample_kind": row.get("sample_kind"),
                "window_seconds": row.get("window_seconds"),
                "gross_tx_per_min": metrics["gross_tx_per_min"],
                "net_tx_per_min": metrics["net_tx_per_min"],
                "fee_usd_per_min": metrics["fee_usd_per_min"],
                "emissions_usd_per_min": metrics["emissions_usd_per_min"],
                "nev_usd_per_min": metrics["nev_usd_per_min"],
                "tx_types": row.get("tx_types_json") or {},
                "null_reasons": row.get("null_reasons_json") or {},
                "lag_seconds": row.get("lag_seconds"),
                "source": row.get("source"),
                "head_height": row.get("head_height"),
            }
        )
    points.sort(key=lambda item: item.get("observed_at") or "")
    return points


def build_model():
    universe = load_universe()
    chains = load_chains()
    conn = connect()
    try:
        rows = [_hydrate(row) for row in load_activity(conn)]
    finally:
        conn.close()
    active = active_index_coins(universe)
    active_ids = {c["chain_id"] for c in active if c.get("status") == "mapped" and c.get("chain_id")}
    by_chain_rows = {}
    for row in rows:
        by_chain_rows.setdefault(row["chain_id"], []).append(row)
    windows = select_latest_windows([r for r in rows if r["chain_id"] in active_ids])
    ranked = rank_chains(list(windows.values()))
    chain_meta = chains["by_id"]

    def coins_for(chain_id, include_delisted):
        out = []
        for coin in universe["coins"]:
            if coin.get("chain_id") != chain_id or coin.get("is_stablecoin"):
                continue
            if coin.get("status") != "mapped":
                continue
            if coin.get("is_delisted") and not include_delisted:
                continue
            out.append(
                {
                    "ticker": coin["ticker"],
                    "role": coin.get("asset_role"),
                    "standard": coin.get("standard"),
                    "multiplier": coin.get("multiplier"),
                    "underlying": coin.get("underlying"),
                    "delisted": bool(coin.get("is_delisted")),
                    "confidence": coin.get("confidence"),
                }
            )
        return out

    def pack(item, ranked_flag):
        cid = item["chain_id"]
        meta = chain_meta.get(cid, {})
        row = item.get("row") or {}
        reasons = row.get("null_reasons_json") or {}
        lag_unknown = "lag_seconds" in reasons or "observed_at" in reasons
        return {
            "chain_id": cid,
            "name": meta.get("name", cid),
            "rank": item.get("rank"),
            "ranked": ranked_flag,
            "composite": item.get("composite"),
            "composite_z": item.get("composite_z"),
            "components_used": item.get("components_used") or [],
            "component_count": item.get("component_count") or 0,
            "percentiles": item.get("percentiles") or {},
            "metrics": item.get("metrics") or metric_bundle(row),
            "reason": item.get("reason"),
            "lag_seconds": None if lag_unknown else row.get("lag_seconds"),
            "lag_unknown": lag_unknown,
            "window_seconds": row.get("window_seconds"),
            "observed_at": row.get("observed_at"),
            "source": row.get("source"),
            "head_height": row.get("head_height"),
            "tx_types": row.get("tx_types_json") or {},
            "null_reasons": reasons,
            "minute_note": meta.get("minute_note"),
            "minute_feasible": meta.get("minute_feasible"),
            "coins": coins_for(cid, include_delisted=False),
            "delisted_coins": coins_for(cid, include_delisted=True),
            "history": _history(by_chain_rows.get(cid, [])),
        }

    # delisted-only coins should not appear in coins AND delisted. Fix:
    # coins_for(include_delisted=True) returns all. I'll split in pack... 
    # I passed include_delisted False for coins and True for delisted which includes active.
    # Fix below by filtering.

    table = [pack(item, True) for item in ranked["ranked"]]
    unranked = [pack(item, False) for item in ranked["unranked"]]
    for entry in table + unranked:
        entry["delisted_coins"] = [c for c in entry["delisted_coins"] if c["delisted"]]

    attempted = set(by_chain_rows)
    no_sample = []
    for cid in sorted(active_ids):
        if cid in windows:
            continue
        meta = chain_meta.get(cid, {})
        no_sample.append(
            {
                "chain_id": cid,
                "name": meta.get("name", cid),
                "ranked": False,
                "reason": "collector has not written a window sample yet",
                "minute_note": meta.get("minute_note"),
                "minute_feasible": meta.get("minute_feasible"),
                "coins": coins_for(cid, False),
                "delisted_coins": [c for c in coins_for(cid, True) if c["delisted"]],
                "history": _history(by_chain_rows.get(cid, [])),
                "metrics": {},
                "null_reasons": {},
                "tx_types": {},
                "lag_unknown": True,
            }
        )

    unmapped = []
    stables = []
    for coin in universe["coins"]:
        if coin.get("is_stablecoin"):
            stables.append({"ticker": coin["ticker"], "notes": coin.get("notes"), "delisted": coin.get("is_delisted")})
        elif coin.get("status") == "unmapped" and not coin.get("is_delisted"):
            unmapped.append({"ticker": coin["ticker"], "notes": coin.get("notes")})

    gross_ok = 0
    for cid in active_ids:
        hist = by_chain_rows.get(cid) or []
        if any(r.get("gross_tx_count") is not None for r in hist):
            gross_ok += 1

    model = {
        "generated_at": rows[-1]["collected_at"] if rows else None,
        "policy": universe.get("policy"),
        "version": universe.get("version"),
        "counts": {
            "active_non_stable": len(active),
            "active_mapped": len([c for c in active if c.get("status") == "mapped"]),
            "active_unmapped": len(unmapped),
            "active_chains": len(active_ids),
            "chains_with_gross": gross_ok,
            "chains_ranked": len(table),
            "chains_unranked": len(unranked) + len(no_sample),
            "history_rows": len(rows),
        },
        "ranked": table,
        "unranked": unranked + no_sample,
        "unmapped": unmapped,
        "stables": stables,
        "component_universe_sizes": ranked["component_universe_sizes"],
    }
    return model


def write_dashboard(path=OUT):
    model = build_model()
    DERIVED.parent.mkdir(parents=True, exist_ok=True)
    DERIVED.write_text(json.dumps(model, indent=2), encoding="utf-8")
    html = HTML.replace("__DATA__", json.dumps(model).replace("</", "<\\/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Hyperliquid chain activity index</title>
<style>
  :root {
    --bg: #10140f;
    --bg2: #181e16;
    --ink: #f4f0e4;
    --muted: #b4ad9a;
    --line: #2c3426;
    --lime: #d6f25c;
    --mint: #8ee0b8;
    --coral: #ff8b6a;
    --amber: #f0c36a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: radial-gradient(1200px 500px at 10% -10%, #24301c 0%, var(--bg) 55%);
    color: var(--ink);
    font: 15px/1.45 "Liberation Sans", "DejaVu Sans", sans-serif;
  }
  header { padding: 28px 28px 8px; max-width: 1280px; margin: 0 auto; }
  h1 { font-size: 32px; line-height: 1.05; margin: 0 0 8px; letter-spacing: -0.03em; }
  h1 span { color: var(--lime); }
  .sub { color: var(--muted); max-width: 760px; }
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; max-width: 1280px; margin: 16px auto; padding: 0 28px; }
  .stat { background: var(--bg2); border: 1px solid var(--line); padding: 12px 14px; }
  .stat b { display: block; font-size: 22px; font-variant-numeric: tabular-nums; }
  .stat span { color: var(--muted); font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; }
  main { max-width: 1280px; margin: 0 auto; padding: 0 28px 48px; }
  .wrap { overflow-x: auto; border: 1px solid var(--line); background: rgba(16,20,15,0.8); }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th, td { padding: 8px 10px; text-align: right; border-bottom: 1px solid var(--line); white-space: nowrap; }
  th { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); position: sticky; top: 0; background: #141a12; }
  td:first-child, th:first-child, td:nth-child(2), th:nth-child(2) { text-align: left; }
  tr.row { cursor: pointer; }
  tr.row:hover, tr.row.active { background: #24301c; }
  .name { font-weight: 700; }
  .muted { color: var(--muted); }
  .pos { color: var(--mint); }
  .neg { color: var(--coral); }
  section.detail { margin-top: 18px; display: grid; grid-template-columns: 1.4fr 0.8fr; gap: 12px; }
  .card { background: var(--bg2); border: 1px solid var(--line); padding: 14px; }
  h2 { margin: 0 0 8px; font-size: 20px; }
  svg.chart { width: 100%; height: 220px; background: #10150f; }
  .axis { fill: var(--muted); font-size: 11px; }
  ul.coins { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 6px; }
  ul.coins li { border: 1px solid var(--line); padding: 3px 7px; font-size: 13px; }
  .role { color: var(--amber); font-size: 11px; }
  .reasons { font-size: 13px; color: var(--muted); }
  .reasons li { margin: 4px 0; }
  .bars { display: flex; flex-direction: column; gap: 4px; }
  .barline { display: grid; grid-template-columns: 140px 1fr 48px; gap: 8px; align-items: center; font-size: 12px; }
  .track { background: #10150f; height: 8px; }
  .fill { background: var(--lime); height: 8px; }
  h3 { margin: 14px 0 6px; font-size: 14px; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); }
  @media (max-width: 860px) {
    .stats { grid-template-columns: 1fr 1fr; }
    section.detail { grid-template-columns: 1fr; }
    header, main, .stats { padding-left: 14px; padding-right: 14px; }
  }
</style>
</head>
<body>
<header>
  <h1>Chain activity, <span>net of distributions</span></h1>
  <p class="sub">Native chains of non-stablecoin Hyperliquid perps. Gross counts include reward and consensus traffic. Net removes it where the source can tell those transactions apart. The composite is the mean percentile of the components a chain actually has.</p>
</header>
<div class="stats" id="stats"></div>
<main>
  <div class="wrap"><table id="board"></table></div>
  <section class="detail">
    <div class="card" id="chart-card"></div>
    <div class="card" id="side-card"></div>
  </section>
  <h3>Unranked and missing</h3>
  <div class="wrap"><table id="unranked"></table></div>
  <h3>Active perps left unmapped</h3>
  <div class="card" id="unmapped"></div>
</main>
<script id="payload" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("payload").textContent);
const fmt = (n, d=1) => {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
  const value = Number(n);
  if (value === 0) return "0";
  const abs = Math.abs(value);
  const digits = abs < 0.01 ? 2 : d;
  if (abs < 0.01) return value.toLocaleString(undefined, {maximumSignificantDigits: digits});
  return value.toLocaleString(undefined, {maximumFractionDigits: digits});
};
const stats = document.getElementById("stats");
const c = DATA.counts;
stats.innerHTML = [
  ["Active non-stable perps", c.active_non_stable],
  ["Mapped / unmapped", c.active_mapped + " / " + c.active_unmapped],
  ["Chains with gross txs", c.chains_with_gross + " / " + c.active_chains],
  ["Ranked chains", c.chains_ranked],
].map(([k,v]) => `<div class="stat"><span>${k}</span><b>${v}</b></div>`).join("");

function metricCells(row) {
  const m = row.metrics || {};
  const lag = row.lag_unknown ? "n/a" : fmt(row.lag_seconds, 0) + "s";
  return `<td class="pos">${fmt(m.net_tx_per_min, 1)}</td>
    <td>${fmt(m.gross_tx_per_min, 1)}</td>
    <td class="neg">${fmt(m.excluded_tx_per_min, 1)}</td>
    <td>${fmt(m.net_native_transfer_usd_per_min, 0)}</td>
    <td>${fmt(m.address_intensity_per_min, 1)}</td>
    <td>${fmt(m.fee_usd_per_min, 2)}</td>
    <td>${fmt(m.emissions_usd_per_min, 2)}</td>
    <td class="${m.nev_usd_per_min == null ? "" : (m.nev_usd_per_min < 0 ? "neg" : "pos")}">${fmt(m.nev_usd_per_min, 2)}</td>
    <td>${fmt(row.window_seconds, 0)}s</td>
    <td>${lag}</td>
    <td>${(row.components_used || []).length || 0}/3</td>`;
}
const head = `<tr><th>Rank</th><th>Chain</th><th>Index</th><th>Net tx/min</th><th>Gross tx/min</th><th>Excluded/min</th><th>Net native USD/min</th><th>Addr intensity</th><th>Fee USD/min</th><th>Emissions USD/min</th><th>NEV USD/min</th><th>Window</th><th>Lag</th><th>Parts</th><th>Coins</th></tr>`;
const board = document.getElementById("board");
board.innerHTML = head + DATA.ranked.map(row => `<tr class="row" data-id="${row.chain_id}">
  <td>${row.rank}</td><td class="name">${row.name}</td><td>${fmt(row.composite, 1)}</td>
  ${metricCells(row)}<td>${row.coins.length}</td></tr>`).join("");
document.getElementById("unranked").innerHTML = `<tr><th>Chain</th><th>Why it is not ranked</th><th>Gross tx/min</th><th>Lag note</th></tr>` +
  DATA.unranked.map(row => `<tr class="row" data-id="${row.chain_id}"><td class="name">${row.name}</td><td class="muted">${row.reason || ""}</td><td>${fmt((row.metrics||{}).gross_tx_per_min, 1)}</td><td class="muted">${row.minute_note || ""}</td></tr>`).join("");

function series(history, key) {
  return history.filter(p => p[key] !== null && p[key] !== undefined).map(p => ({t: p.observed_at, y: p[key]}));
}
function chart(history) {
  const gross = series(history, "gross_tx_per_min");
  const net = series(history, "net_tx_per_min");
  const w = 640, h = 220, pad = 28;
  const all = gross.concat(net);
  if (!all.length) return `<p class="muted">No per-minute points yet. Counts without a measured window are not plotted as a rate.</p>`;
  const ys = all.map(p => p.y);
  const min = Math.min(...ys), max = Math.max(...ys);
  const span = max - min || 1;
  const xs = all.map(p => p.t);
  const tmin = xs[0], tmax = xs[xs.length - 1];
  function x(t) {
    if (tmin === tmax) return pad;
    const a = Date.parse(t), b = Date.parse(tmin), c = Date.parse(tmax);
    return pad + (w - pad * 2) * ((a - b) / (c - b));
  }
  function y(v) { return h - pad - (h - pad * 2) * ((v - min) / span); }
  function path(points) {
    return points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  }
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img">
    <text class="axis" x="${pad}" y="16">tx per minute</text>
    <text class="axis" x="${pad}" y="${h - 8}">${(tmin || "").slice(5, 16)} → ${(tmax || "").slice(5, 16)}</text>
    <path d="${path(gross)}" fill="none" stroke="#f0c36a" stroke-width="1.6"/>
    <path d="${path(net)}" fill="none" stroke="#8ee0b8" stroke-width="2"/>
  </svg>
  <p class="muted">Amber is gross tx/min. Mint is net tx/min. A point exists only when that sample had a measured window.</p>`;
}
function bars(types) {
  const entries = Object.entries(types || {}).filter(([, v]) => typeof v === "number");
  if (!entries.length) return `<p class="muted">No type breakdown on the latest window.</p>`;
  const max = Math.max(...entries.map(([, v]) => v)) || 1;
  return `<div class="bars">` + entries.sort((a,b) => b[1]-a[1]).slice(0, 12).map(([k,v]) =>
    `<div class="barline"><span>${k}</span><div class="track"><div class="fill" style="width:${(100*v/max).toFixed(1)}%"></div></div><span>${fmt(v, 0)}</span></div>`).join("") + `</div>`;
}
function reasons(map) {
  const items = Object.entries(map || {});
  if (!items.length) return "";
  return `<h3>Nulls and limits</h3><ul class="reasons">` + items.map(([k,v]) => `<li><b>${k}.</b> ${v}</li>`).join("") + `</ul>`;
}
function show(id) {
  const row = DATA.ranked.concat(DATA.unranked).find(r => r.chain_id === id);
  if (!row) return;
  document.querySelectorAll("tr.row").forEach(tr => tr.classList.toggle("active", tr.dataset.id === id));
  const coins = (row.coins || []).map(c => `<li>${c.ticker} <span class="role">${c.role || ""}${c.multiplier ? " ×"+c.multiplier : ""}</span></li>`).join("");
  document.getElementById("chart-card").innerHTML = `<h2>${row.name}</h2>
    <p class="muted">${row.source || "no source yet"} · head ${row.head_height || "—"} · window ${fmt(row.window_seconds, 0)}s · ${row.minute_feasible === false ? "minute cadence limited" : "polled on the collector interval"}</p>
    ${chart(row.history || [])}
    <h3>Latest type mix</h3>${bars(row.tx_types)}
    ${reasons(row.null_reasons)}
    <p class="muted">${row.minute_note || ""}</p>`;
  document.getElementById("side-card").innerHTML = `<h2>Hyperliquid coins</h2>
    <ul class="coins">${coins || "<li class='muted'>none active</li>"}</ul>
    <h3>Delisted on the same chain</h3>
    <ul class="coins">${(row.delisted_coins || []).map(c => `<li>${c.ticker}</li>`).join("") || "<li class='muted'>none</li>"}</ul>
    <h3>Composite</h3>
    <p>${row.ranked ? fmt(row.composite, 1) + " percentile mean across " + (row.components_used || []).join(", ") : (row.reason || "unranked")}</p>
    <p class="muted">Z-score mean ${fmt(row.composite_z, 2)}. Parts used ${(row.components_used || []).length} of 3.</p>`;
}
document.body.addEventListener("click", (event) => {
  const tr = event.target.closest("tr.row");
  if (tr) show(tr.dataset.id);
});
document.getElementById("unmapped").innerHTML = DATA.unmapped.length
  ? `<ul class="reasons">` + DATA.unmapped.map(c => `<li><b>${c.ticker}.</b> ${c.notes || ""}</li>`).join("") + `</ul>`
  : `<p>Every active non-stable perp is mapped.</p>`;
if (DATA.ranked[0]) show(DATA.ranked[0].chain_id);
else if (DATA.unranked[0]) show(DATA.unranked[0].chain_id);
</script>
</body>
</html>
"""
