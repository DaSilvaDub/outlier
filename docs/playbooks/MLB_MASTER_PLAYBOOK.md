# MLB Master Playbook — Provisional Baseline

Status: provisional; initialized 2026-10-24 from the 2026-10-23 TB at MIL audit. There was no pre-existing MLB master playbook file located in the repository during this update. This is a traceable starting baseline, not a validated model specification.

## Current operating rules

1. **Starting pitcher identity:** reconcile each pitcher prop to the confirmed starter and the market's participant/void terms. If identity or role is unresolved, exclude it from candidate publication, preserve a diagnostic record, and assign zero units.
2. **Market identity:** retain operator/book, exact market type and settlement definition, selection, line, quote timestamp, and source ID. Keep two-way and three-way markets separate. Do not merge quotes solely because their display labels look similar.
3. **Freshness and liquidity:** stale, thin-liquidity, and reverse-movement flags remain visible and require the existing stand-down/review gate. A small estimated edge cannot override them without current verifiable prices and policy support.
4. **Probability provenance:** display market-derived, independent predictive, and blended probabilities separately. Do not label a devigged market probability as a player-specific projection. Where required predictive inputs are absent or components materially disagree, prohibit EV promotion and keep zero stake.
5. **Same-game correlation:** group related side, total, and team-total candidates under a shared game thesis. Do not count correlated outcomes as independent calibration observations.
6. **Outcome accounting:** report candidate direction/settlement, actionable recommendations, and confirmed wager ledger separately. Never infer a wager, stake, ROI, or realized EV from a candidate row.
7. **No-bet is valid:** keep existing actionable and source-integrity gates. A winning non-actionable candidate does not retrospectively become an approved pick.

## KEEP / CHANGE / REMOVE from the 2026-10-23 audit

### KEEP

- Fail-closed starter identity checks and zero-unit stand-down.
- Existing quality/actionability gates.
- Immutable capture of original quote, line, probability source, and timestamp.

### CHANGE

- Exclude identity-mismatched pitcher props before candidate export, with a separate diagnostic record.
- Canonicalize quote identity and settlement definition.
- Report correlated same-game candidates as a cluster and separate candidate outcomes from approved/wagered results.
- Preserve and display probability components before any EV calculation.

### REMOVE

- Treating proxy/devig probability as a player-specific projection.
- Treating board tier, hit-rate support, market movement, or one game's result as standalone proof of predictive edge.
- Including non-actionable rows in placed-bet ROI or calibration denominators.

## Rule status and validation plan

All rules below are **PROVISIONAL**, supported by one game only. No rule is REPEATED or VALIDATED.

| Rule ID | Evidence | Minimum validation |
|---|---|---|
| MLB-SP-IDENTITY-01 | Rasmussen SO candidate did not match actual TB starter; existing gate flagged mismatch | 200 pitcher starts, 0 escaped identity mismatches |
| MLB-MKT-QUOTE-01 | Different MIL moneyline/total quote definitions and missing book metadata | 200 game snapshots, measure duplicate/collision and quote reconciliation failures |
| MLB-MKT-MOVE-01 | Stale/thin/reverse movement flags present on three candidates | Prospective timestamped CLV and outcome study by market, flagged versus unflagged |
| MLB-PROB-01 | Devigged, independent, and blended probabilities were inconsistent/incomplete | At least 200 graded forecasts per market segment with chronological holdout; report Brier, log loss, calibration |

## Change history

- 2026-10-24 — Created provisional baseline from `reports/audits/2026-10-23_MLB_Postgame_Audit.md`. Source: frozen 2026-10-23 pack, one completed game, seven candidate rows, zero actionable plays, no stake/transaction record. Reversible; validation pending.
