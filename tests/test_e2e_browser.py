"""End-to-end door een echte browser, met de nadruk op heen én terug.

De andere testbestanden controleren losse endpoints. Dit bestand start de app
zoals hij draait, klikt hem door met Chromium en controleert per scenario of de
wereld ná het ongedaan maken weer is zoals hij was. Dat is precies het gat waar
de handmatige controles doorheen vielen: koppelen werkte, ontkoppelen leek te
werken, en niemand keek of het device daarna terugkwam.

Draaien:

    pip install playwright && playwright install chromium
    pytest tests/test_e2e_browser.py

Zonder Playwright slaat pytest dit bestand over; de rest van de suite blijft
gewoon draaien.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright is niet geïnstalleerd")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import Page, expect, sync_playwright  # noqa: E402

from patch_manager.main import app, database, providers  # noqa: E402

from tests.conftest import CREDENTIALS  # noqa: E402

# De container hier heeft Chromium al staan; op een laptop pakt Playwright zijn
# eigen download. Vandaar geen harde padcontrole maar een zachte.
BUNDLED_CHROMIUM = Path(os.getenv("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium"))

pytestmark = pytest.mark.e2e


# --- de app echt laten draaien ----------------------------------------------

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def live_server():
    """Uvicorn in een thread. Dezelfde `app` en dezelfde database als de andere tests.

    De autouse-fixture uit conftest gooit de database tussen tests leeg en zet
    hem opnieuw op. Dat mag hier, omdat `Database` per handeling een verse
    verbinding opent en dus niets vasthoudt tussen twee tests door.
    """
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base}/api/auth/status", timeout=2)
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        server.should_exit = True
        pytest.fail("De testserver kwam niet op")
    yield base
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as playwright:
        extra = {"executable_path": str(BUNDLED_CHROMIUM)} if BUNDLED_CHROMIUM.exists() else {}
        instance = playwright.chromium.launch(**extra)
        yield instance
        instance.close()


@pytest.fixture
def page(browser, live_server):
    """Een verse sessie per test, met een harde eis: geen enkele JS-fout."""
    context = browser.new_context(viewport={"width": 1600, "height": 1050})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(live_server, wait_until="networkidle")
    if page.locator("#auth-form input[name=username]").count():
        page.fill("#auth-form input[name=username]", CREDENTIALS["username"])
        page.fill("#auth-form input[name=password]", CREDENTIALS["password"])
        page.click("#auth-form button[type=submit]")
    page.wait_for_selector("#app-shell:not(.hidden)", timeout=15000)
    settle(page)
    # De setup-wizard opent zichzelf tot hij is weggeklikt. Escape sluit hem wel,
    # maar onthoudt dat niet -- dus doe het via de knop, zoals een gebruiker.
    if page.locator("#wizard-dialog[open]").count():
        page.click("#wizard-close")
        settle(page)
    yield page
    context.close()
    assert errors == [], f"JavaScript-fouten in de browser: {errors}"


# --- kleine hulpjes ---------------------------------------------------------

def settle(page: Page) -> None:
    """Wachten tot de fetch-ronde van `loadData` is neergedaald."""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(350)


def tab(page: Page, name: str) -> None:
    page.click(f"[data-tab={name}]")
    settle(page)


def discovery(name: str, ip: str = "192.168.1.77") -> str:
    """Een vondst zoals de ARP-adapter hem oplevert, langs de echte weg."""
    return providers._store_record(
        "dhcp-arp", f"arp:{ip}", "network_device", {"ip": ip},
        name=name, entity_type="device", status="up", ip_address=ip,
        mac_address="aa:bb:cc:dd:ee:01",
    )


@pytest.fixture
def with_discovery():
    """Zet de vondst klaar *voordat* de browser laadt.

    Vraag hem in de testhandtekening vóór `page` aan; pytest bouwt fixtures op
    in die volgorde, en dan hoeft de pagina niet herladen te worden.
    """
    return discovery("Gevonden-NAS")


def hidden(page: Page, selector: str) -> bool:
    return bool(page.locator(selector).evaluate("element => element.classList.contains('hidden')"))


# --- scenario's -------------------------------------------------------------

def test_linking_a_discovery_and_unlinking_it_puts_it_back(with_discovery, page: Page) -> None:
    """Loskoppelen moet het device terugzetten waar het vandaan kwam.

    Dit is de klacht uit het gebruik: koppelen lukt, maar komt het ding daarna
    weer in 'Niet-gekoppelde discoveries'? Alleen de heenweg testen zei niets.
    """
    tab(page, "admin")
    before = page.locator("[data-link-entity]").count()
    assert before >= 1, "De discovery staat niet in de lijst"

    page.locator("[data-link-entity]").first.click()
    settle(page)
    assert page.locator("[data-tab=patch]").get_attribute("class").find("active") >= 0

    page.locator("[data-device-card] .port-face").first.click()
    settle(page)
    page.click("#port-form button[type=submit]")
    settle(page)

    tab(page, "admin")
    assert page.locator("[data-link-entity]").count() == before - 1, \
        "Een gekoppeld device hoort uit de discoverylijst te verdwijnen"

    tab(page, "patch")
    page.locator("[data-device-card] .port-face").first.click()
    settle(page)
    assert not hidden(page, "#disconnect-port"), "Een bezette poort hoort een ontkoppelknop te tonen"
    page.once("dialog", lambda box: box.accept())
    page.click("#disconnect-port")
    settle(page)

    tab(page, "admin")
    assert page.locator("[data-link-entity]").count() == before, \
        "Na ontkoppelen hoort het device weer bij de ongekoppelde discoveries te staan"


def test_hiding_a_topology_node_can_be_undone(page: Page) -> None:
    """Verbergen mag geen deur zijn die alleen dichtgaat.

    De backend stuurde verborgen knopen niet mee, dus een verborgen apparaat was
    nergens meer aan te wijzen om het terug te zetten.
    """
    tab(page, "topology")
    page.click("#topology-edit")
    settle(page)
    nodes = page.locator("#topology-canvas [data-node-id]")
    total = nodes.count()
    assert total >= 2, "Te weinig knopen om iets zinnigs te testen"
    node_id = nodes.first.get_attribute("data-node-id")

    nodes.first.click()
    settle(page)
    page.check("#topology-node-form input[name=hidden]")
    page.click("#topology-node-form button[type=submit]")
    settle(page)
    assert page.locator("#topology-canvas [data-node-id]").count() == total - 1
    assert not hidden(page, "#hidden-count"), "De teller hoort te verklappen dat er iets verborgen is"
    assert page.locator("#hidden-count").inner_text() == "1"

    page.check('[data-layer="hidden"]')
    settle(page)
    assert page.locator("#topology-canvas [data-node-id]").count() == total, \
        "Met de laag 'verborgen' aan hoort alles weer in beeld te staan"

    page.locator(f'[data-node-id="{node_id}"]').click()
    settle(page)
    assert page.locator("#topology-node-form input[name=hidden]").is_checked(), \
        "Het vinkje hoort de stand van de knoop te tonen"
    page.uncheck("#topology-node-form input[name=hidden]")
    page.click("#topology-node-form button[type=submit]")
    settle(page)
    page.uncheck('[data-layer="hidden"]')
    settle(page)
    assert page.locator("#topology-canvas [data-node-id]").count() == total
    assert hidden(page, "#hidden-count")


def test_a_topology_node_leads_back_to_the_real_device(page: Page) -> None:
    """De topologie is een weergave; hij hoort niet dood te lopen.

    Verwijderen kan hier bewust niet -- een knoop is geen ding maar een
    afbeelding van een ding. Maar dan moet het ding zelf wel bereikbaar zijn.
    """
    tab(page, "topology")
    page.click("#topology-edit")
    settle(page)
    nodes = page.locator("#topology-canvas [data-node-id]")
    opened = False
    for index in range(nodes.count()):
        nodes.nth(index).click()
        settle(page)
        if not hidden(page, "#open-topology-source"):
            page.click("#open-topology-source")
            settle(page)
            opened = True
            break
        page.keyboard.press("Escape")
        settle(page)
    assert opened, "Geen enkele knoop leidde terug naar een device of netwerkapparaat"
    assert page.locator("#entity-dialog[open], #physical-dialog[open]").count() == 1
    assert page.locator("#topology-node-dialog[open]").count() == 0, \
        "Het knoopdialoog hoort te sluiten; twee open dialogen over elkaar is geen UI"


def test_a_pending_link_is_visible_and_can_be_cancelled(with_discovery, page: Page) -> None:
    """'Kies de fysieke poort' is een stand van de app; die hoort zichtbaar te zijn.

    Eerder was het een onzichtbare variabele die verdween zodra je de lade sloot
    zonder op te slaan.
    """
    tab(page, "admin")
    page.locator("[data-link-entity]").first.click()
    settle(page)
    assert not hidden(page, "#pending-link")
    assert "Gevonden-NAS" in page.locator("#pending-link").inner_text()

    page.locator("[data-device-card] .port-face").first.click()
    settle(page)
    assert page.locator("#port-form select[name=entity_id]").input_value(), \
        "De poortlade hoort de gekozen device al ingevuld te hebben"
    page.keyboard.press("Escape")
    settle(page)
    assert not hidden(page, "#pending-link"), \
        "De lade sluiten zonder op te slaan mag de keuze niet stilletjes weggooien"

    page.locator("[data-device-card] .port-face").nth(1).click()
    settle(page)
    assert page.locator("#port-form select[name=entity_id]").input_value(), \
        "De keuze hoort ook bij de volgende poort nog te gelden"
    page.keyboard.press("Escape")
    settle(page)

    page.click("#cancel-pending")
    settle(page)
    assert hidden(page, "#pending-link")
    page.locator("[data-device-card] .port-face").first.click()
    settle(page)
    assert page.locator("#port-form select[name=entity_id]").input_value() == "", \
        "Na annuleren hoort de lade weer leeg te openen"


def test_saving_a_link_clears_the_pending_state(with_discovery, page: Page) -> None:
    """Opslaan is de andere uitgang uit dezelfde stand."""
    tab(page, "admin")
    page.locator("[data-link-entity]").first.click()
    settle(page)
    page.locator("[data-device-card] .port-face").first.click()
    settle(page)
    page.click("#port-form button[type=submit]")
    settle(page)
    assert hidden(page, "#pending-link")


def test_creating_and_deleting_a_device_leaves_no_trace(page: Page) -> None:
    """Toevoegen en weer weggooien hoort op nul uit te komen."""
    tab(page, "patch")
    before = page.locator("#unpatched-list .chip-device").count()
    rows_before = page.locator("#manual-entities-list [data-entity-delete]").count()

    page.click("#new-entity-button")
    page.wait_for_selector("#entity-dialog[open]")
    page.fill("#entity-form input[name=name]", "Wegwerpapparaat")
    page.select_option("#entity-form select[name=type]", "device")
    page.click("#entity-form button[type=submit]")
    settle(page)
    assert page.locator("#unpatched-list .chip-device").count() == before + 1
    assert page.locator("#manual-entities-list [data-entity-delete]").count() == rows_before + 1

    row = page.locator("#manual-entities-list .entity-row", has_text="Wegwerpapparaat").first
    page.once("dialog", lambda box: box.accept())
    row.locator("[data-entity-delete]").click()
    settle(page)
    assert page.locator("#unpatched-list .chip-device").count() == before
    assert page.locator("#manual-entities-list [data-entity-delete]").count() == rows_before


def test_escape_closes_every_overlay(page: Page) -> None:
    """Half de app reageert op Escape en de andere helft niet is geen UI.

    De lades zijn gewone divs met een blokkerende achtergrond; een `<dialog>`
    sluit vanzelf. Beide horen zich hetzelfde te gedragen.
    """
    tab(page, "patch")
    page.locator("[data-device-card] .port-face").first.click()
    settle(page)
    assert page.locator("#port-drawer").evaluate("element => element.classList.contains('open')")
    page.keyboard.press("Escape")
    settle(page)
    assert not page.locator("#port-drawer").evaluate("element => element.classList.contains('open')")
    assert hidden(page, "#drawer-backdrop"), "De achtergrond hoort mee te verdwijnen"

    page.click("#new-entity-button")
    page.wait_for_selector("#entity-dialog[open]")
    page.keyboard.press("Escape")
    settle(page)
    assert page.locator("#entity-dialog[open]").count() == 0


def test_every_tab_renders_without_errors(page: Page) -> None:
    """Doorklikken zonder JS-fout; de fixture bewaakt dat laatste."""
    for name in ("patch", "topology", "apps", "admin", "patch"):
        tab(page, name)
        assert page.locator(f"#{name}-view.active").count() == 1
