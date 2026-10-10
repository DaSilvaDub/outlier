"""Game-day weather calibration for NFL matchup scripts.

Implements the runbook's weather pillar: in open-air venues, sustained wind of
12-15+ mph or rain lowers deep passing volume in favor of tight-end check-downs
and power rushing; domes are unaffected.

Forecasts come from Open-Meteo (free, no key, global) for the hours from
kickoff through kickoff + 3h at the home stadium. Weather effects become
named ``PropSignal``s on each team's tape roles, so they flow through
``apply_matchup_signals`` exactly like matchup mismatches.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode

from outlier_nfl.config import PROP_LONG_PASS
from outlier_nfl.matchup import MatchupScript, PropSignal, _event_team_codes
from outlier_nfl.tape_nflverse import fetch_bytes
from outlier_nfl.utils import to_eastern_date

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WINDOW_HOURS = 3

WIND_MPH = 12.0
HIGH_WIND_MPH = 15.0
WET_PROB = 60.0
WET_MM = 1.0
COLD_F = 25.0

# Volume adjustments are fractional (-0.10 = 10% haircut), sized from nflverse
# 2019-2025 outdoor games vs. calm (<12 mph) games: team passing yards -5.7% at
# 12-15 mph and -9.5% at >=15 mph, rushing +3.9% at >=15 mph. WET_ADJ follows
# the runbook; nflverse has no precipitation history to test it against.
WIND_ADJ = -0.06
HIGH_WIND_ADJ = -0.10
WET_ADJ = -0.05
MIN_PASS_ADJ = -0.15
GROUND_ADJ = 0.04

# Home stadium coordinates (lat, lon). LAR/LAC share SoFi, NYG/NYJ share MetLife.
STADIUMS: dict[str, tuple[float, float]] = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4008), "BAL": (39.2780, -76.6227),
    "BUF": (42.7738, -78.7870), "CAR": (35.2258, -80.8528), "CHI": (41.8623, -87.6167),
    "CIN": (39.0955, -84.5161), "CLE": (41.5061, -81.6995), "DAL": (32.7473, -97.0945),
    "DEN": (39.7439, -105.0201), "DET": (42.3400, -83.0456), "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107), "IND": (39.7601, -86.1639), "JAX": (30.3239, -81.6373),
    "KC": (39.0489, -94.4839), "LAC": (33.9535, -118.3392), "LAR": (33.9535, -118.3392),
    "LV": (36.0909, -115.1833), "MIA": (25.9580, -80.2389), "MIN": (44.9737, -93.2575),
    "NE": (42.0909, -71.2643), "NO": (29.9511, -90.0812), "NYG": (40.8135, -74.0745),
    "NYJ": (40.8135, -74.0745), "PHI": (39.9008, -75.1675), "PIT": (40.4468, -80.0158),
    "SEA": (47.5952, -122.3316), "SF": (37.4030, -121.9700), "TB": (27.9759, -82.5033),
    "TEN": (36.1665, -86.7713), "WAS": (38.9078, -76.8645),
}
FIXED_ROOF = {"DET", "LV", "MIN", "NO", "LAR", "LAC"}
RETRACTABLE_ROOF = {"ARI", "ATL", "DAL", "HOU", "IND"}

FetchJson = Callable[[str], Mapping[str, Any]]


@dataclass(frozen=True)
class GameWeather:
    """Forecast summary and calibration tags for one game."""

    event_id: str
    home_team: str
    away_team: str
    kickoff_utc: str | None
    venue: str  # outdoor | indoor | retractable | neutral | unknown
    wind_mph: float | None = None
    gust_mph: float | None = None
    temp_f: float | None = None
    precip_mm: float | None = None
    precip_prob: float | None = None
    tags: tuple[str, ...] = ()
    pass_adjustment: float = 0.0
    # F21 provenance. forecast_status: ok | partial_window | no_window_hours |
    # not_needed (indoor) | venue_unverified | no_kickoff | no_stadium.
    # venue_source: schedule (a matched nflverse schedule row) | static (the
    # stadium tables only, so a neutral site cannot be ruled out).
    forecast_status: str = "not_fetched"
    venue_source: str = "static"
    hours_in_window: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        return payload


def _fetch_json(url: str) -> Mapping[str, Any]:
    return json.loads(fetch_bytes(url, timeout=30.0).decode("utf-8"))


def forecast_url(lat: float, lon: float, kickoff: datetime) -> str:
    end = kickoff + timedelta(hours=WINDOW_HOURS)
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,"
        "precipitation_probability,precipitation",
        "wind_speed_unit": "mph",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "mm",
        "timezone": "UTC",
        "start_date": kickoff.date().isoformat(),
        "end_date": end.date().isoformat(),
    }
    return f"{OPEN_METEO_URL}?{urlencode(params)}"


def _parse_kickoff(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


REQUIRED_SERIES = ("temperature_2m", "wind_speed_10m", "wind_gusts_10m",
                   "precipitation_probability", "precipitation")


def _finite(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def forecast_window_status(payload: Mapping[str, Any], kickoff: datetime) -> tuple[str, int]:
    """(status, complete hours) for the kickoff window (F21).

    ``ok`` only when every hour from kickoff's hour through kickoff + WINDOW_HOURS
    has a finite value for every required series.
    """
    hourly = payload.get("hourly") or {}
    start = kickoff.replace(minute=0, second=0, microsecond=0)
    expected = {start + timedelta(hours=h) for h in range(WINDOW_HOURS + 1)}
    complete: set[datetime] = set()
    for i, t in enumerate(hourly.get("time") or []):
        try:
            ts = datetime.fromisoformat(str(t)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts not in expected:
            continue
        if all(i < len(hourly.get(k) or []) and _finite((hourly.get(k) or [])[i])
               for k in REQUIRED_SERIES):
            complete.add(ts)
    n = len(complete)
    if n == len(expected):
        return "ok", n
    return ("no_window_hours" if n == 0 else "partial_window"), n


def weather_verifies(record: Mapping[str, Any] | None) -> bool:
    """Whether a weather record is evidence for the weather pillar (F21).

    A schedule-confirmed indoor venue needs no forecast; an outdoor venue needs a
    complete finite forecast window. Unknown, neutral or retractable venues, a
    static-table-only venue and records without provenance never verify.
    """
    if not record:
        return False
    venue = record.get("venue")
    if venue == "indoor":
        return record.get("venue_source") == "schedule"
    return venue == "outdoor" and record.get("forecast_status") == "ok"


def summarize_forecast(payload: Mapping[str, Any], kickoff: datetime) -> dict[str, float | None]:
    """Mean wind/temp, max gust/precip probability, summed precip over the game window."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    start = kickoff.replace(minute=0, second=0, microsecond=0)
    end = kickoff + timedelta(hours=WINDOW_HOURS)
    idx = []
    for i, t in enumerate(times):
        try:
            ts = datetime.fromisoformat(str(t)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if start <= ts <= end:
            idx.append(i)

    def values(key: str) -> list[float]:
        series = hourly.get(key) or []
        return [float(series[i]) for i in idx if i < len(series) and _finite(series[i])]

    def mean(key: str) -> float | None:
        v = values(key)
        return round(sum(v) / len(v), 1) if v else None

    def peak(key: str) -> float | None:
        v = values(key)
        return round(max(v), 1) if v else None

    precip = values("precipitation")
    return {
        "wind_mph": mean("wind_speed_10m"),
        "gust_mph": peak("wind_gusts_10m"),
        "temp_f": mean("temperature_2m"),
        "precip_mm": round(sum(precip), 1) if precip else None,
        "precip_prob": peak("precipitation_probability"),
    }


def classify(conditions: Mapping[str, float | None]) -> tuple[tuple[str, ...], float]:
    """Weather tags and the pass-volume adjustment for open-air conditions."""
    tags: list[str] = []
    adj = 0.0
    wind = conditions.get("wind_mph")
    if wind is not None and wind >= HIGH_WIND_MPH:
        tags.append("WEATHER_WIND_HIGH")
        adj += HIGH_WIND_ADJ
    elif wind is not None and wind >= WIND_MPH:
        tags.append("WEATHER_WIND")
        adj += WIND_ADJ
    prob, mm = conditions.get("precip_prob"), conditions.get("precip_mm")
    if prob is not None and mm is not None and prob >= WET_PROB and mm >= WET_MM:
        tags.append("WEATHER_WET")
        adj += WET_ADJ
    temp = conditions.get("temp_f")
    if temp is not None and temp <= COLD_F:
        tags.append("WEATHER_COLD")  # informational; no volume change on its own
    return tuple(tags), round(max(adj, MIN_PASS_ADJ), 2)


def venue_status(home: str, schedule_game: Mapping[str, Any] | None) -> str:
    """outdoor / indoor / retractable (closed or unknown) / neutral / unknown."""
    game = schedule_game or {}
    if str(game.get("location") or "").lower() == "neutral":
        return "neutral"
    roof = str(game.get("roof") or "").lower()
    if roof in ("dome", "closed"):
        return "indoor"
    if home in FIXED_ROOF:
        return "indoor"
    if home in RETRACTABLE_ROOF:
        return "outdoor" if roof == "open" else "retractable"
    if roof in ("outdoors", "open") or home in STADIUMS:
        return "outdoor"
    return "unknown"


def game_weather(
    event: Mapping[str, Any],
    schedule_game: Mapping[str, Any] | None = None,
    fetch_json: FetchJson = _fetch_json,
) -> GameWeather:
    """Forecast one slate event; only open-air venues with a kickoff are fetched."""
    event_id = str(event.get("eventId") or event.get("id") or "")
    home, away = _event_team_codes(event)
    kickoff = _parse_kickoff(event.get("scheduledTime") or event.get("startTime"))
    venue = venue_status(home, schedule_game)
    if venue == "indoor":
        status = "not_needed"
    elif venue != "outdoor":
        status = "venue_unverified"
    elif kickoff is None:
        status = "no_kickoff"
    elif home not in STADIUMS:
        status = "no_stadium"
    else:
        status = "not_fetched"
    base = GameWeather(
        event_id=event_id,
        home_team=home,
        away_team=away,
        kickoff_utc=kickoff.isoformat() if kickoff else None,
        venue=venue,
        forecast_status=status,
        venue_source="schedule" if schedule_game else "static",
    )
    if status != "not_fetched" or kickoff is None:
        return base
    lat, lon = STADIUMS[home]
    payload = fetch_json(forecast_url(lat, lon, kickoff))
    conditions = summarize_forecast(payload, kickoff)
    window_status, hours = forecast_window_status(payload, kickoff)
    tags, adj = classify(conditions)
    return replace(
        base,
        forecast_status=window_status,
        hours_in_window=hours,
        wind_mph=conditions["wind_mph"],
        gust_mph=conditions["gust_mph"],
        temp_f=conditions["temp_f"],
        precip_mm=conditions["precip_mm"],
        precip_prob=conditions["precip_prob"],
        tags=tags,
        pass_adjustment=adj,
    )


def weather_signals(
    weather: GameWeather, tapes: Mapping[str, Mapping[str, Any]]
) -> list[PropSignal]:
    """Named per-role signals for both teams when the forecast calls for a pass haircut."""
    if weather.pass_adjustment >= 0:
        return []
    adj = weather.pass_adjustment
    high = "WEATHER_WIND_HIGH" in weather.tags
    tag = next((t for t in weather.tags if t != "WEATHER_COLD"), "WEATHER_WIND")
    confidence = "HIGH" if high else "MEDIUM"
    desc = (
        f"{weather.wind_mph} mph wind" if weather.wind_mph is not None else "adverse weather"
    ) + (" + rain" if "WEATHER_WET" in weather.tags else "")
    plan: list[tuple[str, str, str, float, str]] = [
        ("qb", "PASS_YDS", "UNDER", adj, confidence),
        ("qb", PROP_LONG_PASS, "UNDER", adj, confidence),
        ("wr_deep", "REC_YDS", "UNDER", adj, confidence),
        ("wr_deep", "LONG_REC", "UNDER", adj, confidence),
        ("wr_slot", "REC_YDS", "UNDER", round(adj / 2, 2), "MEDIUM"),
    ]
    if high:
        # MEDIUM keeps these small boosts from promoting prop tiers.
        plan += [
            ("rb1", "RUSH_YDS", "OVER", GROUND_ADJ, "MEDIUM"),
            ("te", "REC", "OVER", GROUND_ADJ, "MEDIUM"),
        ]
    signals: list[PropSignal] = []
    for team in (weather.home_team, weather.away_team):
        roles = tapes.get(team, {})
        for role, market, side, value, conf in plan:
            player = roles.get(role)
            if not player:
                continue
            signals.append(
                PropSignal(
                    event_id=weather.event_id,
                    player_name=str(player),
                    team=team,
                    market=market,
                    side=side,
                    tag=tag,
                    reason=f"{desc} at kickoff: {role} pass-game haircut / ground lean",
                    confidence=conf,
                    volume_adjustment=value,
                )
            )
    return signals


def weather_note(weather: GameWeather) -> str | None:
    if weather.venue != "outdoor":
        return f"Weather: {weather.venue} venue, no adjustment."
    if weather.wind_mph is None and weather.temp_f is None:
        return None
    parts = [
        f"{weather.temp_f}F" if weather.temp_f is not None else None,
        f"wind {weather.wind_mph} mph (gusts {weather.gust_mph})" if weather.wind_mph is not None
        else None,
        f"precip {weather.precip_prob}% / {weather.precip_mm} mm"
        if weather.precip_prob is not None else None,
    ]
    text = ", ".join(p for p in parts if p)
    effect = (
        f"pass volume {weather.pass_adjustment:+.2f} ({', '.join(weather.tags)})"
        if weather.pass_adjustment < 0 else "no adjustment"
    )
    return f"Weather: {text}; {effect}."


def apply_weather(
    scripts: Iterable[MatchupScript],
    weathers: Mapping[str, GameWeather],
    tapes: Mapping[str, Mapping[str, Any]],
) -> list[MatchupScript]:
    """Append weather signals and a weather note to each script with a forecast."""
    out: list[MatchupScript] = []
    for script in scripts:
        weather = weathers.get(script.event_id)
        if weather is None:
            out.append(script)
            continue
        signals = weather_signals(weather, tapes)
        note = weather_note(weather)
        out.append(
            replace(
                script,
                prop_signals=script.prop_signals + tuple(signals),
                notes=script.notes + ((note,) if note else ()),
            )
        )
    return out


def _schedule_index(
    schedule_records: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    index: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for rec in schedule_records:
        key = (str(rec.get("home_team") or ""), str(rec.get("away_team") or ""))
        index.setdefault(key, []).append(rec)
    return index


def load_slate_weather(
    slate_events: Iterable[Mapping[str, Any]],
    schedule_records: Iterable[Mapping[str, Any]] = (),
    fetch_json: FetchJson = _fetch_json,
) -> dict[str, GameWeather]:
    """Forecast every slate event; a failed fetch leaves that game unadjusted."""
    index = _schedule_index(schedule_records)
    out: dict[str, GameWeather] = {}
    for event in slate_events:
        event_id = str(event.get("eventId") or event.get("id") or "")
        if not event_id:
            continue
        home, away = _event_team_codes(event)
        kickoff = _parse_kickoff(event.get("scheduledTime") or event.get("startTime"))
        candidates = index.get((home, away), [])
        game = next(
            (g for g in candidates if kickoff and g.get("gameday") == to_eastern_date(kickoff)),
            candidates[0] if len(candidates) == 1 else None,
        )
        try:
            out[event_id] = game_weather(event, game, fetch_json)
        except Exception as exc:  # forecast is an enhancement; never block the slate
            logger.warning("Weather unavailable for %s: %s", event_id, exc)
    return out
