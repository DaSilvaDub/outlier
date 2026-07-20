"""Unit tests for deterministic game totals projection math."""
from datetime import datetime, timezone
import pytest
from outlier_scrapers.game_totals import MIN_EDGE_TOTALS, TOTAL_KIND_GAME, TOTAL_KIND_TEAM, aggregate_line_p_over, build_game_totals, build_team_totals, build_market_ladder, compute_side_edge, devig_book_pair, interpolate_fair_total, is_eligible_total_record, is_game_total_record, is_team_total_record, pick_best_side, logical_market_key, period_identity
from outlier_scrapers.sizing import compute_sizing

def _norm_record(market_id: str, line: float, position: str, books: list[dict], *, market_type: str='GAMELINE', proposition: str='TOTAL', event_id: str='E1', event_starts_at: str | None='2099-12-31T00:00:00Z', **kwargs) -> dict:
    d = {'market_id': market_id, 'event_id': event_id, 'event_starts_at': event_starts_at, 'market_type': market_type, 'proposition': proposition, 'market': proposition, 'scope': 'full_game', 'line': line, 'position': position, 'matchup': 'A @ B', 'books': books}
    d.update(kwargs)
    return d

def test_devig_book_pair_symmetric():
    pair = devig_book_pair(-110, -110)
    assert pair is not None
    p_over, p_under = pair
    assert abs(p_over + p_under - 1.0) < 0.01
    assert abs(p_over - 0.5) < 0.02

def test_aggregate_line_requires_two_books():
    over = {'dk': -110, 'fd': -108}
    under = {'dk': -110}
    p_over, count, flags = aggregate_line_p_over(over, under)
    assert count == 1
    assert 'SINGLE_BOOK' in flags

def test_aggregate_line_deduplicates_operator_aliases():
    over = {'BetRivers': -110, 'Unibet': -108, 'DraftKings': -105}
    under = {'BetRivers': -110, 'Unibet': -112, 'DraftKings': -115}
    p_over, count, flags = aggregate_line_p_over(over, under)
    assert p_over is not None
    assert count == 2
    assert flags == []

@pytest.mark.parametrize(('over_price', 'under_price'), [(-200, -200), (100, 100)])
def test_aggregate_line_rejects_invalid_overround(over_price, under_price):
    p_over, count, flags = aggregate_line_p_over({'DraftKings': over_price, 'FanDuel': over_price}, {'DraftKings': under_price, 'FanDuel': under_price})
    assert p_over is None
    assert count == 0
    assert 'NO_VALID_CONSENSUS' in flags

def test_aggregate_line_missing_side():
    p_over, count, flags = aggregate_line_p_over({'dk': -110}, {})
    assert p_over is None
    assert 'MISSING_SIDE' in flags

def test_interpolate_fair_total_brackets_half():
    ladder_p = {8.0: 0.58, 8.5: 0.42}
    fair, flags = interpolate_fair_total(ladder_p)
    assert not flags
    assert fair == 8.0 or fair == 8.5

def test_interpolate_fair_total_non_bracketing():
    fair, flags = interpolate_fair_total({8.0: 0.6, 8.5: 0.55})
    assert fair is None
    assert 'NON_BRACKETING_LADDER' in flags

def test_pick_best_side_prefers_higher_edge():
    side, price, edge = pick_best_side(0.55, -110, -110)
    assert side == 'OVER'
    assert edge is not None and edge > 0

