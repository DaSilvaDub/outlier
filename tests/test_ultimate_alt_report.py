import csv
from pathlib import Path

import pytest

from outlier_scrapers.ultimate_alt import ULTIMATE_ALT_HEADER, ULTIMATE_ALT_PARLAYS_HEADER
from outlier_scrapers.ultimate_alt_report import (
    ReportInputError,
    _parse_args,
    analyze_report,
    main,
    render_report,
    run_report,
)


def _leg(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in ULTIMATE_ALT_HEADER}
    row.update(
        {
            "sport": "MLB",
            "league": "MLB",
            "event_id": "event-1",
            "matchup": "TOR @ NYY",
            "alt_type": "TOTAL",
            "market_type": "TEAM_PROP",
            "market": "STRIKEOUTS",
            "selection": "NYY STRIKEOUTS OVER 7.5",
            "team": "NYY",
            "market_id": "market-1",
            "outcome_id": "outcome-1",
            "line": "7.5",
            "book": "DraftKings",
            "implied_prob": "0.52381",
            "conservative_prob": "0.700218",
            "edge_pct": "17.641",
            "ev_pct": "33.679",
            "shadow_status": "QUALIFIED",
            "portfolio_shadow_units": "0.5",
            "stable_wager_id": "stable-1",
            "actionable": "false",
            "board": "ALT_SHADOW_QUALIFIED",
            "scope": "full_game",
            "as_of": "2099-08-08T18:00:00Z",
        }
    )
    row.update(overrides)
    return row


def _parlay(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in ULTIMATE_ALT_PARLAYS_HEADER}
    row.update(
        {
            "rank": "1",
            "num_legs": "2",
            "legs": "NYY STRIKEOUTS OVER 7.5 | TB -1",
            "alt_types": "SPREAD,TOTAL",
            "event_ids": "event-1,event-2",
            "combined_decimal": "3.629",
            "combined_conservative_prob": "0.396797",
            "ev_pct": "43.998",
            "quality_flags": "CROSS_EVENT;SHADOW_ONLY",
        }
    )
    row.update(overrides)
    return row


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _happy_rows() -> list[dict[str, str]]:
    return [
        _leg(),
        _leg(
            event_id="event-2",
            matchup="BAL @ TB",
            alt_type="SPREAD",
            market_type="GAMELINE",
            market="SPREAD",
            selection="TB -1",
            team="TB",
            market_id="market-2",
            outcome_id="outcome-2",
            line="-1",
            edge_pct="4.061",
            ev_pct="7.719",
            stable_wager_id="stable-2",
        ),
        _leg(
            event_id="event-3",
            matchup="CWS @ DET",
            selection="DET STRIKEOUTS UNDER 8.5",
            team="DET",
            market_id="market-3",
            outcome_id="outcome-3",
            line="8.5",
            edge_pct="8.454",
            ev_pct="15.806",
            stable_wager_id="stable-3",
        ),
    ]


def test_report_selects_highest_ev_valid_supplied_parlay_and_stays_shadow_only() -> None:
    rows = _happy_rows()
    lower = _parlay(
        rank="2",
        legs="DET STRIKEOUTS UNDER 8.5 | TB -1",
        event_ids="event-3,event-2",
        ev_pct="24.746",
    )
    invalid = _parlay(
        rank="3",
        event_ids="event-1,event-1",
        ev_pct="99.0",
    )

    analysis = analyze_report(rows, [lower, invalid, _parlay()])
    report = render_report(
        analysis,
        pack_date="2099-08-08",
        source_prompt=r"C:\today\prompt.md",
    )

    assert analysis.verdict == "CONTINUE"
    assert analysis.best_parlay is not None
    assert analysis.best_parlay["ev_pct"] == "43.998"
    assert "Ready for manual promotion" not in report
    assert "does not authorize activation or promotion" in report
    assert "SHADOW VERDICT: CONTINUE" in report
    assert 'source_prompt: "C:\\\\today\\\\prompt.md"' in report


@pytest.mark.parametrize("units", ["", "0", "not-a-number", "NaN", "Infinity"])
def test_malformed_units_downgrade_only_the_candidate(units: str) -> None:
    rows = _happy_rows()
    rows[0]["portfolio_shadow_units"] = units

    analysis = analyze_report(rows, [])

    assert len(analysis.surviving) == 2
    assert analysis.qualified[0].ok is False
    assert any("PORTFOLIO_SHADOW_UNITS" in reason for reason in analysis.qualified[0].reasons)


