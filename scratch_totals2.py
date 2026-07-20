import json
import os
import re

output_path = r"C:\Users\dasil\OneDrive\Desktop\today\totals_overs_90_100.md"

results = {"MLB": [], "WNBA": []}

for league in ['MLB', 'WNBA']:
    path = f'C:/Users/dasil/OneDrive/Documents/outlier/data/{league}/normalized/{league.lower()}_insights_latest.json'
    if not os.path.exists(path):
        continue
        
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
        
    for r in data.get('records', []):
        hit_rate = r.get('hit_rate_pct', 0)
        
        # 90 to 100 hit rate
        if 90 <= hit_rate <= 100:
            market = str(r.get('market_raw', '')).lower()
            text = str(r.get('text', '')).lower()
            
            # Check if it's a team total or game total
            if 'total' in market or 'total' in text or 'o/u' in market or 'o/u' in text:
                # We only want OVERS
                if 'over' in text or 'exceed' in text:
                    # Filter out player props if possible (subject_type == 'team')
                    # Actually, we can just grab everything that mentions 'team total' or 'total'
                    results[league].append(f"({hit_rate}% | {r.get('last_n_record')}) {r.get('text')}")

with open(output_path, "w", encoding="utf-8") as out:
    out.write("# Alt Lines: Team Totals & Game Totals (Overs) - 90-100% Hit Rate\n\n")
    
    for league in ['MLB', 'WNBA']:
        out.write(f"## {league}\n")
        if not results[league]:
            out.write("*No qualifying totals found in recent trends.* (Note: 90%+ hit rates on alt totals are rare).\n\n")
        else:
            # Deduplicate just in case
            unique_items = list(set(results[league]))
            for item in unique_items:
                out.write(f"- {item}\n")
            out.write("\n")

print("Totals file created successfully.")
