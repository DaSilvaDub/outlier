"""Self-contained HTML renderer for the triage cards payload.

Pure string templating with no third-party dependencies: the payload is
embedded as JSON and rendered/filtered client-side with vanilla JS so the file
opens straight from disk. Book odds live inside this user-facing artifact but
are never written to status/report logs.
"""

from __future__ import annotations

import json
from typing import Any

_CSS = """
:root{--bg:#0f1419;--panel:#1a2129;--panel2:#212b35;--line:#2c3a47;--txt:#e6edf3;
--muted:#8b98a5;--a:#3fb950;--b:#58a6ff;--warn:#d29922;--bad:#f85149;--chip:#30363d;}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);position:sticky;top:0;
background:var(--bg);z-index:5}
h1{margin:0 0 4px;font-size:19px}.sub{color:var(--muted);font-size:12px}
.skew{margin-top:8px;padding:6px 10px;border-radius:6px;font-size:12px;
background:#3d2e0a;color:#f0c674;border:1px solid #6b5210;display:inline-block}
.controls{display:flex;flex-wrap:wrap;gap:8px;padding:12px 24px;align-items:center;
border-bottom:1px solid var(--line)}
.controls input,.controls select{background:var(--panel2);color:var(--txt);
border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:13px}
.tab{cursor:pointer;padding:6px 12px;border-radius:6px;border:1px solid var(--line);
background:var(--panel)}.tab.on{border-color:var(--b);color:var(--b)}
.wrap{padding:16px 24px;max-width:1180px}
.cnt{color:var(--muted);font-size:12px;margin:0 0 10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
margin-bottom:10px;overflow:hidden}
.head{display:grid;grid-template-columns:46px 1fr auto;gap:12px;padding:12px 14px;
cursor:pointer;align-items:center}
.rank{font-weight:700;font-size:18px;text-align:center;color:var(--muted)}
.who{font-weight:600}.meta{color:var(--muted);font-size:12px}
.metric{text-align:right}.metric b{font-size:17px}
.evpos{color:var(--a)}.evneg{color:var(--bad)}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;
font-weight:600;margin-left:6px}
.bk-Strong,.bk-Strong-Signal{background:#12361f;color:var(--a);border:1px solid #1d5c33}
.bk-Lean{background:#102a44;color:var(--b);border:1px solid #1c4a78}
.bk-Monitor{background:#3a3209;color:var(--warn);border:1px solid #6b5210}
.bk-Pass{background:#2a2f36;color:var(--muted);border:1px solid var(--line)}
.flags{margin-top:4px}.flag{font-size:10px;padding:1px 6px;border-radius:4px;
margin-right:4px;background:#3a1d1d;color:#ff9d9d;border:1px solid #5c2a2a}
.flag.good{background:#12361f;color:#7ee2a0;border-color:#1d5c33}
.body{display:none;padding:0 14px 14px;border-top:1px solid var(--line)}
.card.open .body{display:block}
.side{background:var(--panel2);border:1px solid var(--line);border-radius:8px;
padding:10px 12px;margin-top:10px}
.side h4{margin:0 0 6px;font-size:13px}.side.hl{border-color:var(--b)}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500}
.kv{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin-top:4px}
.kv b{color:var(--txt)}
.ins{font-size:12px;color:#cdd6df;margin:3px 0;padding-left:10px;border-left:2px solid var(--line)}
.proxy{font-size:11px;color:var(--warn);margin-top:4px}
.note{color:var(--muted);font-size:11px;margin-top:6px;font-style:italic}
"""

