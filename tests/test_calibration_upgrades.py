from __future__ import annotations

import json
from pathlib import Path

from outlier_scrapers import paths, slate_quality
from outlier_scrapers.pack_selection import _resolve_candidate_identity, build_row

PIVETTA_IL = (
    "SD: Nick Pivetta (60-Day IL; Right Forearm Strain; ret 2026-09-07): "
    "Pivetta has been cleared to take the mound in a big-league game for the first time since April 12, "
    "after throwing 4.1 scoreless innings in rehab."
)


def _so_row(*, player: str, injury_flags: str, event_starts_at: str | None = None) -> dict:
    row = {
        "sport": "MLB",
        "market_type": "SO",
        "player": player,
        "selection": f"{player} - Strikeouts OVER 3.5",
        "injury_flags": injury_flags,
    }
    if event_starts_at:
        row["_event_starts_at"] = event_starts_at
    return row


def _so_card(*, side: str, event_id: str, card_id: str, outcome_id: str) -> dict:
    return {
        "board": "A",
        "headline_side": side,
        "market": "SO",
        "market_type": "SO",
        "market_label": "Strikeouts",
        "proposition": "Strikeouts",
        "player": "Nick Pivetta",
        "player_id": "pivetta123",
        "team": "SD",
        "matchup": "WSH @ SD",
        "event_id": event_id,
        "sides": {
            side: {
                "line": 3.5,
                "outcome_id": outcome_id,
                "ev": {
                    "is_alt_line_fallback": False,
                    "devig_decimal": 2.0,
                    "best_ev_pct": 0.05,
                    "kelly_pct": 0.02,
                },
                "signal": {
                    "insight_component": 85.0,
                    "movement_corroboration": 0.0,
                    "orf_component": None,
                    "hit_pct": None,
                },
            }
        },
        "card_id": card_id,
    }


def _so_ev(*, market_id: str, outcome_id: str, event_id: str, side: str) -> list[dict]:
    return [
        {
            "event_id": event_id,
            "market_id": market_id,
            "outcome_id": outcome_id,
            "side": side,
            "current_line": 3.5,
            "book": "FD",
            "book_odds": 110,
            "book_decimal_odds": 2.1,
            "calculated_ev_pct": 0.05,
            "devig_decimal": 2.0,
        }
    ]


def _so_projection(*, outcome_id: str, event_id: str, market_id: str, side: str) -> dict:
    mean = 2.4 if side == "UNDER" else 4.6
    return {
        "status": "eligible",
        "outcome_id": outcome_id,
        "event_id": event_id,
        "market_id": market_id,
        "sport": "MLB",
        "distribution": {
            "mean": mean,
            "line": 3.5,
            "side": side,
            "win_prob": 0.62,
            "push_prob": 0.0,
            "variance": 1.2,
            "model_version": "test-il-return",
        },
        "feature_snapshot_hash": slate_quality.GAMELOG_FEATURE_HASH,
    }


def _build_pivetta_row(*, side: str, event_id: str, card_id: str, outcome_id: str) -> dict | None:
    card = _so_card(side=side, event_id=event_id, card_id=card_id, outcome_id=outcome_id)
    ev_records = _so_ev(market_id=card_id, outcome_id=outcome_id, event_id=event_id, side=side)
    return build_row(
        card,
        ev_records,
        {outcome_id: ev_records},
        sport="MLB",
        odds_ts="2026-09-07T12:00:00Z",
        norm_ts="2026-09-07T12:00:00Z",
        source_ts={},
        event_starts={event_id: "2026-09-07T20:00:00Z"},
        injuries={event_id: PIVETTA_IL},
        projections_by_outcome={
            outcome_id: _so_projection(
                outcome_id=outcome_id, event_id=event_id, market_id=card_id, side=side
            )
        },
        probable_pitchers={"SD": {"pitcher": "Nick Pivetta", "confirmed": True}},
    )


