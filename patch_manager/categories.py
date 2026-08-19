"""Eén categorielijst voor alles wat in de inventaris staat.

De rol van iets stond eerder op drie plekken los van elkaar: een keuzelijst in
het handmatige-deviceformulier, een andere in het netwerkapparaatformulier, en
een derde reeks waarden die de providers zelf verzinnen ('vm', 'container',
'service'). Daardoor kon een discovery een type krijgen dat nergens in een
dropdown stond.

Nu komt alles hiervandaan. `physical` markeert de categorieën die poorten
hebben en dus in `physical_devices` thuishoren; de rest zijn `entities`.
"""

from __future__ import annotations


# (sleutel, label, icoon, hoort-bij-physical_devices)
CATEGORIES: list[tuple[str, str, str, bool]] = [
    ("switch", "Switch", "▤", True),
    ("mesh_ap", "Mesh access point", "◈", True),
    ("access_point", "Access point", "◈", True),
    ("patch_panel", "Patchpanel", "▦", True),
    ("router", "Router/modem", "⇄", False),
    ("host", "Host", "▣", False),
    ("vm", "VM", "◇", False),
    ("lxc", "LXC", "□", False),
    ("container", "Container", "⬡", False),
    ("service", "Service", "◉", False),
    ("nas", "NAS", "▥", False),
    ("camera", "Camera", "●", False),
    ("printer", "Printer", "⎙", False),
    ("iot", "IoT", "✦", False),
    ("device", "Device", "○", False),
]

# Waarop een onbekende categorie terugvalt. Een provider die morgen iets nieuws
# verzint levert dan nog steeds een leesbare rij op in plaats van een lege cel.
FALLBACK = "device"

_BY_KEY = {key: (label, icon, physical) for key, label, icon, physical in CATEGORIES}

ENTITY_KEYS = [key for key, _, _, physical in CATEGORIES if not physical]
PHYSICAL_KEYS = [key for key, _, _, physical in CATEGORIES if physical]


def payload() -> list[dict[str, object]]:
    """De lijst zoals de frontend hem krijgt, zodat daar niets is hardgecodeerd."""
    return [
        {"key": key, "label": label, "icon": icon, "physical": physical}
        for key, label, icon, physical in CATEGORIES
    ]


def label_for(key: str | None) -> str:
    entry = _BY_KEY.get(key or "")
    return entry[0] if entry else (key or _BY_KEY[FALLBACK][0])


def is_known(key: str | None) -> bool:
    return (key or "") in _BY_KEY


def normalize(key: str | None, *, physical: bool = False) -> str:
    """Een bruikbare categorie, ook als de bron iets onbekends aanlevert."""
    entry = _BY_KEY.get((key or "").strip().lower())
    if entry and entry[2] == physical:
        return (key or "").strip().lower()
    return PHYSICAL_KEYS[0] if physical else FALLBACK