def test_build_game_totals_actionable_at_three_pct_edge():
    games_norm = {'generated_at': '2026-07-07T12:00:00Z', 'records': [_norm_record('m1', 8.5, 'OVER', [{'book': 'DK', 'odds': -125}, {'book': 'FD', 'odds': -122}]), _norm_record('m1', 8.5, 'UNDER', [{'book': 'DK', 'odds': 105}, {'book': 'FD', 'odds': 102}]), _norm_record('m1', 9.0, 'OVER', [{'book': 'DK', 'odds': 110}, {'book': 'FD', 'odds': 108}]), _norm_record('m1', 9.0, 'UNDER', [{'book': 'DK', 'odds': -130}, {'book': 'FD', 'odds': -128}])]}
    candidates = [{'market_id': 'm1', 'market_type': 'GAMELINE', 'player_id': '', 'selection': 'A @ B Total O/U OVER 8.5', 'line': 8.5, 'price': -125, 'edge_pct': 0.05, '_proposition': 'TOTAL', '_event_starts_at': '2099-07-07T23:10:00+00:00'}]
    rows = build_game_totals(candidates, games_norm, sport='MLB', now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc))
    assert len(rows) == 1
    row = rows[0]
    assert row['fair_total'] != ''
    if row['edge_pct'] != '' and float(row['edge_pct']) >= MIN_EDGE_TOTALS:
        assert row['actionable'] == 'true'
        assert row['recommended_units_pre_news'] not in ('', None)
    expected_book = 'fd' if row['best_side'] == 'OVER' else 'dk'
    assert row['book'] == expected_book
    assert row['outcome_id'] == row['totals_id']
    assert row['independent_model_prob'] == ''
    side_probability = (
        row['projected_over_prob']
        if row['best_side'] == 'OVER'
        else row['projected_under_prob']
    )
    assert row['market_consensus_prob'] == side_probability
    assert row['final_blended_prob'] == side_probability

def test_build_game_totals_single_book_not_actionable():
    games_norm = {'records': [_norm_record('m2', 174.5, 'OVER', [{'book': 'DK', 'odds': -110}]), _norm_record('m2', 174.5, 'UNDER', [{'book': 'DK', 'odds': -110}])]}
    rows = build_game_totals([], games_norm, sport='WNBA')
    assert rows[0]['actionable'] == 'false'
    assert 'SINGLE_BOOK' in rows[0]['quality_flags']

def test_build_game_totals_integer_line_push_blocked():
    games_norm = {'records': [_norm_record('m3', 8.0, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -108}]), _norm_record('m3', 8.0, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -112}]), _norm_record('m3', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -108}]), _norm_record('m3', 8.5, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -112}])]}
    rows = build_game_totals([], games_norm, sport='MLB')
    row = next((r for r in rows if float(r['line']) == 8.0))
    assert row['sizing_flags'] == 'push_capable_no_prob'
    assert row['actionable'] == 'false'
    assert row['edge_pct'] == ''
    assert row['market_consensus_prob'] == ''
    assert row['independent_model_prob'] == ''
    assert row['final_blended_prob'] == ''

def test_build_game_totals_integer_line_with_push_prob():
    games_norm = {'records': [_norm_record('m3', 7.5, 'OVER', [{'book': 'DK', 'odds': -140}, {'book': 'FD', 'odds': -140}]), _norm_record('m3', 7.5, 'UNDER', [{'book': 'DK', 'odds': 120}, {'book': 'FD', 'odds': 120}]), _norm_record('m3', 8.0, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}]), _norm_record('m3', 8.0, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}]), _norm_record('m3', 8.5, 'OVER', [{'book': 'DK', 'odds': 110}, {'book': 'FD', 'odds': 110}]), _norm_record('m3', 8.5, 'UNDER', [{'book': 'DK', 'odds': -130}, {'book': 'FD', 'odds': -130}])]}
    candidates = [{'market_id': 'm3', 'line': 8.0, 'market_type': 'GAMELINE', '_proposition': 'TOTAL'}]
    rows = build_game_totals(candidates, games_norm, sport='MLB')
    row = next((r for r in rows if float(r['line']) == 8.0))
    assert row['sizing_flags'] == ''
    assert isinstance(row['push_prob'], float)
    assert row['push_prob'] > 0
    assert row['actionable'] == 'false'
    assert row['edge_pct'] != ''
    assert row['decimal_price'] not in (None, '')
    assert row['best_side'] in ('OVER', 'UNDER')
    side = row['best_side']
    p_over, _, _ = aggregate_line_p_over({'DK': -110, 'FD': -110}, {'DK': -110, 'FD': -110})
    assert p_over is not None
    p_side_conditional = p_over if side == 'OVER' else 1.0 - p_over
    decimal = float(row['decimal_price'])
    push = float(row['push_prob'])
    p_side = p_side_conditional * (1.0 - push)
    expected = compute_sizing(decimal_price=decimal, model_prob=p_side, push_prob=push)
    assert expected.edge_pct is not None
    assert float(row['edge_pct']) == pytest.approx(round(expected.edge_pct, 4))
    assert float(row['market_consensus_prob']) == pytest.approx(round(p_side, 4))
    two_way_edge, _ = compute_side_edge(side, p_over, row['best_price'])
    assert two_way_edge is not None
    assert float(row['edge_pct']) != pytest.approx(two_way_edge)

