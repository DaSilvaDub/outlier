"""NBA run pipeline: run every stage, seal the receipts, then decide publication once.

Exit codes: 0 OK (published or bundle-only by mode), 3 required stage not OK
(publication withheld, bundle kept for forensics).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from outlier_nba.receipts import (
    ReceiptLog,
    RunStatus,
    StageReceipt,
    gate_reason,
    receipt,
    run_status,
)
from outlier_nba.run_context import RunContext
from outlier_nba.run_writer import RunWriter

logger = structlog.get_logger("outlier_nba.pipeline")

EXIT_OK = 0
EXIT_WITHHELD = 3

StageFn = Callable[
    [RunContext, RunWriter, dict[str, Any]], "StageReceipt | Sequence[StageReceipt] | None"
]


@dataclass(frozen=True)
class Stage:
    name: str
    required: bool
    run: StageFn


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    status: RunStatus
    exit_code: int
    published: bool
    withheld_reason: str | None
    receipts: list[StageReceipt]


class NbaPipeline:
    def __init__(self, *, nba_dir: Path | str, published_dir: Path | str) -> None:
        self.nba_dir = Path(nba_dir)
        self.published_dir = Path(published_dir)

    def _run_stage(
        self, stage: Stage, ctx: RunContext, writer: RunWriter, state: dict[str, Any]
    ) -> list[StageReceipt]:
        try:
            out = stage.run(ctx, writer, state)
        except Exception as exc:  # a crashing stage is a failed stage, never a skipped one
            logger.warning("stage_crashed", stage=stage.name, error=str(exc))
            return [
                receipt(
                    stage.name,
                    "FAILED",
                    required=stage.required,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            ]
        if out is None:
            return [
                receipt(
                    stage.name,
                    "FAILED",
                    required=stage.required,
                    reason="stage returned no receipt",
                )
            ]
        return [out] if isinstance(out, StageReceipt) else list(out)

    def run(
        self, ctx: RunContext, stages: Sequence[Stage], *, pointer: Path | str | None = None
    ) -> PipelineResult:
        writer = RunWriter(self.nba_dir, ctx.run_id)
        state: dict[str, Any] = {"out": self.published_dir, "nba_dir": self.nba_dir}
        log = ReceiptLog()
        for stage in stages:
            state["receipts"] = log.items  # stages may gate their own side effects on these
            log.extend(self._run_stage(stage, ctx, writer, state))
        receipts = log.seal()  # no receipt may be appended after this line

        status = run_status(receipts)
        withheld = gate_reason(receipts)
        publish = ctx.publishes and withheld is None
        if not publish:
            writer.withhold_publication()
        manifest = {
            "context": ctx.to_dict(),
            "status": status,
            "published": publish,
            "withheld_reason": withheld or ctx.publication_reason,
            "receipts": [r.to_dict() for r in receipts],
        }
        writer.commit(manifest, pointers=[Path(pointer)] if (publish and pointer) else [])
        return PipelineResult(
            run_id=ctx.run_id,
            status=status,
            exit_code=EXIT_WITHHELD if withheld else EXIT_OK,
            published=publish,
            withheld_reason=withheld,
            receipts=receipts,
        )
