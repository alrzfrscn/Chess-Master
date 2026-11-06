"""Time controls and the authoritative game clock.

The clock is intentionally a small value object: the session owns when it runs
and who is on move, this module owns the arithmetic and the preset table.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from chess_master.engine.errors import InvalidInputError

__all__ = ["TIME_CONTROL_PRESETS", "ClockControl", "GameClock", "resolve_time_control"]

TIME_CONTROL_PRESETS: dict[str, dict[str, Any]] = {
    "1+0": {"initial_ms": 60_000, "increment_ms": 0, "label": "1+0", "category": "bullet"},
    "2+1": {"initial_ms": 120_000, "increment_ms": 1_000, "label": "2+1", "category": "bullet"},
    "3+0": {"initial_ms": 180_000, "increment_ms": 0, "label": "3+0", "category": "blitz"},
    "3+2": {"initial_ms": 180_000, "increment_ms": 2_000, "label": "3+2", "category": "blitz"},
    "5+0": {"initial_ms": 300_000, "increment_ms": 0, "label": "5+0", "category": "blitz"},
    "10+0": {"initial_ms": 600_000, "increment_ms": 0, "label": "10+0", "category": "rapid"},
    "15+10": {"initial_ms": 900_000, "increment_ms": 10_000, "label": "15+10", "category": "rapid"},
}

_CATEGORIES = ("bullet", "blitz", "rapid", "classical", "custom")


@dataclass(frozen=True, slots=True)
class ClockControl:
    initial_ms: int
    increment_ms: int = 0
    label: str = ""
    category: str = "custom"

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_ms": self.initial_ms,
            "increment_ms": self.increment_ms,
            "label": self.label,
            "category": self.category,
        }


def _whole_ms(value: Any, field: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise InvalidInputError(f"Time control {field} must be a whole number.")
    if isinstance(value, float):
        if not value.is_integer():
            raise InvalidInputError(f"Time control {field} must be a whole number.")
        value = int(value)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, int) or value < minimum:
        raise InvalidInputError(
            f"Time control {field} must be a whole number of at least {minimum}.",
            details={field: value},
        )
    return value


def resolve_time_control(value: Any) -> ClockControl | None:
    """Turn a preset name, ``None``/``"unlimited"`` or a mapping into a control."""

    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "unlimited", "none", "off"}:
            return None
        preset = TIME_CONTROL_PRESETS.get(text)
        if preset is None:
            raise InvalidInputError(
                "Choose a known time control or build a custom one.",
                details={"time_control": value, "presets": list(TIME_CONTROL_PRESETS)},
            )
        return ClockControl(**preset)
    if not isinstance(value, Mapping):
        raise InvalidInputError(
            "Time control must be a preset name, 'unlimited' or a custom object.",
            details={"time_control": value},
        )

    initial_ms = _whole_ms(value.get("initial_ms"), "initial_ms", 1)
    increment_ms = _whole_ms(value.get("increment_ms", 0), "increment_ms", 0)
    category = str(value.get("category") or _category_for(initial_ms)).strip().lower()
    if category not in _CATEGORIES:
        raise InvalidInputError(
            "Time control category must be bullet, blitz, rapid, classical or custom.",
            details={"category": category, "valid": list(_CATEGORIES)},
        )
    label = str(value.get("label") or _label_for(initial_ms, increment_ms)).strip()[:24]
    return ClockControl(
        initial_ms=initial_ms,
        increment_ms=increment_ms,
        label=label,
        category=category,
    )


def _category_for(initial_ms: int) -> str:
    if initial_ms < 180_000:
        return "bullet"
    if initial_ms < 600_000:
        return "blitz"
    if initial_ms < 1_800_000:
        return "rapid"
    return "classical"


def _label_for(initial_ms: int, increment_ms: int) -> str:
    return f"{initial_ms / 1000:g}+{increment_ms // 1000}"


class GameClock:
    """Remaining time for both sides plus the increment rule."""

    def __init__(
        self,
        control: ClockControl,
        *,
        white_ms: int | None = None,
        black_ms: int | None = None,
        active_color: str = "white",
        running: bool = True,
        last_tick_monotonic: float | None = None,
    ) -> None:
        self.control = control
        self.white_ms = int(control.initial_ms if white_ms is None else white_ms)
        self.black_ms = int(control.initial_ms if black_ms is None else black_ms)
        self.active_color = "black" if active_color == "black" else "white"
        self.running = bool(running)
        self.last_tick_monotonic = (
            time.monotonic() if last_tick_monotonic is None else float(last_tick_monotonic)
        )

    # -- arithmetic ------------------------------------------------------
    def tick(self, now: float | None = None) -> int:
        """Deduct elapsed time from the side on move and return the elapsed ms."""

        if not self.running:
            return 0
        now = time.monotonic() if now is None else float(now)
        elapsed = max(0, int((now - self.last_tick_monotonic) * 1000))
        self.last_tick_monotonic = now
        if elapsed:
            self._deduct(self.active_color, elapsed)
        return elapsed

    def apply_move(self, mover: str, now: float | None = None) -> int:
        """Settle the mover's clock, award the increment and pass the turn."""

        now = time.monotonic() if now is None else float(now)
        elapsed = max(0, int((now - self.last_tick_monotonic) * 1000))
        color = "black" if mover == "black" else "white"
        self._deduct(color, elapsed)
        remaining = self.white_ms if color == "white" else self.black_ms
        # No increment for a move completed after the flag fell.
        if remaining > 0:
            if color == "white":
                self.white_ms += self.control.increment_ms
            else:
                self.black_ms += self.control.increment_ms
        self.active_color = "black" if color == "white" else "white"
        self.last_tick_monotonic = now
        return elapsed

    def _deduct(self, color: str, elapsed_ms: int) -> None:
        if color == "white":
            self.white_ms = max(0, self.white_ms - elapsed_ms)
        else:
            self.black_ms = max(0, self.black_ms - elapsed_ms)

    def flagged_color(self) -> str | None:
        """The side whose flag fell, if any.  The side on move loses ties."""

        if self.active_color == "white":
            if self.white_ms <= 0:
                return "white"
            return "black" if self.black_ms <= 0 else None
        if self.black_ms <= 0:
            return "black"
        return "white" if self.white_ms <= 0 else None

    def stop(self) -> None:
        if self.running:
            self.tick()
        self.running = False

    def start(self, now: float | None = None) -> None:
        self.running = True
        self.last_tick_monotonic = time.monotonic() if now is None else float(now)

    # -- serialization ---------------------------------------------------
    def to_state(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "control": self.control.label,
            "label": self.control.label,
            "category": self.control.category,
            "initial_ms": self.control.initial_ms,
            "increment_ms": self.control.increment_ms,
            "white_ms": self.white_ms,
            "black_ms": self.black_ms,
            "active_color": self.active_color,
            "running": self.running,
            "flagged": self.flagged_color() is not None,
        }
