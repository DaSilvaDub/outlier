---
name: outlier-master-cards-pack-notes
description: Nuances found when analyzing Outlier Desk1_Automated Master Cards betting-pack prompts (e.g. 1_Master_Cards_pack_*.txt) for the outlier repo
metadata:
  type: project
---

When analyzing a `Desk1_Automated/1_Master_Cards_pack_*.txt` file for the outlier repo (per the `analyze-outlier-generic-prompts` skill), two things are easy to miss:

1. **(Fixed 2026-08-05, resolved — historical only) Total Bases (TB) props used to be banned by the prompt's own house rules (`prompts/A.md` §5.2 "Never recommend: ... total bases") even though the global CLAUDE.md MLB whitelist (`ALLOWED_MLB_PLAYER_PROPS`) lists Total Bases as an allowed market.** That §5.2 bullet has since been removed, because Master Card generation now splits into three data-prefiltered variants — `1_Master_Cards_MLB_pack_*.txt` (Moneyline, Spread, Strikeouts, Total Bases), `1_Master_Cards_WNBA_pack_*.txt` (Moneyline, Spread, Points, Assists, Rebounds, and the Pts/Ast/Reb combo props), and `1_Master_Cards_Both_pack_*.txt` (both leagues' whitelists combined) — via `filter_master_card_candidates()` in `.agents/skills/export-manual-outlier-packs/scripts/generate_prompts.py`, restricted to American odds -250 through +150. TB is an intentional, explicitly-supported MLB Master Card market now; do not flag it as banned. If a future Master Cards prompt file re-adds a "never recommend total bases" line, that is a regression worth flagging, not the expected state.

2. **The file is huge (~800 lines) with individual CSV rows that can each be 20k+ tokens** (each row carries a long embedded injury-report text blob per team). The Read tool's `limit` parameter must be dropped to ~1 line at a time once inside the `2+ Unit Candidates Data` CSV block, or it will hit the 25k-token cap and error out. Budget for many small Read calls in that section.

Also worth noting: in this pack format, `model_prob` was identical to `market_consensus_prob` on every observed row, meaning the pack's `local_devig` source is a devigged single-book price relative to market consensus (price-shopping edge), not an independent statistical projection — worth flagging explicitly in the report's rationale/caveats rather than treating the EV% as strong independent alpha.

See also [[analyze-outlier-generic-prompts-skill]] for the routing/report-contract rules that govern these reports.
