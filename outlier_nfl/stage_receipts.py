"""Required/optional stage receipts and the publication gate (forensic report F03, #224).

Every ingestion and validation stage of a run reports a :class:`StageReceipt`.
A run publishes (dated files, ``*_latest.json``, run pointers) only when every
*required* receipt is ``OK`` or ``EMPTY``; otherwise it is kept as a bundle
under ``runs/<run_id>/`` with an explicit ``PARTIAL`` or ``FAILED`` summary,
and the CLI and ``weekly`` exit nonzero.

Receipt statuses (distinct on purpose, so automation can tell them apart):

* ``OK``: the stage ran and its output is complete and valid.
* ``EMPTY``: nothing to do, e.g. no scheduled events on the slate. Not a failure:
  a genuinely empty slate is ``OK`` with zero rows.
* ``INCOMPLETE``: part of the input arrived (a later page failed, a cursor
  stalled, some events' markets failed).
* ``FAILED``: the stage produced nothing usable (e.g. every request failed).
* ``INVALID``: input or normalized output failed schema validation.
* ``UNSUPPORTED``: the slate is outside the supported contract (e.g. postseason).

Run status: ``OK`` when every required receipt is OK/EMPTY; ``PARTIAL`` when the
only problems are ``INCOMPLETE`` receipts; ``FAILED`` otherwise. Both PARTIAL
and FAILED block publication of the whole slate (not only the affected props):
a partial slate published as complete is what F03/F04 forbid.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ReceiptStatus = Literal["OK", "EMPTY", "INCOMPLETE", "FAILED", "INVALID", "UNSUPPORTED"]
RunStatus = Literal["OK", "PARTIAL", "FAILED"]

PASSING: frozenset[str] = frozenset({"OK", "EMPTY"})
MAX_RECEIPT_ERRORS = 5


@dataclass(frozen=True)
class StageReceipt:
    """Outcome of one stage of a run (written to the summary and manifest)."""

    name: str
    required: bool
    status: ReceiptStatus
    reason: str | None = None
    expected: int | None = None
    received: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status in PASSING

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def receipt(
    name: str,
    status: ReceiptStatus,
    *,
    required: bool = True,
    reason: str | None = None,
    expected: int | None = None,
    received: int | None = None,
    errors: Iterable[Any] = (),
) -> StageReceipt:
    errs = [str(e) for e in errors]
    return StageReceipt(
        name=name, required=required, status=status, reason=reason,
        expected=expected, received=received, errors=errs[:MAX_RECEIPT_ERRORS],
    )


def validation_receipt(name: str, errors: Iterable[Any], *, count: int | None = None,
                       empty: bool = False) -> StageReceipt:
    """OK/EMPTY when ``errors`` is empty, else INVALID with the first errors."""
    errs = [str(e) for e in errors]
    if errs:
        return receipt(name, "INVALID", reason=f"{len(errs)} validation error(s)",
                       received=count, errors=errs)
    return receipt(name, "EMPTY" if empty else "OK", received=count)


def quote_integrity_receipt(drop_counts: Mapping[str, int]) -> StageReceipt:
    """Informational receipt of quotes dropped at normalization (F26).

    Never required and always ``OK``, whatever the counts: dropping a foreign
    or conflicting quote is the intended outcome, so it must not make a run
    PARTIAL or bundle-only. ``received`` is the total dropped; ``errors`` lists
    every nonzero count as ``name=count``.
    """
    counts = {k: int(v) for k, v in sorted(drop_counts.items()) if v}
    total = sum(counts.values())
    return StageReceipt(
        name="quote_integrity", required=False, status="OK",
        reason=f"{total} quote(s) dropped at normalization" if total else None,
        received=total, errors=[f"{k}={v}" for k, v in counts.items()],
    )


def failing(receipts: Iterable[StageReceipt]) -> list[StageReceipt]:
    """Required receipts that are not OK/EMPTY, in stage order."""
    return [r for r in receipts if r.required and not r.passed]


def run_status(receipts: Iterable[StageReceipt]) -> RunStatus:
    bad = failing(receipts)
    if not bad:
        return "OK"
    if all(r.status == "INCOMPLETE" for r in bad):
        return "PARTIAL"
    return "FAILED"


def gate_reason(receipts: Iterable[StageReceipt]) -> str | None:
    """``required_stage_not_ok: a=FAILED, b=INCOMPLETE`` or None when the gate passes."""
    bad = failing(receipts)
    if not bad:
        return None
    return "required_stage_not_ok: " + ", ".join(f"{r.name}={r.status}" for r in bad)


def props_admission_check(receipts: list[StageReceipt], slate_events: list[Any],
                          admitted_props: list[Any]) -> None:
    """Make the ``player_props`` receipt record the admitted count; zero for a slate with events is INCOMPLETE.

    It is INCOMPLETE, not FAILED: the feed answered, but the slate's props are
    missing. That is usually transient (not posted yet early in the week, or a
    feed glitch), and the fix is to rerun later, the same as a missing page.
    """
    for i, r in enumerate(receipts):
        if r.name != "player_props":
            continue
        if not r.passed:
            return
        n = len(admitted_props)
        if slate_events and n == 0:
            receipts[i] = receipt(
                "player_props", "INCOMPLETE",
                reason=f"0 props admitted for {len(slate_events)} slate events: it may be too "
                "early (props not posted yet) or the feed may have glitched; rerun later",
                expected=len(slate_events), received=0, errors=r.errors,
            )
        else:
            receipts[i] = receipt("player_props", r.status, reason=r.reason, expected=r.expected,
                                  received=n, errors=r.errors)
        return
