"""Shared scaffolding for the AI research-desk reasoning/research runners.

Holds the provider-agnostic mechanics — request-hash, candidates validation,
game/team totals context injection, atomic front-matter write, and the
versioned per-pass publication path — so per-prompt runners stay thin.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from outlier_scrapers import pack, verdicts

GAME_TOTALS_NAME = "game_totals.csv"
TEAM_TOTALS_NAME = "team_totals.csv"
AI_EXCLUDED_CANDIDATE_FIELDS = frozenset({"historical_edge_pct"})

logger = logging.getLogger(__name__)


class RunnerError(Exception):
    """Raised for any recoverable runner failure (-> exit code 1)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def empty_game_totals_hash() -> str:
    """Stable hash when game_totals.csv is absent."""
    return sha256_bytes(b"")


def empty_team_totals_hash() -> str:
    """Stable hash when team_totals.csv is absent."""
    return sha256_bytes(b"")


def load_game_totals(pack_dir: Path) -> tuple[bytes | None, str]:
    """Return (raw bytes or None, sha256). Missing file hashes as empty."""
    path = pack_dir / GAME_TOTALS_NAME
    if not path.exists():
        return None, empty_game_totals_hash()
    raw = path.read_bytes()
    return raw, sha256_bytes(raw)


def load_team_totals(pack_dir: Path) -> tuple[bytes | None, str]:
    """Return (raw bytes or None, sha256). Missing file hashes as empty."""
    path = pack_dir / TEAM_TOTALS_NAME
    if not path.exists():
        return None, empty_team_totals_hash()
    raw = path.read_bytes()
    return raw, sha256_bytes(raw)


def load_all_totals(pack_dir: Path) -> tuple[bytes | None, str, bytes | None, str]:
    """Return (game_totals_bytes, game_totals_sha256, team_totals_bytes, team_totals_sha256)."""
    totals_bytes, game_totals_sha256 = load_game_totals(pack_dir)
    team_totals_bytes, team_totals_sha256 = load_team_totals(pack_dir)
    return totals_bytes, game_totals_sha256, team_totals_bytes, team_totals_sha256


def _parse_totals_csv(
    totals_bytes: bytes | None,
    *,
    label: str,
    kind_filter: str | None,
) -> list[dict[str, str]]:
    """Parse a totals board; optionally keep only rows of one total_kind.

    Fresh packs write pure streams (game_totals.csv = game only). Legacy mixed
    files may contain both kinds; kind_filter drops the wrong stream so
    consumers never double-count.
    """
    if not totals_bytes:
        return []
    import io
    from outlier_scrapers.game_totals import GAME_TOTALS_HEADER, TOTAL_KIND_GAME, TOTAL_KIND_TEAM

    try:
        reader = csv.DictReader(
            io.StringIO(totals_bytes.decode("utf-8-sig")),
            strict=True,
        )
        if reader.fieldnames != GAME_TOTALS_HEADER:
            raise RunnerError(f"{label} header does not match expected totals schema")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise RunnerError(f"{label} has malformed rows")
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RunnerError(f"{label} is malformed") from exc

    if kind_filter is None:
        return rows

    allowed = {TOTAL_KIND_GAME, TOTAL_KIND_TEAM}
    if kind_filter not in allowed:
        raise RunnerError(f"unsupported totals kind filter: {kind_filter!r}")

    kept: list[dict[str, str]] = []
    for row in rows:
        raw_kind = str(row.get("total_kind") or "").strip().lower()
        # Blank total_kind is treated as game (legacy game-only boards).
        if not raw_kind:
            effective = TOTAL_KIND_GAME
        else:
            effective = raw_kind
        if effective == kind_filter:
            kept.append(row)
    return kept


def parse_game_totals(totals_bytes: bytes | None) -> list[dict[str, str]]:
    """Parse game totals; keep total_kind=game (and blank legacy rows)."""
    from outlier_scrapers.game_totals import TOTAL_KIND_GAME

    return _parse_totals_csv(
        totals_bytes, label=GAME_TOTALS_NAME, kind_filter=TOTAL_KIND_GAME
    )


def parse_team_totals(totals_bytes: bytes | None) -> list[dict[str, str]]:
    """Parse team totals; keep total_kind=team only."""
    from outlier_scrapers.game_totals import TOTAL_KIND_TEAM

    return _parse_totals_csv(
        totals_bytes, label=TEAM_TOTALS_NAME, kind_filter=TOTAL_KIND_TEAM
    )


