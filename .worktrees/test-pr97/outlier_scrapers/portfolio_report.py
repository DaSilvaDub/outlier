import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any

def parse_args(args=None):
    parser = argparse.ArgumentParser(description="Portfolio Risk Shadow Report")
    parser.add_argument("--from", dest="from_date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--packs-dir", dest="packs_dir", default="packs", help="Path to packs directory")
    return parser.parse_args(args)

def run_report(from_date: str, to_date: str, packs_dir: str) -> dict[str, Any]:
    # Accumulate into typed locals rather than into the result dict directly.
    # A heterogeneous dict literal (int / float / list / dict / set values) infers as
    # dict[str, object], and `object` supports neither `+=` nor `.get`/`.add`/`.append`,
    # which is what produced 20 mypy errors here. Locals also let the two set->list
    # conversions and the max_consecutive->consecutive rename happen without mutating a
    # value's type in place, which no dict annotation (or TypedDict) can express.
    start_date = datetime.strptime(from_date, "%Y-%m-%d")
    end_date = datetime.strptime(to_date, "%Y-%m-%d")

    packs_path = Path(packs_dir)

    valid_days = 0
    rejected_days = 0
    gaps: list[str] = []
    legacy_total_units = 0.0
    shadow_total_units = 0.0
    caps_binding: dict[str, int] = {}
    zeroed_rows = 0
    quantization_differences = 0
    missing_identity_counts = 0
    duplicate_collapse_counts = 0
    book_source_exposure: dict[str, float] = {}
    order_invariance_hashes: set[str] = set()
    policy_fingerprints: set[str] = set()
    max_consecutive_shadow_days = 0

    current_date = start_date
    consecutive = 0

    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        sidecar_path = packs_path / date_str / "portfolio_risk.json"

        is_valid = False
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                git_sha = data.get("code_git_sha")
                if git_sha and isinstance(git_sha, str) and git_sha.strip() and git_sha != "unknown":
                    is_valid = True
                    valid_days += 1
                    legacy_total_units += float(data.get("legacy_total_units", 0))
                    shadow_total_units += float(data.get("shadow_total_units", 0))

                    for cap, count in data.get("caps_binding", {}).items():
                        caps_binding[cap] = caps_binding.get(cap, 0) + count

                    zeroed_rows += int(data.get("zeroed_rows", 0))
                    quantization_differences += int(data.get("quantization_differences", 0))
                    missing_identity_counts += int(data.get("missing_identity_counts", 0))
                    duplicate_collapse_counts += int(data.get("duplicate_collapse_counts", 0))

                    for book, exposure in data.get("book_source_exposure", {}).items():
                        book_source_exposure[book] = book_source_exposure.get(book, 0) + float(exposure)

                    hash_val = data.get("order_invariance_hash")
                    if hash_val:
                        order_invariance_hashes.add(hash_val)

                    fingerprint = data.get("policy_fingerprint")
                    if fingerprint:
                        policy_fingerprints.add(fingerprint)
            except Exception:
                pass

        if is_valid:
            consecutive += 1
            if consecutive > max_consecutive_shadow_days:
                max_consecutive_shadow_days = consecutive
        else:
            consecutive = 0
            rejected_days += 1
            gaps.append(date_str)

        current_date += timedelta(days=1)

    unit_retention_ratio = 0.0
    if legacy_total_units > 0:
        unit_retention_ratio = shadow_total_units / legacy_total_units

    # Key order matches the previous dict literal (minus max_consecutive_shadow_days,
    # which was deleted after being copied into consecutive_shadow_days) so the
    # --- JSON --- output is byte-identical for the same inputs.
    return {
        "valid_days": valid_days,
        "rejected_days": rejected_days,
        "gaps": gaps,
        "legacy_total_units": legacy_total_units,
        "shadow_total_units": shadow_total_units,
        "unit_retention_ratio": unit_retention_ratio,
        "caps_binding": caps_binding,
        "zeroed_rows": zeroed_rows,
        "quantization_differences": quantization_differences,
        "missing_identity_counts": missing_identity_counts,
        "duplicate_collapse_counts": duplicate_collapse_counts,
        "book_source_exposure": book_source_exposure,
        "order_invariance_hashes": list(order_invariance_hashes),
        "policy_fingerprints": list(policy_fingerprints),
        "consecutive_shadow_days": max_consecutive_shadow_days,
    }

def generate_text_report(report: dict) -> str:
    lines = [
        "Shadow Report & Activation Evidence",
        "===================================",
        f"Valid Days: {report['valid_days']}",
        f"Rejected/Gap Days: {report['rejected_days']}",
        f"Gaps: {', '.join(report['gaps']) if report['gaps'] else 'None'}",
        f"Max Consecutive Shadow Days: {report['consecutive_shadow_days']} (Needs 14 for gate)",
        "",
        "--- Metrics ---",
        f"Legacy Total Units: {report['legacy_total_units']:.2f}",
        f"Shadow Total Units: {report['shadow_total_units']:.2f}",
        f"Unit Retention Ratio: {report['unit_retention_ratio']:.4f}",
        f"Zeroed Rows: {report['zeroed_rows']}",
        f"Quantization Differences: {report['quantization_differences']}",
        f"Missing Identity Counts: {report['missing_identity_counts']}",
        f"Duplicate Collapse Counts: {report['duplicate_collapse_counts']}",
        "",
        "--- Caps Binding ---"
    ]
    for cap, count in report.get("caps_binding", {}).items():
        lines.append(f"  {cap}: {count}")
        
    lines.append("")
    lines.append("--- Book Source Exposure ---")
    for book, exp in report.get("book_source_exposure", {}).items():
        lines.append(f"  {book}: {exp:.2f}")
        
    lines.append("")
    lines.append("--- Stability ---")
    hashes = report.get('order_invariance_hashes', [])
    lines.append(f"Order Invariance Hashes ({len(hashes)} unique):")
    for h in hashes:
        lines.append(f"  - {h}")
        
    fingerprints = report.get('policy_fingerprints', [])
    lines.append(f"Policy Fingerprints ({len(fingerprints)} unique):")
    for f in fingerprints:
        lines.append(f"  - {f}")
        
    return "\n".join(lines)

def main():
    args = parse_args()
    report = run_report(args.from_date, args.to_date, args.packs_dir)
    print(generate_text_report(report))
    print("\n--- JSON ---")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
