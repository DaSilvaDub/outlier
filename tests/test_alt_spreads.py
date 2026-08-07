from __future__ import annotations

import csv
from datetime import datetime

import pytest

from outlier_scrapers.alt_spreads import build_alt_spreads_board
from outlier_scrapers.pack import write_pack


NOW = datetime.fromisoformat("2099-12-31T10:00:00+00:00")


def _spread(
    *,
    league: str = "MLB",
    event_id: str = "e1",
    position: str = "HOME",
    team: str = "HOME",
    matchup: str = "AWAY @ HOME",
    line: object = 3.5,
    book: str = "Hard Rock",
    odds: int = -250,
    l5: float = 0.8,
    l10: float = 0.8,
) -> dict:
    side_key = "homeSummaryStat" if position == "HOME" else "awaySummaryStat"
    return {
        "league": league,
        "event_id": event_id,
        "event_starts_at": "2099-12-31T20:00:00+00:00",
        "market_id": f"m-{event_id}",
        "outcome_id": f"o-{event_id}-{position}",
        "market_type": "GAMELINE",
        "proposition": "SPREAD",
        "market": "SPREAD",
        "position": position,
        "line": line,
        "scope": "full_game",
        "period_label": None,
        "include_overtime": True,
        "is_active": True,
        "team": team,
        "matchup": matchup,
        "books": [{"book": book, "odds": odds}],
        "stats": {side_key: {"l5": l5, "l10": l10}},
    }


def _board(records: list[dict], league: str = "MLB") -> list[dict]:
    return build_alt_spreads_board(
        {"generated_at": "2099-12-31T12:00:00Z", "records": records},
        league=league,
        target_date="2099-12-31",
        now=NOW,
    )


def test_spread_board_is_exact_and_preserves_explicit_sign_and_selection():
    positive = _spread(event_id="positive", line=3.5)
    negative = _spread(event_id="negative", line=-1.5)
    moneyline = _spread(event_id="moneyline")
    moneyline.update(proposition="MONEYLINE", market="MONEYLINE", line=0)
    total = _spread(event_id="total")
    total.update(proposition="TOTAL", market="TOTAL", position="OVER", team="")
    team_prop = _spread(event_id="team-prop")
    team_prop.update(market_type="TEAM_PROP", proposition="RUNS", market="R")
    partial = _spread(event_id="partial")
    partial.update(scope="first_half", period_label="1H")

    rows = _board([positive, negative, moneyline, total, team_prop, partial])

    assert [(row["event_id"], row["signed_line"], row["selection"]) for row in rows] == [
        ("negative", "-1.5", "HOME -1.5"),
        ("positive", "+3.5", "HOME +3.5"),
    ]
    assert all(row["proposition"] == "SPREAD" for row in rows)


def test_spread_board_uses_selected_team_side_not_opponent_blob():
    accepted = _spread(event_id="accepted", position="HOME", team="HOME")
    accepted["stats"] = {
        "homeSummaryStat": {"l5": 0.75, "l10": 0.75},
        "awaySummaryStat": {"l5": 0.1, "l10": 0.1},
    }
    rejected = _spread(event_id="rejected", position="HOME", team="HOME")
    rejected["stats"] = {
        "homeSummaryStat": {"l5": 0.74, "l10": 0.74},
        "awaySummaryStat": {"l5": 1.0, "l10": 1.0},
    }

    rows = _board([accepted, rejected])

    assert [row["event_id"] for row in rows] == ["accepted"]
    assert rows[0]["l5_pct"] == 75.0
    assert rows[0]["l10_pct"] == 75.0


@pytest.mark.parametrize("book", ["Hard Rock", "Fanatics", "Midnite", "DraftKings", "Novig"])
def test_spread_board_accepts_each_allowed_book(book: str):
    assert len(_board([_spread(book=book)])) == 1


@pytest.mark.parametrize(
    ("event_id", "odds", "expected"),
    [
        ("min", -1000, True),
        ("max", -110, True),
        ("too-low", -1001, False),
        ("too-high", -109, False),
    ],
)
def test_spread_board_enforces_inclusive_odds_boundaries(
    event_id: str, odds: int, expected: bool
):
    assert bool(_board([_spread(event_id=event_id, odds=odds)])) is expected


def test_spread_board_excludes_hardrock_r_and_fails_closed_on_identity():
    invalid = [
        _spread(event_id="hardrock-r", book="Hardrock R"),
        _spread(event_id="bad-position", position="OVER"),
        _spread(event_id="team-mismatch", team="AWAY", position="HOME"),
        _spread(event_id="missing-line", line=None),
        _spread(event_id="missing-team", team=""),
        _spread(event_id="missing-start"),
        _spread(event_id="missing-market"),
        _spread(event_id="missing-outcome"),
        _spread(event_id="missing-stats"),
    ]
    invalid[5]["event_starts_at"] = ""
    invalid[6]["market_id"] = ""
    invalid[7]["outcome_id"] = ""
    invalid[8]["stats"] = {}

    assert _board(invalid) == []


def test_write_pack_emits_spread_only_csvs_without_changing_mixed_bankroll(tmp_path):
    mlb_spread = _spread(event_id="mlb-spread")
    mlb_moneyline = _spread(event_id="mlb-moneyline")
    mlb_moneyline.update(proposition="MONEYLINE", market="MONEYLINE", line=0)
    wnba_spread = _spread(
        league="WNBA",
        event_id="wnba-spread",
        position="AWAY",
        team="AWAY",
        line=-2.5,
    )
    out_dir = tmp_path / "pack"

    write_pack(
        [],
        out_dir,
        games_norm_by_league={
            "MLB": {"generated_at": "now", "records": [mlb_spread, mlb_moneyline]},
            "WNBA": {"generated_at": "now", "records": [wnba_spread]},
        },
        target_date="2099-12-31",
    )

    with (out_dir / "mlb_alt_spreads.csv").open(newline="", encoding="utf-8") as handle:
        mlb_rows = list(csv.DictReader(handle))
    with (out_dir / "wnba_alt_spreads.csv").open(newline="", encoding="utf-8") as handle:
        wnba_rows = list(csv.DictReader(handle))
    with (out_dir / "mlb_alt_bankroll_props.csv").open(newline="", encoding="utf-8") as handle:
        mixed_rows = list(csv.DictReader(handle))

    assert [(row["league"], row["selection"]) for row in mlb_rows] == [
        ("MLB", "HOME +3.5")
    ]
    assert [(row["league"], row["selection"]) for row in wnba_rows] == [
        ("WNBA", "AWAY -2.5")
    ]
    assert {row["proposition"] for row in mixed_rows} == {"MONEYLINE", "SPREAD"}
