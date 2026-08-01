# Research en roadmap — Network Patch Manager

*Opgesteld: 1 augustus 2026 · basis: `main` @ `f23716b` (v0.3.0, na PR #2 en #3)*

**Werkwijze.** Stap 1 is gebaseerd op het lezen van de volledige codebase (niet de README). Stap 2 is gebaseerd op websearch/webfetch op 1 augustus 2026; bij elke bron staat wat **[geverifieerd]** is (documentatie/zoekresultaat gelezen) en wat **[inschatting]** is (vakkennis, niet in deze sessie geverifieerd). Randvoorwaarden gerespecteerd: self-hosted, single-user, SQLite/FastAPI, providers blijven read-only.

---

## 1. Stand van zaken (uit de code)

1. Backend: FastAPI + SQLite (WAL) in één proces; ~1.200 regels `main.py` met alle routes, Pydantic-modellen en achtergrondlus; frontend is één vanilla-JS-bestand (±560 regels) dat elke 30 s de volledige `/api/bootstrap`-payload pollt.
2. Kern-datamodel: `physical_devices`→`ports`→`port_assignments` (handmatige waarheid, één device per poort), `entities` (manual/discovered), `provider_records`+`observations` (TTL, per veld gecollapsed), `conflicts`, `topology_nodes/relations` (met 50-staps undo), `dns_records`, `proxy_hosts`, `speedtest_runs`, `audit_log`.
3. Zeven read-only providers (ARP/ping, Uptime Kuma, Glances, Portainer, Proxmox, AdGuard, NPM) met versleutelde secrets (Fernet, sleutel buiten de DB) en een net matching-beleid: expliciete mapping → MAC → native ID → hostnamevoorstel.
4. Vastliggende keuzes: geen ORM en geen migratieframework (`executescript` + ad-hoc `ALTER TABLE` in `db.py:initialize`), synchrone SQLite per verbinding, geen websockets, draagbare `.pmbackup`-bundels inclusief encryptiesleutel, Docker non-root met host networking en `NET_RAW`.
5. Zwakke plekken uit de code: **geen enkele notificatie** (de app ziet een device down gaan maar vertelt het niemand); **geen schema-migratiepad** (elke modelwijziging is nu handwerk met risico); **poortmodel kent geen poort↔poort-kabels** (een patchpanel of switch-uplink is niet te documenteren, terwijl de app "Patch Manager" heet); **geen VLAN's**; **providers-adapters zijn vrijwel ongetest** (tests dekken `_store_record`/upserts, geen enkele HTTP-flow gemockt); **bootstrap-payload schaalt lineair mee** met inventaris+audit+records en wordt door elke client elke 30 s opgehaald; `main.py` is een god-module; statushistorie bestaat niet (observations worden bewust tot 1 sample per veld gecollapsed), dus "hoe vaak was m'n NAS offline" is onbeantwoordbaar.

---

## 2. Research

### 2.1 Monitoring/status

| Tool | Wat doen zij beter | Wat doe ik beter | Overneembaar idee |
|---|---|---|---|
| **Beszel** | Hub/agent-model: hele platform <100 MB RAM, agents <10 MB (Go, push-model); Docker- en S.M.A.R.T.-stats per host. [geverifieerd] | Geen agents nodig: ik lees bestaande API's (Glances/Portainer/Proxmox) uit — nul installatie op hosts. | Push-arm blijven; wél hun spaarzame metric-weergave (kleine sparklines per host) als UI-patroon. |
| **Uptime Kuma** | 31 monitortypes, ~94 notificatiekanalen, statuspagina's. [geverifieerd] Maar: berucht om SQLite-bloat — 2–5 GB databases, `SQLITE_BUSY`, UI van 30+ s (issues #3286, #4709, #7338). [geverifieerd] | Mijn observations worden juist actief begrensd (`clean_observations`); ik importeer Kuma in plaats van hem te vervangen. | Notificaties zijn de grootste feature-gap; Kuma's DB-problemen zijn het waarschuwende voorbeeld: historie alléén geaggregeerd en begrensd opslaan. |
| **Gatus** | Config-as-code (YAML in git): reviewbaar, herstelbaar zonder DB-backup; rijke condities (statuscode, JSONPath, TLS-expiry). [geverifieerd] | UI-gedreven beheer is voor één gebruiker sneller; mijn config-export/-import (JSON, stabiele ID's) dekt het herstel-scenario al deels. | TLS-certificaat-expiry als observatieveld op proxyhosts (NPM levert `certificate` al via `expand=`). |
| **Netdata** | Per-seconde metrics, anomaliedetectie. Agents kosten 200–500 MB RAM per node. [geverifieerd] | Past niet bij "beperkte resources"; mijn Glances-integratie geeft 80 % van de waarde voor ~0 extra RAM. | Niets structureels; bevestigt de keuze voor lichte polling. |
| **Zabbix** | Enterprise alerting/escalaties/templates. [inschatting: bekend, niet opnieuw geverifieerd] | Veel te zwaar (server+Postgres/MySQL+agents) voor deze VM. | Alleen het concept "alert-debounce/flap-suppressie": pas alarmeren na N opeenvolgende misses. |

### 2.2 DCIM/IPAM & fysieke patching (directe concurrentie)

| Tool | Wat doen zij beter | Wat doe ik beter | Overneembaar idee |
|---|---|---|---|
| **NetBox** | Het referentie-datamodel: **front ports → rear ports** met posities op patchpanels, kabels als eigen entiteit tussen twee terminaties, end-to-end **cable trace** door tussenliggende panelen; sinds 4.5 zelfs "cable profiles" voor breakout-kabels. [geverifieerd: docs frontport.md + NetBox Labs-blog] Django-migraties, bulk-import, REST-API met contract. | NetBox is Postgres+Redis+workers — veel te zwaar hier; geen live status op poorten (dat is bij hen een plugin/externe tool); mijn discovery-conflictmodel ("handmatig wint, afwijking wordt conflict") is eleganter dan hun "source of truth zonder ogen". | **Kabel als eigen record tussen twee poorten** (in plaats van poort→entity), plus een versimpeld front/rear-model voor patchpanelen. Dit is het hart van mijn domein. |
| **Nautobot** | Zelfde model als NetBox + Jobs/automation. [inschatting] | Zelfde zwaarte-argument. | Niets extra's boven NetBox. |
| **RackTables / openDCIM** | Rack-units en fysieke locatie-administratie. [inschatting: verouderde PHP-stacks, weinig actieve ontwikkeling] | Moderne stack, live status, discovery. | `location`-veld bestaat al; hooguit een eenvoudige "kast/rack-volgorde"-weergave (position-veld bestaat al). |
| **phpIPAM** | Volwassen IPAM: subnets, reserveringen, VLAN's; wordt aangeraden als "lichte NetBox". [geverifieerd op hoofdlijnen] | Geen fysieke patching, geen statusverrijking. | Subnet-overzicht (welke IP's zijn bekend/vrij binnen `PATCH_TRUSTED_SUBNETS`) als kleine IPAM-lite view; VLAN-veld per poort. |
| **netdisco** | MAC→switchpoort-mapping via SNMP/LLDP uit forwarding tables — automatische "wat hangt waar". [geverifieerd] | — | **Niet overneembaar met mijn hardware**: SG108E en Deco XE75 Pro hebben geen SNMP/LLDP-API [inschatting, consistent met bestaande README-beperking]. Wel het UX-idee: zoeken op MAC/IP → direct de poort tonen. |
| **Oxidized** | Config-backup van 130+ devicetypes naar git. [geverifieerd] | — | Niet voor mijn devices (geen CLI/API); mijn AdGuard/NPM-import is de "config-zichtbaarheid" die wél kan. Won't. |

### 2.3 Software patch management — oordeel: **beperkte observatie ja, module nee**

- **PatchMon**: agent-gebaseerd (outbound-only), dashboard voor apt/dnf/apk/pacman-updates, community-editie AGPLv3 met deploy/compliance/RBAC. [geverifieerd] **Patchman, Tactical RMM, Wazuh, Fleet/osquery**: allemaal agent- of endpoint-gebaseerd, met eigen server-stacks (Tactical RMM: Mesh+Postgres; Wazuh: fors; Fleet: osquery-vloot). [inschatting op zwaarte, functies geverifieerd op hoofdlijnen]
- **Oordeel:** een volwaardige patch-managementmodule is **scope creep**: het vereist agents of SSH-toegang op elke host (breekt "geen agents, read-only observeren"), schrijfacties (updates uitvoeren ≠ observeren) en een compliance-datamodel dat niets deelt met poorten/kabels. PatchMon náást deze app draaien is strikt beter dan het nabouwen.
- **Wat wél past:** een read-only observatieveld *"updates beschikbaar"* op host-entities via bestaande providers — Proxmox heeft een API-endpoint dat beschikbare apt-updates per node teruggeeft [inschatting: bekend Proxmox-API-gedrag, endpoint niet in deze sessie geverifieerd — check `GET /nodes/{node}/apt/update` vóór de bouw]. Dat is één extra call in `_sync_proxmox`, nul agents, en volledig binnen de kernregel.

### 2.4 Homelab-dashboards & topologie-UI

| Tool/lib | Relevant patroon | Overneembaar |
|---|---|---|
| **Homepage** | 100+ service-widgets, YAML-config, hoge informatiedichtheid per tegel. [geverifieerd] | Compacte statustegels met één kerngetal + sparkline; mijn summary-chips zijn er al, sparklines ontbreken. |
| **Homarr / Dashy** | Drag-and-drop resp. extreme themeability; mobiel bruikbaar. [geverifieerd] | Bevestigt: één goede dark theme volstaat; investeer in tablet-bruikbaarheid, niet in themes. |
| **NetAlertX (ex-Pi.Alert)** | Nieuwe/onbekende-device-alerts, presence-detectie ("always connected"-device weg = alarm), 80+ notificatiekanalen via Apprise, workflows (auto-groeperen op vendor), **OUI-vendorherkenning**. [geverifieerd] | Directe overlap met mijn `dhcp_arp`-provider. Overneembaar: (1) "nieuw device op het netwerk"-notificatie, (2) lokale OUI-lookup (IEEE-bestand meebakken in de image, geen cloud) zodat discoveries meteen "TP-Link / Philips Hue / Sonos" heten in plaats van een kaal MAC. |
| **Cytoscape.js + dagre/ELK** | Volwassen graph-rendering: pan/zoom, hit-testing, compound nodes (= mijn groepen!), dagre/ELK-layouts voor nette hiërarchie. Vuistregel 2026: "Cytoscape voor analyse, vis-network voor snelle interactieve diagrammen, Sigma voor grote WebGL-graphs". [geverifieerd] | Mijn hand-geschreven SVG (layout + drag + multiselect in ±120 regels) is knap maar zit tegen zijn plafond: geen pan/zoom, geen edge-routing, layout-code groeit. Cytoscape.js is één script zonder framework-eis en ondersteunt compound nodes die 1-op-1 op mijn parent/group-model passen. |

---

## 3. Aanbevelingen

### 3.1 Functionaliteit

| Wat | Waarom (tool/pijnpunt) | Waar in de code | Effort | Risico |
|---|---|---|---|---|
| **F1. Notificaties bij statuswissel + nieuw device** (ntfy zelf-gehost, generieke webhook, optioneel SMTP; met N-misses-debounce en stiltevenster 's nachts) | Grootste gap t.o.v. álle monitoringtools (Kuma 94 kanalen, NetAlertX 80+); de maintenance-loop ziet alles al maar zwijgt | nieuwe `notifications.py`; hook in `maintenance_loop` (main.py) waar status naar `unknown`/`down` kantelt en in `_store_record` bij nieuwe discovered entity | M | Laag; ntfy/webhook = 1 POST, geen nieuwe deps (httpx aanwezig) |
| **F2. Kabelmodel v2: kabel = record tussen twee uiteinden (poort↔poort of poort↔entity), patchpanel met front/rear-paren; kabeltrace in de poortdrawer** | NetBox front/rear-model is de standaard; nu is switch→patchpanel→wandcontactdoos→device niet vast te leggen — het kern-domein van een "Patch Manager" | `db.py` (nieuwe `cables`-tabel, `port_assignments` wordt kabel-uiteinde), `main.py` poort-endpoints, `app.js` drawer + trace-weergave | L | Middel: datamigratie van bestaande assignments nodig → vereist B1 (migraties) eerst |
| **F3. Uptime-/presence-historie per entity**: per dag geaggregeerd (up-percentage, laatste wissel), sparkline in UI; harde retentiegrens (bv. 90 dagen) | "Hoe vaak was de NAS offline?" is nu onbeantwoordbaar; Kuma's 5 GB-SQLite-drama's laten zien waarom alleen-geaggregeerd de juiste vorm is | nieuwe tabel `status_history` gevuld in `maintenance_loop`; sparkline naast speedtest-chart in `app.js` | M | Laag mits alléén dag-aggregaten (geen raw samples) |
| **F4. OUI-vendorherkenning voor discoveries** (lokaal IEEE OUI-bestand in de image) | NetAlertX: discovery met "TP-Link" als naam is direct herkenbaar, een kaal MAC niet; volledig offline | `providers.py` `_store_record` (vendor-veld), OUI-data in Dockerfile | S | Laag; OUI-bestand ~2 MB, build-time gedownload |
| **F5. Read-only "updates beschikbaar" via Proxmox-provider** | PatchMon-onderzoek (2.3): volle module = scope creep; dit ene observatieveld geeft 80 % van de waarde binnen de kernregel | `providers.py` `_sync_proxmox` (+1 API-call per node), badge in `app.js` | S | Laag; eerst API-endpoint verifiëren [inschatting] |

### 3.2 Backend & architectuur

| Wat | Waarom | Waar in de code | Effort | Risico |
|---|---|---|---|---|
| **B1. Lichtgewicht schema-migraties**: `PRAGMA user_version` + genummerde migratiefuncties, uitgevoerd in `initialize()`; test die van een oude fixture-DB naar actueel migreert | Elke serieuze aanbeveling hierboven wijzigt het schema; nu is dat `executescript` + ad-hoc `ALTER` — bij F2 gaat dat fout. NetBox/Nautobot danken hun evolutie aan migraties | `db.py` (`initialize`, nieuw `MIGRATIONS`-register) | S–M | Laag; geen Alembic nodig, 40 regels eigen code volstaan |
| **B2. Providertests met gemockte HTTP** (`httpx.MockTransport` — geen extra dependency): per adapter minimaal happy path + auth-fout + mismatch-payload | 7 adapters, 0 % HTTP-dekking; elke refactor van `providers.py` is nu blind varen | nieuw `tests/test_providers.py`; `ProviderManager` een client-factory laten accepteren | M | Laag |
| **B3. `main.py` opsplitsen in APIRouters** (auth, inventory, topology, providers, backups, dns, speedtest) + Pydantic-responsemodellen i.p.v. `dict[str, Any]` | 1.200 regels god-module; responsecontract bestaat nu niet (OpenAPI-docs tonen `object`) | `patch_manager/routers/*.py`, `main.py` wordt compositie | M | Laag; puur verplaatsen, tests bewaken gedrag |
| **B4. Licht poll-endpoint**: `/api/summary` (counts, statuses, speedtest-laatste, open conflicten) voor de 30 s-poll; volledige bootstrap alleen bij load en na mutaties | Bootstrap sleept audit (200 rijen), alle provider_records en volledige topologie elke 30 s mee — Kuma-achtige zelfvergiftiging bij groei | `main.py`/router + `app.js` `setInterval` | S–M | Laag |

### 3.3 Security & operations

| Wat | Waarom | Waar in de code | Effort | Risico |
|---|---|---|---|---|
| **S1. Passphrase-encryptie voor `.pmbackup`** (scrypt-afgeleide sleutel over de bundel; passphrase alleen bij download/import gevraagd) | Bundel bevat nu dé encryptiesleutel naast de data — "behandel als wachtwoord" documenteren is zwakker dan het afdwingen; `cryptography` zit er al in | `db.py` `create_backup_bundle`/`validate_backup`/`restore_backup`, kleine UI-prompt | M | Middel: passphrase kwijt = back-up waardeloos → duidelijk waarschuwen, nachtelijke bundel evt. zonder |
| **S2. App-observability**: `/health` uitbreiden met DB-check, laatste geslaagde maintenance-tick en per-provider laatste succes; optioneel `/metrics` in Prometheus-tekstformaat (handgeschreven, geen deps) | De app bewaakt alles behalve zichzelf; een hangende maintenance-loop is nu onzichtbaar (healthcheck test alleen HTTP) | `main.py` health-route + heartbeat-timestamp uit `maintenance_loop` | S | Laag |
| **S3. Auditlog-retentie** (bv. 5.000 rijen, opruimen in `_maintenance_housekeeping`) | Tabel groeit onbegrensd; UI toont toch maar 200 | `db.py`/`main.py` housekeeping | S | Laag |
| **S4. Restore-drill in CI/tests**: test die een `.pmbackup` maakt, DB muteert, terugzet en secrets-decryptie verifieert ná sleutelwissel | Back-ups zijn er; bewijs dat restore onder alle paden werkt (legacy, bundel, verkeerde sleutel) is de helft van de waarde | uitbreiden `tests/test_app.py` (roundtrip bestaat al — uitbreiden met sleutelwissel-scenario) | S | Laag |

### 3.4 UI/UX & visueel ontwerp

| Wat | Waarom | Waar in de code | Effort | Risico |
|---|---|---|---|---|
| **U1. Topologie op Cytoscape.js + dagre-layout** (compound nodes = bestaande groepen; pan/zoom/fit gratis; eigen SVG-code vervalt) | Eigen SVG zit aan z'n plafond (geen pan/zoom, layout-code groeit); Cytoscape is framework-loos en past op het bestaande nodes/relations-JSON | `static/app.js` (renderTopology/autoTopologyLayout eruit), één vendored JS-file | M–L | Middel: interactiegedrag (drag→PATCH positions, undo) 1-op-1 nabouwen; datamodel blijft ongemoeid |
| **U2. Globale zoek/command-palette (Ctrl+K)**: entity/poort/DNS/MAC → spring naar poort of node | netdisco's kernwaarde ("waar hangt dit MAC?") in UX-vorm; alles is al client-side aanwezig in `state.data` | `app.js` + klein dialog in `index.html` | S–M | Laag |
| **U3. Printbaar patchschema** (poortenlijst per apparaat met kabellabels/kleuren, `@media print`-stylesheet) | Niemand in het veld (NetBox incl.) levert een nette print voor in de meterkast [inschatting]; mijn data is er compleet voor | `styles.css` print-media + printknop in patch-view | S | Laag |
| **U4. Tablet/mobiel-pass + toegankelijkheid**: poortgrid naar cards onder 768 px, hit-targets ≥44 px, focus-styles, contrast-audit op de statuskleuren | Beheer gebeurt in de praktijk naast de patchkast met een telefoon; dashboards-onderzoek bevestigt dat mobiel bepalend is voor dagelijks gebruik | `styles.css`, kleine markup-aanpassingen | M | Laag |

**Frontend-framework?** Advies: **niet migreren.** ±560 regels vanilla JS is nog beheersbaar en de constraint is beperkte tijd. Het reële risico zit alleen in de topologie-editor; U1 verplaatst precies dat deel naar een gespecialiseerde library. Als de app ooit >1.500 regels frontend nadert: eilandsgewijs Preact/Lit op de topology-view eerst (migratiepad zonder big bang), kosten ~een weekend, baten pas aantoonbaar bij die omvang.

---

## 4. Geprioriteerde lijst (MoSCoW)

**Must**
1. **B1 Migraties** — de enabler: F2, F3, F4 en S3 wijzigen allemaal het schema; zonder migratiepad is elke stap hierna een handmatige, riskante ingreep op een database met echte administratie erin.
2. **F1 Notificaties** — de grootste functionele achterstand op elke onderzochte monitoringtool, met de kleinste bouwkosten: de detectie draait al elke 30 s, alleen het vertellen ontbreekt.
3. **B2 Providertests** — zeven integraties zijn het fundament van de topologie en tegelijk het minst geteste deel; vóór F-werk aan providers begint, moet dit vangnet er zijn.
4. **B3 Opsplitsen main.py** — voorwaarde om Must 1–3 en alles daarna schoon te kunnen bouwen; puur mechanisch en door de bestaande tests gedekt.

**Should**: F2 kabelmodel v2 (kern-domein, maar pas ná B1+B2), F3 statushistorie, U1 Cytoscape, S1 passphrase-bundels, B4 summary-endpoint, S2 observability, F4 OUI-lookup.

**Could**: U2 command-palette, U3 print-schema, F5 Proxmox-updates, S3 auditretentie, S4 restore-drill, U4 mobiel-pass, VLAN-veld per poort, CSV-bulkimport, subnet-overzicht (IPAM-lite), TOTP.

**Won't (bewust)**: eigen monitoring-agents (Beszel-model — breekt "geen installatie op hosts"; polling van bestaande API's volstaat), volwaardige patch-managementmodule (zie 2.3 — PatchMon ernaast draaien is beter), SNMP/LLDP-switchportmapping (hardware ondersteunt het niet), Postgres of ander DB-systeem (SQLite met WAL en begrensde tabellen is hier ruim voldoende — Kuma's problemen kwamen door onbegrensde historie, niet door SQLite), frontend-framework-migratie nu, en élke vorm van provider-terugschrijven (geen enkel voorstel hierboven vereist het; de kernregel blijft intact).

---

## 5. Bronnen

Alle bronnen geraadpleegd op 1 augustus 2026 via websearch; NetBox-documentatie daarnaast direct gelezen.

- NetBox front ports (datamodel, geverifieerd): https://raw.githubusercontent.com/netbox-community/netbox/main/docs/models/dcim/frontport.md · Cable profiles: https://netboxlabs.com/blog/understanding-cable-profiles-in-netbox-4-5/
- Uptime Kuma SQLite-problemen (geverifieerd): https://github.com/louislam/uptime-kuma/issues/3286 · https://github.com/louislam/uptime-kuma/issues/7338 · https://medium.com/@sn.osmanalp/how-i-reduced-uptime-kuma-database-from-5gb-to-2-7mb-and-made-it-1-850x-faster-b3de8ab8879a
- Gatus vs Kuma (geverifieerd): https://talos.tools/compare/uptime-kuma-vs-gatus · https://selfhostpicks.com/uptime-kuma-vs-gatus-vs-healthchecks/
- Beszel (geverifieerd): https://akashrajpurohit.com/blog/beszel-selfhosted-server-monitoring-solution/ · https://instapods.com/apps/beszel/vs/netdata/
- NetAlertX (geverifieerd): https://github.com/netalertx/NetAlertX · https://docs.netalertx.com/COMMUNITY_GUIDES/
- PatchMon (geverifieerd): https://github.com/PatchMon/PatchMon · https://patchmon.net/open-source · https://hai.wxs.ro/tools/patchmon/
- netdisco (geverifieerd): https://github.com/netdisco/netdisco
- Oxidized (geverifieerd): https://github.com/ytti/oxidized
- Topologie-libraries (geverifieerd): https://www.pkgpulse.com/guides/cytoscape-vs-vis-network-vs-sigma-graph-visualization-2026 · https://npm-compare.com/cytoscape,d3-graphviz,vis-network
- Dashboards (geverifieerd): https://homelabcompass.com/alternatives/self-hosted-dashboard · https://www.pistack.xyz/posts/self-hosted-homepage-dashboards-homepage-dashy-homarr-guide/
- phpIPAM/NetBox-alternatieven (op hoofdlijnen geverifieerd): https://industrialmonitordirect.com/blogs/knowledgebase/netbox-vs-nautobot-vs-phpipam-network-ipam-database-comparison

**Als inschatting gemarkeerd (niet in deze sessie geverifieerd):** Zabbix/Tactical RMM/Wazuh/Fleet-zwaarte in detail, Proxmox `apt/update`-API-endpoint, SNMP-afwezigheid op SG108E/Deco (consistent met de bestaande README), RackTables/openDCIM-onderhoudsstatus, "niemand levert een goede print-view".

---

## 6. Wat ik zelf als eerste zou doen

1. **B1 — migraties (een middag werk).** Niet omdat het spannend is, maar omdat het alles daarna goedkoop en veilig maakt; elk uur hierin verdient zich terug bij F2/F3/F4. Zonder dit blijft elke schemawijziging een risico voor een database met jouw echte patchadministratie.
2. **F1 — notificaties via ntfy/webhook.** De hoogste waarde per uur bouwtijd in het hele onderzoek: de detectie (statuskanteling, nieuwe discovery) bestaat al in `maintenance_loop` en `_store_record`; er hoeft alleen een POST achteraan. Daarmee verschuift de app van "kijken als ik eraan denk" naar "hij waarschuwt mij" — het verschil tussen een administratie en een bewakingssysteem.
3. **B2 — providertests met `httpx.MockTransport`.** Vóór het grote werk (kabelmodel, Cytoscape) begint, moet het minst geteste maar meest kritieke deel een vangnet hebben; anders wordt elke volgende stap trager in plaats van sneller.
