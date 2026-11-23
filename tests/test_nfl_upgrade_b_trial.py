"""Upgrade B live trial: gate switches, trial staking, input capture and the trial scorecard."""

from __future__ import annotations

import dataclasses
import gzip
import json
from pathlib import Path

import pytest
from test_nfl_best_bets import FIXTURES_DIR, _by_line, _inputs, _movement, _pick, _prop

from outlier_nfl.best_bets import (
    ESTIMATED,
    PROVISIONAL,
    REJECTED,
    UPGRADE_B_GATES,
    VALIDATED,
    VERIFIED,
    GateConfig,
    StakePolicy,
    _price,
    build_best_bets,
    render_best_bets_markdown,
    trace_settings,
)
from outlier_nfl.pipeline import NflPipeline
from outlier_nfl.settle import load_boxscores
from outlier_nfl.trace_capture import CAPTURE_NAME, load_trace_inputs, save_trace_inputs
from outlier_nfl.trial_scorecard import (
    EXIT_OUTPUT_EXISTS,
    arm_metrics,
    build_scorecard,
    collect_picks,
    evaluate_stop_criteria,
    flat_profit_units,
    wilson_interval,
)
from outlier_nfl.trial_scorecard import (
    main as scorecard_main,
)
from outlier_nfl.upgrade_b_trial import (
    DEFAULT_TRIAL_CONFIG_PATH,
    TRIAL_CONFIG_ENV,
    StopCriteria,
    load_trial_config,
    parse_trial_config,
)

SETTLE = Path(__file__).parent / "fixtures" / "nfl" / "settle"


# --- GateConfig ---------------------------------------------------------------


def test_gate_config_refuses_unknown_gates_and_keys():
    with pytest.raises(ValueError, match="unknown Upgrade B gate"):
        GateConfig(disabled=frozenset({"odds_bnad"}))
    with pytest.raises(ValueError, match="unknown gate config key"):
        GateConfig.from_dict({"l10_minimum": 0.6})
    with pytest.raises(ValueError, match="unknown Upgrade B gate"):
        GateConfig.legacy().with_gates({"nope"})


def test_legacy_and_with_gates():
    legacy = GateConfig.legacy()
    assert not any(legacy.on(g) for g in UPGRADE_B_GATES)
    assert legacy.epa_p_per_z == 0.01
    one = legacy.with_gates({"odds_band"})
    assert one.on("odds_band") and not one.on("line_move_chase")
    assert one.epa_p_per_z == 0.01  # thresholds carried over, only the switches change
    assert GateConfig.from_dict({"disabled": ["odds_band"]}).to_dict()["enabled"] == [
        g for g in UPGRADE_B_GATES if g != "odds_band"
    ]


def test_line_move_chase_gate_off_restores_the_old_boost():
    on = _pick(build_best_bets(_inputs(movement=_movement(open_line=4.5, last_line=5.5))))
    assert on["verdict"] == PROVISIONAL
    assert on["upgrade_b"] == {"gates_fired": ["line_move_chase"], "blocked_only_by": "line_move_chase"}

    cfg = GateConfig(disabled=frozenset({"line_move_chase"}))
    off = _pick(build_best_bets(_inputs(movement=_movement(open_line=4.5, last_line=5.5), gate_config=cfg)))
    assert off["pillars"]["market"]["status"] == VERIFIED
    assert off["pillars"]["market"]["delta"] == pytest.approx(0.01)
    assert off["upgrade_b"]["gates_fired"] == []


@pytest.mark.parametrize(
    ("rates", "token"),
    [({"l10_hit_rate": 0.6}, "L10"), ({"season_hit_rate": 0.6}, "season")],
)
def test_multi_window_floor_thresholds_follow_config(rates, token):
    props = [_prop(**rates), _prop("UNDER")]
    capped = _pick(build_best_bets(_inputs(props=props)))
    assert capped["upgrade_b"]["blocked_only_by"] == "multi_window_floor"
    assert any(token in n for n in capped["pillars"]["historical"]["notes"])

    looser = GateConfig(l10_min=0.55, season_min=0.55)
    passed = _pick(build_best_bets(_inputs(props=props, gate_config=looser)))
    assert passed["pillars"]["historical"]["status"] == VERIFIED
    assert passed["upgrade_b"]["gates_fired"] == []