def _count_actionable_totals(
    rows: list[dict[str, str]], *, label: str
) -> int:
    required = ("totals_id", "market_id", "selection", "line", "price")
    count = 0
    for row in rows:
        if str(row.get("actionable") or "").strip().lower() != "true":
            continue
        if any(not str(row.get(field) or "").strip() for field in required):
            raise RunnerError(f"actionable {label} row is missing identity fields")
        count += 1
    return count


def count_actionable_game_totals(totals_bytes: bytes | None) -> int:
    """Count actionable game totals only after schema and identity validation."""
    return _count_actionable_totals(
        parse_game_totals(totals_bytes), label=GAME_TOTALS_NAME
    )


def count_actionable_team_totals(totals_bytes: bytes | None) -> int:
    """Count actionable team totals only after schema and identity validation."""
    return _count_actionable_totals(
        parse_team_totals(totals_bytes), label=TEAM_TOTALS_NAME
    )


def has_actionable_game_totals(totals_bytes: bytes | None) -> bool:
    return count_actionable_game_totals(totals_bytes) > 0


def has_actionable_team_totals(totals_bytes: bytes | None) -> bool:
    return count_actionable_team_totals(totals_bytes) > 0


def has_actionable_any_totals(
    game_totals_bytes: bytes | None,
    team_totals_bytes: bytes | None = None,
) -> bool:
    """True if either totals stream has at least one actionable row."""
    return has_actionable_game_totals(game_totals_bytes) or has_actionable_team_totals(
        team_totals_bytes
    )


def append_totals_block(
    base: str,
    totals_bytes: bytes | None,
    team_totals_bytes: bytes | None = None,
) -> str:
    """Append labeled game_totals.csv / team_totals.csv context when present."""
    out = base
    if totals_bytes:
        out = (
            out
            + "\n\n===== GAME_TOTALS.CSV (projection board) =====\n"
            + totals_bytes.decode("utf-8-sig")
        )
    if team_totals_bytes:
        out = (
            out
            + "\n\n===== TEAM_TOTALS.CSV (projection board) =====\n"
            + team_totals_bytes.decode("utf-8-sig")
        )
    return out


def filter_candidates_for_ai(raw_bytes: bytes) -> bytes:
    """Remove descriptive-only fields before candidates reach any AI model."""
    try:
        reader = csv.DictReader(
            io.StringIO(raw_bytes.decode("utf-8-sig")),
            strict=True,
        )
        fieldnames = reader.fieldnames
        if not fieldnames:
            raise RunnerError("candidates.csv has no header")
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise RunnerError("candidates.csv is malformed") from exc

    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise RunnerError("candidates.csv has malformed rows")

    ai_fieldnames = [
        field for field in fieldnames if field not in AI_EXCLUDED_CANDIDATE_FIELDS
    ]
    out_io = io.StringIO()
    writer = csv.DictWriter(out_io, fieldnames=ai_fieldnames)
    writer.writeheader()
    writer.writerows(
        {field: row.get(field, "") for field in ai_fieldnames} for row in rows
    )
    return out_io.getvalue().encode("utf-8-sig")


def build_reasoning_data_block(
    candidates_bytes: bytes,
    totals_bytes: bytes | None,
    team_totals_bytes: bytes | None = None,
) -> str:
    """Merge candidates.csv and optional game/team totals for reasoning passes."""
    base = "candidates.csv:\n" + candidates_bytes.decode("utf-8-sig")
    return append_totals_block(base, totals_bytes, team_totals_bytes)


def compute_request_hash(request_data: dict) -> str:
    """Canonical, order-independent hash of the full request definition."""
    canonical = json.dumps(request_data, sort_keys=True).encode("utf-8")
    return sha256_bytes(canonical)


