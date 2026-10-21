"""Stage receipts and the publication gate (re-implemented from the NFL design, plan M1/M13).

A run publishes only when every *required* receipt is ``OK`` or ``EMPTY``.
:class:`ReceiptLog` is sealed before the publication decision is taken, so no
stage can report a failure after the decision (NFL finding B).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ReceiptStatus = Literal["OK", "EMPTY", "INCOMPLETE", "FAILED", "INVALID", "UNSUPPORTED"]
RunStatus = Literal["OK", "PARTIAL", "FAILED"]

PASSING: frozenset[str] = frozenset({"OK", "EMPTY"})
MAX_RECEIPT_ERRORS = 5


@dataclass(frozen=True)
class StageReceipt:
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
        name=name,
        required=required,
        status=status,
        reason=reason,
        expected=expected,
        received=received,
        errors=errs[:MAX_RECEIPT_ERRORS],
    )


def validation_receipt(
    name: str,
    errors: Iterable[Any],
    *,
    count: int | None = None,
    empty: bool = False,
    required: bool = True,
) -> StageReceipt:
    errs = [str(e) for e in errors]
    if errs:
        return receipt(
            name,
            "INVALID",
            required=required,
            reason=f"{len(errs)} validation error(s)",
            received=count,
            errors=errs,
        )
    return receipt(name, "EMPTY" if empty else "OK", required=required, received=count)


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
    bad = failing(receipts)
    if not bad:
        return None
    return "required_stage_not_ok: " + ", ".join(f"{r.name}={r.status}" for r in bad)


class ReceiptLog:
    """Append-only receipt collector that refuses writes once sealed."""

    def __init__(self) -> None:
        self._items: list[StageReceipt] = []
        self._sealed = False

    def append(self, item: StageReceipt) -> None:
        if self._sealed:
            raise RuntimeError(f"receipt {item.name!r} appended after the publication decision")
        self._items.append(item)

    def extend(self, items: Iterable[StageReceipt]) -> None:
        for item in items:
            self.append(item)

    def seal(self) -> list[StageReceipt]:
        self._sealed = True
        return list(self._items)

    @property
    def items(self) -> list[StageReceipt]:
        return list(self._items)