def test_odds_band_and_yardage_gates_switch_off_in_price_pillar():
    over = _prop(market="REC_YDS", line=65.5, odds=145, implied=40.82)
    under = _prop("UNDER", market="REC_YDS", line=65.5, odds=-175, implied=63.64)
    by_line = _by_line(over, under)
    capped = _price(over, 0.72, by_line)
    assert capped.status == ESTIMATED
    assert [g["gate"] for g in capped.evidence["gates_fired"]] == ["odds_band", "yardage_over_cap"]

    off = _price(over, 0.72, by_line, cfg=GateConfig(disabled=frozenset({"odds_band", "yardage_over_cap"})))
    assert off.status == VERIFIED
    assert "gates_fired" not in off.evidence

    wider = _price(over, 0.72, by_line, cfg=GateConfig(max_odds=150, disabled=frozenset({"yardage_over_cap"})))
    assert wider.status == VERIFIED


def test_two_gates_on_one_pick_is_not_blocked_only_by_either():
    props = [_prop(odds=145, implied=40.82, l10_hit_rate=0.6), _prop("UNDER", odds=-175, implied=63.64)]
    pick = _pick(build_best_bets(_inputs(props=props)))
    assert pick["verdict"] == PROVISIONAL
    assert pick["upgrade_b"]["gates_fired"] == ["multi_window_floor", "odds_band"]
    assert pick["upgrade_b"]["blocked_only_by"] is None


def test_trace_settings_report_the_config_used():
    cfg = GateConfig(l10_min=0.6, disabled=frozenset({"yardage_over_cap"}))
    settings = trace_settings(cfg, StakePolicy("flat", 0.005, "trial"))
    assert settings["multi_window_l10_min"] == 0.6
    assert settings["continuous_yardage_over_capped"] is False
    assert settings["stake_policy"] == {"mode": "flat", "flat_fraction": 0.005, "label": "trial"}
    payload = build_best_bets(_inputs(gate_config=cfg))
    assert payload["settings"]["gates"]["disabled"] == ["yardage_over_cap"]
    assert payload["audit"]["upgrade_b"]["enabled_gates"] == [
        g for g in UPGRADE_B_GATES if g != "yardage_over_cap"
    ]


# --- StakePolicy --------------------------------------------------------------


def test_stake_policy_validation():
    with pytest.raises(ValueError, match="stake mode"):
        StakePolicy("martingale")
    with pytest.raises(ValueError, match="flat_fraction"):
        StakePolicy("flat", 0.0)
    with pytest.raises(ValueError, match="flat_fraction"):
        StakePolicy("flat", 0.05)  # above MAX_STAKE


@pytest.mark.parametrize(("policy", "expected"), [
    (StakePolicy(), None), (StakePolicy("flat", 0.005, "trial"), 0.005), (StakePolicy("paper"), 0.0),
])
def test_validated_stake_follows_policy_and_kelly_is_kept(policy, expected):
    payload = build_best_bets(_inputs(stake_policy=policy))
    pick = _pick(payload)
    assert pick["verdict"] == VALIDATED
    kelly = pick["pillars"]["price"]["evidence"]["stake_if_validated"]
    assert kelly > 0
    assert pick["kelly_stake_fraction"] == kelly
    assert pick["stake_fraction"] == (kelly if expected is None else expected)
    for other in payload["picks"]:
        if other["verdict"] != VALIDATED:
            assert other["stake_fraction"] == 0.0 and other["kelly_stake_fraction"] == 0.0


def test_markdown_names_a_non_kelly_stake_mode():
    flat = render_best_bets_markdown(build_best_bets(_inputs(stake_policy=StakePolicy("flat", 0.005, "t"))), title="x")
    assert "**Stake mode: FLAT (t)** - flat 0.50% of bankroll" in flat
    paper = render_best_bets_markdown(build_best_bets(_inputs(stake_policy=StakePolicy("paper"))), title="x")
    assert "paper trade, no money staked" in paper
    kelly = render_best_bets_markdown(build_best_bets(_inputs()), title="x")
    assert "Stake mode" not in kelly


# --- Trial config -------------------------------------------------------------


