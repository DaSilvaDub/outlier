from __future__ import annotations

from typing import Any, Iterable

from .auth import is_props_url


def wait_for_visible(page: Any, selectors: Iterable[str], timeout: int = 3000) -> Any | None:
    for selector in selectors:
        try:
            element = page.wait_for_selector(selector, state="visible", timeout=timeout)
            if element:
                return element
        except Exception:
            continue
    return None


def click_first_visible(page: Any, selectors: Iterable[str], timeout: int = 2500) -> bool:
    """Click the first visible matching element.

    This intentionally fixes the dead-code splice present in the NBA reference scraper.
    """
    element = wait_for_visible(page, selectors, timeout=timeout)
    if not element:
        return False
    try:
        element.click()
        return True
    except Exception:
        return False


def is_on_props_page(page: Any, league: str) -> bool:
    try:
        return is_props_url(page.url, league)
    except Exception:
        return False

