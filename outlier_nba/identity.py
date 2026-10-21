"""Player and team identity: one name key used everywhere (plan M2).

NFL finding A: ``alt_floors._name_key`` skipped Jr/III suffixes, so a suffixed
name on one feed failed to match the same player on another and an inactive
player could pass. Every NBA name comparison goes through :func:`name_key`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from outlier_scrapers.registry import get_sport_config, normalize_team

IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})

_NBA = get_sport_config("NBA", allow_disabled=True)


def name_key(name: Any) -> str:
    """Lowercase ASCII key: punctuation stripped, hyphens split, trailing suffix dropped.

    A suffix is dropped only when another token remains, so a lone ``"Jr"`` keeps
    its token rather than collapsing to the empty key.
    """
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = text.encode("ascii", "ignore").decode().lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]", "", text)
    tokens = text.split()
    while len(tokens) > 1 and tokens[-1] in SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def resolve_player(
    name: Any, roster: Mapping[str, str | Sequence[str]]
) -> tuple[str | None, list[str]]:
    """Map a raw name to a player id via ``roster`` (keyed by :func:`name_key`).

    Unknown or ambiguous names return ``(None, [IDENTITY_UNRESOLVED])``: the row
    is flagged disqualifying, never silently dropped or guessed.
    """
    hit = roster.get(name_key(name))
    if isinstance(hit, str) and hit:
        return hit, []
    if isinstance(hit, Sequence) and not isinstance(hit, str) and len(set(hit)) == 1:
        return str(hit[0]), []
    return None, [IDENTITY_UNRESOLVED]


def resolve_team(value: Any) -> str | None:
    """Canonical NBA tricode, or None (never a passthrough guess)."""
    return normalize_team(_NBA, value)
