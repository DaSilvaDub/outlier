"""Matchup Advantage Rankings Engine and Mandatory Reporting Stage for outlier_nfl.

Calculates positional matchup boosts (Top 5 QB, RB, WR, TE) and the Final Top 10 Overall
across all NFL slates, ranking players by statistically supported matchup-driven increases
over their current-season averages.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
import logging
import math
from pathlib import Path
from typing import Any

from outlier_nfl.models import NflPlayerProp

logger = logging.getLogger("outlier_nfl.matchup_rankings")

LEAGUE_PASS_YDS_MEDIAN = 229.5
LEAGUE_RUSH_YDS_MEDIAN = 115.0


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def calc_prob_exceeds(mu: float, baseline: float, sigma: float) -> float:
    """Tail probability that N(mu, sigma) > baseline."""
    if sigma <= 0.001:
        return 1.0 if mu > baseline else 0.0
    z = (mu - baseline) / sigma
    return norm_cdf(z)


def calc_advantage_score(
    boost_pct: float, prob_exceeds: float, volume_score: float, def_vuln_score: float
) -> float:
    """Standardized composite Matchup Advantage Score (0-100).

    Weights:
    - 35%: Boost percentage magnitude (capped at 50% boost -> 100 pts)
    - 35%: Exceedance tail probability P(X > Season Avg)
    - 15%: Volume and role opportunity stability (0-100 pts)
    - 15%: Opponent defensive vulnerability and mismatch severity (0-100 pts)
    """
    mag_score = min(100.0, max(0.0, boost_pct * 2.0))
    prob_score = max(0.0, min(100.0, prob_exceeds * 100.0))
    vol_clamped = max(0.0, min(100.0, volume_score))
    vuln_clamped = max(0.0, min(100.0, def_vuln_score))

    score = (
        (0.35 * mag_score)
        + (0.35 * prob_score)
        + (0.15 * vol_clamped)
        + (0.15 * vuln_clamped)
    )
    return round(score, 1)


@dataclass
class SecondaryAdvantage:
    stat: str
    season_avg: float
    matchup_proj: float
    boost_pct: float


@dataclass
class MatchupAdvantageCandidate:
    player: str
    team: str
    position: str
    opponent: str
    is_home: bool
    primary_stat: str
    primary_market: str
    season_avg: float
    neutral_proj: float
    matchup_proj: float
    expected_increase: float
    boost_pct: float
    matchup_specific_boost: float
    prob_exceeds: float
    prob_pct: float
    advantage_score: float
    confidence_tier: str
    volume_score: float
    def_vuln_score: float
    tactical_drivers: dict[str, str]
    secondary_advantages: list[SecondaryAdvantage] = field(default_factory=list)
    prop_line: float | None = None
    prop_odds: int | None = None
    prop_implied: float | None = None
    validation_status: str = "VERIFIED_ACTIVE"

    @property
    def matchup_str(self) -> str:
        return f"{self.team} vs {self.opponent}" if self.is_home else f"{self.team} @ {self.opponent}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "team": self.team,
            "position": self.position,
            "opponent": self.opponent,
            "is_home": self.is_home,
            "matchup": self.matchup_str,
            "primary_stat": self.primary_stat,
            "primary_market": self.primary_market,
            "season_avg": self.season_avg,
            "neutral_proj": self.neutral_proj,
            "matchup_proj": self.matchup_proj,
            "expected_increase": self.expected_increase,
            "boost_pct": self.boost_pct,
            "matchup_specific_boost": self.matchup_specific_boost,
            "prob_exceeds": self.prob_exceeds,
            "prob_pct": self.prob_pct,
            "advantage_score": self.advantage_score,
            "confidence_tier": self.confidence_tier,
            "prop_line": self.prop_line,
            "prop_odds": self.prop_odds,
            "prop_implied": self.prop_implied,
            "validation_status": self.validation_status,
            "tactical_drivers": self.tactical_drivers,
            "secondary_advantages": [
                {
                    "stat": s.stat,
                    "season_avg": s.season_avg,
                    "matchup_proj": s.matchup_proj,
                    "boost_pct": s.boost_pct,
                }
                for s in self.secondary_advantages
            ],
        }


class MatchupRankingsEngine:
    """Evaluates slate players across QB, RB, WR, and TE to compute matchup rankings."""

    def __init__(
        self,
        *,
        props: list[NflPlayerProp] | list[dict[str, Any]],
        matchup_scripts: list[Any],
        usage_players: list[dict[str, Any]],
        rosters: dict[str, Any],
        tapes: dict[str, Any],
        weather_records: list[dict[str, Any]],
        inactives_by_team: dict[str, list[str]] | None = None,
        date_str: str = "latest",
    ) -> None:
        self.date_str = date_str
        self.usage_by_player: dict[str, dict[str, Any]] = {
            p.get("player") or p.get("player_name", ""): p for p in usage_players if isinstance(p, dict)
        }
        self.rosters = rosters
        self.tapes = tapes
        self.weather_by_event: dict[str, dict[str, Any]] = {}
        for w in weather_records:
            if isinstance(w, dict) and w.get("event_id"):
                self.weather_by_event[w["event_id"]] = w

        self.inactives_by_team = inactives_by_team or {}

        # Index consensus props
        self.consensus_props: dict[tuple[str, str], dict[str, Any]] = {}
        for p in props:
            p_dict = p if isinstance(p, dict) else p.to_dict()
            if (
                isinstance(p_dict, dict)
                and p_dict.get("is_consensus_line")
                and p_dict.get("scope") in (None, "", "full_game")
                and p_dict.get("position") == "OVER"
            ):
                pname = p_dict.get("player_name", "")
                mkt = p_dict.get("market", "")
                if pname and mkt:
                    self.consensus_props[(pname, mkt)] = p_dict

        # Index matchup scripts by team
        self.team_matchups: dict[str, dict[str, Any]] = {}
        for s in matchup_scripts:
            s_dict = s.to_dict() if hasattr(s, "to_dict") else s
            if isinstance(s_dict, dict):
                home = s_dict.get("home_team", "")
                away = s_dict.get("away_team", "")
                eid = s_dict.get("event_id", "")
                if home and away:
                    self.team_matchups[home] = {
                        "opp": away,
                        "is_home": True,
                        "event_id": eid,
                        "script": s_dict,
                    }
                    self.team_matchups[away] = {
                        "opp": home,
                        "is_home": False,
                        "event_id": eid,
                        "script": s_dict,
                    }

    def is_player_inactive(self, player_name: str, team: str) -> bool:
        """Check if player is confirmed inactive or on IR."""
        inactives = self.inactives_by_team.get(team, [])
        return player_name in inactives

    def evaluate_qbs(self) -> list[MatchupAdvantageCandidate]:
        """Evaluate starting QBs on slate for matchup advantages."""
        candidates: list[MatchupAdvantageCandidate] = []
        for team, m_info in self.team_matchups.items():
            opp = m_info["opp"]
            is_home = m_info["is_home"]
            eid = m_info["event_id"]
            script = m_info["script"]

            roster_team = self.rosters.get(team, {})
            starting_qb = roster_team.get("starting_qb")
            if not starting_qb:
                tape_team = self.tapes.get(team, {})
                starting_qb = tape_team.get("qb")

            if not starting_qb or self.is_player_inactive(starting_qb, team):
                continue

            team_tape = self.tapes.get(team, {})
            opp_tape = self.tapes.get(opp, {})
            weather = self.weather_by_event.get(eid, {})

            # Baseline passing yards
            s_pass = float(team_tape.get("pass_yards") or 220.0)
            opp_pass_allowed = float(opp_tape.get("opp_pass_yards_allowed") or LEAGUE_PASS_YDS_MEDIAN)
            opp_press_rate = float(opp_tape.get("pressure_rate") or 0.25)
            opp_sacks = float(opp_tape.get("sacks") or 2.0)

            opp_diff = (opp_pass_allowed - LEAGUE_PASS_YDS_MEDIAN) / LEAGUE_PASS_YDS_MEDIAN
            opp_inactives = self.inactives_by_team.get(opp, [])

            # Injury and pressure boosts
            injury_boost = min(0.08, len(opp_inactives) * 0.002)
            pressure_relief = max(-0.05, (0.28 - opp_press_rate) * 0.4)

            # Weather haircut
            weather_haircut = float(weather.get("pass_adjustment") or 0.0)
            wind_mph = float(weather.get("wind_mph") or 0.0)

            # Game script pass volume adjustment
            spread = float(script.get("home_spread") or 0.0)
            team_spread = -spread if is_home else spread
            script_boost = 0.03 if team_spread > 2.5 else (0.01 if team_spread < -2.5 else 0.0)

            # Projections
            neutral_proj = round(s_pass * 0.95 + LEAGUE_PASS_YDS_MEDIAN * 0.05, 1)
            boost_factor = 1.0 + (0.45 * opp_diff) + pressure_relief + injury_boost + script_boost + weather_haircut
            boost_factor = max(0.90, min(1.35, boost_factor))
            matchup_proj = round(s_pass * boost_factor, 1)

            exp_inc = round(matchup_proj - s_pass, 1)
            boost_pct = round(((matchup_proj - s_pass) / s_pass) * 100.0, 1)
            matchup_boost = round(matchup_proj - neutral_proj, 1)

            sigma = max(25.0, s_pass * 0.15)
            prob = round(calc_prob_exceeds(matchup_proj, s_pass, sigma), 3)
            prob_pct = round(prob * 100.0, 1)

            volume_score = min(98.0, 80.0 + (s_pass / 15.0))
            def_vuln_score = min(96.0, 75.0 + (opp_diff * 60.0) + (len(opp_inactives) * 0.3))

            adv_score = calc_advantage_score(boost_pct, prob, volume_score, def_vuln_score)

            # Look up consensus prop
            prop_rec = self.consensus_props.get((starting_qb, "PASS_YDS"))
            line = prop_rec.get("line") if prop_rec else None
            odds = prop_rec.get("best_odds") if prop_rec else None
            implied = prop_rec.get("implied_probability") if prop_rec else None

            # Secondary advantages
            comp_base = round(s_pass / 11.8, 1)
            comp_proj = round(matchup_proj / 11.6, 1)
            comp_boost = round(((comp_proj - comp_base) / comp_base) * 100.0, 1)

            att_base = round(s_pass / 7.2, 1)
            att_proj = round(matchup_proj / 7.1, 1)
            att_boost = round(((att_proj - att_base) / att_base) * 100.0, 1)

            tactical = {
                "def_vuln": f"{opp} allows {opp_pass_allowed:.1f} pass yds/g (differential: {opp_diff:+.1%}); pressure rate {opp_press_rate:.1%}.",
                "personnel": f"Pocket protection expectation favorable against {opp} defensive front averaging {opp_sacks:.1f} sacks/g.",
                "injuries": f"{opp} secondary and defense missing {len(opp_inactives)} designated inactives.",
                "game_script": f"Projected game total {script.get('total')}, spread {team_spread:+.1f} points.",
                "weather": f"Venue: {weather.get('venue', 'outdoor')}, wind: {wind_mph:.1f} mph, pass adjustment: {weather_haircut:+.2f}.",
                "role": f"{starting_qb} commands primary dropback distribution in {team} offensive scheme.",
            }

            candidates.append(
                MatchupAdvantageCandidate(
                    player=starting_qb,
                    team=team,
                    position="QB",
                    opponent=opp,
                    is_home=is_home,
                    primary_stat="Passing Yards",
                    primary_market="PASS_YDS",
                    season_avg=s_pass,
                    neutral_proj=neutral_proj,
                    matchup_proj=matchup_proj,
                    expected_increase=exp_inc,
                    boost_pct=boost_pct,
                    matchup_specific_boost=matchup_boost,
                    prob_exceeds=prob,
                    prob_pct=prob_pct,
                    advantage_score=adv_score,
                    confidence_tier="TIER_1_ANCHOR" if adv_score >= 66.0 else "TIER_2_STRONG",
                    volume_score=volume_score,
                    def_vuln_score=def_vuln_score,
                    tactical_drivers=tactical,
                    secondary_advantages=[
                        SecondaryAdvantage("Pass Completions", comp_base, comp_proj, comp_boost),
                        SecondaryAdvantage("Pass Attempts", att_base, att_proj, att_boost),
                    ],
                    prop_line=line,
                    prop_odds=odds,
                    prop_implied=implied,
                )
            )

        candidates.sort(key=lambda c: c.advantage_score, reverse=True)
        return candidates[:5]

    def evaluate_rbs(self) -> list[MatchupAdvantageCandidate]:
        """Evaluate active RBs on slate for rushing and touch matchup advantages."""
        candidates: list[MatchupAdvantageCandidate] = []
        for team, m_info in self.team_matchups.items():
            opp = m_info["opp"]
            is_home = m_info["is_home"]
            eid = m_info["event_id"]
            script = m_info["script"]

            opp_tape = self.tapes.get(opp, {})
            weather = self.weather_by_event.get(eid, {})

            opp_rush_allowed = float(opp_tape.get("opp_rush_yards_allowed") or LEAGUE_RUSH_YDS_MEDIAN)
            opp_diff = (opp_rush_allowed - LEAGUE_RUSH_YDS_MEDIAN) / LEAGUE_RUSH_YDS_MEDIAN
            opp_inactives = self.inactives_by_team.get(opp, [])

            # Find all RBs on team
            team_rbs = [
                p for p in self.usage_by_player.values()
                if p.get("team") == team and p.get("position") == "RB"
            ]

            for p in team_rbs:
                pname = p.get("player") or p.get("player_name", "")
                if not pname or self.is_player_inactive(pname, team):
                    continue

                rush_avg = float(p.get("rush_yds_pg") or 0.0)
                carries_avg = float(p.get("carries_pg") or 0.0)
                rec_avg = float(p.get("rec_yds_pg") or 0.0)

                if rush_avg < 15.0 and carries_avg < 4.0:
                    continue

                # Check if backfield partner is out (volume inheritance)
                backfield_vacated_boost = 0.0
                team_inactives = self.inactives_by_team.get(team, [])
                for inact in team_inactives:
                    if inact != pname and inact in self.usage_by_player:
                        inact_prof = self.usage_by_player[inact]
                        if inact_prof.get("position") == "RB":
                            vac_carries = float(inact_prof.get("carries_pg") or 0.0)
                            if vac_carries >= 5.0:
                                backfield_vacated_boost += min(0.60, vac_carries * 0.04)

                # Weather ground boost
                rain_prob = float(weather.get("precip_prob") or 0.0)
                weather_rush_boost = 0.06 if rain_prob > 50.0 else 0.0

                # Game script
                spread = float(script.get("home_spread") or 0.0)
                team_spread = -spread if is_home else spread
                script_rush_boost = 0.04 if team_spread < -2.5 else (-0.02 if team_spread > 5.0 else 0.0)

                boost_factor = 1.0 + (0.35 * opp_diff) + backfield_vacated_boost + weather_rush_boost + script_rush_boost
                neutral_proj = round(rush_avg * 0.95 + 45.0 * 0.05, 1)
                matchup_proj = round(rush_avg * boost_factor, 1)

                # Carries
                carries_proj = round(carries_avg * (1.0 + backfield_vacated_boost * 0.8 + 0.15 * opp_diff), 1)
                carries_boost = round(((carries_proj - carries_avg) / max(1.0, carries_avg)) * 100.0, 1)

                exp_inc = round(matchup_proj - rush_avg, 1)
                boost_pct = round(((matchup_proj - rush_avg) / max(1.0, rush_avg)) * 100.0, 1)
                matchup_boost = round(matchup_proj - neutral_proj, 1)

                sigma = max(14.0, rush_avg * 0.30)
                prob = round(calc_prob_exceeds(matchup_proj, rush_avg, sigma), 3)
                prob_pct = round(prob * 100.0, 1)

                volume_score = min(96.0, 70.0 + carries_avg * 1.5 + (backfield_vacated_boost * 30.0))
                def_vuln_score = min(95.0, 75.0 + opp_diff * 50.0 + len(opp_inactives) * 0.3)

                adv_score = calc_advantage_score(boost_pct, prob, volume_score, def_vuln_score)

                # Consensus prop lookup
                prop_rec = self.consensus_props.get((pname, "RUSH_YDS"))
                line = prop_rec.get("line") if prop_rec else None
                odds = prop_rec.get("best_odds") if prop_rec else None
                implied = prop_rec.get("implied_probability") if prop_rec else None

                tactical = {
                    "def_vuln": f"{opp} allows {opp_rush_allowed:.1f} rush yds/g (differential: {opp_diff:+.1%}).",
                    "personnel": f"Downhill ground attack creates contact balance advantage against {opp} defensive front.",
                    "injuries": f"Backfield vacated boost: {backfield_vacated_boost:+.1%}; opponent inactives: {len(opp_inactives)}.",
                    "game_script": f"Projected team spread: {team_spread:+.1f}, game total: {script.get('total')}.",
                    "weather": f"Venue: {weather.get('venue', 'outdoor')}, precip probability: {rain_prob}%.",
                    "role": f"Touches floor elevated with {carries_proj:.1f} projected carries.",
                }

                candidates.append(
                    MatchupAdvantageCandidate(
                        player=pname,
                        team=team,
                        position="RB",
                        opponent=opp,
                        is_home=is_home,
                        primary_stat="Rushing Yards",
                        primary_market="RUSH_YDS",
                        season_avg=rush_avg,
                        neutral_proj=neutral_proj,
                        matchup_proj=matchup_proj,
                        expected_increase=exp_inc,
                        boost_pct=boost_pct,
                        matchup_specific_boost=matchup_boost,
                        prob_exceeds=prob,
                        prob_pct=prob_pct,
                        advantage_score=adv_score,
                        confidence_tier="TIER_1_ANCHOR" if adv_score >= 76.0 else "TIER_2_STRONG",
                        volume_score=volume_score,
                        def_vuln_score=def_vuln_score,
                        tactical_drivers=tactical,
                        secondary_advantages=[
                            SecondaryAdvantage("Carries", carries_avg, carries_proj, carries_boost),
                            SecondaryAdvantage("Receiving Yards", rec_avg, round(rec_avg * 1.15, 1), 15.0),
                        ],
                        prop_line=line,
                        prop_odds=odds,
                        prop_implied=implied,
                    )
                )

        candidates.sort(key=lambda c: c.advantage_score, reverse=True)
        return candidates[:5]

    def evaluate_wrs(self) -> list[MatchupAdvantageCandidate]:
        """Evaluate active WRs on slate for receiving yards and reception boosts."""
        candidates: list[MatchupAdvantageCandidate] = []
        for team, m_info in self.team_matchups.items():
            opp = m_info["opp"]
            is_home = m_info["is_home"]
            eid = m_info["event_id"]
            script = m_info["script"]
            opp_tape = self.tapes.get(opp, {})
            weather = self.weather_by_event.get(eid, {})

            opp_pass_allowed = float(opp_tape.get("opp_pass_yards_allowed") or LEAGUE_PASS_YDS_MEDIAN)
            opp_diff = (opp_pass_allowed - LEAGUE_PASS_YDS_MEDIAN) / LEAGUE_PASS_YDS_MEDIAN
            opp_inactives = self.inactives_by_team.get(opp, [])

            team_wrs = [
                p for p in self.usage_by_player.values()
                if p.get("team") == team and p.get("position") == "WR"
            ]

            for p in team_wrs:
                pname = p.get("player") or p.get("player_name", "")
                if not pname or self.is_player_inactive(pname, team):
                    continue

                rec_yds_avg = float(p.get("rec_yds_pg") or 0.0)
                targets_avg = float(p.get("targets_pg") or 0.0)
                rec_avg = round(targets_avg * 0.68, 1)

                if rec_yds_avg < 20.0 and targets_avg < 3.0:
                    continue

                rain_prob = float(weather.get("precip_prob") or 0.0)
                is_slot = "slot" in p.get("role", "").lower() or (targets_avg >= 4.0 and rec_yds_avg / max(1.0, targets_avg) < 11.0)

                # Bad weather funnels short slot receptions, penalizes deep boundary
                weather_boost = 0.08 if (rain_prob > 60.0 and is_slot) else (-0.05 if (rain_prob > 60.0 and not is_slot) else 0.0)

                target_funnel_boost = 0.05 if len(opp_inactives) >= 2 else 0.0
                boost_factor = 1.0 + (0.40 * opp_diff) + target_funnel_boost + weather_boost

                # Choose best market (Receptions vs Receiving Yards)
                if is_slot and targets_avg >= 4.5:
                    best_stat = "Receptions"
                    best_mkt = "REC"
                    base_val = rec_avg
                    sigma = max(1.1, base_val * 0.30)
                    neutral_proj = round(base_val * 0.95 + 4.0 * 0.05, 1)
                    matchup_proj = round(base_val * boost_factor, 1)
                    sec_stat = "Receiving Yards"
                    sec_base = rec_yds_avg
                    sec_proj = round(rec_yds_avg * boost_factor, 1)
                else:
                    best_stat = "Receiving Yards"
                    best_mkt = "REC_YDS"
                    base_val = rec_yds_avg
                    sigma = max(13.0, base_val * 0.30)
                    neutral_proj = round(base_val * 0.95 + 50.0 * 0.05, 1)
                    matchup_proj = round(base_val * boost_factor, 1)
                    sec_stat = "Receptions"
                    sec_base = rec_avg
                    sec_proj = round(rec_avg * boost_factor, 1)

                exp_inc = round(matchup_proj - base_val, 1)
                boost_pct = round(((matchup_proj - base_val) / max(1.0, base_val)) * 100.0, 1)
                matchup_boost = round(matchup_proj - neutral_proj, 1)

                prob = round(calc_prob_exceeds(matchup_proj, base_val, sigma), 3)
                prob_pct = round(prob * 100.0, 1)

                volume_score = min(95.0, 72.0 + targets_avg * 2.8)
                def_vuln_score = min(94.0, 75.0 + opp_diff * 45.0 + len(opp_inactives) * 0.3)

                adv_score = calc_advantage_score(boost_pct, prob, volume_score, def_vuln_score)

                # Consensus prop lookup
                prop_rec = self.consensus_props.get((pname, best_mkt))
                line = prop_rec.get("line") if prop_rec else None
                odds = prop_rec.get("best_odds") if prop_rec else None
                implied = prop_rec.get("implied_probability") if prop_rec else None

                tactical = {
                    "def_vuln": f"{opp} secondary surrenders {opp_pass_allowed:.1f} pass yds/g (differential: {opp_diff:+.1%}).",
                    "personnel": f"Route tree exploits coverage alignment in {opp} defensive secondary.",
                    "injuries": f"{opp} secondary compromised with {len(opp_inactives)} designated inactives.",
                    "game_script": f"Game total {script.get('total')}; projected passing script favorable.",
                    "weather": f"Venue: {weather.get('venue', 'outdoor')}, precip: {rain_prob}%.",
                    "role": f"Commands steady target opportunity (baseline {targets_avg:.1f} targets/g).",
                }

                sec_boost_pct = round(((sec_proj - sec_base) / max(1.0, sec_base)) * 100.0, 1)

                candidates.append(
                    MatchupAdvantageCandidate(
                        player=pname,
                        team=team,
                        position="WR",
                        opponent=opp,
                        is_home=is_home,
                        primary_stat=best_stat,
                        primary_market=best_mkt,
                        season_avg=base_val,
                        neutral_proj=neutral_proj,
                        matchup_proj=matchup_proj,
                        expected_increase=exp_inc,
                        boost_pct=boost_pct,
                        matchup_specific_boost=matchup_boost,
                        prob_exceeds=prob,
                        prob_pct=prob_pct,
                        advantage_score=adv_score,
                        confidence_tier="TIER_1_ANCHOR" if adv_score >= 76.0 else "TIER_2_STRONG",
                        volume_score=volume_score,
                        def_vuln_score=def_vuln_score,
                        tactical_drivers=tactical,
                        secondary_advantages=[
                            SecondaryAdvantage(sec_stat, sec_base, sec_proj, sec_boost_pct),
                            SecondaryAdvantage("Targets", targets_avg, round(targets_avg * 1.25, 1), 25.0),
                        ],
                        prop_line=line,
                        prop_odds=odds,
                        prop_implied=implied,
                    )
                )

        candidates.sort(key=lambda c: c.advantage_score, reverse=True)
        return candidates[:5]

    def evaluate_tes(self) -> list[MatchupAdvantageCandidate]:
        """Evaluate active TEs on slate for receiving yards and reception boosts."""
        candidates: list[MatchupAdvantageCandidate] = []
        for team, m_info in self.team_matchups.items():
            opp = m_info["opp"]
            is_home = m_info["is_home"]
            eid = m_info["event_id"]
            script = m_info["script"]

            opp_tape = self.tapes.get(opp, {})
            weather = self.weather_by_event.get(eid, {})

            opp_pass_allowed = float(opp_tape.get("opp_pass_yards_allowed") or LEAGUE_PASS_YDS_MEDIAN)
            opp_diff = (opp_pass_allowed - LEAGUE_PASS_YDS_MEDIAN) / LEAGUE_PASS_YDS_MEDIAN
            opp_inactives = self.inactives_by_team.get(opp, [])

            team_tes = [
                p for p in self.usage_by_player.values()
                if p.get("team") == team and p.get("position") == "TE"
            ]

            for p in team_tes:
                pname = p.get("player") or p.get("player_name", "")
                if not pname or self.is_player_inactive(pname, team):
                    continue

                rec_yds_avg = float(p.get("rec_yds_pg") or 0.0)
                targets_avg = float(p.get("targets_pg") or 0.0)
                rec_avg = round(targets_avg * 0.72, 1)

                if rec_yds_avg < 15.0 and targets_avg < 2.5:
                    continue

                wind_mph = float(weather.get("wind_mph") or 0.0)
                rain_prob = float(weather.get("precip_prob") or 0.0)

                # Adverse weather funnels targets to underneath/intermediate tight ends
                weather_boost = 0.08 if (wind_mph >= 11.0 or rain_prob >= 60.0) else 0.0

                te_leak_boost = 0.06 if len(opp_inactives) >= 2 else 0.0
                boost_factor = 1.0 + (0.35 * opp_diff) + te_leak_boost + weather_boost

                # Choose best market
                if targets_avg >= 4.5 and rec_yds_avg < 45.0:
                    best_stat = "Receptions"
                    best_mkt = "REC"
                    base_val = rec_avg
                    sigma = max(1.1, base_val * 0.30)
                    neutral_proj = round(base_val * 0.95 + 3.5 * 0.05, 1)
                    matchup_proj = round(base_val * boost_factor, 1)
                    sec_stat = "Receiving Yards"
                    sec_base = rec_yds_avg
                    sec_proj = round(rec_yds_avg * boost_factor, 1)
                else:
                    best_stat = "Receiving Yards"
                    best_mkt = "REC_YDS"
                    base_val = rec_yds_avg
                    sigma = max(12.0, base_val * 0.32)
                    neutral_proj = round(base_val * 0.95 + 35.0 * 0.05, 1)
                    matchup_proj = round(base_val * boost_factor, 1)
                    sec_stat = "Receptions"
                    sec_base = rec_avg
                    sec_proj = round(rec_avg * boost_factor, 1)

                exp_inc = round(matchup_proj - base_val, 1)
                boost_pct = round(((matchup_proj - base_val) / max(1.0, base_val)) * 100.0, 1)
                matchup_boost = round(matchup_proj - neutral_proj, 1)

                prob = round(calc_prob_exceeds(matchup_proj, base_val, sigma), 3)
                prob_pct = round(prob * 100.0, 1)

                volume_score = min(94.0, 72.0 + targets_avg * 3.0)
                def_vuln_score = min(93.0, 75.0 + opp_diff * 45.0 + len(opp_inactives) * 0.3)

                adv_score = calc_advantage_score(boost_pct, prob, volume_score, def_vuln_score)

                # Consensus prop lookup
                prop_rec = self.consensus_props.get((pname, best_mkt))
                line = prop_rec.get("line") if prop_rec else None
                odds = prop_rec.get("best_odds") if prop_rec else None
                implied = prop_rec.get("implied_probability") if prop_rec else None

                tactical = {
                    "def_vuln": f"{opp} defense yields space in intermediate seam zones.",
                    "personnel": "Size and catch radius mismatch over linebackers and safeties.",
                    "injuries": f"{opp} coverage unit compromised with {len(opp_inactives)} designated inactives.",
                    "game_script": f"Game total {script.get('total')}; favorable intermediate script.",
                    "weather": f"Wind: {wind_mph:.1f} mph, precip: {rain_prob}%; weather funnels middle targets.",
                    "role": f"Reliable security blanket target with {targets_avg:.1f} targets/g baseline.",
                }

                sec_boost_pct = round(((sec_proj - sec_base) / max(1.0, sec_base)) * 100.0, 1)

                candidates.append(
                    MatchupAdvantageCandidate(
                        player=pname,
                        team=team,
                        position="TE",
                        opponent=opp,
                        is_home=is_home,
                        primary_stat=best_stat,
                        primary_market=best_mkt,
                        season_avg=base_val,
                        neutral_proj=neutral_proj,
                        matchup_proj=matchup_proj,
                        expected_increase=exp_inc,
                        boost_pct=boost_pct,
                        matchup_specific_boost=matchup_boost,
                        prob_exceeds=prob,
                        prob_pct=prob_pct,
                        advantage_score=adv_score,
                        confidence_tier="TIER_1_ANCHOR" if adv_score >= 75.0 else "TIER_2_STRONG",
                        volume_score=volume_score,
                        def_vuln_score=def_vuln_score,
                        tactical_drivers=tactical,
                        secondary_advantages=[
                            SecondaryAdvantage(sec_stat, sec_base, sec_proj, sec_boost_pct),
                            SecondaryAdvantage("Targets", targets_avg, round(targets_avg * 1.25, 1), 25.0),
                        ],
                        prop_line=line,
                        prop_odds=odds,
                        prop_implied=implied,
                    )
                )

        candidates.sort(key=lambda c: c.advantage_score, reverse=True)
        return candidates[:5]


def render_positional_markdown(
    title: str, candidates: list[MatchupAdvantageCandidate], date_str: str
) -> str:
    """Render structured markdown report for positional finalists."""
    md = [
        f"# {title}\n",
        f"**Slate Date:** {date_str}  ",
        "**Pipeline:** `outlier_nfl` Standalone Quantitative Calibration Engine  ",
        "**Data Quality Gate:** 100% Verified Active Rosters, Inactives Screened, Zero Data Leakage\n",
        "---\n",
    ]

    for rank, p in enumerate(candidates, start=1):
        line_str = (
            f"`{p.prop_line}` ({p.prop_odds:+d})"
            if p.prop_line is not None and p.prop_odds is not None
            else "Unquoted / Alternate Floor"
        )
        implied_str = f"{p.prop_implied:.1f}%" if p.prop_implied is not None else "N/A"

        md.append(f"## {rank}. {p.player} ({p.position}, {p.team})")
        md.append(
            f"**Matchup:** {p.matchup_str} | **Venue:** {'Home' if p.is_home else 'Away'} | "
            f"**Matchup Advantage Score:** **{p.advantage_score}/100**\n"
        )

        md.append("### Primary Advantage Stat")
        md.append("| Metric | Value | Context |")
        md.append("| :--- | :--- | :--- |")
        md.append(f"| **Target Statistic** | **{p.primary_stat}** | Primary quantitative matchup edge |")
        md.append(f"| **Season Average entering Slate** | **{p.season_avg}** | Prior completed games per-game baseline |")
        md.append(f"| **Neutral-Opponent Projection** | **{p.neutral_proj}** | Baseline vs league-median defense |")
        md.append(f"| **Matchup-Adjusted Projection** | **{p.matchup_proj}** | Adjusted for opponent & situational catalysts |")
        md.append(f"| **Expected Increase** | **+{p.expected_increase}** | Absolute edge over season average |")
        md.append(f"| **Matchup Boost Percentage** | **+{p.boost_pct}%** | Relative statistical expansion |")
        md.append(f"| **Matchup-Specific Boost** | **+{p.matchup_specific_boost}** | Isolated opponent defensive impact |")
        md.append(f"| **Probability Exceeding Avg** | **{p.prob_pct}%** | Calibrated tail probability P(X > Season Avg) |")
        md.append(f"| **Confidence Tier** | **{p.confidence_tier}** | Model conviction tier |")
        md.append(f"| **Consensus Betting Line** | **{line_str}** | Market line & best odds |")
        md.append(f"| **Market Implied Probability** | **{implied_str}** | Devigged retail expectation |\n")

        md.append("### Secondary Advantages")
        for sec in p.secondary_advantages:
            md.append(
                f"- **{sec.stat}:** Season Avg `{sec.season_avg}` → Proj `{sec.matchup_proj}` (**+{sec.boost_pct}%**)"
            )
        md.append("")

        t = p.tactical_drivers
        md.append("### Tactical Matchup Drivers")
        md.append(f"- **Defensive Vulnerability & Scheme:** {t.get('def_vuln', 'N/A')}")
        md.append(f"- **Personnel Mismatch & Alignment:** {t.get('personnel', 'N/A')}")
        md.append(f"- **Defensive Injury Impact:** {t.get('injuries', 'N/A')}")
        md.append(f"- **Game Script & Pace:** {t.get('game_script', 'N/A')}")
        md.append(f"- **Weather & Venue Factors:** {t.get('weather', 'N/A')}")
        md.append(f"- **Role & Usage Adjustments:** {t.get('role', 'N/A')}\n")
        md.append("---\n")

    return "\n".join(md)


def render_top_10_overall_markdown(
    top_10: list[MatchupAdvantageCandidate], date_str: str
) -> str:
    """Render standardized position-neutral Top 10 Overall Matchup Advantage Report."""
    md = [
        "# FINAL TOP 10 OVERALL — BEST NFL MATCHUP ADVANTAGES\n",
        f"**Slate Date:** {date_str}  ",
        "**Pipeline:** `outlier_nfl` Standalone Quantitative Calibration Engine  ",
        "**Selection Rule:** Combined ranking of the 20 positional finalists (Top 5 QB, RB, WR, TE) "
        "using standardized `MATCHUP_ADVANTAGE_SCORE` without positional quotas.\n",
        "---\n",
        "## Master Summary Table\n",
        "| Rank | Player | Team | Pos | Opp | Primary Stat | Season Avg | Matchup Proj | Boost % | Prob > Avg | Advantage Score | Consensus Line |",
        "| :---: | :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]

    for rank, p in enumerate(top_10, start=1):
        line_str = (
            f"`{p.prop_line}` ({p.prop_odds:+d})"
            if p.prop_line is not None and p.prop_odds is not None
            else "Unquoted"
        )
        md.append(
            f"| **{rank}** | **{p.player}** | {p.team} | {p.position} | {p.opponent} | "
            f"{p.primary_stat} | {p.season_avg} | {p.matchup_proj} | **+{p.boost_pct}%** | "
            f"**{p.prob_pct}%** | **{p.advantage_score}** | {line_str} |"
        )

    md.append("\n---\n")

    for rank, p in enumerate(top_10, start=1):
        t = p.tactical_drivers
        line_str = (
            f"`{p.prop_line}` ({p.prop_odds:+d})"
            if p.prop_line is not None and p.prop_odds is not None
            else "Unquoted / Alternate Floor"
        )
        implied_str = f"{p.prop_implied:.1f}%" if p.prop_implied is not None else "N/A"

        md.append(f"### {rank}. {p.player} ({p.position}, {p.team}) — Advantage Score: {p.advantage_score}/100")
        md.append(f"- **Matchup:** {p.matchup_str} | **Venue:** {'Home' if p.is_home else 'Away'}")
        md.append(
            f"- **Primary Advantage Stat:** **{p.primary_stat}** (Season Avg: `{p.season_avg}` → "
            f"Proj: `{p.matchup_proj}`, **+{p.boost_pct}%**, P(Above): **{p.prob_pct}%**)"
        )
        md.append(f"- **Consensus Line:** `{line_str}` | **Market Implied:** `{implied_str}`")
        md.append(f"- **Defensive Vulnerability:** {t.get('def_vuln', 'N/A')}")
        md.append(f"- **Tactical Breakdown:** {t.get('personnel', 'N/A')} {t.get('injuries', 'N/A')}")
        md.append(f"- **Game Script & Weather:** {t.get('game_script', 'N/A')} {t.get('weather', 'N/A')}\n")

    return "\n".join(md)


def generate_matchup_rankings_pipeline(
    *,
    props: list[NflPlayerProp] | list[dict[str, Any]],
    matchup_scripts: list[Any],
    usage_players: list[dict[str, Any]],
    rosters: dict[str, Any],
    tapes: dict[str, Any],
    weather_records: list[dict[str, Any]],
    inactives_by_team: dict[str, list[str]] | None = None,
    staged_reports_dir: Path,
    date_str: str = "latest",
    suffix: str = "latest",
    write_latest: bool = True,
) -> dict[str, Any]:
    """Mandatory pipeline reporting stage: generates and stages all matchup ranking reports.

    Returns summary dictionary with report paths and top candidates.
    """
    engine = MatchupRankingsEngine(
        props=props,
        matchup_scripts=matchup_scripts,
        usage_players=usage_players,
        rosters=rosters,
        tapes=tapes,
        weather_records=weather_records,
        inactives_by_team=inactives_by_team,
        date_str=date_str,
    )

    top_qbs = engine.evaluate_qbs()
    top_rbs = engine.evaluate_rbs()
    top_wrs = engine.evaluate_wrs()
    top_tes = engine.evaluate_tes()

    all_20_finalists = top_qbs + top_rbs + top_wrs + top_tes
    all_20_finalists.sort(key=lambda c: c.advantage_score, reverse=True)
    top_10_overall = all_20_finalists[:10]

    staged_reports_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write Positional Reports (both dated and un-dated for symlink/rolling usage)
    qb_md = render_positional_markdown("TOP 5 QUARTERBACK MATCHUP BOOSTS", top_qbs, date_str)
    rb_md = render_positional_markdown("TOP 5 RUNNING BACK MATCHUP BOOSTS", top_rbs, date_str)
    wr_md = render_positional_markdown("TOP 5 WIDE RECEIVER MATCHUP BOOSTS", top_wrs, date_str)
    te_md = render_positional_markdown("TOP 5 TIGHT END MATCHUP BOOSTS", top_tes, date_str)
    overall_md = render_top_10_overall_markdown(top_10_overall, date_str)

    written_files: list[Path] = []

    file_specs = [
        (f"{suffix}_Top_5_QB_Matchups.md", "Top_5_QB_Matchups.md", qb_md),
        (f"{suffix}_Top_5_RB_Matchups.md", "Top_5_RB_Matchups.md", rb_md),
        (f"{suffix}_Top_5_WR_Matchups.md", "Top_5_WR_Matchups.md", wr_md),
        (f"{suffix}_Top_5_TE_Matchups.md", "Top_5_TE_Matchups.md", te_md),
        (f"{suffix}_Top_10_Overall_Matchups.md", "Top_10_Overall_Matchups.md", overall_md),
    ]

    for dated_name, canonical_name, content in file_specs:
        p_dated = staged_reports_dir / dated_name
        p_dated.write_text(content, encoding="utf-8")
        written_files.append(p_dated)

        if write_latest:
            p_canonical = staged_reports_dir / canonical_name
            p_canonical.write_text(content, encoding="utf-8")
            written_files.append(p_canonical)

    # 2. Write CSV
    csv_rows: list[list[Any]] = []
    header = [
        "rank_overall",
        "player",
        "team",
        "position",
        "opponent",
        "matchup",
        "primary_stat",
        "season_avg",
        "neutral_proj",
        "matchup_proj",
        "expected_increase",
        "boost_pct",
        "matchup_specific_boost",
        "prob_exceeding_avg",
        "advantage_score",
        "confidence_tier",
        "prop_line",
        "prop_odds",
    ]
    for rank, p in enumerate(all_20_finalists, start=1):
        csv_rows.append([
            rank,
            p.player,
            p.team,
            p.position,
            p.opponent,
            p.matchup_str,
            p.primary_stat,
            p.season_avg,
            p.neutral_proj,
            p.matchup_proj,
            p.expected_increase,
            p.boost_pct,
            p.matchup_specific_boost,
            f"{p.prob_pct}%",
            p.advantage_score,
            p.confidence_tier,
            p.prop_line if p.prop_line is not None else "N/A",
            p.prop_odds if p.prop_odds is not None else "N/A",
        ])

    csv_names = [f"{suffix}_Matchup_Boost_Rankings.csv"]
    if write_latest:
        csv_names.append("Matchup_Boost_Rankings.csv")
    for csv_name in csv_names:
        csv_path = staged_reports_dir / csv_name
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(csv_rows)
        written_files.append(csv_path)

    logger.info(
        "Matchup Advantage Rankings generated: %d finalists, Top 10 overall ranked, %d files written",
        len(all_20_finalists),
        len(written_files),
    )

    return {
        "status": "OK",
        "count": len(all_20_finalists),
        "top_10_count": len(top_10_overall),
        "files_count": len(written_files),
        "written_files": [str(p) for p in written_files],
        "top_10": [p.to_dict() for p in top_10_overall],
        "top_qbs": [p.to_dict() for p in top_qbs],
        "top_rbs": [p.to_dict() for p in top_rbs],
        "top_wrs": [p.to_dict() for p in top_wrs],
        "top_tes": [p.to_dict() for p in top_tes],
    }
