"""Phase 3a (#225): projection provenance, push-aware probabilities, TD scope and EV approval."""

from __future__ import annotations

from typing import Any

import pytest

from outlier_nfl import best_bets as bb

from outlier_nfl.projection import attach_model_p_hierarchy_record, project_hit_probability_v2


def _row(**kw: Any) -> dict[str, Any]:
    base = {"player_name": "Lamar Jackson", "market": "RUSH_YDS", "position": "OVER",
            "line": 52.5, "l10_hit_rate": 60.0, "l10_sample": 10, "l20_hit_rate": 55.0,
            "l20_sample": 20}
    return {**base, **kw}


# F06a ----------------------------------------------------------------------

def test_hierarchy_fallback_clears_stale_projection_method_and_count() -> None:
    rec = _row(model_p=0.61, model_p_source="projection_nflverse_gaussian",
               model_p_method="gamelog_gaussian", model_p_n_games=3)
    attach_model_p_hierarchy_record(rec, week_index={})  # no weeks: projection unavailable
    assert rec["model_p_source"].startswith("empirical_hit_rate")
    assert rec.get("model_p_method") is None and rec.get("model_p_n_games") is None


def test_hierarchy_projection_keeps_its_own_method_and_count() -> None:
    weeks = [{"rushing_yards": y} for y in (40, 60, 80)]
    rec = _row(model_p_method="stale")
    attach_model_p_hierarchy_record(rec, week_index={"LAMARJACKSON": weeks})
    assert rec["model_p_source"] == "projection_nflverse_gaussian"
    assert (rec["model_p_method"], rec["model_p_n_games"]) == ("gamelog_gaussian", 3)


# F06b ----------------------------------------------------------------------

def test_enrich_close_hierarchy_honours_overwrite_model_p_false() -> None:
    from outlier_nfl.enrich_close import attach_close_fields

    weeks = {"LAMARJACKSON": [{"rushing_yards": y} for y in (40, 60, 80)]}
    keep = _row(model_p=0.7, model_p_source="empirical_hit_rate_market_prior")
    attach_close_fields(keep, attach_model_p="hierarchy", week_index=weeks, overwrite_model_p=False)
    assert (keep["model_p"], keep["model_p_source"]) == (0.7, "empirical_hit_rate_market_prior")

    replace = _row(model_p=0.7, model_p_source="empirical_hit_rate_market_prior")
    attach_close_fields(replace, attach_model_p="hierarchy", week_index=weeks, overwrite_model_p=True)
    assert replace["model_p_source"] == "projection_nflverse_gaussian"


def test_hierarchy_fills_a_missing_model_p_when_not_overwriting() -> None:
    weeks = {"LAMARJACKSON": [{"rushing_yards": y} for y in (40, 60, 80)]}
    rec = _row()
    attach_model_p_hierarchy_record(rec, week_index=weeks, overwrite=False)
    assert rec["model_p_source"] == "projection_nflverse_gaussian"


# F07 -----------------------------------------------------------------------

def _v2(market: str, line: float, position: str, values: list[float], col: str):
    return project_hit_probability_v2(player_name="x", market=market, line=line,
                                      position=position, week_rows=[{col: v} for v in values])


def test_poisson_integer_under_excludes_the_push() -> None:
    r = _v2("PASS_TD", 2.0, "UNDER", [1, 2, 3], "passing_tds")  # mean 2
    assert r is not None
    assert r.model_p == pytest.approx(0.406006, abs=1e-6)
    assert r.p_push == pytest.approx(0.270671, abs=1e-6)
    over = _v2("PASS_TD", 2.0, "OVER", [1, 2, 3], "passing_tds")
    assert over is not None
    assert over.model_p + r.model_p + r.p_push == pytest.approx(1.0, abs=3e-6)
    assert r.p_loss == pytest.approx(over.model_p, abs=1e-6)


def test_poisson_half_line_has_no_push_and_zero_rate_is_coherent() -> None:
    r = _v2("PASS_TD", 1.5, "UNDER", [1, 2, 3], "passing_tds")
    assert r is not None and r.p_push == 0.0
    z = _v2("PASS_TD", 0.0, "UNDER", [0, 0, 0], "passing_tds")
    assert z is not None and (z.model_p, z.p_push, z.p_loss) == (0.0, 1.0, 0.0)


def test_gaussian_integer_line_marks_push_unmodeled() -> None:
    r = _v2("PASS_YDS", 268.0, "OVER", [281, 255, 300], "passing_yards")
    assert r is not None and r.p_push is None and r.p_loss is None
    h = _v2("PASS_YDS", 268.5, "OVER", [281, 255, 300], "passing_yards")
    assert h is not None and h.p_push == 0.0 and h.p_loss == pytest.approx(1 - h.model_p)


def test_hierarchy_records_win_push_loss() -> None:
    rec = {"player_name": "Kelce", "market": "REC", "position": "UNDER", "line": 5.0}
    attach_model_p_hierarchy_record(rec, week_index={"KELCE": [{"receptions": v} for v in (6, 4, 5)]})
    assert rec["model_p"] == rec["model_p_win"] == pytest.approx(0.440493, abs=1e-6)
    assert rec["model_p_push"] == pytest.approx(0.175467, abs=1e-6)
    assert rec["model_p_win"] + rec["model_p_push"] + rec["model_p_loss"] == pytest.approx(1.0, abs=1e-5)


# F08 -----------------------------------------------------------------------