def test_pitcher_returning_from_il_detection():
    assert (
        slate_quality.pitcher_returning_from_il(
            _so_row(
                player="Nick Pivetta",
                injury_flags=PIVETTA_IL,
                event_starts_at="2026-09-07T20:00:00Z",
            )
        )
        is True
    )

    row_15d = _so_row(
        player="Shane Bieber",
        injury_flags=(
            "TOR: Shane Bieber (15-Day IL; Right Shoulder; ret 2026-09-08): "
            "Scheduled for first start off injured list on limited pitch count."
        ),
        event_starts_at="2026-09-08T20:00:00Z",
    )
    assert slate_quality.pitcher_returning_from_il(row_15d) is True

    row_healthy = _so_row(
        player="Trevor Rogers",
        injury_flags="BAL: Colin Selby (60-Day IL; Right Shoulder Surgery; ret 2027-05-01)",
        event_starts_at="2026-09-07T20:00:00Z",
    )
    assert slate_quality.pitcher_returning_from_il(row_healthy) is False

    row_out_season = _so_row(
        player="Spencer Strider",
        injury_flags="ATL: Spencer Strider (60-Day IL; Right Elbow Surgery; ret 2027-05-01)",
        event_starts_at="2026-09-07T20:00:00Z",
    )
    assert slate_quality.pitcher_returning_from_il(row_out_season) is False


def test_pitcher_returning_from_il_uses_ret_date_and_rejects_noise():
    slate = "2026-09-07T20:00:00Z"
    empty_note_today = _so_row(
        player="Nick Pivetta",
        injury_flags="SD: Nick Pivetta (60-Day IL; Right Forearm Strain; ret 2026-09-07)",
        event_starts_at=slate,
    )
    assert slate_quality.pitcher_returning_from_il(empty_note_today) is True

    rehab_assignment = _so_row(
        player="Spencer Strider",
        injury_flags=(
            "ATL: Spencer Strider (60-Day IL; Right Elbow Surgery; ret 2027-05-01): "
            "Continues a rehab assignment at Triple-A."
        ),
        event_starts_at=slate,
    )
    assert slate_quality.pitcher_returning_from_il(rehab_assignment) is False

    not_cleared = _so_row(
        player="Nick Pivetta",
        injury_flags=(
            "SD: Nick Pivetta (60-Day IL; Right Forearm Strain; ret 2026-09-07): "
            "Pivetta has not been cleared to take the mound."
        ),
        event_starts_at=slate,
    )
    assert slate_quality.pitcher_returning_from_il(not_cleared) is False

    mixed_case = _so_row(
        player="Shane Bieber",
        injury_flags=(
            "TOR: Shane Bieber (15-Day IL; Right Shoulder; ret 2026-09-08): "
            "scheduled for FIRST START off injured list on limited PITCH COUNT."
        ),
        event_starts_at="2026-09-08T20:00:00Z",
    )
    assert slate_quality.pitcher_returning_from_il(mixed_case) is True

    recent_ret = _so_row(
        player="Nick Pivetta",
        injury_flags="SD: Nick Pivetta (60-Day IL; Right Forearm Strain; ret 2026-09-04)",
        event_starts_at="2026-09-07T20:00:00Z",
    )
    assert slate_quality.pitcher_returning_from_il(recent_ret) is True

    stale_ret = _so_row(
        player="Nick Pivetta",
        injury_flags="SD: Nick Pivetta (60-Day IL; Right Forearm Strain; ret 2026-08-01)",
        event_starts_at="2026-09-07T20:00:00Z",
    )
    assert slate_quality.pitcher_returning_from_il(stale_ret) is False


def test_mlb_game_prop_hits_rejected_from_candidates():
    card = {
        "headline_side": "OVER",
        "market": "Hits",
        "market_type": "GAME_PROP",
        "market_label": "Hits",
        "proposition": "Hits",
        "sides": {"OVER": {"line": 1.5, "price": -105, "outcome_id": "out123"}},
        "card_id": "mkt123",
        "matchup": "CIN @ LAD",
    }
    identity = _resolve_candidate_identity(card, ev_records=[], by_outcome={}, sport="MLB")
    assert identity is None, "MLB GAME_PROP Hits must be rejected from candidate selection"


