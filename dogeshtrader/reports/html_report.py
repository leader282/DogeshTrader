"""Lightweight Jinja2-backed HTML report rendering.

The backtest engine writes a built-in HTML template directly (see
``backtest.report``). This module is the reusable entry point that loads the
optional Jinja2 template at
``dogeshtrader/reports/templates/backtest.html`` if present, falling back to
the simple inline template otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_summary_html(payload: dict[str, Any]) -> str:
    template_path = Path(__file__).parent / "templates" / "backtest.html"
    if template_path.exists():
        try:
            from jinja2 import Template
        except ImportError:
            template = template_path.read_text()
            return template.replace("{{PAYLOAD_JSON}}", json.dumps(payload, indent=2, default=str))
        tmpl = Template(template_path.read_text())
        return tmpl.render(payload=payload, payload_json=json.dumps(payload, indent=2, default=str))

    # Fallback: tiny inline template.
    return (
        "<!doctype html><html><body><h1>DogeshTrader summary</h1>"
        f"<pre>{json.dumps(payload, indent=2, default=str)}</pre>"
        "</body></html>"
    )


__all__ = ["render_summary_html"]
