# Handoff — AI Research Desk: C automation + orchestrator + status

> Canonical worktree where the code lives: `C:\Users\dasil\.codex\worktrees\5716\outlier`
> This OneDrive copy is for viewing only. gronk executes in the .codex worktree.

**Run tests via PowerShell** (`;` not `&&`):
```powershell
cd "C:\Users\dasil\.codex\worktrees\5716\outlier"; python -m pytest -q
```
**Baseline:** `198 passed`. The trailing `PermissionError ... pytest-current` is a Windows temp-cleanup quirk, not a failure.

## DONE (code written, suite green at 198)
1. **P1-a** raising cores + `classify_exception` + `RunnerError.category`; `ReasoningError` now subclasses `RunnerError`. Each runner = `execute_*` (raises) + `run_*` (int wrapper).
2. **P1-b** `pack.build_dossier` header (matchup/event_id/sport/first-lock) + per-row `market_id`; `build_row` sets `_matchup`.
3. **P1-c** `pack.write_pack` prunes `dossiers/`, prunes orphaned `chatgpt_c/*.md`, writes versioned `manifest.json` (pack-relative paths validated).
4. **C runner** `chatgpt_research.py` + `prompts/C.md`. Locked: gpt-5.5 / effort=high / `web_search` / max_retries=5. Per-game → `chatgpt_c/<sport>_<eid>.md` with `dossier_sha256`+`request_sha256`. Shared `compute_c_request_hash`. `LegResult`; best-effort per leg.
5. **E** `claude_synthesis.py` reads `chatgpt_c/` via manifest; keeps a leg only if BOTH `dossier_sha256` AND `request_sha256` match; folds kept legs into E's hash. Tests rewritten + green.
6. **run_desk.py** orchestrator A·B·D·C·E best-effort; `reasoning_status.json` (atomic, same-volume); FULL/PARTIAL→exit0, DATA_ONLY→nonzero; cached=success; allow-listed logging.

## REMAINING — execute step-by-step, verify + commit each
- **Step 1** `tests/test_runner_common.py` — classify_exception per category, extract_yaml_value, atomic_write_text.
- **Step 2** extend `tests/test_pack.py` — dossier has market_id/event_id; manifest written; shrunk-slate rebuild prunes stale dossier + orphan C; write_manifest rejects absolute/`..`.
- **Step 3** `tests/test_chatgpt_research.py` (mock openai) — web_search present, front-matter hashes, idempotent, --force, one-leg-fails-others-survive, missing-manifest→run returns 1, empty rejected.
- **Step 4** `tests/test_run_desk.py` (monkeypatch execute_* cores) — FULL/exit0, DATA_ONLY/nonzero, PARTIAL/exit0, cached=success, status allow-listed keys.
- **Step 5** `--preflight` using non-generating `models.list` per provider; mock in tests; then RUN LIVE to confirm GEMINI_MODEL/CLAUDE_MODEL resolve.
- **Step 6** docs: README + runbook §7 (new commands, dependency order, status meaning, B/C = grounded single-pass not Deep Research UI).

## Invariants gronk MUST preserve
- C and E both use `chatgpt_research.compute_c_request_hash` (never duplicate).
- E excludes a leg unless BOTH hashes match.
- Exit: cached=success; missing C legs=PARTIAL/0; required pass incomplete=DATA_ONLY/nonzero.
- Atomic writes stay same-volume (temp inside pack_dir). C effort locked `high`.
- Status/logs allow-list only (provider, model, category, elapsed, completeness, output_file).
- Keep all tests green; `reasoning.py` public contract unchanged. Nothing committed yet — commit per step.