@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"passing_tds": 3, "rushing_tds": 0, "receiving_tds": 0}, 0.0),  # passing-only QB
        ({"passing_tds": 2, "rushing_tds": 1, "receiving_tds": 0}, 1.0),  # rushing QB
        ({"rushing_tds": 0, "receiving_tds": 2}, 2.0),  # receiver
        ({"rushing_tds": 0, "receiving_tds": 0, "special_teams_tds": 1}, 1.0),  # return scorer
        ({"passing_tds": 2}, None),  # scoring components absent: unknown, not zero
    ],
)
def test_anytime_td_counts_only_scored_touchdowns(row: dict[str, Any], expected: float | None) -> None:
    from outlier_nfl.projection import week_stat_value

    assert week_stat_value("ANYTIME_TD", row) == expected


# F05 -----------------------------------------------------------------------




def _quote(final_p: float, line: float, odds: int = -110, opp: float = 0.5238):
    prop = {"player_name": "x", "market": "REC", "position": "OVER", "line": line,
            "best_odds": odds, "implied_probability": 0.5238, "scope": "full_game"}
    other = {**prop, "position": "UNDER", "implied_probability": opp}
    by_line = {bb.prop_key(other) + (line,): other}
    return bb._price(prop, final_p, by_line)


def test_positive_edge_with_negative_ev_is_rejected() -> None:
    # p=.51 at -110/-110: no-vig fair .50 (edge +.01) but EV = .51*1.909-1 < 0.
    pillar = _quote(0.51, 5.5)
    assert pillar.evidence["edge"] > 0 and pillar.evidence["ev_per_unit"] < 0
    assert pillar.status == bb.CONTRADICTS
    assert "no positive expected return at the quoted price" in pillar.notes


def test_p_above_break_even_passes_the_price_pillar() -> None:
    pillar = _quote(0.56, 5.5)  # break-even at -110 is 0.5238
    assert pillar.status == bb.VERIFIED and pillar.evidence["ev_at_quote_positive"] is True


def test_integer_line_is_never_validated_until_pushes_are_priced() -> None:
    pillar = _quote(0.60, 5.0)
    assert pillar.status == bb.ESTIMATED
    assert any("push" in n for n in pillar.notes)


def test_push_aware_ev() -> None:
    # Mean 2, UNDER 2 at even money: win .406006, push .270671, loss .323323.
    assert bb.push_aware_ev(0.406006, 0.270671, 2.0) == pytest.approx(0.406006 - 0.323323, abs=1e-6)


# Rebuild: a NEW versioned card, original byte-identical ------------------------

def _card(tmp_path, picks):
    import json

    card = tmp_path / "nfl_best_bets_2026-10-04.json"
    card.write_text(json.dumps({"date": "2026-10-04", "counts": {}, "picks": picks}), encoding="utf-8")
    return card


def _pick(final_p: float, line: float, verdict: str, odds: int = -110, edge: float = 0.01) -> dict:
    pillars = {n: {"status": bb.VERIFIED, "delta": 0.0, "evidence": {}, "notes": []} for n in
               ("historical", "opportunity", "matchup", "injury_weather", "market", "price")}
    return {"verdict": verdict, "player_name": "x", "market": "REC", "position": "OVER",
            "line": line, "best_odds": odds, "final_p": final_p, "edge": edge,
            "stake_fraction": 0.02, "pillars": pillars, "base_p": final_p, "rank": 1,
            "matchup": "A @ B", "team": "A", "fair_p": 0.5, "ev_per_unit": 0.0}


def test_rebuild_writes_versioned_card_and_leaves_original_byte_identical(tmp_path) -> None:
    import json

    from scripts import nfl_rebuild_card as rb

    card = _card(tmp_path, [_pick(0.51, 5.5, bb.VALIDATED), _pick(0.60, 5.0, bb.VALIDATED, edge=0.08),
                            _pick(0.60, 5.5, bb.VALIDATED, edge=0.08)])
    original = card.read_bytes()
    assert rb.main(["--card", str(card), "--version", "phase3a"]) == 0
    assert card.read_bytes() == original
    out = tmp_path / "nfl_best_bets_2026-10-04.v-phase3a.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["original_counts"] == {} and data["counts"] == {
        bb.VALIDATED: 1, bb.PROVISIONAL: 1, bb.REJECTED: 1}
    verdicts = {(p["line"], p["final_p"]): p["verdict"] for p in data["picks"]}
    assert verdicts == {(5.5, 0.51): bb.REJECTED, (5.0, 0.60): bb.PROVISIONAL,
                        (5.5, 0.60): bb.VALIDATED}
    assert all(p["original_verdict"] == bb.VALIDATED for p in data["picks"])
    assert data["rebuild"]["source_sha256"] == __import__("hashlib").sha256(original).hexdigest()
    assert (tmp_path / "nfl_best_bets_2026-10-04.v-phase3a.md").exists()
    # A second run never overwrites the first rebuild, or the original.
    with pytest.raises(SystemExit):
        rb.main(["--card", str(card), "--version", "phase3a"])
    assert card.read_bytes() == original


def test_rebuild_never_promotes_a_verdict(tmp_path) -> None:
    import json

    from scripts import nfl_rebuild_card as rb

    card = _card(tmp_path, [_pick(0.60, 5.5, bb.PROVISIONAL, edge=0.08)])
    rb.main(["--card", str(card), "--version", "v2"])
    data = json.loads((tmp_path / "nfl_best_bets_2026-10-04.v-v2.json").read_text())
    assert data["picks"][0]["verdict"] == bb.PROVISIONAL
