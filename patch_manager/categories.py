"""Eén categorielijst voor alles wat in de inventaris staat.

De rol van iets stond eerder op drie plekken los van elkaar: een keuzelijst in
het handmatige-deviceformulier, een andere in het netwerkapparaatformulier, en
een derde reeks waarden die de providers zelf verzinnen ('vm', 'container',
'service'). Daardoor kon een discovery een type krijgen dat nergens in een
dropdown stond.

Nu komt alles hiervandaan. Twee eigenschappen sturen het gedrag:

- `physical`: heeft poorten en hoort dus in `physical_devices`, niet in
  `entities`.
- `attachable`: kan fysiek aan een netwerkapparaat hangen, met een kabel of als
  poortloze uplink. Een container of een Uptime Kuma-monitor zit niet in een
  switchpoort; die hangt aan de host waar hij op draait.
"""

from __future__ import annotations

from typing import NamedTuple


class Category(NamedTuple):
    key: str
    label: str
    icon: str
    physical: bool
    attachable: bool


CATEGORIES: list[Category] = [
    Category("switch", "Switch", "▤", True, False),
    Category("mesh_ap", "Mesh access point", "◈", True, False),
    Category("access_point", "Access point", "◈", True, False),
    Category("patch_panel", "Patchpanel", "▦", True, False),
    # De glasvezel-ONT: één LAN-poort waar het hele huis achter hangt.
    Category("ont", "ONT (glasvezel)", "◎", True, False),
    Category("router", "Router/modem", "⇄", False, True),
    Category("host", "Host", "▣", False, True),
    Category("nas", "NAS", "▥", False, True),
    Category("camera", "Camera", "●", False, True),
    Category("printer", "Printer", "⎙", False, True),
    Category("iot", "IoT", "✦", False, True),
    Category("device", "Device", "○", False, True),
    # Draaien op een host en hebben geen eigen netwerkpoort.
    Category("vm", "VM", "◇", False, False),
    Category("lxc", "LXC", "□", False, False),
    Category("container", "Container", "⬡", False, False),
    Category("service", "Service", "◉", False, False),
]

# Waarop een onbekende categorie terugvalt. Een provider die morgen iets nieuws
# verzint levert dan nog steeds een leesbare rij op in plaats van een lege cel.
FALLBACK = "device"

_BY_KEY = {item.key: item for item in CATEGORIES}

ENTITY_KEYS = [item.key for item in CATEGORIES if not item.physical]
PHYSICAL_KEYS = [item.key for item in CATEGORIES if item.physical]
ATTACHABLE_KEYS = [item.key for item in CATEGORIES if item.attachable]


def payload() -> list[dict[str, object]]:
    """De lijst zoals de frontend hem krijgt, zodat daar niets is hardgecodeerd."""
    return [item._asdict() for item in CATEGORIES]


def label_for(key: str | None) -> str:
    entry = _BY_KEY.get(key or "")
    return entry.label if entry else (key or _BY_KEY[FALLBACK].label)


def is_known(key: str | None) -> bool:
    return (key or "") in _BY_KEY


def can_attach(key: str | None) -> bool:
    """Mag dit aan een netwerkapparaat hangen?

    Onbekende categorieën mogen wel: die vallen terug op 'device', en een
    onbekende vondst tegenhouden is vervelender dan hem toestaan.
    """
    entry = _BY_KEY.get((key or "").strip().lower())
    return entry.attachable if entry else True


def normalize(key: str | None, *, physical: bool = False) -> str:
    """Een bruikbare categorie, ook als de bron iets onbekends aanlevert."""
    entry = _BY_KEY.get((key or "").strip().lower())
    if entry and entry.physical == physical:
        return entry.key
    return PHYSICAL_KEYS[0] if physical else FALLBACK
