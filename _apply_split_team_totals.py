"""One-shot applicator for feat/split-team-totals. Run then commit immediately."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def write(rel: str, content: str) -> None:
    path = ROOT / rel
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {rel} ({path.stat().st_size})")


# ---------------------------------------------------------------------------
# paths.py
# ---------------------------------------------------------------------------
write(
    "outlier_scrapers/paths.py",
    '''from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"


@dataclass(frozen=True)
class LeaguePaths:
    league: str
    root: Path
    raw: Path
    normalized: Path
    reports: Path

    def ensure(self) -> "LeaguePaths":
        for path in (self.raw, self.normalized, self.reports):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def timestamped(self, directory: Path, stem: str, suffix: str = ".json") -> Path:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return directory / f"{self.league.lower()}_{stem}_{timestamp}{suffix}"

    @property
    def cards(self) -> Path:
        return self.root / "cards"

    def _latest(self, directory: Path, stem: str, suffix: str = ".json") -> Path:
        return directory / f"{self.league.lower()}_{stem}_latest{suffix}"

    def games_normalized_latest(self) -> Path:
        return self._latest(self.normalized, "games")

    def props_normalized_latest(self) -> Path:
        return self._latest(self.normalized, "props")

    def games_enrichment_latest(self) -> Path:
        return self._latest(self.normalized, "games_enrichment")

    def games_line_movement_latest(self) -> Path:
        return self._latest(self.normalized, "games_line_movement")

    def line_movement_latest(self) -> Path:
        return self._latest(self.normalized, "line_movement")

    def cards_latest(self) -> Path:
        return self._latest(self.cards, "cards")

    def games_cards_latest(self) -> Path:
        return self._latest(self.cards, "games_cards")


def league_paths(league: str) -> LeaguePaths:
    token = league.strip().upper()
    root = DATA_DIR / token
    return LeaguePaths(
        league=token,
        root=root,
        raw=root / "raw",
        normalized=root / "normalized",
        reports=root / "reports",
    )


def session_dir() -> Path:
    return CONFIG_DIR / ".outlier_session"


def storage_state_file() -> Path:
    return session_dir() / "storage_state.json"


def legacy_session_file() -> Path:
    return CONFIG_DIR / "outlier_session.json"


def bearer_token_file() -> Path:
    return session_dir() / "api_bearer_token.txt"


def session_metadata_file() -> Path:
    return session_dir() / "session_metadata.json"


def api_request_headers_file() -> Path:
    return session_dir() / "api_request_headers.json"


def otp_status_file() -> Path:
    return session_dir() / "otp_status.json"


def otp_code_file() -> Path:
    return session_dir() / "otp_code.txt"


def storage_state_candidates() -> list[Path]:
    return [storage_state_file(), legacy_session_file()]
''',
)

# ---------------------------------------------------------------------------
# game_totals.py patches (in-place)
# ---------------------------------------------------------------------------
gt = (ROOT / "outlier_scrapers/game_totals.py").read_text(encoding="utf-8")

replacements = [
(
'''def _is_candidate_total(row: dict[str, Any]) -> bool:
    mt = row.get("market_type")
    if mt == "GAMELINE" and not row.get("player_id"):
        sel = (row.get("selection") or "").lower()
        prop = str(row.get("_proposition") or row.get("market") or "").upper()
        return "total o/u" in sel or prop == "TOTAL"
    if mt == "TEAM_PROP" and not row.get("player_id"):
        sel = (row.get("selection") or "").lower()
        prop = str(row.get("_proposition") or "").upper()
        return "team total" in sel or prop == "POINTS"
    return False
''',
'''TOTAL_KIND_GAME = "game"
TOTAL_KIND_TEAM = "team"


def _is_candidate_game_total(row: dict[str, Any]) -> bool:
    if row.get("player_id"):
        return False
    mt = row.get("market_type")
    if mt != "GAMELINE":
        return False
    sel = (row.get("selection") or "").lower()
    prop = str(row.get("_proposition") or row.get("market") or "").upper()
    return "total o/u" in sel or prop == "TOTAL"


def _is_candidate_team_total(row: dict[str, Any]) -> bool:
    if row.get("player_id"):
        return False
    mt = row.get("market_type")
    if mt != "TEAM_PROP":
        return False
    sel = (row.get("selection") or "").lower()
    prop = str(row.get("_proposition") or row.get("market") or "").upper()
    return "team total" in sel or prop == "POINTS"


def _is_candidate_total(row: dict[str, Any], *, kind: str | None = None) -> bool:
    """Match candidate rows that correspond to totals markets.

    kind=None matches either game or team totals (legacy helpers).
    """
    if kind == TOTAL_KIND_GAME:
        return _is_candidate_game_total(row)
    if kind == TOTAL_KIND_TEAM:
        return _is_candidate_team_total(row)
    return _is_candidate_game_total(row) or _is_candidate_team_total(row)
'''),
(
'''    "source_timestamps",
]
''',
'''    "source_timestamps",
]
# Shared schema: team totals use the same columns; files are split by stream.
TEAM_TOTALS_HEADER = GAME_TOTALS_HEADER
TOTALS_HEADER = GAME_TOTALS_HEADER
'''),
(
'''def is_eligible_total_record(rec: dict[str, Any]) -> bool:
    mt = str(rec.get("market_type") or "").upper()
    prop = str(rec.get("proposition") or rec.get("market") or "").upper()
    scope = str(rec.get("scope") or "").lower()
    if scope and scope not in FULL_GAME_SCOPES:
        return False
    if mt == "GAMELINE" and prop == "TOTAL":
        return True
    if mt == "TEAM_PROP" and prop == "POINTS":
        return True
    return False
''',
'''def _scope_is_full_game(scope: str) -> bool:
    return (not scope) or scope in FULL_GAME_SCOPES


def is_game_total_record(rec: dict[str, Any]) -> bool:
    """Full-game market totals only (GAMELINE / TOTAL)."""
    mt = str(rec.get("market_type") or "").upper()
    prop = str(rec.get("proposition") or rec.get("market") or "").upper()
    scope = str(rec.get("scope") or "").lower()
    if not _scope_is_full_game(scope):
        return False
    return mt == "GAMELINE" and prop == "TOTAL"


def is_team_total_record(rec: dict[str, Any]) -> bool:
    """Full-game team totals only (TEAM_PROP / POINTS)."""
    mt = str(rec.get("market_type") or "").upper()
    prop = str(rec.get("proposition") or rec.get("market") or "").upper()
    scope = str(rec.get("scope") or "").lower()
    if not _scope_is_full_game(scope):
        return False
    return mt == "TEAM_PROP" and prop == "POINTS"


def is_eligible_total_record(rec: dict[str, Any], *, kind: str | None = None) -> bool:
    """Eligibility for the combined totals projection board.

    kind=None matches either stream (legacy). Prefer is_game_total_record /
    is_team_total_record for new call sites.
    """
    if kind == TOTAL_KIND_GAME:
        return is_game_total_record(rec)
    if kind == TOTAL_KIND_TEAM:
        return is_team_total_record(rec)
    return is_game_total_record(rec) or is_team_total_record(rec)
'''),
(
'''def _index_candidates_by_market(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_candidate_total(row):
            continue
        mid = str(row.get("market_id") or "")
        if mid and mid not in out:
            out[mid] = row
    return out


def _group_records_by_market(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if not is_eligible_total_record(rec):
            continue
        mid = str(rec.get("market_id") or "")
        if not mid:
            continue
        grouped.setdefault(mid, []).append(rec)
    return grouped


def build_game_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for all eligible full-game totals in games_norm."""
    records = (games_norm or {}).get("records") or []
    by_market = _group_records_by_market(records)
    cand_by_market = _index_candidates_by_market(candidate_rows)
    now = now or datetime.now().astimezone()
    output: list[dict[str, Any]] = []
    freshness = _props_freshness(
        games_norm or {}, now=now, max_age_hours=6.0, max_future_hours=5.0 / 60.0
    )
    source_flags: list[str] = []
    if freshness.is_stale:
        source_flags.append("STALE_DATA")
    if (games_norm or {}).get("fetch_errors"):
        source_flags.append("SOURCE_FETCH_ERRORS")
    from outlier_scrapers import pack as pack_module

    for market_id, market_records in by_market.items():
        identity = market_records[0]
        event_id = str(identity.get("event_id") or "")
        mt = str(identity.get("market_type") or "")
        prop = str(identity.get("proposition") or identity.get("market") or "")
        scope = str(identity.get("scope") or "full_game")
        total_kind = "team" if mt == "TEAM_PROP" else "game"
        team = identity.get("team") or identity.get("team_raw") or ""
        matchup = identity.get("matchup") or identity.get("matchup_raw") or ""
