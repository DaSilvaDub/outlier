"""The suite-wide conftest guard keeps tests away from the production feedback ledger."""

import sqlite3

import pytest

from outlier_scrapers import feedback, feedback_db, so_eval, so_promotion
from tests.conftest import REAL_LEDGER_PATHS


def test_default_ledger_paths_point_into_tmp(tmp_path):
    for path in (
        feedback_db.DEFAULT_DB_PATH,
        feedback.DEFAULT_DB_PATH,
        so_eval.DEFAULT_DB,
        so_promotion.DEFAULT_DB,
    ):
        assert path.parent == tmp_path / "ledger"


@pytest.mark.parametrize("real", REAL_LEDGER_PATHS, ids=lambda p: str(p))
def test_connecting_to_real_ledger_fails(real):
    with pytest.raises(RuntimeError, match="real feedback ledger"):
        sqlite3.connect(real)
    with pytest.raises(RuntimeError, match="real feedback ledger"):
        sqlite3.connect(str(real))
    with pytest.raises(RuntimeError, match="real feedback ledger"):
        sqlite3.connect(f"{real.resolve().as_uri()}?immutable=1", uri=True)


def test_connecting_elsewhere_still_works(tmp_path):
    conn = sqlite3.connect(tmp_path / "other.sqlite3")
    conn.close()
    sqlite3.connect(":memory:").close()


@pytest.mark.uses_real_ledger
def test_opt_out_marker_leaves_defaults_alone():
    assert feedback_db.DEFAULT_DB_PATH in REAL_LEDGER_PATHS