def test_mlb_first_inning_hits_rejected_from_candidates():
    card = {
        "headline_side": "OVER",
        "market": "Hits",
        "market_type": "GAME_PROP",
        "market_label": "1st Inning Hits",
        "proposition": "Hits",
        "period_label": "1I",
        "sides": {"OVER": {"line": 0.5, "price": -115, "outcome_id": "out_hits_1i"}},
        "card_id": "mkt_hits_1i",
        "matchup": "CIN @ LAD",
    }
    identity = _resolve_candidate_identity(card, ev_records=[], by_outcome={}, sport="MLB")
    assert identity is None, "first-inning Hits is not an NRFI/YRFI candidate"


def test_mlb_first_inning_game_prop_admitted():
    nrfi = {
        "headline_side": "UNDER",
        "market": "NRFI",
        "market_type": "GAME_PROP",
        "market_label": "1st Inning Runs",
        "proposition": "NRFI",
        "sides": {"UNDER": {"line": 0.5, "price": -120, "outcome_id": "out456"}},
        "card_id": "mkt456",
        "matchup": "NYY @ BOS",
        "scope": "full_game",
    }
    assert _resolve_candidate_identity(nrfi, ev_records=[], by_outcome={}, sport="MLB") is not None

    runs_only = {
        "headline_side": "UNDER",
        "market": "Runs",
        "market_type": "GAME_PROP",
        "market_label": "1st Inning Runs",
        "proposition": "Runs",
        "period_label": "1I",
        "sides": {"UNDER": {"line": 0.5, "price": -120, "outcome_id": "out_runs_1i"}},
        "card_id": "mkt_runs_1i",
        "matchup": "NYY @ BOS",
    }
    assert (
        _resolve_candidate_identity(runs_only, ev_records=[], by_outcome={}, sport="MLB")
        is not None
    )

    yrfi = {
        "headline_side": "OVER",
        "market": "YRFI",
        "market_type": "GAME_PROP",
        "market_label": "YRFI",
        "proposition": "YRFI",
        "sides": {"OVER": {"line": 0.5, "price": -110, "outcome_id": "out_yrfi"}},
        "card_id": "mkt_yrfi",
        "matchup": "NYY @ BOS",
    }
    assert _resolve_candidate_identity(yrfi, ev_records=[], by_outcome={}, sport="MLB") is not None


def test_pitcher_returning_from_il_over_disqualified():
    row = _build_pivetta_row(
        side="OVER", event_id="ev_piv", card_id="mkt_piv", outcome_id="out_piv_o"
    )

    assert row is not None
    assert slate_quality.PITCHER_RETURNING_FROM_IL in row["data_quality_flags"]
    assert "pitcher_rehab_pitch_limit" in row["sizing_flags"]
    assert row["actionable"] == "false"
    assert row["recommended_units_pre_news"] == ""
    assert row["board"] == "A_FLAGGED"
    assert row["projection_mean"] == 4.6


def test_pitcher_returning_from_il_under_not_disqualified():
    row = _build_pivetta_row(
        side="UNDER", event_id="ev_piv_u", card_id="mkt_piv_u", outcome_id="out_piv_u"
    )

    assert row is not None
    assert slate_quality.PITCHER_RETURNING_FROM_IL not in row["data_quality_flags"]
    assert "pitcher_rehab_context" in row["signal_flags"]
    assert "pitcher_rehab_pitch_limit" not in row["sizing_flags"]
    assert row["actionable"] == "true"
    assert row["board"] == "A"
    units = row["recommended_units_pre_news"]
    assert units not in ("", None)
    assert float(units) > 0


def test_live_stake_calibration_matches_policy_source_column():
    artifact = json.loads(
        (paths.PROJECT_ROOT / "calibration" / "stake_calibration.json").read_text(
            encoding="utf-8-sig"
        )
    )
    policy = json.loads(
        (paths.PROJECT_ROOT / "config" / "portfolio_risk.json").read_text(encoding="utf-8")
    )
    expected = policy["calibration"]["source_probability_column"]
    assert artifact["source_probability_column"] == expected
    assert artifact["artifact_version"] == "stake-cal-v1-131c319c2d5d"
    assert artifact["eligible_samples"] == 17817
    assert Path(policy["calibration"]["artifact_path"]).as_posix() == (
        "calibration/stake_calibration.json"
    )
