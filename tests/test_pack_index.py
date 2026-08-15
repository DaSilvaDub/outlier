"""Tests for outlier_scrapers.pack_index: the authoritative pack snapshot
verdict_gate.py consults for pack truth. See docs/plans/2026-08-12-structured-ai-verdicts.md
'Authoritative index' section.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta

import pytest

from outlier_scrapers import pack, pack_index
from outlier_scrapers.game_totals import GAME_TOTALS_HEADER, TOTAL_KIND_GAME, TOTAL_KIND_TEAM

FUTURE = (datetime.now().astimezone() + timedelta(hours=6)).isoformat()
PAST = (datetime.now().astimezone() - timedelta(hours=1)).isoformat()


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})


def candidate_row(**overrides):
    row = {k: "" for k in pack.CANDIDATES_HEADER}
    row.update(
        {
            "sport": "MLB",
            "event_id": "evt1",
            "_event_starts_at": FUTURE,
            "market_id": "mkt1",
            "outcome_id": "out1",
            "market_type": "PLAYER_PROP",
            "player_id": "p1",
            "selection": "Player One Over 5.5",
            "line": "5.5",
            "price": "-110",
            "book": "FD",
            "board": "A",
            "actionable": "true",
        }
    )
    row.update(overrides)
    return row


def totals_row(*, kind=TOTAL_KIND_GAME, **overrides):
    row = {k: "" for k in GAME_TOTALS_HEADER}
    row.update(
        {
            "totals_id": "mkt_t1:8.5:OVER",
            "sport": "MLB",
            "event_id": "evt2",
            "market_id": "mkt_t1",
            "outcome_id": "mkt_t1:8.5:OVER",
            "total_kind": kind,
            "team": "" if kind == TOTAL_KIND_GAME else "NYY",
            "selection": "Over 8.5",
            "line": "8.5",
            "price": "-110",
        }
    )
    row.update(overrides)
    return row


def write_pack(
    pack_dir,
    *,
    candidates=None,
    game_totals=None,
    team_totals=None,
    write_candidates=True,
    write_game_totals=True,
    write_team_totals=True,
):
    pack_dir.mkdir(parents=True, exist_ok=True)
    if write_candidates:
        _write_csv(pack_dir / "candidates.csv", pack.CANDIDATES_HEADER, candidates or [candidate_row()])
    if write_game_totals:
        _write_csv(
            pack_dir / "game_totals.csv",
            GAME_TOTALS_HEADER,
            game_totals if game_totals is not None else [totals_row()],
        )
    if write_team_totals:
        _write_csv(
            pack_dir / "team_totals.csv",
            GAME_TOTALS_HEADER,
            team_totals if team_totals is not None else [totals_row(kind=TOTAL_KIND_TEAM, outcome_id="mkt_t2:8.5:OVER", totals_id="mkt_t2:8.5:OVER", market_id="mkt_t2")],
        )


def _policy_path(tmp_path):
    # A nonexistent path, so load_portfolio_policy falls back to defaults --
    # keeps pack_index tests isolated from the real repo's config/portfolio_risk.json.
    return tmp_path / "no_such_policy.json"


# ---------------------------------------------------------------------------
# Green path
# ---------------------------------------------------------------------------


def test_build_pack_index_green_path(tmp_path):
    pack_dir = tmp_path / "pack"
    write_pack(pack_dir)
    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))

    assert "out1" in index.rows
    assert index.rows["out1"].stream == "candidates"
    assert index.rows["out1"].market_id == "mkt1"
    assert index.rows["out1"].data["selection"] == "Player One Over 5.5"

    assert "mkt_t1:8.5:OVER" in index.rows
    assert index.rows["mkt_t1:8.5:OVER"].stream == "game_totals"
    assert "mkt_t2:8.5:OVER" in index.rows
    assert index.rows["mkt_t2:8.5:OVER"].stream == "team_totals"

    assert isinstance(index.candidates_sha256, str) and len(index.candidates_sha256) == 64
    assert isinstance(index.game_totals_sha256, str) and len(index.game_totals_sha256) == 64
    assert isinstance(index.team_totals_sha256, str) and len(index.team_totals_sha256) == 64


def test_policy_is_loaded(tmp_path):
    pack_dir = tmp_path / "pack"
    write_pack(pack_dir)
    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert index.policy.max_wager_units == 3.0  # PortfolioPolicy default


# ---------------------------------------------------------------------------
# Empty outcome_id totals rows (INSUFFICIENT_DATA path)
# ---------------------------------------------------------------------------


def test_two_empty_outcome_id_totals_rows_do_not_raise(tmp_path):
    pack_dir = tmp_path / "pack"
    empty_a = totals_row(outcome_id="", totals_id="mkt_a", market_id="mkt_a")
    empty_b = totals_row(outcome_id="", totals_id="mkt_b", market_id="mkt_b")
    write_pack(pack_dir, game_totals=[empty_a, empty_b])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert "" not in index.rows
    unindexed_ids = {u.totals_id for u in index.unindexed_totals}
    assert unindexed_ids == {"mkt_a", "mkt_b"}


def test_unindexed_totals_from_team_stream_tagged_correctly(tmp_path):
    pack_dir = tmp_path / "pack"
    empty_team = totals_row(kind=TOTAL_KIND_TEAM, outcome_id="", totals_id="mkt_c", market_id="mkt_c")
    write_pack(pack_dir, team_totals=[empty_team])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    matches = [u for u in index.unindexed_totals if u.totals_id == "mkt_c"]
    assert len(matches) == 1
    assert matches[0].stream == "team_totals"


# ---------------------------------------------------------------------------
# Duplicate outcome_id integrity error
# ---------------------------------------------------------------------------


def test_duplicate_outcome_id_within_totals_raises_pack_integrity_error(tmp_path):
    pack_dir = tmp_path / "pack"
    row_a = totals_row(outcome_id="dup", totals_id="dup")
    row_b = totals_row(outcome_id="dup", totals_id="dup", line="9.5")
    write_pack(pack_dir, game_totals=[row_a, row_b])

    with pytest.raises(pack_index.PackIntegrityError, match="dup"):
        pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))


def test_duplicate_outcome_id_across_streams_raises(tmp_path):
    # A candidates outcome_id colliding with a totals outcome_id is still an
    # integrity error -- the check spans every stream, not just within one.
    pack_dir = tmp_path / "pack"
    cand = candidate_row(outcome_id="shared_id")
    totals = totals_row(outcome_id="shared_id", totals_id="shared_id")
    write_pack(pack_dir, candidates=[cand], game_totals=[totals])

    with pytest.raises(pack_index.PackIntegrityError, match="shared_id"):
        pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))


def test_both_sides_of_one_market_id_survive_distinctly(tmp_path):
    # Regression guard for the c_research._market_index collision this
    # index replaces: two outcome_ids sharing one market_id (OVER/UNDER of
    # the same market) must both survive in `rows`, not overwrite each other.
    pack_dir = tmp_path / "pack"
    over = candidate_row(market_id="shared_market", outcome_id="over_side", selection="Over 5.5")
    under = candidate_row(market_id="shared_market", outcome_id="under_side", selection="Under 5.5")
    write_pack(pack_dir, candidates=[over, under])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert index.rows["over_side"].data["selection"] == "Over 5.5"
    assert index.rows["under_side"].data["selection"] == "Under 5.5"
    assert index.rows["over_side"].market_id == index.rows["under_side"].market_id == "shared_market"


# ---------------------------------------------------------------------------
# Locked candidates
# ---------------------------------------------------------------------------


def test_locked_candidate_goes_to_dropped_not_rows(tmp_path):
    pack_dir = tmp_path / "pack"
    locked = candidate_row(outcome_id="locked_out", _event_starts_at=PAST)
    write_pack(pack_dir, candidates=[locked])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert "locked_out" not in index.rows
    assert "locked_out" in index.dropped
    assert index.dropped["locked_out"].stream == "candidates"


def test_unparseable_start_time_goes_to_dropped(tmp_path):
    pack_dir = tmp_path / "pack"
    bad = candidate_row(outcome_id="bad_time", _event_starts_at="not-a-timestamp")
    write_pack(pack_dir, candidates=[bad])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert "bad_time" not in index.rows
    assert "bad_time" in index.dropped


# ---------------------------------------------------------------------------
# players / injuries
# ---------------------------------------------------------------------------


def test_player_present_for_kept_candidate(tmp_path):
    pack_dir = tmp_path / "pack"
    row = candidate_row(player_id="p42", event_id="evt42", team_name="Yankees")
    write_pack(pack_dir, candidates=[row])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert "p42" in index.players
    assert index.players["p42"].event_id == "evt42"


def test_player_present_even_for_locked_row(tmp_path):
    # A player whose only row got locked still exists in the pack -- an
    # `unknown_player` check must not depend on lock status.
    pack_dir = tmp_path / "pack"
    row = candidate_row(player_id="p_locked", outcome_id="locked_out", _event_starts_at=PAST)
    write_pack(pack_dir, candidates=[row])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert "p_locked" in index.players


def test_empty_player_id_not_indexed(tmp_path):
    pack_dir = tmp_path / "pack"
    row = candidate_row(player_id="", outcome_id="no_player")
    write_pack(pack_dir, candidates=[row])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert "" not in index.players


def test_injuries_indexed_by_event(tmp_path):
    pack_dir = tmp_path / "pack"
    row = candidate_row(event_id="evt_hurt", injury_flags="SP questionable")
    write_pack(pack_dir, candidates=[row])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert index.injuries.get("evt_hurt") == "SP questionable"


def test_locks_carries_raw_event_start(tmp_path):
    pack_dir = tmp_path / "pack"
    row = candidate_row(event_id="evt_lock", _event_starts_at=FUTURE)
    write_pack(pack_dir, candidates=[row])

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert index.locks.get("evt_lock") == FUTURE


# ---------------------------------------------------------------------------
# Missing totals files -> empty-file hashes, no crash
# ---------------------------------------------------------------------------


def test_missing_totals_files_use_empty_hash(tmp_path):
    from outlier_scrapers import runner_common as rc

    pack_dir = tmp_path / "pack"
    write_pack(pack_dir, write_game_totals=False, write_team_totals=False)

    index = pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
    assert index.game_totals_sha256 == rc.empty_game_totals_hash()
    assert index.team_totals_sha256 == rc.empty_team_totals_hash()
    assert index.unindexed_totals == ()


# ---------------------------------------------------------------------------
# Header mismatch -> PackIntegrityError
# ---------------------------------------------------------------------------


def test_candidates_header_mismatch_raises(tmp_path):
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir(parents=True)
    with open(pack_dir / "candidates.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["wrong", "header"])
        writer.writeheader()
    _write_csv(pack_dir / "game_totals.csv", GAME_TOTALS_HEADER, [])
    _write_csv(pack_dir / "team_totals.csv", GAME_TOTALS_HEADER, [])

    with pytest.raises(pack_index.PackIntegrityError):
        pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))


def test_missing_candidates_file_raises(tmp_path):
    pack_dir = tmp_path / "pack"
    write_pack(pack_dir, write_candidates=False)

    with pytest.raises(pack_index.PackIntegrityError):
        pack_index.build_pack_index(pack_dir, policy_path=_policy_path(tmp_path))
