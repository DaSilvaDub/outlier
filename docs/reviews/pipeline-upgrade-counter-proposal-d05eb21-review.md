# Code Review: Commit d05eb21 "feat: add automated Prompt C style research pass"

**Reviewer**: code-reviewer subagent (ID 019f1b86-d36b-76c1-9cbe-1e0170fce971)
**Date reviewed**: 2026-07-01
**Duration**: ~72 minutes, 76 tool calls
**Commit reviewed**: d05eb21 (the isolated tree state implementing pipeline upgrade counter-proposal)
**Counter-proposal doc**: pipeline-upgrade-counter-proposal.md (v2, 2026-06-29)

**Note on synchronization**: This review was performed against d05eb21, which at the time of review existed only in one stray worktree/clone (ai-runners). The implementation has since been materialized into the central shared repo at commit 88083ff ("feat: sync pipeline upgrade...") + follow-ups so that Codex, Gemini, and other agents see the same sources after `git fetch`.

---

## Verdict

**WARNING** — 0 CRITICAL, 6 HIGH issues. Resolve HIGHs before considering the upgrade complete / merged to production baselines.

The C research pass and pack scaffolding work in isolation for the targeted use case, but Tier 2 daily orchestration is not fully runnable, several AGENTS.md principles are violated, and key counter-proposal requirements (verbatim selections, "C" in pipeline, sizing, _raw) are not met.

---

## HIGH Severity Issues

### 1. build_selection() appends side even when label already contains it (verbatim violation)

**Location**: `outlier_scrapers/pack.py:197-211` (build_selection)

**Example**:
- Input: name="Steven Kwan", label="Steven Kwan - Bases OVER", side="OVER", line=1.5
- Output: "Steven Kwan - Bases OVER OVER 1.5"

**Why bad**:
- ROLE_BLOCK / prompts/C.md and counter-proposal require quoting pack lines **verbatim**.
- Mangled `selection` in candidates.csv / briefing.md poisons all downstream B/C research.

**Fix recommendation**: Do not append `side` if it is already present (case-insensitive, suffix or word match) in the chosen `core`. Prefer upstream verbatim selection column when available.

### 2. "C" never wired into daily_job --analysis-profile

**Location**: `outlier_scrapers/daily_job.py:176-182`

```python
if profile == "full":
    run_steps = ["A", "B", "D", "E"]   # C missing
...
if profile == "local":
    ... steps=["E"]
```

**Impact**: The new automated Prompt C pass is implemented (`c_research.py`, run_desk support) but never executed by the "authoritative" daily orchestrator. "full" profile does not include it.

**Fix**: Add "C" to the appropriate profiles (e.g. full: ["A","B","C","D","E"] or per counter-proposal sequencing).

### 3. daily_job.py cannot be imported — missing sibling modules

**Location**: imports at top of `daily_job.py`

```python
from .api import ...
from .otp_fetcher import ...
from . import refresh
```

**Reality**: These files do not exist in the tree. `test_daily_job.py` uses guards and skips most tests.

**Impact**: The Tier 2 "single exit, lock, manifest, profile-driven orchestration" claimed by the upgrade is not deliverable in this commit.

**Status note**: May be intentional partial delivery or dependency on files present in other worktrees. Needs resolution or explicit guards + documentation.

### 4. sizing.py stub (or incomplete) zeros edge/units — violates "preserve sizing"

**Location**: `outlier_scrapers/sizing.py`

(Reviewer saw all-zero returns. Later snapshots in central showed more complete `compute_sizing` + kelly logic; verify against counter-proposal §4 requirements: 2% min edge, 3-unit cap, etc.)

**Fix**: Ensure `build_row` receives and uses real sizing values that respect the policy. Do not silently zero.

### 5. rank_rows() mutates caller-provided rows (immutability violation)

**Location**: `outlier_scrapers/pack.py ~372`

```python
for r in rows:
    if "_stream" not in r:
        r["_stream"] = "props"   # mutation
```

