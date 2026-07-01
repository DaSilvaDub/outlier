# HANDOFF: finish the multi-agent sync setup

Claude built most of the sync system. Three things remain. Execute these in
order from the repo root (`C:\Users\dasil\OneDrive\Documents\outlier`).
Identify yourself as your tool name (`codex` / `grok` / `antigravity`) in the
commit trailers below.

## Already done (do NOT redo)
- AGENTS.md — "Multi-Agent Sync Protocol" section appended
- CLAUDE.md — created
- .agent-log/ — README.md, _TEMPLATE.md, SUMMARY.md created
- .githooks/post-commit — created (not yet enabled)

## STEP 1 — Enable the git hook (required, once per machine)
```
git config core.hooksPath .githooks
```
If on macOS/Linux ever, also: `chmod +x .githooks/post-commit`

## STEP 2 — Create the Grok pointer file (GROK.md)
Create a file named `GROK.md` in the repo root with this content:
```
# Grok — start here

You are agent `grok` in this repo. Multiple AI agents share this codebase.
Read `AGENTS.md` first (the "Multi-Agent Sync Protocol" is mandatory).
On start: read `.agent-log/SUMMARY.md`, the latest `.agent-log/` entries, and
`git log --oneline -15`.
On end: commit with an `Agent: grok` trailer, write a `.agent-log/` session
note, and update `.agent-log/SUMMARY.md`.
```
(Codex and Antigravity both read AGENTS.md natively — no extra file needed.)

## STEP 3 — Commit everything with your agent trailer
```
git add -A
git commit -m "Add multi-agent sync system (AGENTS.md protocol, .agent-log, post-commit hook)

Agent: codex"
```
Replace `codex` with your actual tool name.

## STEP 4 — Verify the hook fired
After STEP 3, confirm a new file appeared:
```
git ls-files --others --exclude-standard .agent-log/
ls .agent-log/
```
You should see a new `YYYY-MM-DD-<agent>-<hash>.md` file auto-created by the
hook, containing the commit metadata and changed files. If it did NOT appear,
the hook is not enabled — re-run STEP 1 and commit again.

## STEP 5 — Report back
Leave a one-line note in `.agent-log/SUMMARY.md` under "Current state" saying
setup is verified, then commit that too (with your `Agent:` trailer).
Tell Daniel it's done so Claude can do a final verification pass.
```
```
