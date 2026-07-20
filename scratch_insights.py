import json
import os
import re

output_path = r"C:\Users\dasil\OneDrive\Desktop\today\100_percent_hits.md"

high_variance_keywords = [
    "home run", "stolen base", "double", "triple", "steals", "blocks",
    "first basket", "first inning", "nrfi", "yrfi", "margin of victory"
]

results = {"MLB": [], "WNBA": []}

for league in ['MLB', 'WNBA']:
    path = f'C:/Users/dasil/OneDrive/Documents/outlier/data/{league}/normalized/{league.lower()}_insights_latest.json'
    if not os.path.exists(path):
        continue
        
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
        
    for r in data.get('records', []):
        if r.get('hit_rate_pct') == 100:
            last_n = r.get('last_n_record', '')
            # Match 5/5 or 10/10
            if last_n in ["5/5", "10/10"]:
                text = r.get('text', '').lower()
                market = r.get('market_raw', '').lower()
                
                # Check variance
                is_high_variance = any(kw in text or kw in market for kw in high_variance_keywords)
                if not is_high_variance:
                    results[league].append(r['text'])

with open(output_path, "w", encoding="utf-8") as out:
    out.write("# 100% Hit Rate Props (L5 & L10)\n\n")
    out.write("*Note: Filtered out high-variance props (Home Runs, Stolen Bases, Doubles, Triples, Blocks, Steals, First Inning, etc.)*\n\n")
    
    for league in ['MLB', 'WNBA']:
        out.write(f"## {league}\n")
        if not results[league]:
            out.write("*No qualifying props found.*\n\n")
        else:
            for item in results[league]:
                out.write(f"- {item}\n")
            out.write("\n")

print("File created successfully.")