''',
'''def _index_candidates_by_market(
    rows: list[dict[str, Any]], *, kind: str
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _is_candidate_total(row, kind=kind):
            continue
        mid = str(row.get("market_id") or "")
        if mid and mid not in out:
            out[mid] = row
    return out


def _group_records_by_market(
    records: list[dict[str, Any]], *, kind: str
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if not is_eligible_total_record(rec, kind=kind):
            continue
        mid = str(rec.get("market_id") or "")
        if not mid:
            continue
        grouped.setdefault(mid, []).append(rec)
    return grouped


def build_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    kind: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for one totals stream (game or team)."""
    if kind not in (TOTAL_KIND_GAME, TOTAL_KIND_TEAM):
        raise ValueError(f"unsupported totals kind: {kind!r}")
    records = (games_norm or {}).get("records") or []
    by_market = _group_records_by_market(records, kind=kind)
    cand_by_market = _index_candidates_by_market(candidate_rows, kind=kind)
    now = now or datetime.now().astimezone()
    output: list[dict[str, Any]] = []
    freshness = _props_freshness(
        games_norm or {}, now=now, max_age_hours=6.0, max_future_hours=5.0 / 60.0
    )
    source_flags: list[str] = []
    if freshness.is_stale:
        source_flags.append("STALE_DATA")
    if (games_norm or {}).get("fetch_errors"):
        source_flags.append("SOURCE_FETCH_ERRORS")
    from outlier_scrapers import pack as pack_module

    for market_id, market_records in by_market.items():
        identity = market_records[0]
        event_id = str(identity.get("event_id") or "")
        prop = str(identity.get("proposition") or identity.get("market") or "")
        scope = str(identity.get("scope") or "full_game")
        total_kind = kind
        team = identity.get("team") or identity.get("team_raw") or ""
        matchup = identity.get("matchup") or identity.get("matchup_raw") or ""
'''),
(
'''    output.sort(
        key=lambda r: (-(float(r["edge_pct"]) if r.get("edge_pct") not in (None, "") else -1.0), r.get("market_id", ""))
    )
    return output


def _empty_row(
''',
'''    output.sort(
        key=lambda r: (-(float(r["edge_pct"]) if r.get("edge_pct") not in (None, "") else -1.0), r.get("market_id", ""))
    )
    return output


def build_game_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for game totals only (GAMELINE / TOTAL)."""
    return build_totals(
        candidate_rows, games_norm, sport=sport, kind=TOTAL_KIND_GAME, now=now
    )


def build_team_totals(
    candidate_rows: list[dict[str, Any]],
    games_norm: dict[str, Any] | None,
    *,
    sport: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build projection rows for team totals only (TEAM_PROP / POINTS)."""
    return build_totals(
        candidate_rows, games_norm, sport=sport, kind=TOTAL_KIND_TEAM, now=now
    )


def _empty_row(
'''),
]

for i, (old, new) in enumerate(replacements):
    if old not in gt:
        raise SystemExit(f"game_totals replacement {i} not found")
    gt = gt.replace(old, new, 1)

(ROOT / "outlier_scrapers/game_totals.py").write_text(gt, encoding="utf-8", newline="\n")
print("patched game_totals.py", (ROOT / "outlier_scrapers/game_totals.py").stat().st_size)

# ---------------------------------------------------------------------------
# runner_common — import from previously written content via re-read template
# ---------------------------------------------------------------------------
# Reuse the full file we already designed: run inline by importing write of full body.
exec(open(ROOT / "_apply_split_team_totals_runner_common.py", encoding="utf-8").read()) if (ROOT / "_apply_split_team_totals_runner_common.py").exists() else None

print("apply core done — remaining files via sibling scripts")
print("OK core paths+game_totals")
