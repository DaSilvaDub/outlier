# Pipeline Upgrade — Counter-Proposal (v2)

**Re:** codex "Trustworthy Daily Pipeline Upgrade" plan (worktree `a318709`)
**Author:** independent review (Claude)
**Date:** 2026-06-29 · **Rev:** v3 (build environment corrected)
**Verdict:** Adopt the three-tier sequencing and clean-HEAD approach **after** the corrections in §2. Build baseline and worktree location are fixed in §0.

> **v3 changelog:** added §0 (verified build baseline + worktree topology) after the failed OneDrive-worktree attempt; corrected the clean-HEAD target from `a318709` to `master @ ad30323` (which already contains a318709); listed cleanup of the orphan `ai-runners` repo and phantom worktree registration.
> **v2 changelog:** corrected the retry claim (§2.2), the selection-dedup root cause (§2.3), the scope of the Claude-model fix (§2.4), reclassified model retirement as a separate defect not the June-28 cause (§2.1), recorded the verified test baseline (§5), fixed the quota algorithm to be implementation-complete (§3.1), and added the ruff dependency caveat (§6).

---

## 0. Build environment & worktree topology (verified 2026-06-29)

**Build baseline = `master @ ad30323`**, not `a318709`. `git merge-base --is-ancestor` confirms `master` already contains `a318709` (chain: `a318709` → `da23703` Add multi-agent sync system → `ad30323`). All Tier work targets `master`.

**Worktrees must live OUTSIDE OneDrive.** The healthy worktrees already exist under `C:\Users\dasil\.codex\worktrees\…` (local, non-synced):
- Plan target `a318709`: e.g. `…\.codex\worktrees\3316\outlier` (and 17bc/33b2/dad6).
- **`5716` read-only reference** = `C:\Users\dasil\.codex\worktrees\5716\outlier` @ `2aff065`.

**Do not create worktrees inside `C:\…\OneDrive\…`.** The attempt to add `OneDrive\Documents\outlier-worktrees\ai-runners` failed repeatedly because that path is a OneDrive reparse point (`dar--l`) and sync corrupts the worktree's `.git` gitfile reference. This is the worktree-specific face of the known OneDrive quirk.

**Cleanup left by the failed attempt (gated — needs GO):**
1. Orphan repo `OneDrive\Documents\outlier-worktrees\ai-runners` — a standalone `git init` with `origin` set but **zero commits**. Nothing to save; delete the folder.
2. Locked **phantom worktree** registration `…\outlier-worktrees\ai-runners-dummy` → branch `ai-runners-wt` (folder absent). Unlock + remove, then prune.
3. Stray branches `ai-runners-wt{,2,3}` if present.

```powershell
cd "C:\Users\dasil\OneDrive\Documents\outlier"
git worktree unlock "C:/Users/dasil/OneDrive/Documents/outlier-worktrees/ai-runners-dummy"
git worktree remove --force "C:/Users/dasil/OneDrive/Documents/outlier-worktrees/ai-runners-dummy"
git worktree prune -v
Remove-Item -Recurse -Force "C:\Users\dasil\OneDrive\Documents\outlier-worktrees\ai-runners"
git branch -D ai-runners-wt ai-runners-wt2 ai-runners-wt3   # ignore "not found"
# New worktree, OUTSIDE OneDrive:
git worktree add -b ai-runners "C:\Users\dasil\outlier-wt\ai-runners" master
```

---

## 1. Verified diagnosis (unchanged from v1, with two corrections marked)

Checked against the live tree (source, `packs/2026-06-28/`, logs, `models.py`).

| Finding | Status | Evidence |
|---|---|---|
| Status conflation: `daily_job` exits 0 while reasoning is `PARTIAL` | ✅ | `daily_job.main` returns 0; `reasoning_status.json`=`PARTIAL`, A/B/D failed. Reasoning ran as a **separate skill invocation after** `daily_job` exited — nothing reconciles them. |
| Blanket caveat poisons all MLB signals | ✅ | `briefing.md` L5 flags the **whole** props-LM stream `CAVEAT — partial; 10 fetch errors`; L8 → "UNRELIABLE." 10/5,573 failures, none overlapping candidates. |
| WNBA crowd-out (23 MLB / 2 WNBA) | ✅ | `pack.rank_rows` sorts **globally** (`board_a[:15] + board_b[:10]`); MLB volume wins. Run had 172 WNBA game cards + 370 player cards available. |
| Missing identity / push fields | ✅ | All 25 rows: `player_id` empty, `push_prob` empty. **Root cause: `cards._identity()`** (not pack). |
| Doubled `selection` strings | ✅ | **Root cause: `pack.build_selection()`** joins `name + label + side + line`; the label already contains the player → `"Steven Kwan Steven Kwan - Bases OVER 1.5"`. |
| Silent date substitution | ✅ | `pack.select_date` falls back to `max(dated)` and never drops undated rows. |
| No manifest / run_status / run_id / writer-lock | ✅ | `grep` → none. |
| ~~Retry path is 403-only~~ | ❌ **corrected** | See §2.2 — the base client already retries 403/429/5xx ×3. |
| ~~Retired Claude model caused June-28 failure~~ | ❌ **corrected** | See §2.1 — June-28 failure was insufficient credits. |

---

## 2. Corrections required before adoption

