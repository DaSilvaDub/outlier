"""Multi-Agent Documentation & Invariants Synchronizer

Keeps all agent documentation files (docs/ENT-SYNC-GLOBAL-PROMPT.md, AGENTS.md,
CLAUDE.md, GROK.md, GEMINI.md, and local user home configs) synchronized across
all AI harnesses (Codex, Claude, Grok, Gemini/Antigravity).
"""

from pathlib import Path
import re
import sys

REPO_ROOT = Path(r"C:\Users\dasil\Dev\GitHub\outlier")
USER_HOME = Path(r"C:\Users\dasil")

INVARIANT_HEADER = "HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:"

COMMON_INVARIANTS_BLOCK = """HOUSE RULE — MLB PROP EXTRACTION & DRIVE SYNC INVARIANTS:
1. MLB Player Prop Allowed Markets: Home Runs (Over-only), Strikeouts, Hits, Total Bases, HRR, Hits Allowed, Earned Runs, Outs, RBIs, Singles/Doubles/Triples, Batting Walks, Batters Faced, Pitches Thrown.
2. MLB Team Props: All 18 team props allowed.
3. Side Restrictions: Home Runs and "To Record X..." milestone lines are Over-only. Drop Home Runs Under and milestone Under lines.
5. Candidate Actionable & Identity Invariants: actionable is "true" ONLY if board == "A", units > 0, edge_pct > 0, and no suspect/disqualifying flags exist. Rows with suspect flags or blank units are actionable = "false" and board = "A_FLAGGED". build_row() must auto-infer missing team/opponent from matchup.
6. Drive/OneDrive File Copy Resiliency: organize_today_run2.py must use safe_copy() retry loops to handle transient cloud sync file locks ([WinError 32]).
7. Totals & Alternate Team Totals Invariants: is_team_total_record() matches sport-specific scoring tokens ("RUNS", "GOALS", "POINTS", "TOTAL"). build_alt_team_totals() exports alternate lines to alt_team_totals.csv, and generate_prompts.py builds 5 Desk 3 Totals prompt files using prompts/Totals_Analysis.md.
8. Daily Structural Integrity Audit: (a) Prohibited markets (Walks Allowed, Total Bases, Hits Allowed) strictly excluded at generation; (b) Scheduled slate games in context.events never receive UNINDEXED_SLATE_GAME; (c) Non-actionable rows (actionable=false) clear recommended_units_pre_news to ""; (d) Briefing totals deduplicated against candidate market IDs; (e) Integer totals derived via +0.5/-0.5 ladder lines or flagged push_capable_no_prob."""

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
