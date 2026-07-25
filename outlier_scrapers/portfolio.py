import json
import hashlib
import math
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, Optional
from collections import defaultdict

def quantize_down(units: float, increment: float = 0.5) -> float:
    import math
    if increment <= 0:
        raise ValueError("Increment must be positive")
    return math.floor(units / increment) * increment

@dataclass
class PortfolioPolicy:
    schema_version: str = "1.0"
    policy_version: str = "1.0"
    mode: str = "shadow"
    streams_in_scope: list[str] = field(default_factory=lambda: ["candidates", "game_totals", "team_totals"])
    stake_increment: float = 0.5
    max_wager_units: float = 3.0
    max_daily_units: float = 20.0
    max_event_units: float = 4.0
    max_player_units: float = 3.0
    max_team_units: float = 5.0
    max_market_type_units: float = 6.0
    max_correlated_cluster_units: float = 5.0
    max_book_units: float = 8.0
    non_authoritative_book_policy: str = "flag_and_report_only"
    shadow_multipliers_neutral: bool = True

    def __init__(
        self,
        schema_version: str = "1.0",
        policy_version: str = "1.0",
        mode: str = "shadow",
        streams_in_scope: Optional[list[str]] = None,
        stake_increment: float = 0.5,
        max_wager_units: float = 3.0,
        max_daily_units: float = 20.0,
        max_event_units: float = 4.0,
        max_player_units: float = 3.0,
        max_team_units: float = 5.0,
        max_market_type_units: float = 6.0,
        max_correlated_cluster_units: float = 5.0,
        max_book_units: float = 8.0,
        non_authoritative_book_policy: str = "flag_and_report_only",
        shadow_multipliers_neutral: bool = True,
    calibration: Any = None,
    uncertainty: Any = None,
    drawdown: Any = None,
        shadow_mode: Optional[bool] = None,
        **kwargs: Any,
    ):
        self.schema_version = schema_version
        self.policy_version = policy_version
        if shadow_mode is not None:
            self.mode = "shadow" if shadow_mode else "enforce"
        else:
            self.mode = mode
        self.streams_in_scope = streams_in_scope if streams_in_scope is not None else ["candidates", "game_totals", "team_totals"]
        self.stake_increment = stake_increment
        self.max_wager_units = max_wager_units
        self.max_daily_units = max_daily_units
        self.max_event_units = max_event_units
        self.max_player_units = max_player_units
        self.max_team_units = max_team_units
        self.max_market_type_units = max_market_type_units
        self.max_correlated_cluster_units = max_correlated_cluster_units
        self.max_book_units = max_book_units
        self.non_authoritative_book_policy = non_authoritative_book_policy
        self.shadow_multipliers_neutral = shadow_multipliers_neutral

    @property
    def shadow_mode(self) -> bool:
        return self.mode == "shadow"


REQUIRED_POLICY_KEYS = {
    "schema_version", "policy_version", "mode", "streams_in_scope",
    "stake_increment", "max_wager_units", "max_daily_units", "max_event_units",
    "max_player_units", "max_team_units", "max_market_type_units",
    "max_correlated_cluster_units", "max_book_units",
    "non_authoritative_book_policy", "shadow_multipliers_neutral",
    "calibration", "uncertainty", "drawdown"
}


def load_portfolio_policy(path: Optional[Path | str] = None) -> PortfolioPolicy:
    if path is None:
        from outlier_scrapers import paths
        path = paths.PROJECT_ROOT / "config" / "portfolio_risk.json"
    else:
        path = Path(path)

    if not path.exists():
        return PortfolioPolicy()

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    extra_keys = set(data.keys()) - REQUIRED_POLICY_KEYS
    if extra_keys:
        raise ValueError(f"Unknown keys in portfolio policy schema: {extra_keys}")

    mode = data.get("mode")
    if mode not in ("shadow", "enforce"):
        raise ValueError(f"Invalid mode: {mode}. Must be 'shadow' or 'enforce'.")

    numeric_cap_keys = [
        "stake_increment", "max_wager_units", "max_daily_units", "max_event_units",
        "max_player_units", "max_team_units", "max_market_type_units",
        "max_correlated_cluster_units", "max_book_units"
    ]
    for key in numeric_cap_keys:
        val = data.get(key)
        if not isinstance(val, (int, float)) or val <= 0:
            raise ValueError(f"Cap key '{key}' must be finite and positive, got {val}")

    return PortfolioPolicy(**data)


