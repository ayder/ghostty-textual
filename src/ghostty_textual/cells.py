"""Immutable cell data and bounded generation-scoped interners."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Rgb = tuple[int, int, int]


class InternerFull(Exception):
    """The style table is full and frame extraction must restart."""


@dataclass(frozen=True, slots=True)
class CellStyle:
    fg: Rgb | None = None
    bg: Rgb | None = None
    underline_color: Rgb | None = None
    bold: bool = False
    faint: bool = False
    italic: bool = False
    blink: bool = False
    inverse: bool = False
    invisible: bool = False
    strikethrough: bool = False
    overline: bool = False
    underline: int = 0


DEFAULT_STYLE = CellStyle()


@dataclass(frozen=True, slots=True)
class Cell:
    text: str
    width: Literal[0, 1, 2]  # 0 is only a wide-grapheme continuation/tail
    style_id: int
    link_id: int | None = None


@dataclass(slots=True)
class StyleInterner:
    limit: int = 4096
    generation: int = 0
    _forward: dict[CellStyle, int] = field(default_factory=dict)
    _reverse: list[CellStyle] = field(default_factory=list)

    def intern(self, style: CellStyle) -> int:
        existing = self._forward.get(style)
        if existing is not None:
            return existing
        if len(self._reverse) >= self.limit:
            raise InternerFull(f"style table is at its limit of {self.limit}")
        style_id = len(self._reverse)
        self._forward[style] = style_id
        self._reverse.append(style)
        return style_id

    def resolve(self, style_id: int) -> CellStyle:
        if not 0 <= style_id < len(self._reverse):
            raise KeyError(style_id)
        return self._reverse[style_id]

    def table(self) -> tuple[CellStyle, ...]:
        return tuple(self._reverse)

    def set_limit(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("style limit must be positive")
        self.limit = limit

    def rollover(self) -> None:
        self._forward.clear()
        self._reverse.clear()
        self.generation += 1


@dataclass(slots=True)
class LinkInterner:
    limit: int = 1024
    max_uri_bytes: int = 2048
    generation: int = 0
    _forward: dict[str, int] = field(default_factory=dict)
    _reverse: list[str] = field(default_factory=list)

    def intern(self, uri: str) -> int | None:
        if len(uri.encode("utf-8")) > self.max_uri_bytes:
            return None
        existing = self._forward.get(uri)
        if existing is not None:
            return existing
        if len(self._reverse) >= self.limit:
            return None
        link_id = len(self._reverse)
        self._forward[uri] = link_id
        self._reverse.append(uri)
        return link_id

    def resolve(self, link_id: int) -> str:
        if not 0 <= link_id < len(self._reverse):
            raise KeyError(link_id)
        return self._reverse[link_id]

    def table(self) -> tuple[str, ...]:
        return tuple(self._reverse)

    def rollover(self) -> None:
        self._forward.clear()
        self._reverse.clear()
        self.generation += 1
