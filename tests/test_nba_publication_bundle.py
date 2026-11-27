"""Publication regressions: stale companions, exact prices, and destination agreement."""

import csv
import json
from datetime import UTC, datetime, timedelta

import pytest

from scripts import export_nba_extra_pack as exporter
from scripts import nba_publication_bundle as bundle


def write_csv(path, rows):
    fields = list(rows[0]) if rows else exporter.FIELDNAMES
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def card(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "check_disk_space", lambda **kwargs: None)
    tip = datetime.now(UTC) + timedelta(hours=1)
    slate = tip.astimezone(exporter.ZoneInfo("America/New_York")).date().isoformat()
    row = dict(
        slate_date=slate,
        game_time=tip.isoformat(),
        matchup="AAA @ BBB",
        player="Player A",
        market="PTS",
        line="20.5",
        recommended_side="OVER",
        recommended_odds="-110",
        over_odds="-110",
        quote_timestamp=datetime.now(UTC).isoformat(),
    )
    src, pack = tmp_path / "source.csv", tmp_path / "nba_only.csv"
    write_csv(src, [row])
    assert exporter.export_nba_pack(src, pack, slate_date=slate) == 1
    return row, pack, slate


def test_companions_require_exact_authorized_contract(card, tmp_path):
    row, pack, slate = card
    src, dst = tmp_path / "grades.csv", tmp_path / "published.csv"
    write_csv(
        src,
        [
            dict(row, grade="STANDARD"),
            dict(row, player="Other", grade="STANDARD"),
            dict(row, recommended_odds="-115", grade="STANDARD"),
            dict(row, slate_date="2000-01-01", grade="STANDARD"),
        ],
    )
    assert bundle.sanitize(src, dst, pack, slate) == 1
    assert bundle.read_rows(dst)[1][0]["recommended_odds"] == "-110"
    bundle.verify(dst, slate)


def test_empty_current_result_replaces_old_destination(card, tmp_path):
    _, pack, slate = card
    write_csv(pack, [])
    pack.with_suffix(".status.json").write_text(
        json.dumps(
            dict(
                slate_date=slate,
                row_count=0,
                actionable_rows=0,
                status="awaiting_odds",
                csv_sha256=bundle.digest(pack),
            )
        )
    )
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            dict(
                slate_date=slate,
                after_rows=0,
                actionable_rows=0,
                status="awaiting_odds",
                csv_sha256=bundle.digest(pack),
            )
        )
    )
    destination = tmp_path / "Desktop" / "nba_only.csv"
    destination.parent.mkdir()
    destination.write_text("stale rows")
    bundle.copy_verified(pack, destination, slate, summary)
    assert bundle.read_rows(destination)[1] == []
    assert bundle.verify(destination, slate)["status"] == "awaiting_odds"


def test_summary_or_csv_tampering_blocks_publication(card, tmp_path):
    _, pack, slate = card
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps(dict(slate_date=slate, after_rows=520)))
    with pytest.raises(ValueError, match="summary disagrees"):
        bundle.copy_verified(pack, tmp_path / "dest.csv", slate, summary)
    pack.write_text(pack.read_text() + "corrupt")
    with pytest.raises(ValueError, match="Unverified"):
        bundle.verify(pack, slate)


def test_game_start_is_rechecked_at_destination(card, tmp_path, monkeypatch):
    _, pack, slate = card
    monkeypatch.setattr(bundle, "publication_eligible", lambda row, date: False)
    with pytest.raises(ValueError, match="Unverified"):
        bundle.copy_verified(pack, tmp_path / "dest.csv", slate)
    assert not (tmp_path / "dest.csv").exists()
