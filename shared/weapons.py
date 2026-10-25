"""Ten distinct weapon progression levels, shared by client and server."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Weapon:
    name: str
    damage: int
    interval: float
    spread: float
    color: str
    magazine_size: int
    reserve_size: int
    max_range: float
    zoom_fov: float


WEAPONS = [
    Weapon("P2000", 25, .35, .008, "#b7c1cc", 13, 52, 120, 70),
    Weapon("MP7", 24, .10, .016, "#767d86", 30, 90, 120, 66),
    Weapon("Nova", 55, .85, .035, "#8c755e", 8, 32, 34, 74),
    Weapon("FAMAS", 26, .10, .014, "#7a8d62", 25, 75, 120, 61),
    Weapon("Desert Eagle", 62, .48, .008, "#c49a54", 7, 35, 120, 56),
    Weapon("AK-47", 36, .10, .012, "#775d42", 30, 90, 120, 64),
    Weapon("M4A4", 32, .09, .010, "#596d51", 30, 90, 120, 62),
    Weapon("AUG", 34, .12, .008, "#697f8d", 30, 90, 120, 48),
    Weapon("AWP", 100, 1.2, .001, "#54606d", 5, 25, 300, 18),
    Weapon("Negev", 16, .06, .045, "#777c83", 100, 200, 120, 68),
]


def for_kills(kills: int) -> Weapon:
    return WEAPONS[min(max(kills, 0) // 10, len(WEAPONS) - 1)]
