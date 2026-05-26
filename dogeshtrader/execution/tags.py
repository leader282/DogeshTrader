"""Order tag composition.

Kite's order ``tag`` field has a 20-char limit and a restricted charset. We
prepend ``DEFAULT_ORDER_TAG_PREFIX`` and pack a short strategy id + a short
intent suffix so operators can reconcile orders on the Kite dashboard.
"""

from __future__ import annotations

import re

from dogeshtrader.constants import KITE_TAG_MAX_LEN

_TAG_SAFE_RE = re.compile(r"[^a-zA-Z0-9_]")


def _safe(value: str) -> str:
    return _TAG_SAFE_RE.sub("", value)


def compose_tag(prefix: str, strategy_id: str, intent_id: str) -> str:
    """Build a tag <= 20 chars in the form ``<prefix><sid>_<short>``."""
    prefix_clean = _safe(prefix).lower()
    sid = _safe(strategy_id).lower()
    short = _safe(intent_id).lower().lstrip("ord_")[:6]

    budget = KITE_TAG_MAX_LEN - len(prefix_clean) - 1 - len(short)  # 1 = underscore
    budget = max(budget, 1)
    sid_short = sid[:budget]
    tag = f"{prefix_clean}{sid_short}_{short}"
    return tag[:KITE_TAG_MAX_LEN]


def parse_tag(tag: str) -> dict[str, str]:
    """Best-effort reverse of :func:`compose_tag`."""
    return {"raw": tag}


__all__ = ["compose_tag", "parse_tag"]
