"""Terminal colour configuration without a Textual dependency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ghostty_textual.cells import Rgb


def _default_palette() -> tuple[Rgb, ...]:
    base: tuple[Rgb, ...] = (
        (0, 0, 0),
        (204, 85, 85),
        (85, 204, 85),
        (205, 205, 85),
        (84, 84, 204),
        (204, 85, 204),
        (85, 205, 205),
        (204, 204, 204),
        (85, 85, 85),
        (255, 110, 110),
        (110, 255, 110),
        (255, 255, 110),
        (110, 110, 255),
        (255, 110, 255),
        (110, 255, 255),
        (255, 255, 255),
    )
    cube = tuple(
        (r, g, b)
        for r in (0, 95, 135, 175, 215, 255)
        for g in (0, 95, 135, 175, 215, 255)
        for b in (0, 95, 135, 175, 215, 255)
    )
    gray = tuple((8 + 10 * index,) * 3 for index in range(24))
    return base + cube + gray


@dataclass(frozen=True, slots=True)
class TerminalTheme:
    foreground: Rgb = (229, 229, 229)
    background: Rgb = (0, 0, 0)
    cursor: Rgb = (229, 229, 229)
    palette: tuple[Rgb, ...] = _default_palette()

    def __post_init__(self) -> None:
        if len(self.palette) != 256:
            raise ValueError("terminal palette must contain exactly 256 colours")

    @classmethod
    def from_textual(cls, app_theme: Any) -> TerminalTheme:
        """Build from an object exposing Textual-like foreground/background colors."""

        def as_rgb(value: object) -> Rgb:
            rich = getattr(value, "rich_color", value)
            triplet = getattr(rich, "triplet", None)
            if triplet is None:
                raise ValueError("theme colour has no RGB triplet")
            return (int(triplet.red), int(triplet.green), int(triplet.blue))

        return cls(
            foreground=as_rgb(app_theme.foreground),
            background=as_rgb(app_theme.background),
        )


DEFAULT_THEME = TerminalTheme()
