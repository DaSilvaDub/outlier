---
name: sync-report
description: Run the mandatory outlier STEP 0 sync report from the canonical checkout and verify every required gate before continuing any work in this repo.
disable-model-invocation: true
---

Run the canonical outlier sync report and verify it, per `AGENTS.md` STEP 0.

## Why this exists

STEP 0 requires running `report-sync.ps1` by its canonical absolute path, from the
canonical directory, before any other work — but it is easy to get this wrong in a way
that looks right. Running it from a directory git cannot read (most notably
`C:\Users\dasil\OneDrive\Documents\outlier`, whose `.git` is a OneDrive placeholder)
used to make the whole sync silently no-op while still printing a confident
`State vs origin/master: MATCH`. This skill exists so that check is never skipped or
run from the wrong place by accident.

**Do not** use a `!`command`` dynamic-injection substitution to run this — that pipes
the output through the skill-rendering layer before you see it, and the whole point of
STEP 0 is a direct, unfiltered invocation. Run the two commands below as ordinary tool
calls and read the actual output.

## Steps

1. Run, as a literal command (do not `cd` via a separate shell state that might not
   persist — use the working-directory argument your tool provides, or `Set-Location`
   in the same invocation):

   ```powershell
   Set-Location 'C:\Users\dasil\Dev\GitHub\outlier'; & "C:\Users\dasil\Dev\GitHub\outlier\report-sync.ps1"
   ```

2. Read the **entire** output, through the final `RUN-NONCE:` line. Do not summarize or
   truncate it in your own head before checking it — the gates below are pass/fail on
   exact text, not on your impression of the output.

3. Verify every one of these before treating the sync as valid:
   - The trailer reads exactly `REPORT STATUS: OK` (not `FAILED`, and present at all —
     its absence means the script exited before reaching the verdict).
   - The exit code was `0`.
   - The output ends with a `RUN-NONCE:` line. A paste with no nonce, or a nonce whose
     `utc=`/`head=` don't match what you'd expect from a run you just triggered, means
     the output was truncated, edited, or stale — treat it as invalid and re-run.
   - `VALIDATE: OK` and `State vs origin/master: MATCH` both appear in the body (the
     trailer summarizes these, but confirm the underlying lines are actually present).

4. If `REPORT STATUS: FAILED` or a non-zero exit: read the `failures:` list in the
   verdict, fix the named cause, and re-run from step 1. Do not proceed with other work
   on this repo while sync state is unverified, and do not quote a FAILED report as
   proof of any state.

5. If `REPORT STATUS: OK`: proceed with the user's actual task. You do not need to
   re-run this again later in the same session unless something suggests drift (e.g.
   another ent mentions running `-SyncAllWorktrees`, or a "commit not found" question
   comes up — re-run and paste fresh in that case, per STEP 0's own instruction never
   to answer such questions from memory or an old paste).

## Full output required

Whether this succeeds or fails, paste the complete console output back to the user
context you're operating in — not a summary. That is the entire mechanism that lets a
human or another ent trust the result without re-running it themselves.
