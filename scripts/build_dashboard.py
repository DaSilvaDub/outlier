#!/usr/bin/env python3
"""
build_dashboard.py — Outlier daily dashboard generator.

Reads the latest pack (packs/<YYYY-MM-DD>/candidates.csv + briefing.md) and the
per-league freshness reports (data/<league>/reports/cards_status_latest.json),
then writes a single self-contained dashboard.html at the repo root.

No third-party dependencies. Offline-safe (charts are inline SVG; no CDN).
Re-run anytime:  python scripts/build_dashboard.py
"""
from __future__ import annotations

import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / "packs"
DATA = ROOT / "data"
OUT = ROOT / "dashboard.html"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ----------------------------- data loading ------------------------------- #
def pack_dates() -> list[str]:
    """All real slate dates (newest first), excluding the 1900 placeholder."""
    if not PACKS.exists():
        return []
    ds = [
        p.name
        for p in PACKS.iterdir()
        if p.is_dir() and DATE_RE.match(p.name) and p.name != "1900-01-01"
    ]
    return sorted(ds, reverse=True)


def read_candidates(date: str) -> list[dict]:
    f = PACKS / date / "candidates.csv"
    if not f.exists():
        return []
    with f.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def parse_freshness(date: str) -> dict:
    """Pull SLATE date + the Freshness/Coverage bullet lines from briefing.md."""
    f = PACKS / date / "briefing.md"
    out = {"slate": date, "lines": []}
    if not f.exists():
        return out
    text = f.read_text(encoding="utf-8", errors="replace").splitlines()
    in_fresh = False
    for ln in text:
        if ln.startswith("SLATE:"):
            out["slate"] = ln.split(":", 1)[1].strip()
        if ln.strip().startswith("### Freshness"):
            in_fresh = True
            continue
        if in_fresh:
            if ln.strip().startswith("###") or ln.strip().startswith("Any CAVEAT"):
                in_fresh = False
                continue
            if ln.strip().startswith("- "):
                out["lines"].append(ln.strip()[2:].strip())
    return out


def read_status() -> list[dict]:
    """Per-league coverage + skew from data/<league>/reports/cards_status_latest.json."""
    rows = []
    for league in ("MLB", "WNBA"):
        f = DATA / league / "reports" / "cards_status_latest.json"
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        cov = d.get("coverage", {}) or {}
        skew = d.get("snapshot_skew", {}) or {}
        rows.append(
            {
                "league": d.get("league", league),
                "status": d.get("status", "?"),
                "missing": d.get("missing_feeds", []) or [],
                "props_markets": cov.get("props_markets"),
                "movement_records": cov.get("movement_records"),
                "board_a": cov.get("board_a_cards"),
                "board_b": cov.get("board_b_cards"),
                "skew_hours": skew.get("skew_hours"),
                "is_skewed": skew.get("is_skewed"),
            }
        )
    return rows


