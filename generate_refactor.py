import os

with open('original_write_pack.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = [
    "@dataclass\n",
    "class PackSnapshot:\n",
    "    rows: list[dict[str, Any]]\n",
    "    opportunity_output: list[dict[str, Any]]\n",
    "    totals_rows: list[dict[str, Any]]\n",
    "    team_totals_rows: list[dict[str, Any]]\n",
    "    alt_tt_rows: list[dict[str, Any]]\n",
    "    alt_tt_parlays: list[dict[str, Any]]\n",
    "    alt_spread_rows: list[dict[str, Any]]\n",
    "    bankroll_rows_by_league: dict[str, list[dict[str, Any]]]\n",
    "    alt_player_rows: list[dict[str, Any]]\n",
    "    alt_player_parlays: list[dict[str, Any]]\n",
    "    ultimate_alt_rows: list[dict[str, Any]]\n",
    "    ultimate_alt_parlays: list[dict[str, Any]]\n",
    "    sidecar: dict[str, Any]\n",
    "    by_event: dict[tuple[str, str], list[dict[str, Any]]]\n",
    "\n",
    "def compute_pack_snapshot(\n",
    "    rows: list[dict[str, Any]],\n",
    "    pack_date: str,\n",
    "    *,\n",
    "    games_norm_by_league: dict[str, Any] | None = None,\n",
    "    props_norm_by_league: dict[str, Any] | None = None,\n",
    "    opportunity_rows: list[dict[str, Any]] | None = None,\n",
    ") -> PackSnapshot:\n"
]

# Validation
new_lines.extend(lines[14:34]) # lines 15-34 of original file (0 indexed is 14 to 33)

# Totals computation
new_lines.extend(lines[91:217])

# Portfolio risk allocation
new_lines.extend(lines[218:227]) # git sha and load policy
new_lines.extend(lines[264:474]) # everything from all_projected to sidecar definition

# calculate opportunity_output
new_lines.extend([
    "    selected_keys = {_opportunity_key(row) for row in rows}\n",
    "    opportunity_output: list[dict[str, Any]] = []\n",
    "    for source_row in opportunity_rows if opportunity_rows is not None else rows:\n",
    "        row = dict(source_row)\n",
    "        opportunity_key = _opportunity_key(row)\n",
    "        row[\"selected\"] = \"true\" if opportunity_key in selected_keys else \"false\"\n",
    "        opportunity_output.append(row)\n\n"
])

# calculate by_event
new_lines.extend([
    "    by_event: dict[tuple[str, str], list[dict[str, Any]]] = {}\n",
    "    for r in rows:\n",
    "        eid = r.get(\"event_id\")\n",
    "        if eid:\n",
    "            by_event.setdefault((r[\"sport\"], str(eid)), []).append(r)\n\n"
])

# Return PackSnapshot
new_lines.extend([
    "    return PackSnapshot(\n",
    "        rows=rows,\n",
    "        opportunity_output=opportunity_output,\n",
    "        totals_rows=totals_rows,\n",
    "        team_totals_rows=team_totals_rows,\n",
    "        alt_tt_rows=alt_tt_rows,\n",
    "        alt_tt_parlays=alt_tt_parlays,\n",
    "        alt_spread_rows=alt_spread_rows,\n",
    "        bankroll_rows_by_league=bankroll_rows_by_league,\n",
    "        alt_player_rows=alt_player_rows,\n",
    "        alt_player_parlays=alt_player_parlays,\n",
    "        ultimate_alt_rows=ultimate_alt_rows,\n",
    "        ultimate_alt_parlays=ultimate_alt_parlays,\n",
    "        sidecar=sidecar,\n",
    "        by_event=by_event,\n",
    "    )\n\n",
])


# write_pack_snapshot
new_lines.extend([
    "def write_pack_snapshot(\n",
    "    snapshot: PackSnapshot,\n",
    "    out_dir: Path,\n",
    "    freshness_lines: list[str] | None = None,\n",
    "    *,\n",
    "    coverage: dict[str, dict[str, int]] | None = None,\n",
    "    feed_health_by_league: dict[str, dict[str, Any]] | None = None,\n",
    "    projection_records: list[dict[str, Any]] | None = None,\n",
    ") -> None:\n"
])

# Enforce check block 1 (this includes load policy)
new_lines.extend([
    "    from outlier_scrapers.portfolio import load_portfolio_policy\n",
    "    import json\n",
    "    policy = load_portfolio_policy()\n"
])
new_lines.extend(lines[34:82]) # immutable enforce check 1