def policy_fingerprint(policy: PortfolioPolicy) -> str:
    policy_dict = {
        "schema_version": policy.schema_version,
        "policy_version": policy.policy_version,
        "mode": policy.mode,
        "streams_in_scope": sorted(policy.streams_in_scope),
        "stake_increment": policy.stake_increment,
        "max_wager_units": policy.max_wager_units,
        "max_daily_units": policy.max_daily_units,
        "max_event_units": policy.max_event_units,
        "max_player_units": policy.max_player_units,
        "max_team_units": policy.max_team_units,
        "max_market_type_units": policy.max_market_type_units,
        "max_correlated_cluster_units": policy.max_correlated_cluster_units,
        "max_book_units": policy.max_book_units,
        "non_authoritative_book_policy": policy.non_authoritative_book_policy,
        "shadow_multipliers_neutral": policy.shadow_multipliers_neutral,
    }
    canonical_json = json.dumps(policy_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass
class EventExposure:
    home_team_id: str
    away_team_id: str


def build_event_exposure_index(games_norm_payload: Mapping[str, Any]) -> dict[str, EventExposure]:
    index = {}
    records = games_norm_payload.get("records", [])
    for rec in records:
        event_id = str(rec.get("event_id") or "")
        if not event_id:
            continue
        index[event_id] = EventExposure(
            home_team_id=str(rec.get("home_team_id") or ""),
            away_team_id=str(rec.get("away_team_id") or ""),
        )
    return index


def stable_wager_id(row: Mapping[str, Any]) -> str:
    sport = str(row.get("sport") or "")
    event_id = str(row.get("event_id") or "")

    line_val = row.get("normalized_line")
    if line_val is None or line_val == "" or line_val == "NA":
        norm_line = "NA"
    else:
        try:
            norm_line = f"{float(line_val):.2f}"
        except (ValueError, TypeError):
            norm_line = "NA"

    normalized_book = str(row.get("normalized_book") or "")

    market_id = row.get("market_id")
    outcome_id = row.get("outcome_id")
    if market_id and outcome_id:
        return f"{sport}:{event_id}:{market_id}:{outcome_id}:{norm_line}:{normalized_book}"

    risk_market_family = str(row.get("risk_market_family") or "")
    risk_subject_id = str(row.get("risk_subject_id") or "")
    risk_scope = str(row.get("risk_scope") or "")
    risk_side = str(row.get("risk_side") or "")

    return f"{sport}:{event_id}:{risk_market_family}:{risk_subject_id}:{risk_scope}:{risk_side}:{norm_line}:{normalized_book}"


def project_risk_identity(row: Mapping[str, Any], stream: str) -> dict[str, Any]:
    projected = dict(row)

    headline_side = str(row.get("headline_side") or row.get("side") or "").upper()
    risk_side = headline_side
    risk_scope = str(row.get("scope") or "full_game")

    projected["risk_side"] = risk_side
    projected["risk_scope"] = risk_scope

    exposure_team_ids = []
    missing = False

    home_team_id = str(row.get("home_team_id") or "")
    away_team_id = str(row.get("away_team_id") or "")
    team_id = str(row.get("team_id") or row.get("team") or "")

    if stream == "game_totals":
        risk_market_family = "game_total"
        risk_subject_id = f"{away_team_id}@{home_team_id}" if away_team_id and home_team_id else "game"
        if home_team_id and away_team_id:
            exposure_team_ids = [away_team_id, home_team_id]
        else:
            missing = True
    elif stream == "team_totals":
        risk_market_family = "team_total"
        risk_subject_id = team_id
        if team_id:
            exposure_team_ids = [team_id]
        else:
            missing = True
    else:  # player_props or candidates
        risk_market_family = "player_prop"
        player_id = str(row.get("player_id") or row.get("player") or "")
        risk_subject_id = player_id
        if team_id:
            exposure_team_ids = [team_id]
        else:
            missing = True

        if not player_id:
            missing = True

    projected["risk_market_family"] = risk_market_family
    projected["risk_subject_id"] = risk_subject_id
    projected["exposure_team_ids"] = exposure_team_ids

    book_raw = row.get("book") or row.get("bookLabel") or ""
    projected["normalized_book"] = str(book_raw)

    if projected["normalized_book"]:
        if row.get("is_proxy_book"):
            projected["book_source"] = "proxy_book"
        elif row.get("is_arbitrary") or row.get("book_source") == "arbitrary_first_available":
            projected["book_source"] = "arbitrary_first_available"
        elif row.get("book_source") == "explicit_best_price" or row.get("best_price") or row.get("book"):
            projected["book_source"] = "explicit_best_price"
        else:
            projected["book_source"] = "explicit_best_price"
    else:
        projected["book_source"] = "missing"
        missing = True

    projected["normalized_line"] = row.get("line") or row.get("priced_line") or "NA"
    projected["stable_wager_id"] = stable_wager_id(projected)

    opponent = row.get("opponent") or row.get("opp_name") or ""
    projected["opponent"] = str(opponent)

    projected["missing_risk_identity"] = missing
    return projected


def collapse_duplicate_outcomes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped = defaultdict(list)
    for r in rows:
        key = (
            r.get("sport"),
            r.get("event_id"),
            r.get("risk_market_family"),
            r.get("risk_scope"),
            r.get("risk_subject_id"),
            r.get("risk_side"),
            r.get("normalized_line")
        )
        grouped[key].append(r)

    result = []

    for key, group in grouped.items():
        def sort_key(row):
            odds = row.get("decimal_odds")
            if odds is None:
                odds = row.get("price", -999999)
            book = row.get("normalized_book", "")
            return (-float(odds), book)

        sorted_group = sorted(group, key=sort_key)

        winning_row = dict(sorted_group[0])
        winning_row["risk_role"] = "PRIMARY"

        sport = winning_row.get("sport", "")
        event_id = winning_row.get("event_id", "")

        def build_clusters(row):
            c = []
            if event_id:
                c.append(f"{sport}:{event_id}:same_event:1")
            if row.get("risk_subject_id"):
                c.append(f"{sport}:{event_id}:same_player:{row['risk_subject_id']}")
            c.append(f"{sport}:{event_id}:same_outcome:{row.get('risk_market_family')}:{row.get('risk_scope')}:{row.get('risk_subject_id')}:{row.get('risk_side')}")
            return c

        winning_row["correlation_cluster_ids"] = build_clusters(winning_row)
        winning_row["soft_correlation_multiplier"] = 1.0

        result.append(winning_row)

        for loser in sorted_group[1:]:
            loser_row = dict(loser)
            loser_row["risk_role"] = "DEDUPED_AWAY"
            loser_row["dedup_reason"] = "duplicate_outcome_other_book"
            loser_row["units"] = 0
            loser_row["correlation_cluster_ids"] = build_clusters(loser_row)
            loser_row["soft_correlation_multiplier"] = 1.0
            result.append(loser_row)

    return result


def compute_dedup_key(projected_row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(projected_row.get("sport", "")),
        str(projected_row.get("event_id", "")),
        str(projected_row.get("risk_market_family", "")),
        str(projected_row.get("risk_scope", "")),
        str(projected_row.get("risk_subject_id", "")),
        str(projected_row.get("risk_side", "")),
        str(projected_row.get("normalized_line", "")),
        str(projected_row.get("normalized_book", ""))
    )


def unify_and_dedup_streams(rows: Sequence[Mapping[str, Any]], policy: PortfolioPolicy) -> list[dict[str, Any]]:
    results = []
    groups = defaultdict(list)

    for r in rows:
        row = dict(r)

        if row.get("is_parlay"):
            row["risk_role"] = "EXCLUDED"
            row["exclusion_reason"] = "stream_excluded_parlay"
            results.append(row)
            continue

        key = compute_dedup_key(row)
        groups[key].append(row)

    for key, group in groups.items():
        if len(group) == 1:
            row = group[0]
            row["risk_role"] = "PRIMARY"
            results.append(row)
            continue

        def sort_key(r):
            is_cand = (r.get("stream") == "candidates")
            wager_id = str(r.get("stable_wager_id", ""))
            return (not is_cand, wager_id)

        sorted_group = sorted(group, key=sort_key)
        winner = sorted_group[0]
        winner["risk_role"] = "PRIMARY"
        results.append(winner)

        primary_units = winner.get("recommended_units_pre_news")
        if primary_units is None:
            primary_units = winner.get("portfolio_units")
        if primary_units is None:
            primary_units = winner.get("units", 0)

        for loser in sorted_group[1:]:
            if policy.shadow_mode:
                loser["portfolio_shadow_units"] = 0
                loser["portfolio_units_of_primary"] = primary_units
                loser["risk_role"] = "DERIVED_VIEW"
            else:
                loser["recommended_units_pre_news"] = 0
                loser["portfolio_units"] = 0
                loser["risk_role"] = "DERIVED_VIEW"
            results.append(loser)

    return sorted(results, key=lambda x: str(x.get("stable_wager_id", "")))


@dataclass(frozen=True)
class AllocationResult:
    allocated_units: dict[str, float]
    cap_reasons: dict[str, list[str]]
    binding_constraints: list[str]
    utilization: dict[str, float]
    order_invariance_hash: str


def _get_group_keys(row: Mapping[str, Any]) -> dict[str, str]:
    keys = {}
    wager_id = row.get("stable_wager_id") or stable_wager_id(row)
    if wager_id:
        keys["wager"] = f"wager:{wager_id}"
    keys["daily"] = "daily:all"
    if row.get("event_id"):
        keys["event"] = f"event:{row['event_id']}"

    player_id = row.get("risk_subject_id") or row.get("player_id") or row.get("player")
    if player_id:
        keys["player"] = f"player:{player_id}"

    exposure_teams = row.get("exposure_team_ids")
    if exposure_teams:
        for team_id in exposure_teams:
            keys[f"team:{team_id}"] = f"team:{team_id}"
    else:
        team_id = row.get("team_id") or row.get("team")
        if team_id:
            keys[f"team:{team_id}"] = f"team:{team_id}"

    mkt_family = row.get("risk_market_family") or row.get("market_type")
    if mkt_family:
        keys["market_type"] = f"market_type:{mkt_family}"

    for cluster_id in row.get("correlation_cluster_ids", []):
        keys[f"cluster:{cluster_id}"] = f"cluster:{cluster_id}"

    book = row.get("normalized_book") or row.get("book") or row.get("sportsbook")
    if book:
        keys["book"] = f"book:{book}"

    return keys


def _get_group_capacities(policy: PortfolioPolicy) -> dict[str, float]:
    return {
        "wager": policy.max_wager_units,
        "daily": policy.max_daily_units,
        "event": policy.max_event_units,
        "player": policy.max_player_units,
        "team": policy.max_team_units,
        "market_type": policy.max_market_type_units,
        "cluster": policy.max_correlated_cluster_units,
        "book": policy.max_book_units,
    }


def allocate_portfolio_risk(
    rows: Sequence[Mapping[str, Any]],
    policy: PortfolioPolicy,
    reserved_exposure: Optional[Mapping[str, float]] = None,
) -> AllocationResult:
    reserved = reserved_exposure or {}
    capacities = _get_group_capacities(policy)

    eligible_rows = []
    allocated_units = {}

    for r in rows:
        row = dict(r)
        wager_id = row.get("stable_wager_id") or stable_wager_id(row)
        if not wager_id:
            continue

        role = row.get("risk_role", "PRIMARY")
        pre_cap = float(row.get("pre_cap_units", row.get("units", 0.0)))

        is_actionable = str(row.get("actionable", "true")).lower() == "true"
        is_board_a = str(row.get("board", "A")).upper() == "A"
        is_missing_identity = bool(row.get("missing_risk_identity", False))

        if role == "PRIMARY" and pre_cap > 0.0 and is_actionable and is_board_a and not is_missing_identity:
            eligible_rows.append(row)
            allocated_units[wager_id] = round(pre_cap, 4)
        else:
            allocated_units[wager_id] = 0.0

    group_sums = defaultdict(float)
    row_keys = {}
    for row in eligible_rows:
        wager_id = row.get("stable_wager_id") or stable_wager_id(row)
        pre_cap = float(row.get("pre_cap_units", row.get("units", 0.0)))
        keys = _get_group_keys(row)
        row_keys[wager_id] = keys
        for key_name, group_key in keys.items():
            group_sums[group_key] += pre_cap

    group_ratios = {}
    for row in eligible_rows:
        wager_id = row.get("stable_wager_id") or stable_wager_id(row)
        keys = row_keys[wager_id]

        for key_name, group_key in keys.items():
            group_type = key_name.split(":")[0]
            cap = capacities.get(group_type, policy.max_wager_units)
            avail = max(0.0, cap - reserved.get(group_key, 0.0))

            pre_cap_sum = group_sums[group_key]
            if pre_cap_sum > 0:
                ratio = avail / pre_cap_sum
            else:
                ratio = 1.0

            group_ratios[group_key] = ratio

    base_units = {}
    current_utilization = defaultdict(float)
    cap_reasons = defaultdict(list)

    for row in eligible_rows:
        wager_id = row.get("stable_wager_id") or stable_wager_id(row)
        keys = row_keys[wager_id]
        pre_cap = float(row.get("pre_cap_units", row.get("units", 0.0)))

        min_ratio = 1.0
        binding_reasons = []
        for key_name, group_key in keys.items():
            r = group_ratios.get(group_key, 1.0)
            if r < min_ratio:
                min_ratio = r
                binding_reasons = [group_key]
            elif r == min_ratio and r < 1.0:
                binding_reasons.append(group_key)

        scaled = pre_cap * min_ratio

        inc = policy.stake_increment
        if inc > 0:
            base = quantize_down(scaled, inc)
        else:
            base = scaled

        base = round(max(0.0, base), 4)
        base_units[wager_id] = base
        allocated_units[wager_id] = base

        if min_ratio < 1.0:
            cap_reasons[wager_id] = binding_reasons

        for key_name, group_key in keys.items():
            current_utilization[group_key] = round(current_utilization[group_key] + base, 4)

    def sort_key(row):
        edge = float(row.get("risk_adjusted_edge", row.get("edge_pct", 0.0)))
        return (
            -edge,
            str(row.get("event_id", "")),
            str(row.get("market_id", "")),
            str(row.get("outcome_id", "")),
            str(row.get("stable_wager_id", ""))
        )

    sorted_rows = sorted(eligible_rows, key=sort_key)

    inc = policy.stake_increment
    if inc > 0:
        while True:
            added_any = False
            for row in sorted_rows:
                wager_id = row.get("stable_wager_id") or stable_wager_id(row)
                pre_cap = float(row.get("pre_cap_units", row.get("units", 0.0)))
                current = allocated_units[wager_id]

                if current + inc > pre_cap + 1e-9:
                    continue

                keys = row_keys[wager_id]
                can_add = True
                for key_name, group_key in keys.items():
                    group_type = key_name.split(":")[0]
                    cap = capacities.get(group_type, policy.max_wager_units)
                    avail = max(0.0, cap - reserved.get(group_key, 0.0))
                    if current_utilization[group_key] + inc > avail + 1e-9:
                        can_add = False
                        break

                if can_add:
                    allocated_units[wager_id] = round(allocated_units[wager_id] + inc, 4)
                    for key_name, group_key in keys.items():
                        current_utilization[group_key] = round(current_utilization[group_key] + inc, 4)
                    added_any = True

            if not added_any:
                break

    utilization = {k: round(v, 4) for k, v in current_utilization.items()}
    binding_constraints = []

    for group_key, used in current_utilization.items():
        group_type = group_key.split(":")[0]
        cap = capacities.get(group_type, policy.max_wager_units)
        avail = max(0.0, cap - reserved.get(group_key, 0.0))
        if avail > 0 and used >= avail - 1e-9:
            binding_constraints.append(group_key)

    binding_constraints = sorted(list(set(binding_constraints)))

    sorted_alloc = {k: round(allocated_units[k], 4) for k in sorted(allocated_units.keys())}
    hash_input = json.dumps(sorted_alloc, separators=(",", ":"))
    order_invariance_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

    return AllocationResult(
        allocated_units=allocated_units,
        cap_reasons=dict(cap_reasons),
        binding_constraints=binding_constraints,
        utilization=utilization,
        order_invariance_hash=order_invariance_hash,
    )
