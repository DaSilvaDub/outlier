## 2026-09-06T15:38:01Z

You are test_writer_1.
Your working directory is: C:\Users\dasil\Dev\GitHub\outlier\.agents\test_writer_1
The authoritative original user request is recorded at: C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md
Target worktree repository path: C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights
Current branch: feat/outlier-props-insights
Project architecture & specs: C:\Users\dasil\Dev\GitHub\outlier\.agents\orchestrator_1\PROJECT.md

MANDATORY FIRST STEP: Read C:\Users\dasil\Dev\GitHub\outlier\.agents\ORIGINAL_REQUEST.md before doing any work.

Objective:
Write a comprehensive, opaque-box TDD test suite in C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights\tests\test_outlier_props.py covering all acceptance criteria and edge cases in ORIGINAL_REQUEST.md.

Scope Boundaries:
- Write ONLY to tests/test_outlier_props.py (and test fixtures if necessary, though extensive fixtures already exist in tests/fixtures/outlier/).
- DO NOT touch or modify any production code (cfb_analytics/*).
- DO NOT touch any Elo project codes or tests (see FORBIDDEN ELO FILES in PROJECT.md).
- DO NOT modify existing passing tests in tests/.
- All tests must run in replay mode (CFB_HTTP_MODE=replay) and never reach the network.
- DO NOT run any paid reasoning or external AI models.

Test Suite Requirements for tests/test_outlier_props.py:
1. Trap 1: Test verifying parser attributes prices to the correct sportsbook from odds[].book, even when outcome.books is out of order (using event_4a8966e71bd7d4f2fd73daa3e80a97c2943b7593_GAMELINE.json fixture where outcome.books[0] == 'THESCOREBET' but odds[0]['book'] == 'DRAFTKINGS').
2. Trap 2: Test verifying proposition prices are unioned across multiple market cards and deduplicated by (book, market, side, line).
3. Whitelist Filtering: Test verifying unwhitelisted markets (DOUBLE_RESULT, MONEYLINE_THREE_WAY, WINNING_MARGIN, player props, etc.) are strictly dropped and not ingested.
4. Team Props Attribution: Test verifying team props (POINTS, OFFENSIVE_YARDS, RECEIVING_YARDS, RUSHING_YARDS) correctly map to home/away team_id and sides are restricted to OVER/UNDER.
5. Graceful Degradation: Test verifying empty prop responses ({"markets": []}) and empty insights responses ({"insights": []}) degrade cleanly without crashing gameline ingestion.
6. Schema Migration 10: Test verifying Migration 10 applies cleanly to an existing database, existing gameline rows keep their exact original snapshot_ids, and new columns (team_id, player_id, scope, market_type) and prop_consensus table work.
7. Idempotency: Test verifying re-running ingestion against the same slate produces no duplicate rows in odds_snapshots or prop_consensus.
8. Prop Consensus: Test verifying min_books_for_consensus = 3 floor is applied to prop consensus.
9. Ingestion Summary: Test verifying IngestSummary captures prop rows by family, distinct books, and failures.

Completion Criteria:
1. Write and save tests/test_outlier_props.py.
2. Verify existing tests still pass and note any expected failures on unbuilt features.
3. Write C:\Users\dasil\Dev\GitHub\outlier\.agents\test_writer_1\handoff.md detailing the test suite structure, test cases created, and how to run them.
4. Send a completion message to parent.
