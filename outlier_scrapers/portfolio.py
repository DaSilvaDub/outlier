import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True)
class PortfolioPolicy:
    stake_increment: float
    max_wager_units: float
    max_daily_units: float
    max_event_units: float
    max_player_units: float
    max_team_units: float
    max_market_type_units: float
    max_correlated_cluster_units: float
    max_book_units: float


@dataclass(frozen=True)
class AllocationResult:
    allocated_units: dict[str, float]
    cap_reasons: dict[str, list[str]]
    binding_constraints: list[str]
    utilization: dict[str, float]
    order_invariance_hash: str


def _get_group_keys(row: Mapping[str, Any]) -> dict[str, str]:
    keys = {}
    keys["wager"] = f"wager:{row.get('stable_wager_id', '')}"
    keys["daily"] = "daily:all"
    if row.get("event_id"):
        keys["event"] = f"event:{row['event_id']}"
    if row.get("player_id"):
        keys["player"] = f"player:{row['player_id']}"
    if row.get("team"):
        keys["team"] = f"team:{row['team']}"
    if row.get("market_type"):
        keys["market_type"] = f"market_type:{row['market_type']}"
    if row.get("cluster_id"):
        keys["cluster"] = f"cluster:{row['cluster_id']}"
    if row.get("sportsbook"):
        keys["book"] = f"book:{row['sportsbook']}"
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
    
    # Identify eligible rows and their pre_cap_units
    eligible_rows = []
    allocated_units = {}
    
    for row in rows:
        wager_id = row.get("stable_wager_id")
        if not wager_id:
            continue
            
        actionable = str(row.get("actionable")).lower() == "true"
        board = row.get("board") == "A"
        units = float(row.get("units", 0.0))
        
        if actionable and board and units > 0:
            eligible_rows.append(row)
        else:
            allocated_units[wager_id] = 0.0
            
    # Calculate group totals for pre_cap_units
    group_sums = defaultdict(float)
    row_keys = {}
    for row in eligible_rows:
        wager_id = row["stable_wager_id"]
        units = float(row.get("units", 0.0))
        keys = _get_group_keys(row)
        row_keys[wager_id] = keys
        for group_type, key in keys.items():
            group_sums[key] += units
            
    # Calculate available capacities
    available_capacities = {}
    for group_type, cap in capacities.items():
        # Will handle specific keys dynamically
        pass
        
    group_ratios = {}
    # To compute ratios properly, we need the available capacity per specific key
    # group_ratio = available_capacity / sum(pre_cap_units of wagers in group)
    for row in eligible_rows:
        wager_id = row["stable_wager_id"]
        keys = row_keys[wager_id]
        
        ratios_for_row = []
        for group_type, key in keys.items():
            # Available capacity = policy_cap - reserved_for_this_key
            cap = capacities[group_type]
            avail = max(0.0, cap - reserved.get(key, 0.0))
            
            pre_cap_sum = group_sums[key]
            if pre_cap_sum > 0:
                ratio = avail / pre_cap_sum
            else:
                ratio = 1.0
                
            group_ratios[key] = ratio
            
    # Proportional scale
    base_units = {}
    current_utilization = defaultdict(float)
    cap_reasons = defaultdict(list)
    
    for row in eligible_rows:
        wager_id = row["stable_wager_id"]
        keys = row_keys[wager_id]
        pre_cap = float(row.get("units", 0.0))
        
        min_ratio = 1.0
        binding_reasons = []
        for group_type, key in keys.items():
            r = group_ratios[key]
            if r < min_ratio:
                min_ratio = r
                binding_reasons = [key]
            elif r == min_ratio and r < 1.0:
                binding_reasons.append(key)
                
        scaled = pre_cap * min_ratio
        
        # quantize down
        inc = policy.stake_increment
        if inc > 0:
            # avoid floating point floor precision issues
            steps = int(round(scaled * 1e8) / round(inc * 1e8))
            base = float(steps) * inc
            base = round(base, 4)
        else:
            base = round(scaled, 4)
            
        # Defensive check against negative
        base = max(0.0, base)
        base_units[wager_id] = base
        allocated_units[wager_id] = base
        
        if min_ratio < 1.0:
            cap_reasons[wager_id] = binding_reasons
            
        for group_type, key in keys.items():
            current_utilization[key] += base
            
    # Greedy residual fill
    # Order wagers by (-edge, event_id, market_id, outcome_id, stable_wager_id)
    def sort_key(row):
        return (
            -float(row.get("edge_pct", 0.0)),
            row.get("event_id", ""),
            row.get("market_id", ""),
            row.get("outcome_id", ""),
            row.get("stable_wager_id", "")
        )
        
    sorted_rows = sorted(eligible_rows, key=sort_key)
    
    inc = policy.stake_increment
    if inc > 0:
        while True:
            added_any = False
            for row in sorted_rows:
                wager_id = row["stable_wager_id"]
                pre_cap = float(row.get("units", 0.0))
                current = allocated_units[wager_id]
                
                if current + inc > pre_cap + 1e-9:
                    continue
                    
                keys = row_keys[wager_id]
                can_add = True
                for group_type, key in keys.items():
                    cap = capacities[group_type]
                    avail = max(0.0, cap - reserved.get(key, 0.0))
                    if current_utilization[key] + inc > avail + 1e-9:
                        can_add = False
                        break
                        
                if can_add:
                    allocated_units[wager_id] = round(allocated_units[wager_id] + inc, 4)
                    for group_type, key in keys.items():
                        current_utilization[key] = round(current_utilization[key] + inc, 4)
                    added_any = True
            
            if not added_any:
                break

    # Compute utilization and binding constraints
    utilization = dict(current_utilization)
    binding_constraints = []
    
    for key, used in current_utilization.items():
        group_type = key.split(":")[0]
        cap = capacities.get(group_type, 0.0)
        avail = max(0.0, cap - reserved.get(key, 0.0))
        if avail > 0 and used >= avail - 1e-9:
            binding_constraints.append(key)
            
    binding_constraints = sorted(list(set(binding_constraints)))
    
    # Compute SHA-256 order_invariance_hash
    # deterministic allocation mapping sorted by stable_wager_id
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
