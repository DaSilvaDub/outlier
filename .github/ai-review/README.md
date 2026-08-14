# Automated multi-AI pull-request review

Every non-draft pull request and meaningful update fans out to four independent reviewers,
then a fifth Codex consensus pass waits for the repository's required CI checks and posts an
enforced merge recommendation.

```text
pull_request opened / synchronize / reopened / ready_for_review
                              |
                 bounded GitHub diff artifact
                              |
          +---------+---------+---------+---------+
          |         |                   |         |
        Codex     Claude              Gemini     Grok
          +---------+---------+---------+---------+
                              |
                    required CI checks
                              |
                     consensus + gate
                              |
             five idempotent PR comments
```

The existing `.github/workflows/claude.yml` remains available for manual `@claude` requests.

## Required repository secrets

These are separately billed API credentials; consumer subscriptions do not necessarily include
API usage.

- `OPENAI_API_KEY` — Codex review and the consensus pass.
- `ANTHROPIC_API_KEY` — Claude review.
- `GEMINI_API_KEY` — Gemini review.
- `XAI_API_KEY` — Grok review.

Configure each secret in GitHub repository settings or with `gh secret set`, for example:

```powershell
gh secret set OPENAI_API_KEY --repo DaSilvaDub/outlier
gh secret set ANTHROPIC_API_KEY --repo DaSilvaDub/outlier
gh secret set GEMINI_API_KEY --repo DaSilvaDub/outlier
gh secret set XAI_API_KEY --repo DaSilvaDub/outlier
```

## Optional repository variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_REVIEW_MODEL` | `gpt-5.3-codex` | Correctness/regression reviewer |
| `OPENAI_CONSENSUS_MODEL` | `gpt-5.6-terra` | Final synthesis and merge verdict |
| `ANTHROPIC_REVIEW_MODEL` | `claude-opus-4-8` | Architecture/data-flow reviewer |
| `GEMINI_REVIEW_MODEL` | `gemini-3.5-flash` | Test/type/security reviewer |
| `XAI_REVIEW_MODEL` | `grok-4.6` | Adversarial reviewer |
| `AI_REQUIRED_CHECKS` | `test,typecheck` | Check-run names that must pass |
| `AI_REVIEW_MAX_DIFF_CHARS` | `600000` | Hard cap on the diff sent to providers |

The model variables make migrations possible without editing workflow logic. Review model IDs
against current provider documentation before changing them.

## Safety and failure behavior

- The workflow uses `pull_request`, checks out the trusted base SHA, and fetches the PR diff as
  data through GitHub's API. It never checks out or executes code from the PR head.
- Secret-bearing jobs are limited to same-repository branches. GitHub withholds provider secrets
  from fork pull requests, so fork reviews are intentionally skipped.
- Provider jobs have read-only GitHub permissions. Only the final publisher can write issue/PR
  comments, and it never receives provider keys.
- Stable hidden markers update the five existing comments after every synchronize event instead
  of appending duplicates.
- Missing credentials, malformed provider responses, failed reviewers, failed CI, and incomplete
  CI all downgrade the consensus deterministically. The final check passes only for an enforced
  `MERGE` recommendation.
- Required checks are intentionally stricter than GitHub's permissive protection semantics:
  only a `success` conclusion passes; `neutral`, `skipped`, missing, and pending fail closed.
- Diff input is capped and explicitly marked when truncated. Truncation forces the automated
  consensus to `HOLD` so a partial review cannot authorize a merge.

## Local verification

The adapter uses only Python's standard library. Run its focused tests and workflow lint before
changing provider request/response contracts:

```powershell
pytest tests/test_ai_pr_review.py -q
ruff check scripts/ai_pr_review.py tests/test_ai_pr_review.py
actionlint .github/workflows/ai-pr-review.yml
```
