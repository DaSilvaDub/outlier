"""Tests for the triage-cards HTML renderer's output safety (F4).

The payload carries scraped, externally-sourced strings (player, matchup,
insights, book names). They must not be able to inject markup/script into the
rendered local artifact — neither through the server-side template nor through
the embedded JSON data island.
"""

from outlier_scrapers.cards_html import render_html


def _payload(**over):
    p = {
        "league": "MLB",
        "generated_at": "2026-07-11T00:00:00Z",
        "coverage": {},
        "snapshot_skew": {},
        "board_a": [],
        "board_b": [],
    }
    p.update(over)
    return p


def test_escapes_league_and_generated_at():
    out = render_html(_payload(league="<script>alert(1)</script>", generated_at="<b>x</b>"))
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "<b>x</b>" not in out


def test_escapes_snapshot_skew_reason():
    out = render_html(
        _payload(
            snapshot_skew={
                "is_skewed": True,
                "skew_hours": 5,
                "reason": "<img src=x onerror=alert(1)>",
            }
        )
    )
    assert "<img src=x onerror=alert(1)>" not in out
    assert "&lt;img" in out


def test_neutralizes_markup_in_payload_data_island():
    # A scraped card field containing a raw <script> tag must be neutralized so
    # it cannot break out of / start a live element inside the data island.
    card = {"player": "<script>alert(1)</script>", "market": "K", "sides": {}}
    out = render_html(_payload(board_a=[card]))
    assert "<script>alert(1)" not in out  # opening tag from data is neutralized
    assert "alert(1)" in out              # data itself is preserved (escaped)


def test_client_side_escape_helper_is_wired():
    # The runtime DOM is built via innerHTML; an esc() helper must exist so the
    # per-card scraped strings are escaped before insertion. Guards against the
    # fix being silently removed.
    out = render_html(_payload())
    assert "const esc=" in out
