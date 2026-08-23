import csv
import glob
from pathlib import Path

for f in ['alt_player_props.csv', 'alt_team_totals.csv', 'wnba_alt_spreads.csv']:
    print(f"\n--- {f} ---")
    p = Path(f'packs/2026-08-23/{f}')
    if not p.exists():
        continue
    rows = list(csv.DictReader(p.open('r')))
    for r in rows:
        u = r.get('recommended_units') or r.get('recommended_units_pre_news') or ''
        if u and float(u) > 0:
            if f == 'alt_player_props.csv':
                print(f"{r['player']} {r['market']} {r.get('position', 'OVER')} {r['line']} ({u} units)")
            elif f == 'alt_team_totals.csv':
                print(f"{r['team']} Team Total {r.get('position', 'OVER')} {r['line']} ({u} units)")
            elif f == 'wnba_alt_spreads.csv':
                print(f"{r['team']} {r['signed_line']} ({u} units)")