def test_repo_trial_config_is_flat_with_pre_registered_criteria():
    trial = load_trial_config(DEFAULT_TRIAL_CONFIG_PATH)
    assert trial.active
    assert trial.stake_policy.mode == "flat"
    assert trial.stake_policy.flat_fraction == 0.005
    assert trial.gate_config == GateConfig()  # Upgrade B as merged
    assert trial.weeks == (12, 13, 14, 15)
    assert trial.stop_criteria == StopCriteria()  # the file and the code defaults agree


def test_missing_trial_config_means_no_trial(tmp_path, monkeypatch):
    monkeypatch.setenv(TRIAL_CONFIG_ENV, str(tmp_path / "absent.json"))
    trial = load_trial_config()
    assert not trial.active
    assert trial.stake_policy.mode == "kelly"


@pytest.mark.parametrize("raw, match", [
    ({"schema": 2}, "schema"),
    ({"schema": 1, "stakes": {}}, "unknown trial config key"),
    ({"schema": 1, "stake": {"mode": "flat", "fraction": 0.01}}, "unknown stake key"),
    ({"schema": 1, "stake": {"mode": "flat"}}, "flat_fraction"),
    ({"schema": 1, "stop_criteria": {"win_floor": 0.5}}, "unknown stop_criteria key"),
    ({"schema": 1, "gates": {"disabled": ["odds"]}}, "unknown Upgrade B gate"),
])
def test_malformed_trial_config_raises(raw, match):
    with pytest.raises(ValueError, match=match):
        parse_trial_config(raw)


def test_unparseable_trial_config_raises(tmp_path):
    bad = tmp_path / "trial.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_trial_config(bad)


# --- Capture --------------------------------------------------------------------


def test_capture_round_trip_reproduces_the_card_and_is_deterministic(tmp_path):
    inputs = _inputs(movement=_movement(open_line=4.5, last_line=5.5))
    a = save_trace_inputs(tmp_path / "a.json.gz", inputs, meta={"date": "2026-10-04"})
    b = save_trace_inputs(tmp_path / "b.json.gz", inputs, meta={"date": "2026-10-04"})
    assert a.read_bytes() == b.read_bytes()
    loaded, meta = load_trace_inputs(a)
    assert meta == {"date": "2026-10-04"}
    assert build_best_bets(loaded)["picks"] == build_best_bets(inputs)["picks"]
    raw = json.loads(gzip.decompress(a.read_bytes()))
    assert raw["recorded_gate_config"]["enabled"] == list(UPGRADE_B_GATES)


def test_pipeline_uses_trial_config_and_captures_inputs(tmp_path, monkeypatch):
    trial = tmp_path / "trial.json"
    trial.write_text(json.dumps({"schema": 1, "name": "t", "stake": {"mode": "paper"},
                                 "gates": {"disabled": ["odds_band"]}}), encoding="utf-8")
    monkeypatch.setenv(TRIAL_CONFIG_ENV, str(trial))
    NflPipeline(data_dir=tmp_path).run(date="2026-09-13", offline_fixtures_dir=FIXTURES_DIR,
                                        write_latest=False, reports_dir=tmp_path / "reports")
    card = json.loads((tmp_path / "NFL" / "normalized" / "nfl_best_bets_2026-09-13.json").read_text("utf-8"))
    assert card["trial"]["active"] and card["trial"]["stake"]["mode"] == "paper"
    assert card["settings"]["gates"]["disabled"] == ["odds_band"]
    captures = list((tmp_path / "NFL" / "runs").glob(f"*/{CAPTURE_NAME}"))
    assert len(captures) == 1
    manifest = json.loads((captures[0].parent / "manifest.json").read_text("utf-8"))
    assert CAPTURE_NAME in {a["name"] for a in manifest["artifacts"]}  # sealed with a sha256
    replayed, meta = load_trace_inputs(captures[0])
    assert meta["date"] == "2026-09-13"
    assert replayed.gate_config == GateConfig()  # a replay chooses its own gates
    same = dataclasses.replace(replayed, gate_config=GateConfig(disabled=frozenset({"odds_band"})))
    replay = build_best_bets(same)

    def key(p):
        return (p["rank"], p["player_name"], p["market"], p["position"], p["verdict"], p["final_p"])

    assert [key(p) for p in replay["picks"]] == [key(p) for p in card["picks"]]