# ------------------------------- helpers ---------------------------------- #
def fnum(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def clean_selection(s: str) -> str:
    """Selections often double the player name; collapse an immediate repeat."""
    if not s:
        return s
    # collapse 'Name Name - rest' -> 'Name - rest' when name is duplicated
    m = re.match(r"^(.+?)\s+\1\s+-\s+(.*)$", s)
    if m:
        return f"{m.group(1)} - {m.group(2)}"
    return s


def american(v) -> str:
    n = fnum(v)
    if n is None:
        return "—"
    n = int(round(n))
    return f"+{n}" if n > 0 else str(n)


# ------------------------------ rendering --------------------------------- #
def build_rows(cands: list[dict]) -> list[dict]:
    rows = []
    for c in cands:
        edge = fnum(c.get("edge_pct"))
        rows.append(
            {
                "sport": c.get("sport", ""),
                "sel": clean_selection(c.get("selection", "")),
                "mkt": c.get("market_type", ""),
                "line": c.get("line", ""),
                "price": american(c.get("price")),
                "book": c.get("book", ""),
                "edge": edge,                       # fraction (0.20 = 20%)
                "units": fnum(c.get("recommended_units_pre_news")),
                "maxu": fnum(c.get("max_units")),
                "lopen": c.get("line_open", ""),
                "lnow": c.get("line_now", ""),
                "pub": c.get("public_money_pct", ""),
                "lev": c.get("research_leverage", ""),
                "ev": fnum(c.get("outlier_ev_pct")),
            }
        )
    # EV rows first (desc edge), then signal-only rows
    rows.sort(key=lambda r: (r["edge"] is None, -(r["edge"] or 0)))
    return rows


def edge_buckets(rows: list[dict]) -> list[tuple[str, int]]:
    buckets = [
        ("<5%", 0), ("5–10%", 0), ("10–15%", 0), ("15–20%", 0), ("20%+", 0)
    ]
    for r in rows:
        e = r["edge"]
        if e is None:
            continue
        pct = e * 100
        if pct < 5:
            buckets[0] = (buckets[0][0], buckets[0][1] + 1)
        elif pct < 10:
            buckets[1] = (buckets[1][0], buckets[1][1] + 1)
        elif pct < 15:
            buckets[2] = (buckets[2][0], buckets[2][1] + 1)
        elif pct < 20:
            buckets[3] = (buckets[3][0], buckets[3][1] + 1)
        else:
            buckets[4] = (buckets[4][0], buckets[4][1] + 1)
    return buckets


def svg_bars(pairs: list[tuple[str, float]], unit: str = "") -> str:
    """Simple horizontal bar chart as inline SVG."""
    if not pairs:
        return "<p class='muted'>No data.</p>"
    maxv = max((v for _, v in pairs), default=0) or 1
    rowh, w, lblw = 30, 360, 70
    h = rowh * len(pairs) + 10
    parts = [f"<svg viewBox='0 0 {lblw + w + 60} {h}' width='100%' role='img'>"]
    for i, (lbl, v) in enumerate(pairs):
        y = i * rowh + 8
        bw = int((v / maxv) * w) if maxv else 0
        parts.append(
            f"<text x='{lblw - 8}' y='{y + 14}' text-anchor='end' "
            f"class='svglbl'>{html.escape(str(lbl))}</text>"
        )
        parts.append(
            f"<rect x='{lblw}' y='{y}' width='{bw}' height='18' rx='3' "
            f"class='svgbar'></rect>"
        )
        vtxt = f"{v:g}{unit}"
        parts.append(
            f"<text x='{lblw + bw + 6}' y='{y + 14}' class='svgval'>{vtxt}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def trend_data() -> list[tuple[str, float]]:
    """Total recommended units per recent pack date (oldest→newest, last 7)."""
    out = []
    for d in reversed(pack_dates()[:7]):
        rows = build_rows(read_candidates(d))
        total = sum((r["units"] or 0) for r in rows)
        out.append((d[5:], round(total, 1)))  # MM-DD label
    return out


def render(date: str) -> str:
    cands = read_candidates(date)
    rows = build_rows(cands)
    fresh = parse_freshness(date)
    status = read_status()

    n_total = len(rows)
    by_sport = {}
    for r in rows:
        by_sport[r["sport"]] = by_sport.get(r["sport"], 0) + 1
    total_units = round(sum((r["units"] or 0) for r in rows), 1)
    ev_rows = [r for r in rows if r["edge"] is not None]
    avg_edge = round(sum(r["edge"] for r in ev_rows) / len(ev_rows) * 100, 1) if ev_rows else 0
    top_edge = round(max((r["edge"] for r in ev_rows), default=0) * 100, 1)

    gen_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # freshness banner items
    fresh_items = ""
    for ln in fresh["lines"]:
        caveat = "CAVEAT" in ln.upper()
        cls = "fz caveat" if caveat else "fz ok"
        fresh_items += f"<span class='{cls}'>{html.escape(ln)}</span>"

    # status table
    strows = ""
    for s in status:
        sk = "—"
        if s["skew_hours"] is not None:
            sk = f"{s['skew_hours']:g}h" + (" ⚠" if s["is_skewed"] else "")
        badge = "ok" if str(s["status"]).lower() == "ok" else "warn"
        strows += (
            f"<tr><td>{html.escape(str(s['league']))}</td>"
            f"<td><span class='badge {badge}'>{html.escape(str(s['status']))}</span></td>"
            f"<td>{s['props_markets'] if s['props_markets'] is not None else '—'}</td>"
            f"<td>{s['movement_records'] if s['movement_records'] is not None else '—'}</td>"
            f"<td>{s['board_a'] if s['board_a'] is not None else '—'} / "
            f"{s['board_b'] if s['board_b'] is not None else '—'}</td>"
            f"<td>{sk}</td></tr>"
        )

    # candidate table rows
    trows = ""
    for r in rows:
        edge_txt = f"{r['edge']*100:.1f}%" if r["edge"] is not None else "—"
        edge_sort = f"{r['edge']*100:.4f}" if r["edge"] is not None else "-999"
        units_txt = f"{r['units']:g}" if r["units"] is not None else "—"
        units_sort = f"{r['units']:.2f}" if r["units"] is not None else "-1"
        move = ""
        lo, ln_ = fnum(r["lopen"]), fnum(r["lnow"])
        if lo is not None and ln_ is not None and lo != ln_:
            arrow = "▲" if ln_ > lo else "▼"
            move = f"<span class='move'>{r['lopen']}→{r['lnow']} {arrow}</span>"
        elif r["lnow"]:
            move = f"<span class='muted'>{html.escape(str(r['lnow']))}</span>"
        sport_cls = "mlb" if r["sport"] == "MLB" else "wnba"
        trows += (
            f"<tr data-sport='{r['sport']}'>"
            f"<td><span class='tag {sport_cls}'>{r['sport']}</span></td>"
            f"<td class='sel'>{html.escape(r['sel'])}</td>"
            f"<td>{html.escape(r['mkt'])}</td>"
            f"<td class='num'>{html.escape(str(r['line']))}</td>"
            f"<td class='num'>{r['price']}</td>"
            f"<td>{html.escape(r['book'])}</td>"
            f"<td class='num edge' data-sort='{edge_sort}'>{edge_txt}</td>"
            f"<td class='num' data-sort='{units_sort}'>{units_txt}</td>"
            f"<td>{move}</td>"
            f"<td>{html.escape(str(r['lev']))}</td>"
            f"</tr>"
        )

    buckets = edge_buckets(rows)
    edge_chart = svg_bars([(b, n) for b, n in buckets])
    trend = trend_data()
    trend_chart = svg_bars(trend, unit="u")

    sport_chips = " ".join(
        f"<span class='chip'>{html.escape(s)}: <b>{n}</b></span>"
        for s, n in sorted(by_sport.items())
    )

    return TEMPLATE.format(
        slate=html.escape(fresh["slate"]),
        gen_at=gen_at,
        n_total=n_total,
        total_units=total_units,
        avg_edge=avg_edge,
        top_edge=top_edge,
        sport_chips=sport_chips,
        fresh_items=fresh_items or "<span class='muted'>No freshness data.</span>",
        strows=strows or "<tr><td colspan='6' class='muted'>No status reports.</td></tr>",
        trows=trows or "<tr><td colspan='10' class='muted'>No candidates in latest pack.</td></tr>",
        edge_chart=edge_chart,
        trend_chart=trend_chart,
    )


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outlier Board — {slate}</title>
<style>
:root{{--bg:#0e1116;--panel:#161b22;--panel2:#1c232d;--line:#2a3340;--txt:#e6edf3;
--muted:#8b97a7;--accent:#3fb950;--accent2:#58a6ff;--warn:#d29922;--bad:#f85149;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px 20px 60px}}
header{{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px}}
h1{{font-size:22px;margin:0;letter-spacing:.3px}}
h1 .dot{{color:var(--accent)}}
.sub{{color:var(--muted);font-size:13px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}}
.kpi{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.kpi .v{{font-size:24px;font-weight:700}}
.kpi .l{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.5px}}
.kpi .l b,.chip b{{color:var(--accent2)}}
.chips{{margin-top:6px}}.chip{{display:inline-block;background:var(--panel2);
border:1px solid var(--line);border-radius:20px;padding:2px 10px;margin:2px 4px 2px 0;font-size:12px}}
.banner{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:14px 0}}
.banner .h{{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin-bottom:8px}}
.fz{{display:inline-block;font-size:12px;padding:3px 9px;border-radius:6px;margin:3px 6px 3px 0;border:1px solid var(--line)}}
.fz.ok{{background:rgba(63,185,80,.10);color:#7ee787;border-color:rgba(63,185,80,.3)}}
.fz.caveat{{background:rgba(210,153,34,.12);color:#e3b341;border-color:rgba(210,153,34,.35)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media(max-width:820px){{.grid2{{grid-template-columns:1fr}}}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}}
.panel h2{{font-size:14px;margin:0 0 12px;letter-spacing:.3px}}
.toolbar{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:22px 0 10px}}
.toolbar .f{{margin-left:auto}}
button.filt{{background:var(--panel2);color:var(--txt);border:1px solid var(--line);
border-radius:7px;padding:6px 14px;cursor:pointer;font-size:13px}}
button.filt.on{{background:var(--accent2);color:#06121f;border-color:var(--accent2);font-weight:600}}
input#q{{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
border-radius:7px;padding:6px 12px;font-size:13px;min-width:200px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
thead th{{text-align:left;color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;
letter-spacing:.5px;padding:8px 10px;border-bottom:1px solid var(--line);cursor:pointer;user-select:none;white-space:nowrap}}
thead th:hover{{color:var(--txt)}}
tbody td{{padding:9px 10px;border-bottom:1px solid #20262f;vertical-align:middle}}
tbody tr:hover{{background:#1a212b}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.sel{{font-weight:600;max-width:280px}}
td.edge{{font-weight:700;color:#7ee787}}
.tag{{font-size:11px;font-weight:700;padding:2px 7px;border-radius:5px}}
.tag.mlb{{background:rgba(88,166,255,.15);color:#79c0ff}}
.tag.wnba{{background:rgba(219,109,182,.15);color:#f0a3d6}}
.badge{{font-size:11px;padding:2px 8px;border-radius:5px;text-transform:uppercase;font-weight:700}}
.badge.ok{{background:rgba(63,185,80,.15);color:#7ee787}}
.badge.warn{{background:rgba(210,153,34,.15);color:#e3b341}}
.move{{color:#e3b341;font-size:12px}}.muted{{color:var(--muted)}}
.svgbar{{fill:var(--accent2)}}.svglbl{{fill:var(--muted);font-size:12px}}.svgval{{fill:var(--txt);font-size:12px;font-weight:600}}
footer{{margin-top:30px;color:var(--muted);font-size:12px;text-align:center}}
</style></head>
<body><div class="wrap">
<header>
  <h1><span class="dot">●</span> OUTLIER BOARD <span class="sub">— slate {slate}</span></h1>
  <div class="sub">generated {gen_at} · auto-rebuilt daily</div>
</header>

<div class="kpis">
  <div class="kpi"><div class="v">{n_total}</div><div class="l">Candidates</div>
    <div class="chips">{sport_chips}</div></div>
  <div class="kpi"><div class="v">{total_units}u</div><div class="l">Total units staked</div></div>
  <div class="kpi"><div class="v">{avg_edge}%</div><div class="l">Avg edge (EV rows)</div></div>
  <div class="kpi"><div class="v">{top_edge}%</div><div class="l">Top edge</div></div>
</div>

<div class="banner">
  <div class="h">Freshness / Coverage</div>
  {fresh_items}
</div>

<div class="grid2">
  <div class="panel"><h2>Edge distribution</h2>{edge_chart}</div>
  <div class="panel"><h2>Units staked · last packs</h2>{trend_chart}</div>
</div>

<div class="panel" style="margin-top:16px">
  <h2>Feed status &amp; coverage</h2>
  <table><thead><tr><th>League</th><th>Status</th><th>Props mkts</th>
  <th>Movement recs</th><th>Board A / B</th><th>Skew</th></tr></thead>
  <tbody>{strows}</tbody></table>
</div>

<div class="toolbar">
  <button class="filt on" data-s="ALL">All</button>
  <button class="filt" data-s="MLB">MLB</button>
  <button class="filt" data-s="WNBA">WNBA</button>
  <input id="q" placeholder="filter player / market…">
</div>

<table id="board"><thead><tr>
  <th data-c="0">Sport</th><th data-c="1">Selection</th><th data-c="2">Market</th>
  <th data-c="3" class="num">Line</th><th data-c="4" class="num">Price</th>
  <th data-c="5">Book</th><th data-c="6" class="num">Edge</th>
  <th data-c="7" class="num">Units</th><th data-c="8">Line move</th><th data-c="9">Lev</th>
</tr></thead><tbody>{trows}</tbody></table>

<footer>Outlier pipeline · self-contained snapshot · re-run
<code>python scripts/build_dashboard.py</code> to refresh manually.</footer>
</div>
<script>
const tb=document.querySelector('#board tbody');
const rows=()=>Array.from(tb.querySelectorAll('tr'));
// filter by sport
let sport='ALL';
document.querySelectorAll('.filt').forEach(b=>b.onclick=()=>{{
  document.querySelectorAll('.filt').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');sport=b.dataset.s;apply();}});
const q=document.getElementById('q');q.oninput=apply;
function apply(){{const t=q.value.toLowerCase();rows().forEach(r=>{{
  const okS=sport==='ALL'||r.dataset.sport===sport;
  const okT=!t||r.textContent.toLowerCase().includes(t);
  r.style.display=(okS&&okT)?'':'none';}});}}
// sort
let sortDir={{}};
document.querySelectorAll('#board thead th').forEach(th=>th.onclick=()=>{{
  const c=+th.dataset.c;const dir=sortDir[c]=!sortDir[c];
  const rs=rows();rs.sort((a,b)=>{{
    let x=cell(a,c),y=cell(b,c);
    const nx=parseFloat(x),ny=parseFloat(y);
    if(!isNaN(nx)&&!isNaN(ny)){{x=nx;y=ny;}}
    if(x<y)return dir?-1:1;if(x>y)return dir?1:-1;return 0;}});
  rs.forEach(r=>tb.appendChild(r));}});
function cell(r,c){{const td=r.children[c];
  return td.dataset.sort!==undefined?td.dataset.sort:td.textContent.trim();}}
</script>
</body></html>"""


def main():
    dates = pack_dates()
    if not dates:
        OUT.write_text("<h1>No packs found.</h1>", encoding="utf-8")
        print("No pack dates found under", PACKS)
        return
    latest = dates[0]
    html_out = render(latest)
    OUT.write_text(html_out, encoding="utf-8")
    print(f"Wrote {OUT}  (slate {latest}, {len(read_candidates(latest))} candidates)")


if __name__ == "__main__":
    main()
