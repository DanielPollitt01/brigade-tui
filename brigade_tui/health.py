"""The source health strip: one row per enabled source.

Brigade reads three stores, and a store can be missing or fail to read. A
missing store returns no sessions. Without a health strip that looks exactly
like an empty store: silent, zero-length, and wrong. The strip names every
source the app is configured to read, its store path, how the store is
opened, and how many sessions it reported. A source that is not present, or
that fails to read, is marked ``unavailable`` or ``error`` so the silence is
visible.

The strip never uses the three reserved state colours (red, green, blue).
Those belong to the session grid alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text


@dataclass(frozen=True)
class SourceHealth:
    """The health of one source at one poll.

    ``count`` is the number of sessions the source reported. It is only
    meaningful when ``available`` is True. An unavailable source reports no
    sessions, and the strip says so rather than showing a bare zero.
    """

    harness: str
    path: str
    open_mode: str
    available: bool
    count: int
    error: str | None = None

    @property
    def status(self) -> str:
        """The word or number the strip shows for this source."""
        if self.error:
            return "error"
        if not self.available:
            return "unavailable"
        return f"{self.count} session{'s' if self.count != 1 else ''}"


def render_health_strip(health: list[SourceHealth]) -> Text:
    """Render one line naming every source, its path, mode and count.

    An available source is shown in the default colour. An unavailable or
    errored source is dimmed. No reserved state colour is used.
    """
    text = Text()
    text.append("sources ", style="bold")
    if not health:
        text.append("(none)", style="dim")
        return text
    for index, item in enumerate(health):
        if index:
            text.append("  |  ", style="dim")
        style = "" if item.available else "dim"
        text.append(item.harness, style=f"bold {style}".strip())
        text.append(f"  {item.path}  ", style=style)
        text.append(f"[{item.open_mode}]", style=f"italic {style}".strip())
        text.append(f"  {item.status}", style=style)
    return text
