"""F26: duplicate and schema checks with enforced keys and ownership (#226)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from outlier_nfl import boxscore_nflverse, tape_nflverse
from outlier_nfl.boxscore import BoxScoreError
from outlier_nfl.normalizer import build_schedule_index
from outlier_nfl.props import extract_player_props
from outlier_nfl.schema import validate_normalized_dataset

GAMES = [{"game_id": "2026_01_BUF_KC", "season": "2026", "week": "1", "gameday": "2026-09-13",
          "game_type": "REG", "away_team": "BUF", "home_team": "KC", "away_score": "20",
          "home_score": "27"}]


def _team(team: str, opp: str, rush: str) -> dict[str, str]:
    return {"game_id": "2026_01_BUF_KC", "season": "2026", "week": "1", "season_type": "REG",
            "team": team, "opponent_team": opp, "rushing_yards": rush, "passing_yards": "200",
            "def_sacks": "2", "attempts": "30", "sacks_suffered": "2"}


KC, BUF = _team("KC", "BUF", "120"), _team("BUF", "KC", "90")


def test_identical_duplicate_team_game_counts_once() -> None:
    per = tape_nflverse.build_per_game([KC, dict(KC), BUF], GAMES, 2026)
    assert len(per["KC"]) == 1 and len(per["BUF"]) == 1


def test_conflicting_duplicate_team_game_drops_the_game() -> None:
    per = tape_nflverse.build_per_game([KC, _team("KC", "BUF", "150"), BUF], GAMES, 2026)
    assert per == {}


def test_asymmetric_opponent_rows_are_refused() -> None:
    odd = _team("BUF", "MIA", "90")  # BUF's row names a different opponent
    assert tape_nflverse.build_per_game([KC, odd], GAMES, 2026) == {}


@pytest.mark.parametrize("which", ["team", "game"])
def test_missing_tape_header_is_an_error(which: str) -> None:
    team_rows = [{k: v for k, v in r.items() if k != "opponent_team"} for r in (KC, BUF)] \
        if which == "team" else [KC, BUF]
    game_rows = [{k: v for k, v in GAMES[0].items() if k != "home_score"}] \
        if which == "game" else GAMES
    with pytest.raises(tape_nflverse.TapeSchemaError):
        tape_nflverse.build_per_game(team_rows, game_rows, 2026)


def _csv(path: Path, rows: list[dict[str, str]]) -> Path:
    cols = list(dict.fromkeys(c for r in rows for c in r))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return path


def _stat(yds: str) -> dict[str, str]:
    return {"game_id": "2026_01_BUF_KC", "season": "2026", "week": "1", "season_type": "REG",
            "player_id": "00-K", "player_display_name": "Travis Kelce", "team": "KC",
            "receiving_yards": yds, "receptions": "4"}


def _load(tmp_path: Path, rows: list[dict[str, str]]) -> dict[str, Any]:
    ev = boxscore_nflverse.load_nflverse_events(
        season=2026, week=1, cache_dir=tmp_path, games_csv=_csv(tmp_path / "g.csv", GAMES),
        stats_csv=_csv(tmp_path / "s.csv", rows))
    return ev[0].players


def test_stats_without_game_id_column_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(BoxScoreError, match="game_id"):
        _load(tmp_path, [{k: v for k, v in _stat("70").items() if k != "game_id"}])


def test_identical_duplicate_stat_row_counts_once(tmp_path: Path) -> None:
    assert _load(tmp_path, [_stat("70"), _stat("70")])["TRAVISKELCE"]["RECEIVING:YDS"] == 70.0


def test_conflicting_duplicate_stat_rows_drop_the_player(tmp_path: Path) -> None:
    assert _load(tmp_path, [_stat("70"), _stat("95")]) == {}


SCHED = {"events": [{"eventId": "ev1", "home": {"teamId": "kc", "alias": "KC"},
                     "away": {"teamId": "buf", "alias": "BUF"}}]}


def _quote(odds: int) -> dict[str, Any]:
    return {"outcome": {"eventId": "ev1", "marketId": "m", "outcomeId": "o", "teamId": "kc",
                        "proposition": "RECEIVING_YARDS", "position": "OVER", "line": 60.5,
                        "playerName": "Travis Kelce", "playerId": "p-k", "bestOdds": odds}}


def test_props_dedupe_identical_and_drop_conflicting() -> None:
    idx = build_schedule_index(SCHED)
    assert len(extract_player_props({"props": [_quote(-110), _quote(-110)]}, idx)) == 1
    assert extract_player_props({"props": [_quote(-110), _quote(120)]}, idx) == []


def _rec(odds: int, team: str = "KC") -> dict[str, Any]:
    return {"event_id": "ev1", "player_name": "Travis Kelce", "player_id": "p-k",
            "market": "REC_YDS", "line": 60.5, "position": "OVER", "team": team,
            "opponent": "BUF", "matchup": "BUF @ KC", "implied_probability": 52.4,
            "books": [{"book": "DRAFTKINGS", "odds": odds}], "best_odds": odds}


def test_normalized_validation_enforces_keys_and_ownership() -> None:
    assert validate_normalized_dataset([_rec(-110)], dataset_type="props") == []
    dup = validate_normalized_dataset([_rec(-110), _rec(120)], dataset_type="props")
    assert len(dup) == 1 and "duplicate key" in dup[0]
    foreign = validate_normalized_dataset([_rec(-110, team="MIA")], dataset_type="props")
    assert len(foreign) == 1 and "not in event" in foreign[0]


@pytest.mark.parametrize("matchup", ["Buffalo Bills @ Kansas City Chiefs", "buf-bills @ kc-chiefs",
                                     "BUF @ KC", "Bills @ Chiefs"])
def test_ownership_resolves_full_name_slug_and_alias_matchups(matchup: str) -> None:
    rec = {**_rec(-110), "matchup": matchup}
    assert validate_normalized_dataset([rec], dataset_type="props") == []


def test_ownership_prefers_carried_event_teams_and_still_fails_a_foreign_team() -> None:
    rec = {**_rec(-110, team="MIA"), "matchup": "Buffalo Bills @ Kansas City Chiefs"}
    assert "not in event" in validate_normalized_dataset([rec], dataset_type="props")[0]
    carried = {**_rec(-110), "matchup": "garbled", "home_team": "KC", "away_team": "BUF"}
    assert validate_normalized_dataset([carried], dataset_type="props") == []


def test_unresolvable_matchup_skips_the_ownership_check() -> None:
    rec = {**_rec(-110, team="MIA"), "matchup": "Team One @ Team Two"}
    assert validate_normalized_dataset([rec], dataset_type="props") == []


def test_extracted_props_carry_normalized_event_teams() -> None:
    sched = {"events": [{"eventId": "ev1", "home": {"teamId": "kc", "name": "Kansas City Chiefs"},
                         "away": {"teamId": "buf", "name": "Buffalo Bills"}}]}
    prop = extract_player_props({"props": [_quote(-110)]}, build_schedule_index(sched))[0]
    assert (prop.home_team, prop.away_team) == ("KC", "BUF")
    assert validate_normalized_dataset([prop.to_dict()], dataset_type="props") == []


def test_main_and_alt_listing_at_same_price_is_one_quote() -> None:
    from outlier_nfl.schema import dedupe_quotes, player_prop_key

    main = {**_rec(-110), "market_raw": "RECEIVING_YARDS", "market_id": "m-main",
            "outcome_id": "o-main", "is_consensus_line": False}
    alt = {**main, "market_raw": "ALT_RECEIVING_YARDS", "market_id": "m-alt",
           "outcome_id": "o-alt", "is_consensus_line": True}
    counts: dict[str, int] = {}
    kept = dedupe_quotes([main, alt], player_prop_key, "player_prop", counts)
    assert kept == [alt]  # the consensus copy wins
    assert counts == {"identical_player_prop": 1, "conflicting_player_prop": 0}
    assert dedupe_quotes([main, {**alt, "best_odds": 120,
                                 "books": [{"book": "DRAFTKINGS", "odds": 120}]}],
                         player_prop_key, "player_prop", counts) == []
    assert counts["conflicting_player_prop"] == 2


def test_drop_counts_reach_an_informational_receipt() -> None:
    from collections import Counter

    from outlier_nfl.stage_receipts import failing, quote_integrity_receipt, run_status

    counts: Counter[str] = Counter()
    props = [_quote(-110), _quote(120), {"outcome": {**_quote(-110)["outcome"], "eventId": ""}},
             {"outcome": {**_quote(-110)["outcome"], "teamId": "MIA", "outcomeId": "x"}}]
    assert extract_player_props({"props": props}, build_schedule_index(SCHED), counts) == []
    r = quote_integrity_receipt(counts)
    assert r.status == "OK" and r.required is False and r.received == 4
    assert r.errors == ["conflicting_player_prop=2", "no_event_id=1", "team_not_in_event=1"]
    assert failing([r]) == [] and run_status([r]) == "OK"
    assert quote_integrity_receipt(Counter()).to_dict()["received"] == 0
