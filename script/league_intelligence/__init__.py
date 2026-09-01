"""Read-only League Intelligence / Analytics Terminal application facade."""

from .application import (
    MODEL_VERSION,
    architecture,
    build_terminal,
    render_player_rankings_markdown,
)

__all__ = [
    "MODEL_VERSION",
    "architecture",
    "build_terminal",
    "render_player_rankings_markdown",
]