def test_build_game_totals_live_event_flag():
    games_norm = {'records': [_norm_record('m4', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -108}], event_starts_at='2020-01-01T00:00:00Z'), _norm_record('m4', 8.5, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -112}], event_starts_at='2020-01-01T00:00:00Z'), _norm_record('m4', 9.0, 'OVER', [{'book': 'DK', 'odds': 100}, {'book': 'FD', 'odds': 102}], event_starts_at='2020-01-01T00:00:00Z'), _norm_record('m4', 9.0, 'UNDER', [{'book': 'DK', 'odds': -120}, {'book': 'FD', 'odds': -122}], event_starts_at='2020-01-01T00:00:00Z')], 'generated_at': '2025-01-01T00:00:00Z'}
    past = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    candidates = [{'market_id': 'm4', 'market_type': 'GAMELINE', 'player_id': '', 'line': 8.5, '_proposition': 'TOTAL', '_event_starts_at': past}]
    rows = build_game_totals(candidates, games_norm, sport='MLB', now=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert 'LOCKED_OR_UNVERIFIED_EVENT' in rows[0]['quality_flags']
    assert rows[0]['actionable'] == 'false'

def test_build_game_totals_missing_start_fails_closed_without_candidate():
    now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
    games_norm = {'generated_at': now.isoformat(), 'records': [_norm_record('m6', 8.5, 'OVER', [{'book': 'DK', 'odds': -125}, {'book': 'FD', 'odds': -122}], event_starts_at=None), _norm_record('m6', 8.5, 'UNDER', [{'book': 'DK', 'odds': 105}, {'book': 'FD', 'odds': 102}], event_starts_at=None), _norm_record('m6', 9.0, 'OVER', [{'book': 'DK', 'odds': 110}, {'book': 'FD', 'odds': 108}], event_starts_at=None), _norm_record('m6', 9.0, 'UNDER', [{'book': 'DK', 'odds': -130}, {'book': 'FD', 'odds': -128}], event_starts_at=None)]}
    row = build_game_totals([], games_norm, sport='MLB', now=now)[0]
    assert 'LOCKED_OR_UNVERIFIED_EVENT' in row['quality_flags']
    assert row['actionable'] == 'false'

def test_build_game_totals_stale_source_fails_closed():
    now = datetime(2026, 7, 7, 12, tzinfo=timezone.utc)
    games_norm = {'generated_at': '2026-07-07T05:00:00Z', 'records': [_norm_record('m7', 8.5, 'OVER', [{'book': 'DK', 'odds': -125}, {'book': 'FD', 'odds': -122}]), _norm_record('m7', 8.5, 'UNDER', [{'book': 'DK', 'odds': 105}, {'book': 'FD', 'odds': 102}]), _norm_record('m7', 9.0, 'OVER', [{'book': 'DK', 'odds': 110}, {'book': 'FD', 'odds': 108}]), _norm_record('m7', 9.0, 'UNDER', [{'book': 'DK', 'odds': -130}, {'book': 'FD', 'odds': -128}])]}
    row = build_game_totals([], games_norm, sport='MLB', now=now)[0]
    assert 'STALE_DATA' in row['quality_flags']
    assert row['actionable'] == 'false'

def test_projected_over_prob_matches_headline_line():
    """projected_over_prob must be the headline line's p_over, not a stale
    leftover from the ladder-building loop. Here the headline (8.5) is inserted
    BEFORE 9.5, so a stale loop variable would report 9.5's probability and the
    over/under pair would not sum to 1 (regression guard for F2)."""
    games_norm = {'records': [_norm_record('m5', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}]), _norm_record('m5', 8.5, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}]), _norm_record('m5', 9.5, 'OVER', [{'book': 'DK', 'odds': 200}, {'book': 'FD', 'odds': 200}]), _norm_record('m5', 9.5, 'UNDER', [{'book': 'DK', 'odds': -250}, {'book': 'FD', 'odds': -250}])]}
    candidates = [{'market_id': 'm5', 'line': 8.5, 'market_type': 'GAMELINE', '_proposition': 'TOTAL'}]
    rows = build_game_totals(candidates, games_norm, sport='MLB')
    row = next((r for r in rows if float(r['line']) == 8.5))
    over = float(row['projected_over_prob'])
    under = float(row['projected_under_prob'])
    assert abs(over + under - 1.0) < 1e-06
    assert abs(over - 0.5) < 0.05