def extract_yaml_request_hash(content: str) -> str | None:
    """Trivial reader for the ``request_sha256`` key in YAML front matter."""
    if not content.startswith("---\n"):
        return None
    end_idx = content.find("\n---\n", 4)
    if end_idx == -1:
        return None
    for line in content[4:end_idx].splitlines():
        if line.startswith("request_sha256:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def validate_candidates(pack_dir: Path, *, allow_empty: bool = False) -> tuple[bytes, str]:
    """Validate candidates.csv exists and has the canonical header.

    At least one unlocked candidate is required unless an actionable totals
    board explicitly enables the header-only state.
    Also re-applies the lock filter in case events started since pack build."""
    candidates_file = pack_dir / "candidates.csv"
    if not candidates_file.exists():
        raise RunnerError(f"Candidates file {candidates_file} does not exist.")

    with open(candidates_file, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header != pack.CANDIDATES_HEADER:
            raise RunnerError("candidates.csv header does not match pack.CANDIDATES_HEADER")

    with open(candidates_file, "r", encoding="utf-8") as f:
        dict_reader = csv.DictReader(f)
        rows = list(dict_reader)

    kept, locked = pack.drop_locked_events(rows, now=datetime.now().astimezone())
    if locked:
        logger.warning(
            "Reasoning-time lock filter dropped %d event(s) locked since pack build",
            len(locked),
        )

    if not kept and not allow_empty:
        raise RunnerError("candidates.csv has no data rows after dropping locked events.")

    import io
    out_io = io.StringIO()
    writer = csv.DictWriter(out_io, fieldnames=pack.CANDIDATES_HEADER)
    writer.writeheader()
    writer.writerows(kept)

    canonical_bytes = out_io.getvalue().encode("utf-8-sig")
    ai_bytes = filter_candidates_for_ai(canonical_bytes)
    return ai_bytes, sha256_bytes(ai_bytes)


def read_required_text(path: Path, label: str) -> str:
    if not path.exists():
        raise RunnerError(f"{label} {path} missing.")
    return path.read_text(encoding="utf-8")


def atomic_write(pack_dir: Path, out_name: str, front_matter: str, body: str) -> None:
    """Write front_matter + body to pack_dir/out_name atomically (tmp + replace)."""
    stem = out_name.rsplit(".", 1)[0]
    fd, tmp_path = tempfile.mkstemp(dir=str(pack_dir), prefix=f"{stem}_tmp_", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(front_matter)
            f.write(body)
        os.replace(tmp_path, pack_dir / out_name)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise RunnerError(f"Failed to write output: {type(e).__name__}") from e


PUBLICATION_FILES = (
    "verdicts.json",
    "violations.json",
    "report_fragment.md",
    "status_fragment.json",
)


@dataclass(frozen=True)
class StructuredRequest:
    kind: str
    schema_version: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class PassArtifacts:
    pass_: str
    request_sha256: str
    schema_version: str
    verdicts_json: bytes
    violations_json: bytes
    report_fragment: bytes
    status_fragment: bytes


@dataclass(frozen=True)
class PublishResult:
    publication_id: str
    request_sha256: str
    path: Path
    wrote: bool


def request_structured(kind: str) -> StructuredRequest:
    """Schema + version a runner hands to a provider before the Markdown stage."""
    return StructuredRequest(
        kind=kind,
        schema_version=verdicts.SCHEMA_VERSION,
        schema=verdicts.json_schema_for(kind),
    )


def parse_envelope(raw_text: str, kind: str) -> verdicts.ParsedEnvelope:
    return verdicts.parse_envelope(raw_text, kind)


def _rename_pass_keys(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {("pass" if key == "pass_" else key): _rename_pass_keys(val) for key, val in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_rename_pass_keys(val) for val in obj]
    return obj


def write_envelope(
    envelope: verdicts.VerdictEnvelope | verdicts.FindingEnvelope | verdicts.ReconciliationEnvelope,
    *,
    request_sha256: str,
    model: str = "",
    candidates_sha256: str = "",
    game_totals_sha256: str = "",
    team_totals_sha256: str = "",
) -> bytes:
    """Serialize a parsed envelope for verdicts.json.

    `publication_id` is intentionally omitted: it is the hash of this file
    (plus the other three artifacts) and cannot appear inside the hashed bytes.
    """
    body = _rename_pass_keys(asdict(envelope))
    body["request_sha256"] = request_sha256
    if model:
        body["model"] = model
    if candidates_sha256:
        body["candidates_sha256"] = candidates_sha256
    if game_totals_sha256:
        body["game_totals_sha256"] = game_totals_sha256
    if team_totals_sha256:
        body["team_totals_sha256"] = team_totals_sha256
    return json.dumps(body, sort_keys=True).encode("utf-8")


def write_violations(violations: Sequence[Any]) -> bytes:
    from outlier_scrapers.verdict_gate import violation_class

    rows = [
        {
            "code": item.code,
            "outcome_id": item.outcome_id,
            "market_id": item.market_id,
            "detail": item.detail,
            "severity": item.severity,
            "class": violation_class(item.code),
        }
        for item in violations
    ]
    return json.dumps(rows, sort_keys=True).encode("utf-8")


def _pack_value_from_detail(detail: str) -> str:
    for marker in ("pack line ", "pack price ", "pack selection ", "pack book "):
        if marker in detail:
            return detail.split(marker, 1)[1].rstrip(".").strip()
    return ""


def build_repair_block(violations: Sequence[Any]) -> str:
    """Machine-generated repair text: code, outcome_id, authoritative pack value.

    The retry must never restate a number the model is expected to produce.
    """
    lines = ["REPAIR"]
    for item in violations:
        pack_value = _pack_value_from_detail(item.detail)
        line = f"- code={item.code} outcome_id={item.outcome_id}"
        if pack_value:
            line += f" pack_value={pack_value}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def structured_request_fields(
    *,
    candidates_sha256: str,
    game_totals_sha256: str,
    team_totals_sha256: str,
    schema_version: str | None = None,
) -> dict[str, str]:
    """Keys that must join every structured-output request hash."""
    return {
        "candidates_sha256": candidates_sha256,
        "game_totals_sha256": game_totals_sha256,
        "team_totals_sha256": team_totals_sha256,
        "schema_version": schema_version or verdicts.SCHEMA_VERSION,
    }


def build_manifest(artifacts: PassArtifacts) -> dict[str, Any]:
    return {
        "pass": artifacts.pass_,
        "request_sha256": artifacts.request_sha256,
        "schema_version": artifacts.schema_version,
        "files": {
            "verdicts.json": sha256_bytes(artifacts.verdicts_json),
            "violations.json": sha256_bytes(artifacts.violations_json),
            "report_fragment.md": sha256_bytes(artifacts.report_fragment),
            "status_fragment.json": sha256_bytes(artifacts.status_fragment),
        },
    }


def publication_id_for(artifacts: PassArtifacts) -> str:
    canonical = json.dumps(build_manifest(artifacts), sort_keys=True).encode("utf-8")
    return sha256_bytes(canonical)


def recompute_publication_id(directory: Path) -> str:
    """Rebuild publication_id from the four artifact files on disk."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rebuilt = {
        "pass": manifest["pass"],
        "request_sha256": manifest["request_sha256"],
        "schema_version": manifest["schema_version"],
        "files": {
            name: sha256_bytes((directory / name).read_bytes()) for name in PUBLICATION_FILES
        },
    }
    return sha256_bytes(json.dumps(rebuilt, sort_keys=True).encode("utf-8"))


def _fsync_path(path: Path) -> None:
    fd = os.open(str(path), os.O_RDWR)
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_publication_tree(staging: Path, artifacts: PassArtifacts) -> None:
    payload = {
        "verdicts.json": artifacts.verdicts_json,
        "violations.json": artifacts.violations_json,
        "report_fragment.md": artifacts.report_fragment,
        "status_fragment.json": artifacts.status_fragment,
    }
    for name, data in payload.items():
        dest = staging / name
        dest.write_bytes(data)
        _fsync_path(dest)
    manifest_bytes = json.dumps(build_manifest(artifacts), sort_keys=True, indent=2).encode("utf-8")
    manifest_path = staging / "manifest.json"
    manifest_path.write_bytes(manifest_bytes + b"\n")
    _fsync_path(manifest_path)
    _fsync_dir(staging)


def _write_current_pointer(
    parent: Path, request_sha256: str, publication_id: str, now: datetime
) -> None:
    payload = {
        "request_sha256": request_sha256,
        "publication_id": publication_id,
        "published_at": now.isoformat(),
    }
    data = json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"
    fd, tmp_path = tempfile.mkstemp(dir=str(parent), prefix="current.json.tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, parent / "current.json")
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def publish_pass(
    pack_dir: Path,
    artifacts: PassArtifacts,
    *,
    now: datetime | None = None,
    pid: int | None = None,
) -> PublishResult:
    """Publish one pass as an immutable versioned directory + current.json swap.

    The directory is assembled under ``<publication_id>.tmp-<pid>`` and made
    visible with a single ``os.replace``. ``current.json`` is updated only
    after that rename. A crash before the pointer swap leaves the previous
    current version fully intact.
    """
    when = now or datetime.now().astimezone()
    process_id = os.getpid() if pid is None else pid
    pub_id = publication_id_for(artifacts)
    parent = pack_dir / "verdicts" / artifacts.pass_
    parent.mkdir(parents=True, exist_ok=True)
    dest = parent / pub_id
    wrote = False
    if not dest.exists():
        staging = parent / f"{pub_id}.tmp-{process_id}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        try:
            _write_publication_tree(staging, artifacts)
            os.replace(staging, dest)
            wrote = True
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise RunnerError(f"Failed to publish pass {artifacts.pass_}") from exc
    _write_current_pointer(parent, artifacts.request_sha256, pub_id, when)
    return PublishResult(
        publication_id=pub_id,
        request_sha256=artifacts.request_sha256,
        path=dest,
        wrote=wrote,
    )


def publish_verdict_pass(
    pack_dir: Path,
    output_text: str,
    *,
    pass_: str,
    request_sha256: str,
    candidates_sha256: str,
    game_totals_sha256: str,
    team_totals_sha256: str,
    model: str,
    now: datetime | None = None,
) -> PublishResult:
    """Parse a verdict envelope, gate it, and publish. Raises RunnerError on fail."""
    from outlier_scrapers import pack_index, paths, verdicts
    from outlier_scrapers.verdict_gate import validate_envelope

    try:
        parsed = parse_envelope(output_text, "verdict")
    except (verdicts.EnvelopeUnparseableError, verdicts.SchemaInvalidError) as exc:
        raise RunnerError(f"Pass {pass_} output is not a valid verdict envelope: {exc}") from exc
    if not isinstance(parsed.envelope, verdicts.VerdictEnvelope):
        raise RunnerError(f"Pass {pass_} output did not parse as a verdict envelope")

    index = pack_index.build_pack_index(
        pack_dir, policy_path=paths.PROJECT_ROOT / "missing-portfolio-policy.json"
    )
    gate = validate_envelope(parsed, index, now or datetime.now().astimezone())
    if gate.pass_fails:
        raise RunnerError(
            f"Pass {pass_} failed structured validation: " + ",".join(gate.fail_reasons)
        )

    codes: dict[str, int] = {}
    for item in gate.violations:
        codes[item.code] = codes.get(item.code, 0) + 1
    status = {
        "envelope_present": True,
        "envelope_kind": "verdict",
        "schema_version": parsed.envelope.schema_version,
        "record_count": len(parsed.envelope.verdicts),
        "bet_count": sum(1 for rec in parsed.envelope.verdicts if rec.verdict == "BET"),
        "rejected_count": sum(1 for item in gate.violations if item.severity == "reject"),
        "violation_codes": codes,
        "mode": "shadow",
        "repair_attempts": 0,
        "structured_output_native": True,
    }
    lines = [f"# Pass {pass_}", ""]
    for rec in parsed.envelope.verdicts:
        lines.append(
            f"- {rec.verdict} {rec.selection} {rec.line} {rec.price} ({rec.recommended_units}u)"
        )
    artifacts = PassArtifacts(
        pass_=pass_,
        request_sha256=request_sha256,
        schema_version=verdicts.SCHEMA_VERSION,
        verdicts_json=write_envelope(
            parsed.envelope,
            request_sha256=request_sha256,
            model=model,
            candidates_sha256=candidates_sha256,
            game_totals_sha256=game_totals_sha256,
            team_totals_sha256=team_totals_sha256,
        ),
        violations_json=write_violations(gate.violations),
        report_fragment=("\n".join(lines) + "\n").encode("utf-8"),
        status_fragment=json.dumps(status, sort_keys=True).encode("utf-8"),
    )
    return publish_pass(pack_dir, artifacts, now=now)


def load_current_publications(pack_dir: Path) -> dict[str, Any]:
    """Read A/D/B/C current.json + verdicts.json into UpstreamPublication maps."""
    from outlier_scrapers.verdict_gate import UpstreamPublication

    loaded: dict[str, Any] = {}
    for pass_name in ("A", "D", "B", "C"):
        pointer = pack_dir / "verdicts" / pass_name / "current.json"
        if not pointer.exists():
            continue
        current = json.loads(pointer.read_text(encoding="utf-8"))
        pub_id = str(current.get("publication_id") or "")
        envelope_path = pack_dir / "verdicts" / pass_name / pub_id / "verdicts.json"
        if not pub_id or not envelope_path.exists():
            continue
        data = json.loads(envelope_path.read_text(encoding="utf-8"))
        key = "findings" if pass_name == "C" else "verdicts"
        records = data.get(key) or []
        record_ids = {str(row.get("record_id") or "") for row in records}
        outcome_ids = {str(row.get("outcome_id") or "") for row in records}
        stakes: dict[str, float] = {}
        if pass_name != "C":
            for row in records:
                if str(row.get("verdict") or "") != "BET":
                    continue
                outcome_id = str(row.get("outcome_id") or "")
                try:
                    stakes[outcome_id] = float(row.get("recommended_units") or 0)
                except (TypeError, ValueError):
                    continue
        loaded[pass_name] = UpstreamPublication(
            pass_=pass_name,
            publication_id=pub_id,
            record_ids=frozenset(item for item in record_ids if item),
            outcome_ids=frozenset(item for item in outcome_ids if item),
            stakes=stakes,
        )
    return loaded


def publish_reconciliation_pass(
    pack_dir: Path,
    output_text: str,
    *,
    request_sha256: str,
    candidates_sha256: str,
    game_totals_sha256: str,
    team_totals_sha256: str,
    model: str,
    now: datetime | None = None,
) -> PublishResult:
    """Parse a reconciliation envelope, gate it against current pubs, publish E."""
    from outlier_scrapers import pack_index, paths, verdicts
    from outlier_scrapers.verdict_gate import validate_envelope

    try:
        parsed = parse_envelope(output_text, "reconciliation")
    except (verdicts.EnvelopeUnparseableError, verdicts.SchemaInvalidError) as exc:
        raise RunnerError(f"Pass E output is not a valid reconciliation envelope: {exc}") from exc
    if not isinstance(parsed.envelope, verdicts.ReconciliationEnvelope):
        raise RunnerError("Pass E output did not parse as a reconciliation envelope")

    index = pack_index.build_pack_index(
        pack_dir, policy_path=paths.PROJECT_ROOT / "missing-portfolio-policy.json"
    )
    pubs = load_current_publications(pack_dir)
    gate = validate_envelope(
        parsed, index, now or datetime.now().astimezone(), current_publications=pubs
    )
    if gate.pass_fails:
        raise RunnerError(
            "Pass E failed structured validation: " + ",".join(gate.fail_reasons)
        )

    codes: dict[str, int] = {}
    for item in gate.violations:
        codes[item.code] = codes.get(item.code, 0) + 1
    status = {
        "envelope_present": True,
        "envelope_kind": "reconciliation",
        "schema_version": parsed.envelope.schema_version,
        "record_count": len(parsed.envelope.reconciliations),
        "bet_count": sum(1 for rec in parsed.envelope.reconciliations if rec.verdict == "BET"),
        "rejected_count": sum(1 for item in gate.violations if item.severity == "reject"),
        "violation_codes": codes,
        "mode": "shadow",
        "repair_attempts": 0,
        "structured_output_native": True,
    }
    lines = ["# Pass E", ""]
    for rec in parsed.envelope.reconciliations:
        lines.append(
            f"- {rec.verdict} {rec.selection} {rec.line} {rec.price} ({rec.recommended_units}u)"
        )
    artifacts = PassArtifacts(
        pass_="E",
        request_sha256=request_sha256,
        schema_version=verdicts.SCHEMA_VERSION,
        verdicts_json=write_envelope(
            parsed.envelope,
            request_sha256=request_sha256,
            model=model,
            candidates_sha256=candidates_sha256,
            game_totals_sha256=game_totals_sha256,
            team_totals_sha256=team_totals_sha256,
        ),
        violations_json=write_violations(gate.violations),
        report_fragment=("\n".join(lines) + "\n").encode("utf-8"),
        status_fragment=json.dumps(status, sort_keys=True).encode("utf-8"),
    )
    return publish_pass(pack_dir, artifacts, now=now)