def test_malformed_trial_config_fails_the_card(tmp_path, monkeypatch):
    trial = tmp_path / "trial.json"
    trial.write_text(json.dumps({"schema": 1, "stake": {"mode": "flat"}}), encoding="utf-8")
    monkeypatch.setenv(TRIAL_CONFIG_ENV, str(trial))
    NflPipeline(data_dir=tmp_path).run(date="2026-09-13", offline_fixtures_dir=FIXTURES_DIR,
                                        write_latest=False, reports_dir=tmp_path / "reports")
    assert not (tmp_path / "NFL" / "normalized" / "nfl_best_bets_2026-09-13.json").exists()


# --- Scorecard ------------------------------------------------------------------


def _card_pick(record: dict, verdict: str, *, upgrade_b=None, base_p=0.6, kelly=0.01) -> dict:
    pick = {k: record[k] for k in ("event_id", "event_starts_at", "matchup", "team", "opponent",
                                   "player_name", "market", "position", "line", "best_odds")}
    pick.update(verdict=verdict, confidence_tier=record.get("confidence_tier"), base_p=base_p,
                final_p=base_p, stake_fraction=0.005 if verdict == VALIDATED else 0.0,
                kelly_stake_fraction=kelly if verdict == VALIDATED else 0.0,
                upgrade_b=upgrade_b or {"gates_fired": [], "blocked_only_by": None},
                pillars={"price": {"evidence": {"implied": record["implied_probability"] / 100}},
                         "historical": {"evidence": {"empirical_blend_p": 0.8}}})
    return pick


def _fixture_cards(updated_at="2026-09-17T18:00:00Z"):
    records = json.loads((SETTLE / "predictions_tier1.json").read_text("utf-8"))["records"]
    by_name = {r["player_name"]: r for r in records}
    picks = [
        _card_pick(by_name["Jahmyr Gibbs"], VALIDATED),          # 52 vs 87.5 O -> L
        _card_pick(by_name["Amon-Ra St. Brown"], VALIDATED),     # 142 vs 79.5 O -> W
        _card_pick(by_name["Josh Allen"], PROVISIONAL,
                   upgrade_b={"gates_fired": ["odds_band"], "blocked_only_by": "odds_band"}),
        _card_pick(by_name["Jameson Williams"], REJECTED),
    ]
    card = {"updated_at": updated_at, "settings": {"stake_policy": {"mode": "flat"}}, "picks": picks}
    return [(Path("card.json"), card)]


def test_collect_picks_drops_post_kickoff_cards_and_keeps_latest_pre_kickoff_verdict():
    early = _fixture_cards("2026-09-17T15:00:00Z")
    late = _fixture_cards("2026-09-17T23:00:00Z")  # 7:00 PM ET, before the 8:15 PM ET kickoff
    late[0][1]["picks"][0]["verdict"] = PROVISIONAL
    after = _fixture_cards("2026-09-18T01:00:00Z")  # after kickoff: never a bet
    after[0][1]["picks"][1]["verdict"] = REJECTED
    picks = collect_picks(early + late + after)
    by_name = {p["player_name"]: p["verdict"] for p in picks}
    assert by_name["Jahmyr Gibbs"] == PROVISIONAL       # the later pre-kickoff card wins
    assert by_name["Amon-Ra St. Brown"] == VALIDATED    # the post-kickoff downgrade is ignored
    assert len(picks) == 4


def test_scorecard_grades_arms_and_attaches_verified_clv():
    from outlier_nfl.enrich_close import load_book_close_feed

    events = load_boxscores(SETTLE / "boxscores_det_buf.json")
    close = load_book_close_feed(SETTLE / "book_close_feed_from_odds_api.json")
    sc = build_scorecard(_fixture_cards(), events, close_index=close)
    v = sc["arms"][VALIDATED]
    assert (v["picks"], v["wins"], v["losses"]) == (2, 1, 1)
    gibbs = next(r for r in sc["validated_rows"] if r["player_name"] == "Jahmyr Gibbs")
    assert gibbs["result"] == "L"
    # Gibbs bet at -111 (52.61%), closed -102 (50.495%): the close was a better price -> negative CLV
    assert gibbs["clv_verified"] and gibbs["clv_implied_pts"] == pytest.approx(50.495 - 52.61, abs=0.01)
    assert v["clv_n"] >= 1
    assert sc["arms"]["blocked_only_by:odds_band"]["picks"] == 1
    assert sc["stop_criteria"]["verdict"] == "CONTINUE"
    assert sc["stop_criteria"]["rules"]["win_rate"]["status"] == "waiting"
    assert sc["reliability"]["by_base_p"][0]["n"] == 4  # every graded pick, all verdicts (base_p 0.60)


