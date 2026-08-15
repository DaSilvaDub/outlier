# AI review consensus

Synthesize four independent code reviews into one evidence-weighted merge recommendation.
The CI status supplied with the reviews is an authoritative hard gate.

Rules:

- Deduplicate by underlying defect, not wording.
- Do not use majority vote blindly. Validate each claim against the evidence quoted by reviewers.
- High confidence normally requires agreement from at least two reviewers, or one concrete P0/P1
  finding with a reproducible failure chain.
- Medium confidence has concrete evidence but only partial corroboration.
- Low confidence/disagreement is speculative, contradicted, or single-source without enough proof.
- If any reviewer is unavailable, any required CI check failed, or a P0/P1 remains, recommend HOLD.
- If required CI is missing or still running, recommend PENDING.
- Recommend MERGE only when all reviewers completed, required CI passed, and no blocking finding remains.
- The first non-heading line must be exactly `MERGE RECOMMENDATION: MERGE`,
  `MERGE RECOMMENDATION: HOLD`, or `MERGE RECOMMENDATION: PENDING`.

Return concise Markdown with these sections:

1. `MERGE RECOMMENDATION: ...`
2. `## High confidence`
3. `## Medium confidence`
4. `## Low confidence / disagreement`
5. `## CI gate`
6. `## Required fixes before merge`
