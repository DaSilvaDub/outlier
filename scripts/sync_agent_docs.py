"""Multi-Agent Documentation & Invariants Synchronizer

Keeps all agent documentation files (docs/ENT-SYNC-GLOBAL-PROMPT.md, AGENTS.md,
CLAUDE.md, GROK.md, GEMINI.md, and local user home configs) synchronized across
all AI harnesses (Codex, Claude, Grok, Gemini/Antigravity).
"""

from pathlib import Path
import re

REPO_ROOT = Path(r"C:\Users\dasil\Dev\GitHub\outlier")
USER_HOME = Path(r"C:\Users\dasil")

INVARIANT_HEADER = "HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:"

COMMON_INVARIANTS_BLOCK = """HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:
1. MLB Player Prop Allowed Markets (strict whitelist, full-game only; canonical codes in normalizer ALLOWED_MLB_PLAYER_PROPS): Strikeouts (SO), Hits (H), Total Bases (TB), Outs (OUTS), Doubles (2B), HRR (Hits+Runs+RBIs), Earned Runs (ER), Batting Walks (BB). Any other player prop (including HR, HA/Hits Allowed, RBI, 1B/Singles, 3B/Triples, BF/Batters Faced, PT/Pitches Thrown, BBA/Walks Allowed) is dropped at generation.
2. MLB Team Props (strict whitelist, full-game only; ALLOWED_MLB_TEAM_PROPS): Hits (H), Strikeouts (SO), Walks (BB), Runs (R), Total (TOTAL). All other TEAM_PROP markets are dropped. Game lines (GAMELINE: Moneyline, Spread, Game Total) are always preserved — not subject to prop whitelists.
3. Side Restrictions: Doubles (2B) are UNDER-only — drop 2B OVER at generation. Home Runs (HR) are pack-excluded entirely (desk hard ban via EXCLUDED_MARKETS).
5. Candidate Actionable & Identity Invariants: actionable is "true" ONLY if board == "A", units > 0, edge_pct > 0, and no suspect/disqualifying flags exist. Rows with suspect flags or blank units are actionable = "false" and board = "A_FLAGGED". build_row() must auto-infer missing team/opponent from matchup.
6. Drive/OneDrive File Copy Resiliency: organize_today_run2.py must use safe_copy() retry loops to handle transient cloud sync file locks ([WinError 32]).
7. Totals & Alternate Team Totals Invariants: is_team_total_record() matches sport-specific scoring tokens ("RUNS", "R", "TOTAL_RUNS", "GOALS", "POINTS", "TOTAL", "TEAM_TOTAL"). build_alt_team_totals() exports alternate lines to alt_team_totals.csv, tests/test_game_totals.py verifies multi-sport eligibility, and generate_prompts.py loads prompts/Totals_Analysis.md (falling back to A.md if missing).
8. Daily Structural Integrity Audit: (a) Non-whitelisted MLB player/team props strictly excluded at generation (ALLOWED_MLB_PLAYER_PROPS / ALLOWED_MLB_TEAM_PROPS); pack also hard-excludes HR and Walks Allowed tokens; (b) Scheduled slate games in context.events never receive UNINDEXED_SLATE_GAME; (c) Non-actionable rows (actionable=false) clear recommended_units_pre_news to ""; (d) Briefing totals deduplicated against candidate market IDs; (e) Integer totals derived via +0.5/-0.5 ladder lines or flagged push_capable_no_prob.
9. Whitelisted ≠ Recommendable (generation scope vs. desk-recommendation scope; ruled 2026-08-12, see docs/plans/2026-08-12-structured-ai-verdicts.md): ALLOWED_MLB_PLAYER_PROPS / ALLOWED_MLB_TEAM_PROPS govern what enters a pack at generation only — they do not by themselves make a market desk-recommendable. HRR and BB (both player and team forms) keep entering packs unchanged. The AI research desk may never recommend (BET) the HRR player prop or the player Batting Walks (BB) prop; the team BB prop remains recommendable — the desk ban is scoped to market_type == PLAYER_PROP for BB, and to any scope for HRR.
10. Structured desk verdicts (see docs/plans/2026-08-12-structured-ai-verdicts.md): PackIndex is the sole pack truth for the gate. Readers outside the publish path resolve `packs/<date>/verdicts/desk_snapshot.json`, never a pass-level `current.json`. `verdicts/` is never named in `DERIVED_PACK_OUTPUTS` and must not be `unlink()`ed on rebuild. No-E fallback requires A+D+B quorum; unanimous BET uses min stake and is labeled `synthesis_source: fallback_no_e`. `verdict_records` is keyed by `(decision_id, pass, publication_id, record_id)`. Legacy `C_verdict` is CONTRADICTS > CONFIRMS > NEUTRAL, else empty. Never invoke paid/live reasoning unless the user explicitly asks this turn."""

IN_REPO_FILES = [
    REPO_ROOT / "docs" / "ENT-SYNC-GLOBAL-PROMPT.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / "GROK.md",
    REPO_ROOT / "GEMINI.md",
]

USER_HOME_FILES = [
    USER_HOME / ".grok" / "AGENTS.md",
    USER_HOME / ".claude" / "CLAUDE.md",
    USER_HOME / ".codex" / "AGENTS.md",
    USER_HOME / ".gemini" / "GEMINI.md",
    USER_HOME / ".gemini" / "AGENTS.md",
]

def update_file_invariants(file_path: Path) -> bool:
    if not file_path.exists():
        return False
    
    content = file_path.read_text(encoding="utf-8")
    
    if INVARIANT_HEADER in content:
        # Replace existing invariants block
        new_content = re.sub(
            r"HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:.*?(?=\Z|\n\n#|\n\n##|\Z)",
            COMMON_INVARIANTS_BLOCK,
            content,
            flags=re.DOTALL
        )
    else:
        # Append invariants block
        new_content = content.rstrip() + "\n\n" + COMMON_INVARIANTS_BLOCK + "\n"
        
    if new_content != content:
        file_path.write_text(new_content, encoding="utf-8")
        print(f"[sync] Updated invariants in: {file_path}")
        return True
    else:
        print(f"[sync] Already in sync: {file_path}")
        return False

def sync_all():
    print("=== Synchronizing Invariants & Global Instructions across ALL Agent Documentation Files ===")
    updated_count = 0
    all_target_files = IN_REPO_FILES + USER_HOME_FILES
    for path in all_target_files:
        if update_file_invariants(path):
            updated_count += 1
    print(f"=== Sync Complete. {updated_count} files updated across repository and user home paths. ===")

if __name__ == "__main__":
    sync_all()