def test_flat_profit_and_wilson():
    assert flat_profit_units("W", -110) == pytest.approx(100 / 110)
    assert flat_profit_units("W", 130) == pytest.approx(1.3)
    assert flat_profit_units("L", 130) == -1.0
    assert flat_profit_units("P", -110) == 0.0
    assert flat_profit_units(None, -110) is None
    lo, hi = wilson_interval(58, 100)
    assert lo == pytest.approx(0.4821, abs=1e-3) and hi == pytest.approx(0.6720, abs=1e-3)
    assert wilson_interval(0, 0) == (None, None)


def test_arm_metrics_drawdown_and_kelly():
    rows = [{"result": r, "best_odds": 100, "kelly_stake_fraction": 0.02}
            for r in ("W", "L", "L", "L", "W", "P")]
    m = arm_metrics(rows)
    assert (m["wins"], m["losses"], m["pushes"]) == (2, 3, 1)
    assert m["flat_units"] == -1.0
    assert m["max_drawdown_units"] == 3.0  # +1 peak, then three losses to -2
    assert m["kelly_bankroll_change"] == pytest.approx(-0.02)


def _m(wins, losses, clv_n=0, clv=None, dd=0.0):
    rows = [{"result": "W", "best_odds": -110}] * wins + [{"result": "L", "best_odds": -110}] * losses
    m = arm_metrics(rows)
    m.update(clv_n=clv_n, clv_mean_pts=clv, max_drawdown_units=dd)
    return m


@pytest.mark.parametrize(("m", "verdict", "rule", "status"), [
    (_m(30, 30), "CONTINUE", "win_rate", "waiting"),
    (_m(52, 50), "REVIEW_GATES", "win_rate", "TRIGGERED"),         # 51.0% < 53% after 100+
    (_m(20, 20, clv_n=40, clv=-0.4), "REVIEW_GATES", "clv", "TRIGGERED"),
    (_m(20, 20, dd=15.0), "REVIEW_GATES", "drawdown", "TRIGGERED"),
    (_m(118, 82, clv_n=150, clv=1.1), "EARNED_TRUST", "success", "ok"),   # 59% of 200: bound ~53.3%
    (_m(116, 84, clv_n=150, clv=1.1), "CONTINUE", "success", "not_met"),  # 58% of 200: bound ~52.2%
    (_m(118, 82, clv_n=150, clv=-0.2), "REVIEW_GATES", "clv", "TRIGGERED"),  # wins without CLV: suspect
])
def test_stop_criteria(m, verdict, rule, status):
    out = evaluate_stop_criteria(m, StopCriteria())
    assert out["verdict"] == verdict
    assert out["rules"][rule]["status"] == status


def test_scorecard_cli_writes_and_refuses_overwrite(tmp_path):
    card = tmp_path / "nfl_best_bets_2026-09-17.json"
    card.write_text(json.dumps(_fixture_cards()[0][1]), encoding="utf-8")
    out_json, out_md = tmp_path / "sc.json", tmp_path / "sc.md"
    args = ["--cards", str(tmp_path / "nfl_best_bets_*.json"), "--boxscores", str(SETTLE / "boxscores_det_buf.json"),
            "--trial-config", str(DEFAULT_TRIAL_CONFIG_PATH), "--out-json", str(out_json), "--out-md", str(out_md)]
    assert scorecard_main(args) == 0
    assert json.loads(out_json.read_text("utf-8"))["arms"][VALIDATED]["picks"] == 2
    assert "## Stop criteria: **CONTINUE**" in out_md.read_text("utf-8")
    assert scorecard_main(args) == EXIT_OUTPUT_EXISTS