**AGENTS.md rule**: "Always create new objects, never mutate existing ones."

**Fix**: Return new dicts (e.g. `{**r, "_stream": ...}`).

### 6. No preservation of _raw / passthrough upstream fields

**AGENTS.md**: preserve _raw.

**Evidence**: `build_row` starts with `{k:"" for k in CANDIDATES_HEADER}` then whitelist update. Any `_raw_*`, original card data, etc. are dropped.

**Fix**: Explicitly copy through keys starting with `_` (or a documented set) from card/ref/inputs into the output row.

---

## Other Issues (MEDIUM / LOW)

- Selection dedup logic is only a prefix check and does not catch the side-duplication case above.
- Docstring in pack.py overclaims ownership of `player_id`/`push_prob` (those come from cards._identity per proposal diagnosis).
- Test surface is narrow (only 3 modules visible in the tree; 8 pass / 5 skipped). Claim of "full relevant suite" was local to C + run_desk. Counter-proposal baseline (195 passed) not reproducible here due to visibility + missing modules.
- Some functions long; `prompts/C.md` minor formatting.
- Several research runners are short stubs (acceptable for staged rollout if labeled).

---

## Strengths (what works well)

- Hash / idempotency / stale refresh logic sound and tested (`runner_common`).
- `ROLE_BLOCK` correctly passed as system instruction to the Gemini C runner.
- Freshness reporting is per-stream + candidate-scoped with CAVEAT (not blanket "UNRELIABLE").
- `decisions.csv` is inert header only (matches §3).
- No secrets in logs.
- Atomic tmp+replace writes.
- `select_date` strict (requested or max, warning on miss, no silent fallback).
- Round-robin + global fill quota implemented in `rank_rows`.

---

## Counter-Proposal Compliance (summary)

| Area                          | Status     | Notes |
|-------------------------------|------------|-------|
| Tier 1 quota (rr + fill)      | ✅        | Implemented |
| Strict slate date             | ✅        | Good |
| Candidate-scoped freshness    | ✅        | Good |
| decisions.csv scaffold        | ✅        | Inert |
| Display dedup                 | ⚠️ Partial | Buggy duplication |
| player_id / push_prob         | ⚠️        | Pack reads correctly; ownership is upstream |
| "C" research pass             | ⚠️        | Coded but not in daily_job profiles |
| Tier 2: profile + lock + manifest | ⚠️     | Code present, but un-runnable (imports) |
| Preserve _raw (AGENTS)        | ❌        | Dropped |
| Immutability (AGENTS)         | ❌        | Mutation in rank_rows |
| Sizing preservation           | ⚠️        | Was stub at review time |
| Tests / coverage              | ⚠️        | Narrow only |

---

## Recommended Next Steps

1. **Sync & visibility**: Ensure all agents start from the canonical central tree (or a registered worktree) and `git fetch` before reviewing/running. See `SYNC.md`.
2. **Fix HIGHs in priority order** (use central so all envs benefit):
   - Fix `build_selection` duplication + add regression test.
   - Add "C" to daily_job profiles.
   - Resolve or document the missing daily_job dependencies.
   - Ensure real sizing flows through (no zeros).
   - Make rank_rows and build_row immutable / _raw-preserving.
3. Re-run the reviewer (or targeted checks) against the fixed central HEAD.
4. Expand test surface (at minimum unit tests for the fixed pack functions).
5. When clean: commit on central + instruct all agents to refresh their worktrees.

---

<subagent_meta>
id=019f1b86-d36b-76c1-9cbe-1e0170fce971
type=code-reviewer
tool_calls=76
turns=1
duration_ms=4332608
</subagent_meta>

**Full raw subagent transcript** available via the harness session (get_command_or_subagent_output with the ID). This document captures the findings for persistence across worktrees/agents.

Last synced: 2026-07-01 (central master now carries the implementation state + this review).
