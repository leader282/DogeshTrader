"""File-based emergency kill switch.

Engaging the switch creates a file under the data directory. Every run mode
polls that file before accepting a new entry, so a second process can halt
trading without sharing memory. Clearing the file resumes entries. Exits stay
allowed so an open position can be flattened.
"""

from __future__ import annotations

from pathlib import Path


class KillSwitch:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def is_engaged(self) -> bool:
        return self.path.exists()

    def reason(self) -> str:
        if not self.path.exists():
            return ""
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return "engaged"
        return text or "engaged"

    def engage(self, reason: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(reason.strip() + "\n", encoding="utf-8")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def kill_switch_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "KILL"


__all__ = ["KillSwitch", "kill_switch_path"]
