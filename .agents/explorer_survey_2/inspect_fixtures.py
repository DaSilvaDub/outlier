"""Inspect Outlier fixtures in tests/fixtures/outlier/."""
import json
from pathlib import Path
from collections import defaultdict
from statistics import median

fixtures_dir = Path(r"C:\Users\dasil\Dev\GitHub\cfb-analytics-worktrees\outlier-props-insights\tests\fixtures\outlier")

print("--- ALL FIXTURE FILES ---")
for f in sorted(fixtures_dir.glob("*")):
    print(f"{f.name:65} {f.stat().st_size:10} bytes")

print("\n--- NON-GAMELINE / INSIGHTS CONTENT ---")
for f in sorted(fixtures_dir.glob("*.json")):
    if "GAMELINE" in f.name or "schedule" in f.name:
        continue
    data = json.loads(f.read_text(encoding="utf-8"))
    print(f"{f.name}: {data}")

gameline_files = sorted(fixtures_dir.glob("*_GAMELINE.json"))
print(f"\n--- GAMELINE ANALYSIS ({len(gameline_files)} events) ---")

prop_stats = defaultdict(lambda: {"cards": 0, "events": set(), "books": set(), "books_per_event": defaultdict(set)})
all_outcome_keys = set()
player_id_occurrences = []

for f in gameline_files:
    event_id = f.stem.split("_")[1]
    data = json.loads(f.read_text(encoding="utf-8"))
    markets = data.get("markets", [])
    for m in markets:
        prop = m.get("proposition")
        prop_stats[prop]["cards"] += 1
        prop_stats[prop]["events"].add(event_id)
        for out in m.get("outcomes", []):
            all_outcome_keys.update(out.keys())
            if any("player" in k.lower() or "athlete" in k.lower() for k in out.keys()):
                player_id_occurrences.append((f.name, prop, out))
            for odd in out.get("odds", []):
                bk = odd.get("book")
                if bk:
                    prop_stats[prop]["books"].add(bk)
                    prop_stats[prop]["books_per_event"][event_id].add(bk)

for prop, s in sorted(prop_stats.items()):
    b_counts = [len(s["books_per_event"][ev]) for ev in s["events"]]
    med = median(b_counts) if b_counts else 0.0
    distinct_books = sorted(s["books"])
    print(f"Prop: {prop:<22} | Cards: {s['cards']:>2} | Events: {len(s['events'])}/3 | DistinctBooks: {len(distinct_books):>2} | MedianBooks: {med} | Books: {', '.join(distinct_books)}")

print("\n--- OUTCOME KEYS FOUND IN GAMELINE ---")
print(sorted(all_outcome_keys))
print("Player ID occurrences count:", len(player_id_occurrences))

print("\n--- TRAP 1 (ORDER MISMATCH) CHECK ---")
trap1_mismatches = 0
for f in gameline_files:
    data = json.loads(f.read_text(encoding="utf-8"))
    for m in data.get("markets", []):
        for out in m.get("outcomes", []):
            b_list = out.get("books", [])
            odds_books = [o.get("book") for o in out.get("odds", []) if o.get("book")]
            if b_list and odds_books and b_list != odds_books:
                trap1_mismatches += 1
                if trap1_mismatches <= 3:
                    print(f"Trap 1 mismatch in {f.name} prop={m.get('proposition')}: outcome.books={b_list} vs odds.books={odds_books}")
print("Total Trap 1 outcome.books != odds[].book mismatches:", trap1_mismatches)
