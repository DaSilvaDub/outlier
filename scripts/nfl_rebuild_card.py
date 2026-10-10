#!/usr/bin/env python
"""Rebuild a published NFL best-bets card's verdicts under the phase 3a price rules (#225).

Writes a NEW versioned card next to the original (``<stem>.v-<version>.json``
and ``.md``) and never modifies the original. It refuses to run if the output
already exists or would be the original.

Only the price pillar is re-evaluated, from the numbers already on each pick
(``final_p``, ``best_odds`` -> decimal, ``edge``, ``line``):
  * VALIDATED needs a positive expected return at the quoted price (F05),
  * an integer line can push, so its price pillar is at most ESTIMATED (F07).
The other five pillars keep their recorded status. The rules only tighten, so
a pick can lose VALIDATED but never gain it. REJECTED stays REJECTED.

Windows (PowerShell, from C:/Users/dasil/Dev/GitHub/outlier):
    python -m scripts.nfl_rebuild_card --card data/NFL/normalized/nfl_best_bets_2026-10-04.json --version phase3a
    python -m scripts.nfl_rebuild_card --card data/NFL/normalized/nfl_best_bets_2026-10-05.json --version phase3a
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from outlier_nfl.best_bets import (
    CONTRADICTS,
    ESTIMATED,
    PROVISIONAL,
    REJECTED,
    VALIDATED,
    VERDICT_ORDER,
    VERIFIED,
    american_to_decimal,
    price_gate,
    render_best_bets_markdown,
)

RULES = "phase3a: EV>0 at the quote required for VALIDATED (F05); integer lines not validated until pushes are priced (F07)"


def versioned_path(card: Path, version: str) -> Path:
    return card.with_name(f"{card.stem}.v-{version}{card.suffix}")


def rebuild_pick(pick: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(pick)
    notes: list[str] = []
    pillars = out.get("pillars") or {}
    price = pillars.get("price") or {}
    ev = None
    if pick.get("best_odds") is not None and pick.get("final_p") is not None and pick.get("edge") is not None:
        dec = american_to_decimal(int(pick["best_odds"]))
        ev, cap, gate_notes = price_gate(float(pick["final_p"]), dec, pick.get("line"), float(pick["edge"]))
        status = price.get("status")
        if cap == CONTRADICTS:
            status = CONTRADICTS
        elif cap == ESTIMATED and status == VERIFIED:
            status = ESTIMATED
        if status != price.get("status"):
            notes.append(f"price {price.get('status')} -> {status}: {'; '.join(gate_notes)}")
        if isinstance(price, dict):
            price["status"] = status
    statuses = {name: (p or {}).get("status") for name, p in pillars.items()}
    verdict = pick.get("verdict")
    if verdict != REJECTED:
        if CONTRADICTS in statuses.values():
            verdict = REJECTED
        elif statuses and all(s == VERIFIED for s in statuses.values()):
            verdict = VALIDATED
        else:
            verdict = PROVISIONAL
        # Rules only tighten: never promote above the original verdict.
        if VERDICT_ORDER[verdict] < VERDICT_ORDER[pick["verdict"]]:
            verdict = pick["verdict"]
    if verdict != pick.get("verdict"):
        notes.append(f"verdict {pick.get('verdict')} -> {verdict}")
    out["verdict"] = verdict
    out["original_verdict"] = pick.get("verdict")
    out["ev_at_quote"] = None if ev is None else round(ev, 4)
    out["gaps"] = [f"{n}:{s}" for n, s in statuses.items() if s != VERIFIED]
    if verdict != VALIDATED:
        out["stake_fraction"] = 0.0
    out["rebuild_notes"] = notes
    return out


def rebuild_card(card_path: Path, version: str) -> Path:
    card_path = Path(card_path)
    out = versioned_path(card_path, version)
    if out.resolve() == card_path.resolve():
        raise SystemExit("refusing to overwrite the original card")
    if out.exists():
        raise SystemExit(f"{out} already exists; pick a new --version")
    raw = card_path.read_bytes()
    card = json.loads(raw)
    picks = [rebuild_pick(p) for p in card.get("picks") or []]
    picks.sort(key=lambda p: (VERDICT_ORDER[p["verdict"]],
                              -(p["ev_at_quote"] if p.get("ev_at_quote") is not None else -9.0)))
    for rank, pick in enumerate(picks, start=1):
        pick["original_rank"] = pick.get("rank")
        pick["rank"] = rank
    counts = {v: sum(1 for p in picks if p["verdict"] == v) for v in (VALIDATED, PROVISIONAL, REJECTED)}
    rebuilt = {
        **{k: v for k, v in card.items() if k != "picks"},
        "picks": picks,
        "counts": counts,
        "original_counts": card.get("counts"),
        "rebuild": {"version": version, "rules": RULES, "source_card": card_path.name,
                    "source_sha256": hashlib.sha256(raw).hexdigest()},
    }
    out.write_text(json.dumps(rebuilt, indent=2, ensure_ascii=False), encoding="utf-8")
    title = f"NFL Traced Best Bets - {card.get('date') or card_path.stem} (rebuilt {version})"
    out.with_suffix(".md").write_text(render_best_bets_markdown(rebuilt, title=title), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--card", type=Path, required=True, help="Published nfl_best_bets_<date>.json")
    parser.add_argument("--version", required=True, help="Label for the new card, e.g. phase3a")
    args = parser.parse_args(argv)
    out = rebuild_card(args.card, args.version)
    data = json.loads(out.read_text(encoding="utf-8"))
    print(f"Wrote {out} (original untouched): {data['original_counts']} -> {data['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
