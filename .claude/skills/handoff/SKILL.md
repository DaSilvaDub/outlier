---
name: handoff
description: Close out an outlier session per AGENTS.md's Feature Branch Workflow — commit, push, open a PR, write .agent-log/HANDOFF.md, and leave the worktree clean.
disable-model-invocation: true
---

Run the mandatory end-of-session protocol from `AGENTS.md` ("Feature Branch Workflow" /
"End of Session & Handoff Protocol"). Follow these steps in order; do not skip or
reorder them, and do not silently substitute a step you think is equivalent.

## 0. Branch safety check — do this immediately before committing, not earlier

Concurrent sessions on this repo move canonical's `HEAD` between branches. Confirm you
are still on the branch you think you're on **right before staging anything**:

```powershell
git branch --show-current
```

If this is not the feature branch you did the work on, STOP. Do not commit. Work out
where your actual changes live (`git status`, `git diff`) before proceeding — committing
now would put your work on the wrong branch.

If you are on `master` and your changes are not infra-only (`AGENTS.md`, `CLAUDE.md`,
`GROK.md`, `.githooks/`, `.agent-log/`, `report-sync.ps1`), stop and create a feature
branch first — direct commits to `master` are reserved for exactly those files.

## 1. Commit your work

```
git add <the files you actually changed — never a blanket -A>
git commit -m "$(cat <<'EOF'
<type>: <description>

<body>

Agent: <your name — e.g. claude>
EOF
)"
```

The `Agent: <name>` trailer is mandatory — the repo's `post-commit` hook parses it into
`.agent-log/<date>-<agent>-<hash>.md` automatically, and other ents rely on that
attribution to know who shipped what.

## 2. Push the branch

```
git push -u origin <branch-name>
```

## 3. Open a PR to master

```
gh pr create --base master --title "..." --body "..."
```

Wait for CI (`test`, `typecheck`) before telling the user the PR is ready, and note in
the body if a check was already red on `master` before your branch (so you don't get
blamed for pre-existing breakage).

## 4. Write `.agent-log/HANDOFF.md`

Overwrite it with:

```markdown
# Handoff — <date>

**Last Commit SHA:** <short sha, from `git rev-parse --short HEAD`>
**PR:** <link from step 3, or "not yet opened" if this handoff is mid-task>

## Files Touched
- <path> — <one line on what changed>

## Next Steps
- <what the next agent/session should pick up, or open questions/blockers>
```

## 5. Force-add and commit the handoff file

`.agent-log/` is gitignored (to keep noisy per-session logs out of history), but this
one file is a mandated exception. A plain `git add .agent-log/HANDOFF.md` is silently
rejected with "paths are ignored" — you must force it:

```
git add -f .agent-log/HANDOFF.md
git commit -m "docs(handoff): record session close for <branch-name>"
git push
```

Pushing after the PR is already open updates it automatically — no extra `gh` command
needed.

## 6. Verify the worktree is clean

```
git status --short
```

Should be empty, except possibly the single `.agent-log/<date>-<agent>-<hash>.md` file
the `post-commit` hook generated for your current `HEAD` (that file is itself gitignored
and does not need to be committed — it's local session noise by design, distinct from
the mandated `HANDOFF.md`). If anything else is dirty, resolve it before ending the
session; do not leave stray edits for the next session to trip over.
