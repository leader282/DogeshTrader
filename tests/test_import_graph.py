"""Every module must import cleanly from a cold interpreter.

An in-process import can pass only because a sibling module was imported
first. A fresh interpreter per module catches circular imports that would
otherwise surface only when a user runs one particular CLI command.
"""

from __future__ import annotations

import os
import pkgutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import dogeshtrader


def _cold_import(module: str) -> tuple[str, str] | None:
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return None if proc.returncode == 0 else (module, proc.stderr.strip().splitlines()[-1])


def test_every_module_imports_from_cold_start() -> None:
    modules = sorted(
        m.name for m in pkgutil.walk_packages(dogeshtrader.__path__, prefix="dogeshtrader.")
    )
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        failures = [f for f in pool.map(_cold_import, modules) if f is not None]
    assert not failures, "\n".join(f"{mod}: {err}" for mod, err in failures)
