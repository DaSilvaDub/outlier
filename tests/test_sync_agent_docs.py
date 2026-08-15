from scripts import sync_agent_docs


def test_common_invariants_include_structured_verdict_contracts():
    block = sync_agent_docs.COMMON_INVARIANTS_BLOCK

    assert "9. Whitelisted ≠ Recommendable" in block
    assert "10. Structured desk verdicts" in block
    assert "desk_snapshot.json" in block


def test_update_file_invariants_is_idempotent(tmp_path):
    target = tmp_path / "AGENTS.md"
    target.write_text(
        "# Test\n\n"
        + sync_agent_docs.INVARIANT_HEADER
        + "\n1. stale invariant\n",
        encoding="utf-8",
    )

    assert sync_agent_docs.update_file_invariants(target) is True
    first = target.read_text(encoding="utf-8")
    assert first.count(sync_agent_docs.INVARIANT_HEADER) == 1
    assert "10. Structured desk verdicts" in first

    assert sync_agent_docs.update_file_invariants(target) is False
    assert target.read_text(encoding="utf-8") == first
