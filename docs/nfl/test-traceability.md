# NFL test traceability: forensic report §9

Maps every item in §9 "Required permanent regression and release tests" and
every release acceptance criterion of `reports/nfl-forensic-report-2026-10-06.md`
to the named tests that cover it. Written for #231 part a (phase 9a, per Pipeline
Architect's approved design). Test paths are `tests/<file>::<test>`; `RC` means
the phase 9a file `tests/test_nfl_release_contracts.py`.

Status: **covered** = a named test asserts the contract; **partial** = a related
test exists but does not assert the whole contract; **gap** = no test.
Categories 1, 2, 5 and 7 are filled in 9a. Categories 3, 6 and 8 belong to 9b;
category 4 and the archive manifest belong to 9c. Their statuses are recorded
here but this phase adds no tests for them.

The snapshot harness (`scripts/nfl_snapshot_diff.py`, v13: Slate A steps A1–A21,
Slate B steps B1–B12) is cited where a step exercises the contract end to end.

## 1. Unit calculations (9a)

| Item | Status | Tests |
|---|---|---|
| Strict count wins/losses/pushes | covered | `test_nfl_shadow_settle::test_grade_side_over_under_push_and_yes`; `test_nfl_scorecard::test_pushes_duplicates_and_ungraded_reasons`, `::test_summary_excludes_pushes_and_baseline_reflects_skew`; `test_nfl_pricing_math::test_poisson_integer_under_excludes_the_push`; `test_nfl_settlement_grading::test_non_finite_line_does_not_grade_a_push`; harness A5 |
| v1/v2 probability ownership | covered | `test_nfl_pricing_math::test_hierarchy_projection_keeps_its_own_method_and_count`, `::test_hierarchy_fallback_clears_stale_projection_method_and_count`, `::test_hierarchy_records_win_push_loss`; `test_nfl_shadow_settle::test_projection_v2_requires_three_prior_weeks` |
| TD market semantics | covered | `test_nfl_pricing_math::test_anytime_td_counts_only_scored_touchdowns`; `test_nfl_settlement_grading::test_first_td_is_not_settled_from_anytime_totals`, `::test_anytime_td_still_settles` |
| Missing / nonfinite values | covered | `test_nfl_settlement_grading::test_missing_stat_is_none_not_zero`; `test_nfl_alt_floors::test_nan_odds_never_reach_the_record`; `test_nfl_degenerate_model_p::test_supplied_degenerate_model_p_is_cleared`; `test_nfl_normalizer` (coerce_odds / coerce_float); harness A6 |
| Offered-price EV and finite odds | covered (9a) | RC `::test_price_pillar_ev_is_at_the_offered_price`, `::test_push_aware_ev_never_counts_push_mass_as_win_or_loss`; `test_nfl_pricing_math::test_push_aware_ev`; `test_nfl_best_bets::test_no_edge_at_price_rejects`, `::test_zero_price_is_even_money_and_never_sinks_the_card` |

## 2. Data integrity (9a)

| Item | Status | Tests |
|---|---|---|
| Unique event/team/player/game keys | covered | `test_nfl_schema_integrity::test_normalized_validation_enforces_keys_and_ownership`, `::test_identical_duplicate_team_game_counts_once`, `::test_conflicting_duplicate_team_game_drops_the_game`, `::test_identical_duplicate_stat_row_counts_once`, `::test_conflicting_duplicate_stat_rows_drop_the_player`; `test_nfl_usage::test_duplicate_player_game_counts_once_and_conflicting_expected_drops` |
| Declared counts | covered (9a) | RC `::test_every_declared_count_matches_its_rows` |
| Two teams per game | covered (9a) | RC `::test_every_game_has_two_distinct_teams_and_props_belong_to_them`; `test_nfl_schema_integrity::test_asymmetric_opponent_rows_are_refused` |
| Membership symmetry | covered | `test_nfl_schema_integrity::test_asymmetric_opponent_rows_are_refused`, `::test_ownership_prefers_carried_event_teams_and_still_fails_a_foreign_team`; `test_nfl_tape_nflverse::test_pipeline_maps_inactives_to_both_teams_of_each_event` |
| Joins retain or explicitly reject every input | covered | `test_nfl_fetch_odds_close::test_same_game_name_collision_is_rejected_on_both_keys`, `::test_align_rejects_two_same_game_predictions_with_different_identities`, `::test_identityless_close_row_still_joins_a_prediction_that_has_identity`; `test_nfl_close_ownership::test_single_incompatible_close_is_not_attached_or_aligned`; `test_nfl_schema_integrity::test_drop_counts_reach_an_informational_receipt` |
| Quote owner/book uniqueness | covered | `test_nfl_schema_integrity::test_props_dedupe_identical_and_drop_conflicting`; `test_nfl_fetch_odds_close::test_two_books_for_one_player_stay_a_duplicate_not_a_collision`; `test_nfl_close_ownership::test_two_same_name_players_stay_ambiguous`; harness B12 |