_JS = """
const D=window.__CARDS__;let BOARD='A',Q='',BUCKET='';
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const num=v=>v==null?'-':(typeof v==='number'?(Math.round(v*100)/100):v);
const odds=v=>v==null?'-':(v>0?'+'+v:''+v);
function flagChip(f){const good=f==='proxy_market_value';
return `<span class="flag${good?' good':''}">${f.replace(/_/g,' ')}</span>`;}
function sideBlock(name,sv,hl){
 if(!sv)return'';
 let h=`<div class="side${hl?' hl':''}"><h4>${name} ${num(sv.line)}  <span class="meta">best ${odds(sv.best_odds)}</span></h4>`;
 const hr=sv.hit_rates||{};
 h+=`<div class="kv"><span>L5 <b>${num(hr.l5_pct)}</b></span><span>L10 <b>${num(hr.l10_pct)}</b></span>
 <span>L20 <b>${num(hr.l20_pct)}</b></span><span>H2H <b>${num(hr.h2h_pct)}</b></span>
 <span>Season <b>${num(hr.season_pct)}</b></span><span>Signal <b>${num((sv.signal||{}).composite)}</b></span></div>`;
 const mv=sv.movement;
 if(mv)h+=`<div class="kv"><span>Line ${num(mv.open_line)}→<b>${num(mv.current_line)}</b> (Δ${num(mv.line_delta_from_open)})</span>
 <span>Odds ${odds(mv.open_odds)}→<b>${odds(mv.current_odds)}</b></span><span>moves <b>${num(mv.movement_count)}</b></span></div>`;
 const al=sv.alt_lines||[];
 if(al.length)h+=`<div class="kv"><span>alt lines: ${al.slice(0,8).map(a=>num(a.line)+'@'+odds(a.best_odds)).join('  ')}${al.length>8?' …+'+(al.length-8):''}</span></div>`;
 const ev=sv.ev;
 if(ev){h+=`<table><tr><th>Book</th><th>Odds</th><th>EV%</th><th>Kelly%</th><th>Max</th><th>State</th></tr>`;
 (ev.ev_books||[]).forEach(b=>{h+=`<tr><td>${b.book||'-'}</td><td>${odds(b.book_odds)}</td>
 <td class="${b.calculated_ev_pct>=0?'evpos':'evneg'}">${num(b.calculated_ev_pct)}</td>
 <td>${num(b.kelly_pct)}</td><td>${b.max_bet==null?'-':b.max_bet}</td><td>${b.book_state||'-'}</td></tr>`;});
 h+=`</table><div class="kv"><span>devig <b>${odds(ev.devig_odds)}</b></span><span>vig <b>${num(ev.vig_pct)}%</b></span><span>width <b>${num(ev.width_pct)}%</b></span></div>`;}
 const px=sv.proxy_market_edge;
 if(px)h+=`<div class="proxy">proxy_market edge ${num(px.edge_pct)}% @ ${px.book} (${odds(px.odds)}) vs fair ${num(px.fair_prob_pct)}% — market signal, not Outlier EV</div>`;
 (sv.insights||[]).forEach(i=>{h+=`<div class="ins">${i.text||''} <span class="meta">[rel ${num(i.relevancy)}, ${i.last_n_record||''}]</span></div>`;});
 return h+'</div>';
}
function cardEl(c){
 const wrap=el('div','card');
 const pos=(c.rank_metric==='calculated_ev_pct'&&c.rank_value!=null&&c.rank_value>=0);
 const neg=(c.rank_metric==='calculated_ev_pct'&&c.rank_value!=null&&c.rank_value<0);
 const unit=c.rank_metric==='calculated_ev_pct'?'% EV':'sig';
 const head=el('div','head');
 head.innerHTML=`<div class="rank">${RNK}</div>
 <div><div class="who">${c.player||'—'} · ${c.market||c.market_raw||''}
 <span class="badge bk-${(c.bucket||'').replace(/ /g,'-')}">${c.bucket||''}</span></div>
 <div class="meta">${c.matchup||''} · ${c.headline_side||''} ${headLine(c)} · ${c.scope||''}</div>
 <div class="flags">${(c.flags||[]).map(flagChip).join('')}</div></div>
 <div class="metric"><b class="${pos?'evpos':(neg?'evneg':'')}">${num(c.rank_value)}</b><div class="meta">${unit}</div></div>`;
 const body=el('div','body');
 const hs=c.headline_side;
 body.innerHTML=sideBlock(hs,(c.sides||{})[hs],true)+
   Object.keys(c.sides||{}).filter(s=>s!==hs).map(s=>sideBlock(s,c.sides[s],false)).join('')+
   `<div class="note">card_id ${c.card_id} · group ${c.group_key}</div>`;
 head.onclick=()=>wrap.classList.toggle('open');
 wrap.appendChild(head);wrap.appendChild(body);return wrap;
}
function headLine(c){const sv=(c.sides||{})[c.headline_side];return sv?num(sv.line):'';}
let RNK=0;
function render(){
 const list=BOARD==='A'?D.board_a:D.board_b;
 const q=Q.toLowerCase();
 const rows=list.filter(c=>{
   if(BUCKET&&c.bucket!==BUCKET)return false;
   if(q){const s=`${c.player} ${c.market} ${c.matchup}`.toLowerCase();if(!s.includes(q))return false;}
   return true;});
 const host=document.getElementById('list');host.innerHTML='';
 document.getElementById('cnt').textContent=
  `${rows.length} cards · Board ${BOARD} (${BOARD==='A'?'verified Outlier EV':'signal candidates — heuristic, not EV'})`;
 RNK=0;rows.forEach(c=>{RNK++;host.appendChild(cardEl(c));});
}
function setBoard(b){BOARD=b;document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.b===b));
 const buckets=BOARD==='A'?['','Strong','Lean','Monitor','Pass']:['','Strong Signal','Lean','Monitor','Pass'];
 const sel=document.getElementById('bucket');sel.innerHTML=buckets.map(x=>`<option value="${x}">${x||'All buckets'}</option>`).join('');
 BUCKET='';render();}
window.addEventListener('DOMContentLoaded',()=>{
 document.getElementById('q').addEventListener('input',e=>{Q=e.target.value;render();});
 document.getElementById('bucket').addEventListener('change',e=>{BUCKET=e.target.value;render();});
 setBoard('A');});
"""


def render_html(payload: dict[str, Any]) -> str:
    cov = payload.get("coverage", {})
    skew = payload.get("snapshot_skew", {})
    league = payload.get("league", "")
    gen = payload.get("generated_at", "")
    skew_html = ""
    if skew.get("is_skewed"):
        skew_html = (
            f'<div class="skew">⚠ snapshot skew {skew.get("skew_hours")}h — '
            f"{skew.get('reason')}. Re-run the stale feed before trusting joins.</div>"
        )
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    sub = (
        f"{cov.get('cards_total', 0)} cards · "
        f"Board A {cov.get('board_a_cards', 0)} (EV {cov.get('ev_markets', 0)} markets) · "
        f"Board B {cov.get('board_b_cards', 0)} · "
        f"props {cov.get('props_records', 0)} · insights {cov.get('insights_records', 0)}"
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{league} Triage Cards</title><style>{_CSS}</style></head>
<body>
<header>
  <h1>{league} Triage Cards</h1>
  <div class="sub">Generated {gen} · {sub}</div>
  {skew_html}
</header>
<div class="controls">
  <span class="tab on" data-b="A" onclick="setBoard('A')">Board A · Verified EV</span>
  <span class="tab" data-b="B" onclick="setBoard('B')">Board B · Signals</span>
  <input id="q" placeholder="filter player / market / matchup">
  <select id="bucket"></select>
</div>
<div class="wrap"><p class="cnt" id="cnt"></p><div id="list"></div></div>
<script>window.__CARDS__={data_json};</script>
<script>{_JS}</script>
</body></html>"""
