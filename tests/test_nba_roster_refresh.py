"""Provider-shaped offline fixtures; no live network or production-state writes."""
from __future__ import annotations

import copy
import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nba_roster_registry as reg  # noqa: E402
import filter_nba_ruled_out as flt  # noqa: E402


def feeds(now=None):
    now = now or datetime.now(timezone.utc)
    year = reg.season_year(reg.current_date())
    result = {}
    teams = []
    for number, code in enumerate(sorted(reg.TEAMS), 1):
        tid = str(number)
        teams.append({"team": {"id": tid, "abbreviation": code, "displayName": code}})
        athletes = [{"id": str(number * 100 + i), "displayName": f"{code} Player {i}",
                     "status": {"name": "Active"}, "injuries": []} for i in range(10)]
        result[reg.BASE + f"/teams/{tid}/roster"] = {
            "timestamp": now.isoformat(), "status": "success", "season": {"year": year},
            "team": {"id": tid}, "athletes": athletes}
    result[reg.BASE + "/teams?limit=40"] = {"sports": [{"leagues": [{"season": {"year": year}, "teams": teams}]}]}
    result[reg.BASE + "/injuries"] = {"timestamp": now.isoformat(), "status": "success",
        "season": {"year": year}, "injuries": [{"id": "1", "injuries": [
            {"status": "Out", "athlete": {"displayName": "ATL Player 0", "links": [
                {"href": "https://www.espn.com/nba/player/_/id/100/atl-player"}]}},
            {"status": "Day-To-Day", "athlete": {"id": "101", "displayName": "ATL Player 1"}}
        ]}]}
    return result


def write_registry(tmp_path, data=None):
    data = data or feeds()
    def fetch(url):
        raw = json.dumps(data[url]).encode()
        return {"url": url, "fetched_utc": datetime.now(timezone.utc).isoformat(), "sha256": reg.digest(raw)}, raw
    path = tmp_path / "registry.json"
    reg.refresh(path, reg.current_date(), fetch)
    return path


def pack(path, names, date=None):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=["rank", "slate_date", "player"])
    writer.writeheader()
    for rank, name in enumerate(names, 1):
        writer.writerow({"rank": rank, "slate_date": date or reg.current_date(), "player": name})
    path.write_text(out.getvalue())


def test_complete_evidence_is_reconstructed_and_explicit_status_filtered(tmp_path):
    path = write_registry(tmp_path)
    registry = reg.load(path)
    assert len(registry["teams"]) == 30 and len(registry["sources"]) == 32
    source, output = tmp_path / "pack.csv", tmp_path / "filtered.csv"
    pack(source, ["ATL Player 0", "ATL Player 1", "ATL Player 2"])
    before, after, removed = flt.filter_csv(source, output, registry_path=path)
    assert (before, after, removed) == (3, 2, ["ATL Player 0"])
    assert "ATL Player 1" in output.read_text()  # Injury list present, DTD is not OUT.


def test_refresh_replaces_old_out_and_catches_new_out(tmp_path):
    path = write_registry(tmp_path)
    first = json.loads(path.read_text())
    snapshot = path.parent / first["immutable_snapshot"]
    original_version = snapshot.read_bytes()
    updated = feeds()
    updated[reg.BASE + "/injuries"]["injuries"][0]["injuries"] = [
        {"status": "Out", "athlete": {"id": "102", "displayName": "ATL Player 2"}}]
    path = write_registry(tmp_path, updated)
    assert flt.get_ruled_out_set(path) == {"atl player 2"}
    assert snapshot.read_bytes() == original_version
    assert reg.load(snapshot)["teams"]["ATL"]["roster"][0]["status"] == "OUT"


@pytest.mark.parametrize("change", ["old", "future", "season", "partial", "status", "duplicate"])
def test_bad_provider_evidence_cannot_replace_previous_registry(tmp_path, change):
    path = write_registry(tmp_path)
    original = path.read_bytes()
    data = feeds()
    roster = data[reg.BASE + "/teams/1/roster"]
    if change == "old":
        roster["timestamp"] = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    elif change == "future":
        roster["timestamp"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    elif change == "season":
        roster["season"]["year"] -= 1
    elif change == "partial":
        roster["athletes"] = []
    elif change == "status":
        roster["athletes"][0]["status"] = {"name": "Mystery"}
    else:
        data[reg.BASE + "/teams/2/roster"]["athletes"][0] = copy.deepcopy(roster["athletes"][0])
    with pytest.raises(reg.RegistryError):
        write_registry(tmp_path, data)
    assert path.read_bytes() == original


def test_network_failure_preserves_old_snapshot(tmp_path):
    path = write_registry(tmp_path)
    original = path.read_bytes()
    def fail(url):
        raise OSError("HTTP 403")
    with pytest.raises(OSError):
        reg.refresh(path, reg.current_date(), fail)
    assert path.read_bytes() == original


@pytest.mark.parametrize("change", ["raw", "normalized", "stale", "legacy", "missing"])
def test_invalid_registry_never_modifies_in_place_input(tmp_path, change):
    path = write_registry(tmp_path)
    data = json.loads(path.read_text())
    if change == "raw":
        (path.parent / data["sources"][0]["raw_path"]).write_bytes(b"{}")
    elif change == "normalized":
        data["teams"]["ATL"]["roster"][0]["status"] = "ACTIVE"
    elif change == "stale":
        data["as_of_utc"] = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    elif change == "legacy":
        data = {"teams": {"ATL": {"roster": [{"name": "ATL Player 0", "status": "OUT"}]}}}
    else:
        path.unlink()
    if change not in {"raw", "missing"}:
        path.write_text(json.dumps(data))
    source = tmp_path / "pack.csv"
    pack(source, ["ATL Player 0"])
    before = source.read_bytes()
    with pytest.raises(reg.RegistryError):
        flt.filter_csv(source, source, registry_path=path)
    assert source.read_bytes() == before


@pytest.mark.parametrize("names,date", [(["Unknown Player"], None), (["ATL Player 2"], "1999-01-01")])
def test_unknown_or_wrong_date_pack_fails_without_output(tmp_path, names, date):
    path = write_registry(tmp_path)
    source, output = tmp_path / "pack.csv", tmp_path / "out.csv"
    pack(source, names, date)
    with pytest.raises(reg.RegistryError):
        flt.filter_csv(source, output, registry_path=path)
    assert not output.exists()


def test_current_snapshot_cannot_be_used_as_historical_evidence(tmp_path):
    path = write_registry(tmp_path)
    yesterday = (datetime.now(timezone.utc).astimezone(reg.ZoneInfo("America/New_York")) - timedelta(days=1)).date().isoformat()
    with pytest.raises(reg.RegistryError, match="today/next-day"):
        reg.load(path, yesterday)


def test_explicit_candidate_team_mismatch_is_rejected(tmp_path):
    path = write_registry(tmp_path)
    source, output = tmp_path / "pack.csv", tmp_path / "out.csv"
    source.write_text(f"slate_date,player,team\n{reg.current_date()},ATL Player 2,BOS\n")
    with pytest.raises(reg.RegistryError, match="team does not match"):
        flt.filter_csv(source, output, registry_path=path)
    assert not output.exists()
