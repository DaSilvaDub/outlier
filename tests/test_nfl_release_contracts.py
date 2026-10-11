"""Phase 9a (#231 part a): gap tests for report section 9 categories 1, 2, 5 and 7.

Test-only. Every fixture names the real source shape it was checked against:

- Normalized artifacts (``data/NFL/normalized/nfl_<kind>_<date>.json``) declare
  ``count`` next to ``records`` (and ``actionable_count`` next to
  ``actionable_records`` for high-prob props); ``summary_<date>.json`` declares
  ``*_count`` fields; the best-bets card declares ``counts`` by verdict next to
  ``picks``. Checked against the harness capture of the 2026-09-13 fixture slate.
- Snapshot JSONL rows (``data/NFL/snapshots/nfl_prop_snapshots_<Tuesday>.jsonl``)
  carry ``run_id`` (optional: legacy rows have none), ``taken_at``, ``event_id``,
  ``player_name``, ``market``, ``position``, ``scope``, ``line``, ``books``.
  Checked against the frozen Week 4 replay capture.
- Ledger rows are ``scorecard.GradedSignal`` dicts with ``date``, ``event_id``
  and optional ``run_id``.
- Book-close rows are the ``fetch_odds_close.map_event_odds_to_close_records``
  shape: ``close_source='book_close'``, ``close_status='verified'``,
  ``captured_at``, ``close_line``/``close_odds``/``close_implied``.
- Outlier prop ``stats`` ship ratios only (``l5``/``l10``/``l20``/``h2h``/
  ``curSeason``/``prevSeason``); the empirical model reads those, never a close.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from outlier_nfl import best_bets as bb, scorecard
from outlier_nfl.enrich_close import enrich_prediction_payload, index_book_close_records
from outlier_nfl.best_bets import american_to_decimal
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.snapshots import append_snapshot

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "nfl"
SLATE = "2026-09-13"


@pytest.fixture(scope="module")
def fixture_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("slate")
    NflPipeline(data_dir=root).run(date=SLATE, offline_fixtures_dir=FIXTURES_DIR,
                                   write_latest=False, reports_dir=root / "reports")
    return root / "NFL"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- Category 1: offered-price EV ---------------------------------------------

@pytest.mark.parametrize("odds", [-110, +150, -250])
def test_price_pillar_ev_is_at_the_offered_price(odds):
    """EV uses the quoted best price (Outlier ``bestOdds``), not the de-vigged fair price."""
    pillar = bb._price({"best_odds": odds, "line": 4.5, "position": "OVER"}, 0.6, {})
    dec = american_to_decimal(odds)
    assert pillar.evidence["decimal"] == round(dec, 4)
    assert pillar.evidence["ev_per_unit"] == pytest.approx(bb.push_aware_ev(0.6, 0.0, dec), abs=1e-4)


def test_push_aware_ev_never_counts_push_mass_as_win_or_loss():
    # Integer line: a push returns the stake, so it adds nothing either way.
    assert bb.push_aware_ev(0.4, 0.3, 2.0) == pytest.approx(0.4 * 1.0 - 0.3)
    assert bb.push_aware_ev(0.4, 0.6, 2.0) == pytest.approx(0.4)  # loss mass floors at 0


# --- Category 2: declared counts, two teams per game ----------------------------

def test_every_declared_count_matches_its_rows(fixture_run):
    norm = fixture_run / "normalized"
    checked = 0
    for path in sorted(norm.glob(f"nfl_*_{SLATE}.json")):
        data = _load(path)
        if not isinstance(data, dict):
            continue
        for count_key, rows_key in (("count", "records"),
                                    ("actionable_count", "actionable_records")):
            if count_key in data and rows_key in data:
                assert data[count_key] == len(data[rows_key]), path.name
                checked += 1
    card = _load(norm / f"nfl_best_bets_{SLATE}.json")
    assert sum(card["counts"].values()) == len(card["picks"])
    summary = _load(norm / f"summary_{SLATE}.json")
    assert summary["player_props_count"] == _load(norm / f"nfl_props_{SLATE}.json")["count"]
    assert summary["game_lines_count"] == _load(norm / f"nfl_games_{SLATE}.json")["count"]
    assert checked >= 5


def test_every_game_has_two_distinct_teams_and_props_belong_to_them(fixture_run):
    norm = fixture_run / "normalized"
    teams: dict[str, set[str]] = {}
    for g in _load(norm / f"nfl_games_{SLATE}.json")["records"]:
        home, away = g.get("home_team"), g.get("away_team")
        assert home and away and home != away, g
        seen = teams.setdefault(g["event_id"], {home, away})
        assert seen == {home, away}, g["event_id"]  # every line agrees on the pairing
    for p in _load(norm / f"nfl_props_{SLATE}.json")["records"]:
        assert p["event_id"] in teams
        if p.get("team"):
            assert p["team"] in teams[p["event_id"]], p


# --- Category 5: incremental / repeated updates ----------------------------------

_VOLATILE = ("run_id", "updated_at", "generated_at", "created_utc", "run_started_utc",
             "as_of_utc", "taken_at", "fetched_at_utc", "now_utc", "first_seen", "last_seen")


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()
                if not any(k == v_ or k.endswith(v_) for v_ in _VOLATILE)}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def test_repeated_run_on_the_same_inputs_has_identical_semantic_outputs(fixture_run,
                                                                        tmp_path):
    """Same inputs, a new run: every record (not the run's own id/clock) is identical."""
    NflPipeline(data_dir=tmp_path).run(date=SLATE, offline_fixtures_dir=FIXTURES_DIR,
                                       write_latest=False, reports_dir=tmp_path / "reports")
    for kind in ("props", "games", "calibrated_props", "matchup_scripts", "high_prob_props"):
        name = f"nfl_{kind}_{SLATE}.json"
        a = _load(fixture_run / "normalized" / name)
        b = _load(tmp_path / "NFL" / "normalized" / name)
        assert _scrub(a.get("records")) == _scrub(b.get("records")), name
    card_a = _load(fixture_run / "normalized" / f"nfl_best_bets_{SLATE}.json")
    card_b = _load(tmp_path / "NFL" / "normalized" / f"nfl_best_bets_{SLATE}.json")
    assert card_a["counts"] == card_b["counts"]
    assert [(p["player_name"], p["market"], p["verdict"]) for p in card_a["picks"]] == \
        [(p["player_name"], p["market"], p["verdict"]) for p in card_b["picks"]]


def _graded(run_id, actual=5.0):
    return scorecard.GradedSignal(
        date=SLATE, week=1, event_id="e1", tag="SCRIPT", player="Travis Kelce", team="KC",
        market="REC", side="OVER", prior_avg=4.0, actual=actual, hit_vs_avg=actual > 4.0,
        line=4.5, hit_vs_line=actual > 4.5, run_id=run_id)


def test_changed_run_id_replaces_the_ledger_observation_coherently(tmp_path):
    path = tmp_path / "ledger.jsonl"
    scorecard.update_ledger(path, [_graded("RUN-1", 5.0)], SLATE)
    rows = scorecard.update_ledger(path, [_graded("RUN-2", 3.0)], SLATE)
    assert [(r["run_id"], r["actual"], r["hit_vs_line"]) for r in rows] == [("RUN-2", 3.0, False)]


def test_concurrent_snapshot_appends_lose_no_rows(tmp_path):
    def prop(i):
        return {"is_consensus_line": True, "scope": "full_game", "event_id": f"e{i}",
                "player_name": f"Player {i}", "market": "REC", "position": "OVER",
                "line": 4.5, "best_odds": -110, "books": [{"book": "fanduel", "odds": -110}]}

    def run(i):
        append_snapshot(tmp_path, SLATE, [prop(i), prop(i + 100)],
                        "2026-09-13T15:00:00+00:00", run_id=f"RUN-{i}")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    [path] = list((tmp_path / "snapshots").glob("*.jsonl"))
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
    assert len(rows) == 24 and len({r["run_id"] for r in rows}) == 12


# --- Category 7: a retrospective close never changes the prediction --------------

PRED = {"player_name": "Patrick Mahomes", "market": "PASS_YDS", "line": 250.5,
        "position": "OVER", "matchup": "BAL @ KC", "event_id": "e1", "best_odds": -110,
        "implied_probability": 52.38, "l10_hit_rate": 0.7, "season_hit_rate": 0.72,
        "books": [{"book": "fanduel", "odds": -110}]}
CLOSE = {"player_name": "Patrick Mahomes", "market": "PASS_YDS", "line": 250.5,
         "position": "OVER", "matchup": "BAL @ KC", "event_id": "e1", "close_line": 250.5,
         "close_odds": -150, "close_implied": 60.0, "close_source": "book_close",
         "close_status": "verified", "captured_at": "2026-09-13T16:55:00Z"}
PREDICTION_FIELDS = ("model_p", "model_p_source", "sportsbook_edge_pts", "line", "position",
                     "best_odds", "implied_probability", "sportsbook_implied_probability")


@pytest.mark.parametrize("model", ["empirical_hit_rate_laplace",
                                   "empirical_hit_rate_market_prior", "empirical_hit_rate"])
def test_verified_close_never_changes_prediction_fields(model):
    plain = enrich_prediction_payload({"records": [dict(PRED)]}, mode="explicit",
                                      attach_model_p=model)["records"][0]
    closed = enrich_prediction_payload({"records": [dict(PRED)]}, mode="book_close",
                                       attach_model_p=model,
                                       book_close_index=index_book_close_records([CLOSE]))
    rec = closed["records"][0]
    assert rec["close_source"] == "book_close" and rec["close_odds"] == -150  # attached
    assert {k: rec.get(k) for k in PREDICTION_FIELDS} == \
        {k: plain.get(k) for k in PREDICTION_FIELDS}