# dir unlink
new_lines.extend([
    "    out_dir.mkdir(parents=True, exist_ok=True)\n",
    "    for name in DERIVED_PACK_OUTPUTS:\n",
    "        (out_dir / name).unlink(missing_ok=True)\n",
    "    dossiers_dir = out_dir / \"dossiers\"\n",
    "    if dossiers_dir.exists():\n",
    "        for stale_dossier in dossiers_dir.glob(\"*.md\"):\n",
    "            stale_dossier.unlink()\n"
])

# write portfolio risk json
new_lines.extend(lines[475:478]) # sidecar dump

# write candidates.csv
new_lines.extend([
    "    with open(out_dir / \"candidates.csv\", \"w\", newline=\"\", encoding=\"utf-8\") as f:\n",
    "        writer = csv.DictWriter(f, fieldnames=CANDIDATES_HEADER, extrasaction=\"ignore\")\n",
    "        writer.writeheader()\n",
    "        writer.writerows(snapshot.rows)\n\n",
])

# write opportunities.csv
new_lines.extend([
    "    with open(out_dir / \"opportunities.csv\", \"w\", newline=\"\", encoding=\"utf-8\") as f:\n",
    "        writer = csv.DictWriter(\n",
    "            f, fieldnames=[*CANDIDATES_HEADER, \"selected\"], extrasaction=\"ignore\"\n",
    "        )\n",
    "        writer.writeheader()\n",
    "        writer.writerows(snapshot.opportunity_output)\n\n",
])

# projections jsonl
new_lines.extend(lines[498:501])

# briefing md
new_lines.extend([
    "    (out_dir / \"briefing.md\").write_text(\n",
    "        build_briefing(\n",
    "            snapshot.rows,\n",
    "            out_dir.name,\n",
    "            freshness_lines,\n",
    "            snapshot.totals_rows if snapshot.totals_rows else None,\n",
    "            snapshot.team_totals_rows if snapshot.team_totals_rows else None,\n",
    "            build_candidate_coverage_section(coverage) if coverage is not None else None,\n",
    "            snapshot.ultimate_alt_rows,\n",
    "        ),\n",
    "        encoding=\"utf-8\",\n",
    "    )\n"
])

# coverage, feed health
new_lines.extend(lines[514:522])

# dossiers
new_lines.extend([
    "    dossiers_dir.mkdir(exist_ok=True)\n",
    "    for (sport, eid), erows in snapshot.by_event.items():\n",
    "        slug = erows[0].get(\"_slug\", \"unknown\")\n",
    "        (dossiers_dir / f\"{sport}_{eid}_{slug}.md\").write_text(\n",
    "            build_dossier(erows, sport), encoding=\"utf-8\"\n",
    "        )\n"
])

# decisions
new_lines.extend(lines[533:540])

# game/team totals csv md
new_lines.extend([
    "    with open(out_dir / \"game_totals.csv\", \"w\", newline=\"\", encoding=\"utf-8\") as tf:\n",
    "        writer = csv.DictWriter(tf, fieldnames=GAME_TOTALS_HEADER, extrasaction=\"ignore\")\n",
    "        writer.writeheader()\n",
    "        writer.writerows(snapshot.totals_rows)\n",
    "    (sections_dir / \"game_totals.md\").write_text(\n",
    "        _format_game_totals_md(snapshot.totals_rows), encoding=\"utf-8\"\n",
    "    )\n\n",
    "    with open(out_dir / \"team_totals.csv\", \"w\", newline=\"\", encoding=\"utf-8\") as tf:\n",
    "        writer = csv.DictWriter(tf, fieldnames=TEAM_TOTALS_HEADER, extrasaction=\"ignore\")\n",
    "        writer.writeheader()\n",
    "        writer.writerows(snapshot.team_totals_rows)\n",
    "    (sections_dir / \"team_totals.md\").write_text(\n",
    "        _format_game_totals_md(snapshot.team_totals_rows, title=\"# Team totals\"), encoding=\"utf-8\"\n",
    "    )\n\n",
])

# alt team totals
new_lines.extend([
    "    _write_csv(out_dir / \"alt_team_totals.csv\", ALT_TEAM_TOTALS_HEADER, snapshot.alt_tt_rows)\n",
    "    _write_csv(\n",
    "        out_dir / \"alt_team_total_parlays.csv\",\n",
    "        ALT_TEAM_TOTAL_PARLAYS_HEADER,\n",
    "        snapshot.alt_tt_parlays,\n",
    "    )\n",
    "    (sections_dir / \"alt_team_totals.md\").write_text(\n",
    "        format_alt_team_totals_md(snapshot.alt_tt_rows, snapshot.alt_tt_parlays), encoding=\"utf-8\"\n",
    "    )\n\n",
])

