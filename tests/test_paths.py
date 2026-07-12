from outlier_scrapers.paths import league_paths


def test_league_paths_latest_helpers():
    lp = league_paths("MLB")
    assert lp.league == "MLB"
    assert lp.cards == lp.root / "cards"
    assert lp.games_normalized_latest() == lp.normalized / "mlb_games_latest.json"
    assert lp.props_normalized_latest() == lp.normalized / "mlb_props_latest.json"
    assert lp.games_enrichment_latest() == lp.normalized / "mlb_games_enrichment_latest.json"
    assert lp.games_line_movement_latest() == lp.normalized / "mlb_games_line_movement_latest.json"
    assert lp.line_movement_latest() == lp.normalized / "mlb_line_movement_latest.json"
    assert lp.cards_latest() == lp.cards / "mlb_cards_latest.json"
    assert lp.games_cards_latest() == lp.cards / "mlb_games_cards_latest.json"
