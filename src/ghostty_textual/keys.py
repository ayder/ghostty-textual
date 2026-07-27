"""Transport-neutral input event types and Textual key translation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class KeyEvent:
    key: str
    text: str | None = None
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False


@dataclass(frozen=True, slots=True)
class MouseEvent:
    x: float
    y: float
    action: Literal["press", "release", "motion"] = "press"
    button: int | None = 1
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False


def from_textual(event: Any) -> KeyEvent | None:
    key = str(getattr(event, "key", ""))
    if not key:
        return None
    parts = key.split("+")
    name = parts[-1]
    modifiers = set(parts[:-1])
    aliases = {
        "pageup": "pageup",
        "pagedown": "pagedown",
        "return": "enter",
        "esc": "escape",
    }
    return KeyEvent(
        key=aliases.get(name, name),
        text=getattr(event, "character", None),
        ctrl="ctrl" in modifiers,
        alt="alt" in modifiers,
        shift="shift" in modifiers,
        meta="meta" in modifiers or "super" in modifiers,
    )
