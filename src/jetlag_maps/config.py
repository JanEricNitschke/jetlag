from __future__ import annotations

from enum import StrEnum


class Player(StrEnum):
    SAM_DENBY = "Sam Denby"
    ADAM_CHASE = "Adam Chase"
    BEN_DOYLE = "Ben Doyle"
    BRIAN_MCMANUS = "Brian McManus"
    JOSEPH_PISENTI = "Joseph Pisenti"
    TOBY_HENDY = "Toby Hendy"
    SCOTTY_ALLEN = "Scotty Allen"
    MICHELLE_KHARE = "Michelle Khare"
    AMY_MULLER = "Amy Muller"
    TOM_SCOTT = "Tom Scott"
    MICHAEL_DOWNIE = "Michael Downie"


class Color(StrEnum):
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