# bankroll props
new_lines.extend([
    "    for lg, bankroll_rows in snapshot.bankroll_rows_by_league.items():\n",
    "        _write_csv(\n",
    "            out_dir / f\"{lg.lower()}_alt_bankroll_props.csv\",\n",
    "            ALT_BANKROLL_PROPS_HEADER,\n",
    "            bankroll_rows,\n",
    "        )\n\n"
])

# alt spreads (needs lg keys from bankroll_rows_by_league since games_norm_by_league isn't in snapshot directly, or we can just iterate over unique leagues in spread rows, wait! The original code used `games_norm_by_league or {}`. We can just group alt_spread_rows by league or write them out based on `bankroll_rows_by_league` keys.)
new_lines.extend([
    "    for lg in snapshot.bankroll_rows_by_league:\n",
    "        spread_rows = [row for row in snapshot.alt_spread_rows if row.get(\"league\") == lg]\n",
    "        _write_csv(\n",
    "            out_dir / f\"{lg.lower()}_alt_spreads.csv\",\n",
    "            ALT_SPREADS_HEADER,\n",
    "            spread_rows,\n",
    "        )\n\n"
])

# alt player props
new_lines.extend([
    "    _write_csv(out_dir / \"alt_player_props.csv\", ALT_PLAYER_PROPS_HEADER, snapshot.alt_player_rows)\n",
    "    _write_csv(\n",
    "        out_dir / \"alt_player_props_parlays.csv\",\n",
    "        ALT_PLAYER_PROPS_PARLAYS_HEADER,\n",
    "        snapshot.alt_player_parlays,\n",
    "    )\n",
    "    (sections_dir / \"alt_player_props.md\").write_text(\n",
    "        format_alt_player_props_md(snapshot.alt_player_rows, snapshot.alt_player_parlays), encoding=\"utf-8\"\n",
    "    )\n\n",
])

# ultimate alt
new_lines.extend([
    "    _write_csv(out_dir / \"ultimate_alt.csv\", ULTIMATE_ALT_HEADER, snapshot.ultimate_alt_rows)\n",
    "    _write_csv(\n",
    "        out_dir / \"ultimate_alt_parlays.csv\",\n",
    "        ULTIMATE_ALT_PARLAYS_HEADER,\n",
    "        snapshot.ultimate_alt_parlays,\n",
    "    )\n",
    "    (sections_dir / \"ultimate_alt.md\").write_text(\n",
    "        format_ultimate_alt_md(snapshot.ultimate_alt_rows, snapshot.ultimate_alt_parlays),\n",
    "        encoding=\"utf-8\",\n",
    "    )\n\n"
])

# The new write_pack wrapper
new_lines.extend([
    "def write_pack(\n",
    "    rows: list[dict[str, Any]],\n",
    "    out_dir: Path,\n",
    "    freshness_lines: list[str] | None = None,\n",
    "    *,\n",
    "    games_norm_by_league: dict[str, Any] | None = None,\n",
    "    props_norm_by_league: dict[str, Any] | None = None,\n",
    "    target_date: str | None = None,\n",
    "    coverage: dict[str, dict[str, int]] | None = None,\n",
    "    feed_health_by_league: dict[str, dict[str, Any]] | None = None,\n",
    "    opportunity_rows: list[dict[str, Any]] | None = None,\n",
    "    projection_records: list[dict[str, Any]] | None = None,\n",
    ") -> None:\n",
    "    pack_date = target_date or out_dir.name\n",
    "    snapshot = compute_pack_snapshot(\n",
    "        rows=rows,\n",
    "        pack_date=pack_date,\n",
    "        games_norm_by_league=games_norm_by_league,\n",
    "        props_norm_by_league=props_norm_by_league,\n",
    "        opportunity_rows=opportunity_rows,\n",
    "    )\n",
    "    write_pack_snapshot(\n",
    "        snapshot=snapshot,\n",
    "        out_dir=out_dir,\n",
    "        freshness_lines=freshness_lines,\n",
    "        coverage=coverage,\n",
    "        feed_health_by_league=feed_health_by_league,\n",
    "        projection_records=projection_records,\n",
    "    )\n"
])

with open('new_write_pack.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
