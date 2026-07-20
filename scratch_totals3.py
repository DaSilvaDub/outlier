import json
import os

for league in ['MLB', 'WNBA']:
    path = f'C:/Users/dasil/OneDrive/Documents/outlier/data/{league}/normalized/{league.lower()}_insights_latest.json'
    if not os.path.exists(path):
        continue
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    print(f'{league} Totals:')
    for r in data['records']:
        market = r.get('market_raw', '').lower()
        if 'total' in market or 'total' in r.get('text', '').lower() or 'o/u' in market or 'o/u' in r.get('text', '').lower():
            if 'over' in r.get('text', '').lower() or 'under' in r.get('text', '').lower():
                print(f"  {r['text']} (Hit Rate: {r.get('hit_rate_pct')}%)")