def test_build_market_ladder_groups_sides():
    records = [_norm_record('m1', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}]), _norm_record('m1', 8.5, 'UNDER', [{'book': 'DK', 'odds': -110}])]
    ladder = build_market_ladder(records)
    assert 8.5 in ladder
    assert 'dk' in ladder[8.5]['over']
    assert 'dk' in ladder[8.5]['under']

def test_eligibility_split_game_vs_team():
    game = _norm_record('g1', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}])
    team = _norm_record('t1', 4.5, 'OVER', [{'book': 'DK', 'odds': -110}], market_type='TEAM_PROP', proposition='POINTS')
    half = dict(game)
    half['scope'] = 'first_5_innings'
    assert is_game_total_record(game)
    assert not is_team_total_record(game)
    assert is_team_total_record(team)
    assert not is_game_total_record(team)
    assert not is_game_total_record(half)
    assert not is_team_total_record(half)
    assert is_eligible_total_record(game)
    assert is_eligible_total_record(team)
    assert is_eligible_total_record(game, kind=TOTAL_KIND_GAME)
    assert not is_eligible_total_record(team, kind=TOTAL_KIND_GAME)
    assert is_eligible_total_record(team, kind=TOTAL_KIND_TEAM)
    assert not is_eligible_total_record(game, kind=TOTAL_KIND_TEAM)

def test_build_game_totals_excludes_team_records():
    games_norm = {'records': [_norm_record('m_game', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}]), _norm_record('m_game', 8.5, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}]), _norm_record('m_team', 4.5, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}], market_type='TEAM_PROP', proposition='POINTS'), _norm_record('m_team', 4.5, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}], market_type='TEAM_PROP', proposition='POINTS')]}
    rows = build_game_totals([], games_norm, sport='MLB')
    assert all((r['total_kind'] == TOTAL_KIND_GAME for r in rows))
    assert all((r['market_id'] == 'm_game' for r in rows))
    assert not any((r['market_id'] == 'm_team' for r in rows))

