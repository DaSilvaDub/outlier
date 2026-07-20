import json
import os
import re

output_path = r"C:\Users\dasil\OneDrive\Desktop\today\alt_totals_90_100.md"

results = {"MLB": [], "WNBA": []}

for league in ['MLB', 'WNBA']:
    path = f'C:/Users/dasil/OneDrive/Documents/outlier/data/{league}/normalized/{league.lower()}_insights_latest.json'
    if not os.path.exists(path):
        continue
        
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
        
    for r in data.get('records', []):
        hit_rate = r.get('hit_rate_pct', 0)
        if 90 <= hit_rate <= 100:
            market = r.get('market_raw', '').lower()
            text = r.get('text', '').lower()
            
            # Check for Team Total or Game Total overs
            if ('team total' in market or 'total' in market) and 'over' in text:
                # Add it if it's not a player prop (subject_type == team) or if the text implies team/game totals
                if 'total' in market:
                    results[league].append(r['text'])

with open(output_path, "w", encoding="utf-8") as out:
    out.write("# Alt Lines: Team Totals & Game Totals (Overs) - 90-100% Hit Rate\n\n")
    
    for league in ['MLB', 'WNBA']:
        out.write(f"## {league}\n")
        if not results[league]:
            out.write("*No qualifying totals found.*\n\n")
        else:
            for item in results[league]:
                out.write(f"- {item}\n")
            out.write("\n")

print("Totals file created successfully.")
