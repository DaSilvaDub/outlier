# Claude Code hooks

Mechanical enforcement for two rules that were previously prose-only. Wired up in
[`.claude/settings.json`](../settings.json).

## Two install scopes, two paths — read this before editing

The hooks are registered in **two** places, deliberately, and they point at **different copies**:

| Scope | Settings file | Script path | Why |
|---|---|---|---|
| Project (committed) | `.claude/settings.json` | `C:\Users\dasil\Dev\GitHub\outlier\.claude\hooks\` | Shared with every ent via git; reviewable in a PR |
| User (personal) | `~/.claude/settings.json` | `~/.claude/hooks/outlier-*.ps1` | Fires regardless of cwd |

Two reasons the project copy alone is not enough:

1. **Project settings only load when the session's cwd is that project.** Sessions launched
   from the OneDrive mirror (`OneDrive\Documents\outlier`) never load
   `.claude/settings.json` from canonical — and that mirror is exactly where the sync trap
   lives.
2. **The canonical absolute path is branch-dependent.** It resolves through canonical's
   *currently checked-out branch*. Canonical is frequently sitting on a `risk-opt/*` or other
   feature branch; any branch created before these files landed on `master` does not contain
   `.claude/hooks/`, so the script is missing and `pwsh` exits non-zero. A non-2 exit from a
   `PreToolUse` hook is a **non-blocking error — the command is allowed**. In other words the
   spend guard fails *open* in exactly that case. The user-scope copy under `~/.claude/hooks/`
   is branch-independent and is what actually protects you.

**Consequence: after editing either script, re-copy it to `~/.claude/hooks/`** or the two
copies drift. The repo copy is the source of truth for review and for other ents; the
`~/.claude` copy is the runtime that is always present.

```powershell
Copy-Item .claude\hooks\block-reasoning.ps1 "$env:USERPROFILE\.claude\hooks\outlier-block-reasoning.ps1" -Force
Copy-Item .claude\hooks\check-sync.ps1      "$env:USERPROFILE\.claude\hooks\outlier-check-sync.ps1"      -Force
```

| Script | Event | Matcher | Effect |
|---|---|---|---|
| `block-reasoning.ps1` | `PreToolUse` | `Bash\|PowerShell` | Denies paid reasoning/desk commands |
| `check-sync.ps1` | `SessionStart` | — | Read-only sync verification, injects verdict into context |

---

## `block-reasoning.ps1` — reasoning is OFF by default

Enforces the `AGENTS.md` house rule "Never run reasoning models unless explicitly asked."

Replaces `.claude/hookify.no-reasoning-unless-asked.local.md`, whose decisive defect was
`action: warn` — advisory only, so nothing actually stopped a paid command. This version denies.

That old rule's distribution is a subtlety worth recording, because it is easy to get
backwards: `.gitignore:43` (`.claude/*.local.md`) *does* match the filename, so it looks
local-only. But the file was already **tracked** when `b070caa` introduced that rule, and
`.gitignore` has no effect on already-tracked paths — so it is committed and does reach every
ent and every fresh clone. Plain `git check-ignore` reports "not ignored" for exactly this
reason (it respects the index); only `--no-index` shows the rule matching.

Practical upshot: the old rule is now redundant with this hook. Making `b070caa`'s intent
real requires `git rm --cached` on it, the same treatment #55 gave the tracked sqlite file.
Left in place for now because hookify rules may be consumed by non-Claude ents, which this
`PreToolUse` hook does not cover.

**Matcher covers `PowerShell` as well as `Bash`.** A `Bash`-only guard is bypassed by
running the same command through the PowerShell tool.

Blocked patterns:

- `-m outlier_scrapers.<runner>` for `reasoning`, `gemini_research`, `c_research`,
  `claude_reasoning`, `claude_synthesis`, `run_desk`, `run_desk2`
  (the `-m` form catches `python`, `py`, `python3`, `uv run`, `poetry run`)
- `daily_job ... --run-reasoning`
- `pytest ... test_{reasoning,gemini_research,claude_reasoning,claude_synthesis,c_research}`

`run_desk2` is new coverage — the old hookify pattern omitted it despite the module and its
tests existing.

### Escape hatch: `DESK_OK`

When the user **has** explicitly asked this turn ("run the desk", "run reasoning",
"run A/B/C/D/E"), append the literal token `DESK_OK` to the command:

```
python -m outlier_scrapers.run_desk   # DESK_OK
```

The point is not to be unbypassable — it is that a paid run can no longer happen through
absent-mindedness, and every bypass leaves a record. Blocks and bypasses are both appended to:

```
~/.claude/reasoning-guard.log
```

Format: `<timestamp>\t<BLOCKED|BYPASS(DESK_OK)>\t<command>`. An unexplained `BYPASS` line is
worth asking about.

---

## `check-sync.ps1` — read-only sync verification

STEP 0 is mandated in `AGENTS.md`, `CLAUDE.md`, and `GROK.md`, and was enforced by nothing.

> **Historical note.** This hook was written when the sync tooling itself failed silently:
> launched from a directory git cannot read — such as the OneDrive mirror, whose `.git` is a
> placeholder — the bootstrap stage no-opped and printed `[sync] Not inside a git repo`, yet
> the report still rendered `State vs origin/master: MATCH`, and only the *absence* of
> `VALIDATE: OK` distinguished a skipped sync from a real one. That is fixed:
> `sync-outlier.ps1` no longer takes cwd as an input, skipped bootstraps are fatal, and
> `verify-sync.ps1` ends with a computed `REPORT STATUS: OK|FAILED` trailer plus a per-run
> `RUN-NONCE`. This hook remains useful for a different reason — it tells you when *this
> session's* working directory is not a readable checkout, which governs where your edits land.

This hook **does not call `report-sync.ps1`**. That script force-runs
bootstrap + `-SyncAllWorktrees` before it will inspect anything, which hard-aligns the
`ai-runners` full clone. Running that automatically at every session start could overwrite
another ent's uncommitted work with no human in the loop. So it checks and reports instead:

0. **Relevance gate.** Because this hook is also registered at user scope it fires in every
   project. It exits silently unless the session plausibly concerns outlier — cwd path matches
   `outlier` (which still catches the OneDrive mirror, whose `.git` is unreadable) or the
   checkout's `origin` is the outlier remote. Without this, unrelated sessions would each pay
   for a network fetch.
1. Is the session's cwd a git checkout of the outlier remote? (catches the OneDrive trap)
2. `git fetch origin` — remote-tracking refs only — then canonical branch / HEAD / `origin/master`
3. Is canonical *behind* `origin/master`? (ahead is normal on a feature branch)
4. Are all five Tier-1 upgrade markers present on disk?

On any failure it injects an instruction to run STEP 0 by hand, with the canonical path and
the required gates. Read-only throughout: `rev-parse`, `fetch`, and file reads. No checkout,
no reset, no branch writes. Always exits 0 — a SessionStart hook must not block a session.

### Marker → file mapping

Easy to get wrong, so it is asserted here explicitly:

| Marker | File |
|---|---|
| `player_id` | `outlier_scrapers/pack.py` |
| `round_robin_then_fill` | `outlier_scrapers/pack.py` |
| `CANDIDATES_HEADER` | `outlier_scrapers/pack.py` |
| `decisions.csv` | `outlier_scrapers/pack.py` |
| `_acquire_pack_lock` | `outlier_scrapers/daily_job.py` |

Despite its name, `_acquire_pack_lock` is **not** in `pack.py` and never has been
(`git log -S'_acquire_pack_lock' -- outlier_scrapers/pack.py` is empty). The authority is
`scripts/verify-sync.ps1` (~L81), which checks it against the *daily* blob:
`$hasLock = $gitDaily -match '_acquire_pack_lock'`.