def test_build_team_totals_shape():
    games_norm = {'generated_at': '2026-07-07T12:00:00Z', 'records': [_norm_record('m_team', 4.5, 'OVER', [{'book': 'DK', 'odds': -125}, {'book': 'FD', 'odds': -122}], market_type='TEAM_PROP', proposition='POINTS'), _norm_record('m_team', 4.5, 'UNDER', [{'book': 'DK', 'odds': 105}, {'book': 'FD', 'odds': 102}], market_type='TEAM_PROP', proposition='POINTS'), _norm_record('m_team', 5.0, 'OVER', [{'book': 'DK', 'odds': 110}, {'book': 'FD', 'odds': 108}], market_type='TEAM_PROP', proposition='POINTS'), _norm_record('m_team', 5.0, 'UNDER', [{'book': 'DK', 'odds': -130}, {'book': 'FD', 'odds': -128}], market_type='TEAM_PROP', proposition='POINTS'), _norm_record('m_game', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}]), _norm_record('m_game', 8.5, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}])]}
    rows = build_team_totals([], games_norm, sport='MLB', now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc))
    assert len(rows) == 1
    row = rows[0]
    assert row['total_kind'] == TOTAL_KIND_TEAM
    assert row['market_id'] == 'm_team'
    assert 'Team Total' in row['selection']

def test_period_identity_prefers_period_label_over_wrong_scope():
    rec = _norm_record('inn6', 1.5, 'OVER', [{'book': 'DK', 'odds': -110}], scope='full_game', period_label='6I', periods=[6], include_overtime=False)
    assert period_identity(rec) != 'full_game'
    assert not is_eligible_total_record(rec)

def test_period_identity_full_game_when_no_period_fields():
    rec = _norm_record('fg', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}])
    assert period_identity(rec) == 'full_game'
    assert is_eligible_total_record(rec)

def test_logical_market_key_separates_team_totals():
    home = _norm_record('t1', 88.5, 'OVER', [{'book': 'DK', 'odds': -110}], market_type='TEAM_PROP', proposition='POINTS', team='NYK')
    away = _norm_record('t2', 90.5, 'OVER', [{'book': 'DK', 'odds': -110}], market_type='TEAM_PROP', proposition='POINTS', team='BOS')
    assert logical_market_key(home) != logical_market_key(away)

def test_build_game_totals_excludes_period_pseudo_markets():
    """Inning/half totals must not appear as full-game board rows."""
    books2 = [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -108}]
    books2u = [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -112}]
    games_norm = {'generated_at': '2026-07-07T12:00:00Z', 'records': [_norm_record('fg-a', 8.5, 'OVER', books2, include_overtime=True), _norm_record('fg-a', 8.5, 'UNDER', books2u, include_overtime=True), _norm_record('fg-b', 9.0, 'OVER', books2, include_overtime=True), _norm_record('fg-b', 9.0, 'UNDER', books2u, include_overtime=True), _norm_record('inn6', 1.5, 'OVER', books2, period_label='6I', periods=[6], include_overtime=False), _norm_record('inn6', 1.5, 'UNDER', books2u, period_label='6I', periods=[6], include_overtime=False), _norm_record('f5', 4.5, 'OVER', books2, period_label='F5', periods=[1, 2, 3, 4, 5], include_overtime=False), _norm_record('f5', 4.5, 'UNDER', books2u, period_label='F5', periods=[1, 2, 3, 4, 5], include_overtime=False)]}
    rows = build_game_totals([], games_norm, sport='MLB', now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc))
    assert len(rows) == 1
    row = rows[0]
    assert float(row['line']) in (8.5, 9.0)
    assert row['market_id'] in {'fg-a', 'fg-b'}
    assert row['fair_total'] != '' or 'NON_BRACKETING_LADDER' in row['quality_flags']

def test_build_game_totals_merges_split_full_game_market_ids_into_one_ladder():
    """Same logical full-game total under two market_ids ΓåÆ one board row, merged lines."""
    games_norm = {'generated_at': '2026-07-07T12:00:00Z', 'records': [_norm_record('m-low', 8.0, 'OVER', [{'book': 'DK', 'odds': -140}, {'book': 'FD', 'odds': -140}]), _norm_record('m-low', 8.0, 'UNDER', [{'book': 'DK', 'odds': 120}, {'book': 'FD', 'odds': 120}]), _norm_record('m-high', 9.0, 'OVER', [{'book': 'DK', 'odds': 110}, {'book': 'FD', 'odds': 110}]), _norm_record('m-high', 9.0, 'UNDER', [{'book': 'DK', 'odds': -130}, {'book': 'FD', 'odds': -130}])]}
    rows = build_game_totals([], games_norm, sport='MLB', now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0]['fair_total'] != ''