def test_identity_and_positive_edge_ev_are_a_single_consistent_audit() -> None:
    rows = [
        _leg(stable_wager_id=""),
        _leg(
            event_id="event-2",
            market_id="market-2",
            outcome_id="outcome-2",
            selection="Pitcher ER OVER 0.5",
            alt_type="PLAYER_PROP",
            player="Pitcher",
            player_id="",
            stable_wager_id="stable-2",
        ),
        _leg(
            event_id="event-3",
            market_id="market-3",
            outcome_id="outcome-3",
            selection="TB -1",
            stable_wager_id="stable-3",
            edge_pct="0",
            ev_pct="-1",
        ),
    ]

    analysis = analyze_report(rows, [])
    report = render_report(analysis, pack_date="2099-08-08")

    assert not analysis.surviving
    assert "MISSING_IDENTITY:stable_wager_id" in report
    assert "MISSING_IDENTITY:player_id" in report
    assert "NONPOSITIVE_EDGE; NONPOSITIVE_EV" in report
    assert "Passed supplied-data audit" not in report
    assert analysis.verdict == "REVISE"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"quality_flags": "CROSS_EVENT"}, "MISSING_FLAG:SHADOW_ONLY"),
        ({"event_ids": "event-1,event-1"}, "EVENTS_NOT_DISTINCT"),
        ({"alt_types": "TOTAL"}, "INSUFFICIENT_ALT_TYPE_DIVERSITY"),
        ({"combined_decimal": "4.1"}, "COMBINED_DECIMAL_OUT_OF_RANGE"),
        ({"ev_pct": "2.99"}, "PARLAY_EV_BELOW_GATE"),
        ({"legs": "missing | TB -1"}, "LEG_NOT_UNIQUE_SURVIVOR"),
    ],
)
def test_noncanonical_parlays_fail_closed(overrides: dict[str, str], reason: str) -> None:
    analysis = analyze_report(_happy_rows(), [_parlay(**overrides)])

    assert analysis.best_parlay is None
    assert analysis.verdict == "REVISE"
    assert any(item.startswith(reason) for item in analysis.parlays[0].reasons)


def test_unknown_alt_type_makes_the_global_verdict_insufficient() -> None:
    analysis = analyze_report([_leg(alt_type="MYSTERY")], [])

    assert analysis.verdict == "INSUFFICIENT DATA"
    assert analysis.input_issues == ("alt row 2: unknown alt_type",)


def test_cli_requires_complete_schemas_and_preserves_existing_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    alt_path = tmp_path / "alt.csv"
    parlay_path = tmp_path / "parlays.csv"
    output = tmp_path / "report.md"
    alt_path.write_text("alt_type,shadow_status\nTOTAL,QUALIFIED\n", encoding="utf-8")
    _write_csv(parlay_path, ULTIMATE_ALT_PARLAYS_HEADER, [])
    output.write_text("previous-good-report\n", encoding="utf-8")

    exit_code = main(
        [
            "--alt-csv",
            str(alt_path),
            "--parlay-csv",
            str(parlay_path),
            "--output",
            str(output),
            "--pack-date",
            "2099-08-08",
        ]
    )

    assert exit_code == 2
    assert "missing required columns" in capsys.readouterr().err
    assert output.read_text(encoding="utf-8") == "previous-good-report\n"


def test_run_report_writes_parameterized_atomic_output(tmp_path: Path) -> None:
    alt_path = tmp_path / "ultimate_alt.csv"
    parlay_path = tmp_path / "ultimate_alt_parlays.csv"
    output = tmp_path / "reports" / "shadow.md"
    _write_csv(alt_path, ULTIMATE_ALT_HEADER, _happy_rows())
    _write_csv(parlay_path, ULTIMATE_ALT_PARLAYS_HEADER, [_parlay()])

    analysis = run_report(
        alt_csv=alt_path,
        parlay_csv=parlay_path,
        output=output,
        pack_date="2099-08-08",
        agent="CODEX",
        workflow="test",
    )

    report = output.read_text(encoding="utf-8")
    assert analysis.verdict == "CONTINUE"
    assert 'agent: "CODEX"' in report
    assert 'workflow: "test"' in report
    assert not list(output.parent.glob("*.tmp"))


def test_invalid_pack_date_does_not_publish(tmp_path: Path) -> None:
    with pytest.raises(ReportInputError, match="YYYY-MM-DD"):
        run_report(
            alt_csv=tmp_path / "missing.csv",
            parlay_csv=tmp_path / "also-missing.csv",
            output=tmp_path / "report.md",
            pack_date="08/08/2099",
        )


def test_cli_defaults_match_canonical_ultimate_alt_artifacts() -> None:
    args = _parse_args(["--output", "report.md", "--pack-date", "2099-08-08"])
    assert args.alt_csv == "ultimate_alt.csv"
    assert args.parlay_csv == "ultimate_alt_parlays.csv"