### 2.1 Model retirement is a separate defect, not the June-28 cause
The recorded June-28 failure was **insufficient Anthropic credits** (`reasoning_status.json` notes). The retired model (`claude-3-5-sonnet-20241022`) is a real correctness defect but must be tracked and fixed **independently** — do not attribute the outage to it.

### 2.2 The retry claim was wrong — do not add another retry layer
`OutlierApiClient` **already** retries `{403, 429, 500, 502, 503, 504}` three times with backoff (`api.py`, `RETRYABLE_STATUS_CODES`, `max_retries=3`). Only the later **batch mop-up** in `line_movement` is 403-specific.

- **Implication:** codex's "add bounded adaptive retries" risks a *second* retry layer on top of the client's — the same multiplication defect already visible on the LLM side (`reasoning.py` OpenAI SDK `max_retries=10` **and** `gemini_research.py` its own ×10 loop).
- **Corrected action:** **consolidate to a single retry owner.** Make the batch mop-up reuse the client's existing policy (extend it to 429/transient if needed) rather than introducing a new layer. One owner, bounded attempts + wall-clock budget.

### 2.3 Selection dedup belongs in `build_selection()`, display-only
Fix the duplication in `pack.build_selection()` by de-duplicating **only the display `selection`** string. **Leave raw labels / `_raw` fields unchanged** (per `AGENTS.md`: preserve upstream values). Missing `player_id` is a **separate** fix in `cards._identity()`. Two distinct changes, two distinct owners.

### 2.4 "Fix the Claude model" is not one line
The runners hash and front-matter `thinking="adaptive"` in the request provenance (`claude_reasoning.py` L86–93) but **never send it** in the actual `messages.stream(...)` call (L44–48). So model id, request configuration, provenance hash/front-matter, **and** tests are entangled and must change **together**:
1. Model id in `models.py`.
2. Remove the phantom `thinking="adaptive"` from the request hash + front-matter (or actually send a supported config).
3. Re-key provenance so old outputs invalidate correctly.
4. Update the model/config tests in the same change.

---

## 3. Sequencing (accepted) and the parts that must be implementation-complete

Three tiers, by impact-per-risk:

- **Tier 1 — Contract & correctness** (`pack.py` / `cards.py` / `models.py`): candidate-scoped quality gates (kills the blanket caveat), full quota algorithm (§3.1), `player_id` via `cards._identity()`, display-only selection dedup, strict slate date, model/config/provenance/test fix (§2.4). No new infra.
- **Tier 2 — Authoritative orchestration:** `--analysis-profile {local,openai,full}` with deterministic-report-first and a single owned exit code; then writer-lock + atomic `manifest.json`.
- **Tier 3 — Resilience & calibration scaffold:** consolidate the **single** retry owner (§2.2); seed `decisions.csv` (inert until a grading source exists).

### 3.1 Quota algorithm — full spec (not just "round-robin")
Retain the original deterministic algorithm verbatim:
1. **Round-robin across non-empty `(league, stream)` buckets** within each EV/signal quota, then
2. **fill remaining EV/signal capacity globally**,
3. keeping the 15 EV / 10 signal cap.

"Round-robin allocation" on its own is not implementation-complete — the global fill-up step is what prevents under-filling when a bucket runs dry.

---

## 4. Adopt as-written (no change)
Candidate-scoped gates with stream coverage as context-only; deterministic local report first (provider failure → `degraded`, never erases it); PLAY/LEAN/WATCH/PASS/STAND_DOWN + post-lock rule; preserve sizing (2% min edge, 3-unit cap); never retry 404; exit-code contract (0 valid/degraded, 1 fatal, 2 lock conflict); MLB+WNBA scope only.

---

## 5. Verified test baseline
Full `pytest` (run on the canonical Windows env): **193 passed, 2 failed.**
1. **Claude test** expects `claude-opus-4-8`; runtime uses Claude 3.5 → fix under §2.4.
2. **OpenAI test** expects `max_retries=5`; runtime uses 10 → fix as part of the single-retry-owner consolidation (§2.2).

Both failing tests already encode the intended target — runtime config lags the tests, which is exactly why §2.4's "change together" rule applies. (Note: my mounted/OneDrive copy was sync-stale and still showed the old assertions; this baseline is the authoritative local run.)

---

## 6. Clean-HEAD + tooling caveats
- Reimplement on **`master @ ad30323`** in a **non-OneDrive worktree** (§0); use `…\.codex\worktrees\5716\outlier` (@ `2aff065`) as **read-only reference only** (dirty + incompatible provider config). This removes the largest execution risk.
- Keep the **Claude preflight optional** so it can never block the local-default path.
- **ruff is configured (`[tool.ruff]`) but not a declared dependency** (absent from `pyproject` `dependencies` and `requirements.txt`). Either add it as a dev dependency or keep the `ruff` gate **conditional** (skip cleanly when not installed). Do not make a green-suite acceptance criterion depend on an undeclared tool.

---

## 7. Order of operations
1. **Tier 1** diffs + the §2.4 model/config/provenance/test change. Add regressions for identity fields, semantic market types, display-only selection dedup, strict date, and the **full** quota algorithm (round-robin → global fill).
2. **Tier 2** — `--analysis-profile` + single exit code, then writer-lock + atomic manifest (clean HEAD).
3. **Tier 3** — single retry owner (§2.2) + `decisions.csv` scaffold.
4. Gate per tier: full `pytest` (target: 195 passed, 0 failed) + `ruff` **if installed**. Acceptance = one authoritative status matching exit code, no external calls in default profile, green suite.