def _l10_stats(home_hits: int, away_hits: int | None = None) -> dict:
    def blob(hits: int) -> dict:
        return {'l10': hits / 10.0, 'l10Results': [True] * hits + [False] * (10 - hits)}
    stats = {'homeSummaryStat': blob(home_hits)}
    if away_hits is not None:
        stats['awaySummaryStat'] = blob(away_hits)
    return stats

def test_build_game_totals_blends_l10_into_edge_and_columns():
    games_norm = {'generated_at': '2026-07-07T12:00:00Z', 'records': [_norm_record('m1', 8.5, 'OVER', [{'book': 'DK', 'odds': -120}, {'book': 'FD', 'odds': -120}], stats=_l10_stats(8, 6)), _norm_record('m1', 8.5, 'UNDER', [{'book': 'DK', 'odds': 100}, {'book': 'FD', 'odds': 100}])]}
    rows = build_game_totals([], games_norm, sport='MLB', now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc))
    assert len(rows) == 1
    row = rows[0]
    assert row['best_side'] == 'OVER'
    assert float(row['independent_model_prob']) == pytest.approx(0.7)
    consensus = float(row['market_consensus_prob'])
    blended = float(row['final_blended_prob'])
    assert blended == pytest.approx(round(0.75 * consensus + 0.25 * 0.7, 4), abs=1e-3)
    expected = compute_sizing(decimal_price=float(row['decimal_price']), model_prob=blended, push_prob=0.0)
    assert expected.edge_pct is not None
    assert float(row['edge_pct']) == pytest.approx(expected.edge_pct, abs=1e-3)

def test_build_game_totals_l10_can_flip_best_side_to_over():
    # Market prices favor UNDER; a 10/10 L10 over-record flips the pick.
    games_norm = {'generated_at': '2026-07-07T12:00:00Z', 'records': [_norm_record('m1', 8.5, 'OVER', [{'book': 'DK', 'odds': -125}, {'book': 'FD', 'odds': -125}], stats=_l10_stats(10, 10)), _norm_record('m1', 8.5, 'UNDER', [{'book': 'DK', 'odds': 105}, {'book': 'FD', 'odds': 105}])]}
    rows = build_game_totals([], games_norm, sport='MLB', now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc))
    row = rows[0]
    no_stats = {'generated_at': '2026-07-07T12:00:00Z', 'records': [_norm_record('m1', 8.5, 'OVER', [{'book': 'DK', 'odds': -125}, {'book': 'FD', 'odds': -125}]), _norm_record('m1', 8.5, 'UNDER', [{'book': 'DK', 'odds': 105}, {'book': 'FD', 'odds': 105}])]}
    market_row = build_game_totals([], no_stats, sport='MLB', now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc))[0]
    assert market_row['best_side'] == 'UNDER'
    assert market_row['independent_model_prob'] == ''
    assert row['best_side'] == 'OVER'

def test_implied_prob_scaling_and_rounding():
    games_norm = {'generated_at': '2026-07-07T12:00:00Z', 'records': [_norm_record('m_imp', 8.5, 'OVER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}]), _norm_record('m_imp', 8.5, 'UNDER', [{'book': 'DK', 'odds': -110}, {'book': 'FD', 'odds': -110}])]}
    candidates = [{'market_id': 'm_imp', 'market_type': 'GAMELINE', 'player_id': '', 'line': 8.5, '_proposition': 'TOTAL'}]
    rows = build_game_totals(candidates, games_norm, sport='MLB', now=datetime(2026, 7, 7, 13, tzinfo=timezone.utc))
    assert len(rows) == 1
    row = rows[0]
    # -110 implied prob is 52.380952...% -> 0.52380952...
    # So 0.52381 when rounded to 5 decimal places.
    assert row['implied_prob'] == 0.52381
