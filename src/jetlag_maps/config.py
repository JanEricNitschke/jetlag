"""Player and color configuration for Jet Lag seasons."""

from __future__ import annotations

import functools
from enum import StrEnum
from typing import Self


class Player(StrEnum):
    """A Jet Lag participant, identified by their full display name.

    Attributes
    ----------
    first_name
        The player's given name.
    last_name
        The player's family name.
    aliases
        Alternative first names the player is known by.
    """

    first_name: str
    last_name: str
    aliases: tuple[str, ...]

    SAM_DENBY = "Sam", "Denby"
    ADAM_CHASE = "Adam", "Chase"
    BEN_DOYLE = "Ben", "Doyle"
    BRIAN_MCMANUS = "Brian", "McManus"
    JOSEPH_PISENTI = "Joseph", "Pisenti"
    TOBY_HENDY = "Toby", "Hendy"
    SCOTTY_ALLEN = "Scotty", "Allen", "Scott"
    MICHELLE_KHARE = "Michelle", "Khare"
    AMY_MULLER = "Amy", "Muller"
    TOM_SCOTT = "Tom", "Scott"
    MICHAEL_DOWNIE = "Michael", "Downie"

    def __new__(cls, first_name: str, last_name: str, *aliases: str) -> Self:
        """Create a player from their first and last name and aliases."""
        value = f"{first_name} {last_name}"
        player = str.__new__(cls, value)
        player._value_ = value
        player.first_name = first_name
        player.last_name = last_name
        player.aliases = aliases
        return player

    @classmethod
    @functools.cache
    def _first_name_index(cls) -> dict[str, Player]:
        index: dict[str, Player] = {}
        for player in cls:
            index.setdefault(player.first_name, player)
            for alias in player.aliases:
                index.setdefault(alias, player)
        return index

    @classmethod
    def from_first_name(cls, name: str) -> Player | None:
        """Return the player with the given first name or alias, or ``None``."""
        return cls._first_name_index().get(name)


class Color(StrEnum):
    """A journey color as a ``#RRGGBB`` hex string."""

    BLACK = "#000000"
    WHITE = "#FFFFFF"
    LIGHT_GRAY = "#757575"
    TEAL = "#00796B"
    YELLOW = "#FFEE58"
    DARK_GREEN = "#388E3C"
    DARK_BLUE = "#1976D2"
    LIGHT_BLUE = "#42A5F5"
    RED = "#D32F2F"
    ORANGE = "#F57C00"
    PURPLE = "#7B1FA2"
    PINK = "#EA50EF"