## 3. Integration (9b)

| Item | Status | Tests |
|---|---|---|
| Raw provider-shaped market/period/ID payload through all consumers | covered (9b) | `test_nfl_edges_e2e::test_every_consumer_reads_the_committed_run` (live-shaped `FrozenOutlierClient` payload through every published consumer); harness Slate B (B1–B12); `test_nfl_market_scope` |
| Page-2 failures block publication | covered | `test_nfl_stage_gates::test_props_page_two_failure_is_partial_and_publishes_nothing`; `test_nfl_pagination_completeness::test_page_two_failure_raises_incomplete`; harness B5 |
| Empty forecast/tape must not validate | covered (9b) | `test_nfl_edges_e2e::test_tape_with_no_teams_is_refused`; `test_nfl_weather::test_game_weather_records_provenance_and_empty_forecast_does_not_verify`; `test_nfl_tape_nflverse::test_inadmissible_tape_disables_injury_pillar`; harness B9 |
| Adding alternates must not change primary context | covered | `test_nfl_market_scope::test_alternates_cannot_move_the_primary_environment`, `::test_matchup_context_ignores_one_sided_alternates` |

## 4. Historical backfill (9c)

| Item | Status | Tests |
|---|---|---|
| Immutable archived pregame feeds, as_of_utc and availability stamps | gap | no archive manifest exists (phase-1 archive script deferred). #252's pinned *results* cache is related but covers post-game box scores, not pregame feeds: `test_nfl_boxscore_cache::test_pinned_cache_never_touches_network_even_when_stale`, `::test_pinned_cache_refuses_hash_mismatch`, `::test_pinned_cache_refuses_file_without_sidecar`, `::test_pinned_cache_refuses_missing_file` |
| Current corrected sources explicitly retrospective | partial | `test_nfl_tape_nflverse::test_retrospective_run_refuses_tape_written_after_as_of`; `test_nfl_publication::test_past_as_of_replay_before_kickoff_is_recorded_and_bundle_only` |
| Multi-season fixtures | gap | — |
| Playoffs fixtures | partial | `test_nfl_season_phase::test_postseason_slate_is_rejected_before_publication`; harness B7 |
| Stat-correction fixtures | partial | `test_nfl_boxscore_cache::test_stat_correction_arrives_on_explicit_refresh`, `::test_pinned_parses_the_bytes_it_verified` (#252) |

## 5. Incremental / repeated updates (9a)

| Item | Status | Tests |
|---|---|---|
| Same run ID has identical semantic outputs | covered (9a) | RC `::test_repeated_run_on_the_same_inputs_has_identical_semantic_outputs`; `test_nfl_publication::test_run_writer_refuses_reuse`; `test_nfl_boxscore_cache::test_settle_cli_pinned_regrade_is_reproducible` (#252: a pinned re-grade gives identical output); harness determinism (two captures, no diff) |
| No duplicate ledger/snapshot records | covered | `test_nfl_persistence::test_same_run_id_never_duplicates_snapshot_rows`, `::test_same_run_id_ledger_rows_are_replaced_not_duplicated`; harness A21 |
| Changed run ID adds an observation with coherent provenance | covered (9a) | `test_nfl_persistence::test_changed_run_id_adds_one_observation`; RC `::test_changed_run_id_replaces_the_ledger_observation_coherently`; `test_nfl_publication::test_snapshot_rows_carry_the_run_id` |
| Concurrent jobs cannot lose dates | covered (9a) | `test_nfl_persistence::test_concurrent_ledger_writers_lose_no_dates`, `::test_two_threads_on_a_stale_lock_never_overlap`; RC `::test_concurrent_snapshot_appends_lose_no_rows`; `test_nfl_edges_e2e::test_two_os_processes_lose_no_snapshot_or_ledger_rows` (9b: two spawned OS processes) |

## 6. End-to-end (9b)

| Item | Status | Tests |
|---|---|---|
| Offline valid slate | covered | `test_nfl_best_bets::test_pipeline_run_writes_snapshot_and_traced_card`; harness A1 |
| Empty confirmed slate | covered (9b) | `test_nfl_edges_e2e::test_empty_confirmed_slate_publishes_an_empty_card`; `test_nfl_export_extra_pack::test_valid_empty_slate_exits_zero_with_a_header`; harness B8 |
| Partial page | covered | `test_nfl_stage_gates::test_props_page_two_failure_is_partial_and_publishes_nothing`; harness B5 |
| Unavailable mandatory source | covered | `test_nfl_stage_gates::test_every_event_market_failing_is_failed`; harness B2, B6 |
| Failure during write | covered | `test_nfl_publication::test_failed_run_publishes_nothing`; `test_nfl_best_bets::test_trace_failure_removes_stale_card_and_weekly_run_fails`; `test_nfl_persistence::test_snapshot_lock_timeout_withdraws_card_and_names_the_lock` |
| Full → window | covered | `test_nfl_publication::test_window_run_preserves_bare_date_bytes`, `::test_window_run_writes_only_suffixed_files`; harness A2 |
| After-kickoff run | covered | `test_nfl_publication::test_after_kickoff_run_is_bundle_only`; harness B4 |
| Recovery | covered (9b) | `test_nfl_edges_e2e::test_recovery_after_a_partial_run_republishes_one_coherent_run`; `test_nfl_boxscore_cache::test_refresh_failure_falls_back_to_validated_copy`; `test_nfl_persistence::test_failed_refresh_falls_back_to_the_existing_tape_dir` |
| All consumers resolve the same committed run manifest | covered (9b) | `test_nfl_edges_e2e::test_every_consumer_reads_the_committed_run`, `::test_recovery_after_a_partial_run_republishes_one_coherent_run`; `test_nfl_publication::test_run_bundle_manifest_hashes_match_and_pointer_written_last` |

## 7. Leakage prevention (9a)

| Item | Status | Tests |
|---|---|---|
| Later same-day depth report | covered | `test_nfl_tape_nflverse::test_depth_snapshot_later_same_day_rejected`, `::test_depth_roles_skip_inactive_and_ignore_future_snapshots` |
| Postgame injury update | covered | `test_nfl_tape_point_in_time::test_injury_report_status` (`revised_after_as_of`), `::test_tape_injury_report_requires_point_in_time_rows` |
| Future week stats | covered | `test_nfl_asof_leakage::test_ngs_and_pbp_exclude_weeks_at_or_after_the_slate_week`, `::test_cutoff_also_excludes_earlier_weeks_not_finished_by_as_of`, `::test_trace_drops_week_bound_rows_after_the_cutoff`, `::test_predictions_are_invariant_to_injected_future_rows`, `::test_load_usage_refuses_a_missing_cutoff` |
| Future snapshot | covered | `test_nfl_asof_leakage::test_snapshot_rows_taken_after_as_of_are_ignored` |
| Retrospective close | covered (9a) | RC `::test_verified_close_never_changes_prediction_fields`; `test_nfl_close_provenance::test_only_a_verified_capture_is_book_close` |

## 8. NFL edges (9b)

| Item | Status | Tests |
|---|---|---|
| January REG | covered | `test_nfl_edges_e2e::test_january_regular_season_game_is_reg_and_publishable`; `test_nfl_season_phase::test_january_regular_season_slate_publishes`; `test_nfl_settlement_grading::test_january_february_settlement_uses_the_prior_season` |
| All POST rounds | covered (9b) | `test_nfl_edges_e2e::test_every_playoff_round_is_post_by_type_week_and_schedule_and_is_refused` (WC/DIV/CON/SB, real 2025 rows); `test_nfl_season_phase::test_postseason_slate_is_rejected_before_publication`; harness B7 |
| Bye | covered (9b) | `test_nfl_edges_e2e::test_bye_week_is_not_a_missed_game`; `test_nfl_games_played_smoothing::test_games_played_counts_distinct_weeks_and_unknown_is_none` |
| Reschedule / postponement | covered (9b) | `test_nfl_edges_e2e::test_rescheduled_game_does_not_inherit_its_stale_schedule_row`, `::test_rescheduled_week_stays_pending_until_its_new_kickoff` |
| Neutral / international game | covered (9b) | `test_nfl_edges_e2e::test_neutral_international_game_matches_swapped_teams_and_is_never_forecast`; `test_nfl_season_phase::test_schedule_fallback_matches_neutral_site_game_with_swapped_home_away` |
| DST boundaries | covered (9b) | `test_nfl_edges_e2e::test_eastern_gametime_converts_across_dst`, `::test_dst_boundary_does_not_start_a_game_an_hour_early` |
| Trade | covered | `test_nfl_usage::test_traded_player_role_is_current_team_only` |
| IR / PUP | partial (9b) | `test_nfl_edges_e2e::test_injury_report_has_no_ir_or_pup_status`. The real nflverse injury report has no IR/PUP value (players drop off it), and no NFL consumer reads a roster reserve status, so an IR player is caught only by missing from the depth chart. A real IR/PUP source would be new scope, not a test |
| Inactive starter | covered | `test_nfl_matchup::test_inactive_rb1_falls_back_to_next_healthy_depth_chart_back`; `test_nfl_best_bets::test_inactive_player_rejected` |
| Backup with more book coverage | covered | `test_nfl_roster::test_backup_with_more_quotes_is_not_promoted` |
| Name collisions | covered | `test_nfl_alt_floors::test_same_name_in_two_events_is_two_players`; `test_nfl_close_ownership::test_two_same_name_players_stay_ambiguous`; `test_nfl_fetch_odds_close::test_same_game_name_collision_is_rejected_on_both_keys` |
| Legacy depth schema | covered (9b) | `test_nfl_edges_e2e::test_pre_2025_legacy_depth_schema_season_is_refused` (pre-2025 seasons are refused before publication) |

## Release acceptance criteria

| Criterion | Status | Tests |
|---|---|---|
| Zero unexplained duplicate owners or dropped required rows | covered | §2 rows above; `test_nfl_schema_integrity::test_drop_counts_reach_an_informational_receipt` |
| Zero unavailable-evidence approvals | covered | `test_nfl_best_bets::test_missing_injury_report_is_not_treated_as_clean`; `test_nfl_alt_floors::test_empty_evidenced_starter_set_blocks_qb_floors`, `::test_ineligible_floor_is_inventory_never_ranked`; `test_nfl_roster::test_static_tables_are_not_current_evidence` |
| Correct three-outcome economics | covered | `test_nfl_pricing_math::test_push_aware_ev`, `::test_integer_line_is_never_validated_until_pushes_are_priced`; RC `::test_push_aware_ev_never_counts_push_mass_as_win_or_loss` |
| No as-of violations | covered | §7 rows above; `test_nfl_asof_leakage::test_naive_as_of_is_rejected` |
| Coherent run hashes | covered | `test_nfl_publication::test_run_bundle_manifest_hashes_match_and_pointer_written_last`; `test_nfl_snapshot_diff::test_bundle_manifest_hashes_and_sizes_are_scrubbed` |
| Complete expected slate coverage | partial | `test_nfl_pagination_completeness` (feed completeness); expected-slate-vs-archive check needs the 9c archive manifest |
| Green NFL checks in frozen Python 3.11 CI | covered (CI) | `core` / `typecheck` workflows (F28 gate owned by Pipeline Health); not a pytest test |

## Summary

| Category | Covered | Partial | Gap | Owner |
|---|---|---|---|---|
| 1 Unit calculations | 5 | 0 | 0 | 9a |
| 2 Data integrity | 6 | 0 | 0 | 9a |
| 3 Integration | 4 | 0 | 0 | 9b |
| 4 Historical backfill | 0 | 3 | 2 | 9c |
| 5 Incremental/repeated | 4 | 0 | 0 | 9a |
| 6 End-to-end | 9 | 0 | 0 | 9b |
| 7 Leakage prevention | 5 | 0 | 0 | 9a |
| 8 NFL edges | 12 | 1 | 0 | 9b |
| Release criteria | 6 | 1 | 0 | 9c (coverage) |

Before 9a, categories 1/2/5/7 had 1 + 2 + 3 + 1 items short of full coverage
(offered-price EV; declared counts, two teams per game; same-run semantic
identity, changed-run ledger provenance, concurrent snapshot appends;
retrospective close), now filled by `tests/test_nfl_release_contracts.py`. Phase 9b (#231 part b) fills categories 3, 6 and 8 with `tests/test_nfl_edges_e2e.py`; IR/PUP stays partial because no real source carries that status.
