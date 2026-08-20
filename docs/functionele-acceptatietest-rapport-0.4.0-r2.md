# Network Patch Manager — FAT 0.4.0-R2 na merge

> Uitgevoerd: 20 augustus 2026
> Testbasis: `main` op mergecommit `2328a433ba62dfbbd9ac964472843b36da9c188f`
> Testcontract: [68 user stories en 186 testscripts](./user-stories-en-functionele-acceptatietests.md)
> Werkwijze: nulmeting → verbeteren → volledige hertest

## 1. Besluit

De lokale code-, logica-, UI- en UX-acceptatie is **GO**. Er zijn na herstel
geen bekende functionele fouten over en de volledige geautomatiseerde
releasepoort is groen.

De volledige productieacceptatie blijft **voorwaardelijk**: 9 scripts zijn
Blocked en 19 scripts zijn Not run omdat deze host geen Docker-runtime,
fysieke touchomgeving, echte externe providers of veilige live LAN-fixture
heeft. Deze 28 scripts zijn niet stilzwijgend als Pass geboekt.

| Contractresultaat | Aantal |
|---|---:|
| Pass | 158 |
| Fail | 0 |
| Blocked | 9 |
| Not run | 19 |
| Totaal | 186 |

## 2. Uitgevoerde cyclus

### 2.1 Nulmeting op de gemergede versie

- 138 pytest-scenario's Pass.
- Daarvan 12 end-to-endscenario's in echte Chromium.
- JavaScript-syntax, Python-compile, dependencies en YAML Pass.
- Alle hoofdschermen interactief beoordeeld op desktop en 390 × 844 px.
- Geen JavaScriptfouten in de browserconsole.
- Alle bestaande 26 contract-Fails uit de eerste FAT blijven door de
  hersteltests afgedekt en zijn in R2 Pass.

### 2.2 Verbeteringen na de nulmeting

| ID | P | Gebied | Bevinding | Herstel |
|---|---:|---|---|---|
| R2-UI-001 | P1 | Toegankelijkheid | Gesloten poort- en devicelades stonden buiten beeld, maar bleven focusbaar en zichtbaar voor hulptechnologie. | Beide lades zijn gesloten `inert`, worden pas bij openen interactief en geven focus na sluiten terug aan de startknop. |
| R2-UX-002 | P1 | Databronnen | Normale providerconfiguratie vereiste bewerken van ruwe JSON en bood buiten de wizard geen testverbinding. | Begeleide velden per providertype, begrijpelijke omschrijvingen, een knop **Verbinding testen** en JSON alleen onder een geavanceerde uitklapsectie. |
| R2-UI-003 | P1 | Mobiel Admin | Drie databronacties werden op 390 px tot zeer kleine, slecht leesbare knoppen samengedrukt. | Acties staan mobiel onder elkaar op 362 × 36 px met 10px tekst en normale labels. |
| R2-UI-004 | P2 | Leesbaarheid | `--faint` had circa 2,69:1 contrast in donker en 3,03:1 in licht; veel secundaire tekst was 8–9px. | Contrast is verhoogd naar circa 4,64:1 donker en 4,67:1 licht; belangrijke metadata en acties zijn vergroot. |
| R2-UX-005 | P2 | Sessies | De profielfoto logde direct uit zonder dat de actie herkenbaar was, vooral mobiel. | Zichtbaar logout-icoon, tooltip en dynamische naam `Uitloggen als <gebruiker>`. |
| R2-LOG-006 | P2 | Dashboardlogica | Een kabeltelling werd als `x/y poorten gepatcht` gepresenteerd, terwijl teller en noemer verschillende grootheden waren. | De chip toont nu eenduidig `x kabels vastgelegd`; het afzonderlijke poorttotaal blijft bij de inventaris staan. |
| R2-OPS-007 | P2 | Lokale installatie | De bestaande lokale `.venv` gebruikte Python 3.9 en faalde op `datetime.UTC`; de README noemde geen minimumversie. | README vermeldt expliciet Python 3.11+ en adviseert de versie vóór het maken van de venv te controleren. |

### 2.3 Volledige hertest na herstel

- **143/143 pytest-scenario's Pass**.
- **17/17 Chromium end-to-endscenario's Pass**.
- De vijf nieuwe browserscenario's bewaken:
  - `inert` en focusherstel van lades;
  - actuele navigatiestatus en herkenbaar uitloggen;
  - begeleide providerconfiguratie zonder zichtbare JSON;
  - providerwaarden testen, opslaan en opnieuw openen;
  - leesbare mobiele Admin-acties.
- JavaScript-syntaxcontrole Pass.
- `python -m compileall` Pass.
- `pip check` Pass.
- Compose- en Actions-YAML parsing Pass.
- `git diff --check` Pass.
- Browserconsole: 0 errors en 0 warnings tijdens de visuele hertest.
- Alle hoofdschermen blijven bij 390 px binnen de documentbreedte; alleen de
  topologiekaart scrolt doelbewust in haar eigen vlak.

De twee pytest-waarschuwingen zijn de bekende upstream-deprecationwarnings
van Uvicorn/`websockets`; zij beïnvloeden de functionaliteit niet.

## 3. Beoordeling per hoofddomein

| Domein | Pass | Blocked | Not run | UI, logica en gebruikersgemak |
|---|---:|---:|---:|---|
| Toegang en sessies | 10 | 0 | 0 | Pass; uitloggen is nu herkenbaar en status is toegankelijk benoemd. |
| Algemene bediening | 8 | 0 | 1 | Pass binnen uitgevoerde scope; lange zichtbaarheidspoll niet tijdgestuurd herhaald. |
| Setup-wizard | 10 | 1 | 1 | Pass; live LAN-scan en volledig gevuld bulk-eindscherm vragen een aparte fixture. |
| Patch en inventaris | 29 | 2 | 0 | Pass; muis-, toetsenbord- en heen/terugflows groen. Fysieke touch en printpreview blijven omgevingsafhankelijk. |
| Apps | 5 | 0 | 0 | Pass op logica, veilige links, CRUD, mobiele layout en begrijpelijkheid. |
| Discovery | 8 | 0 | 0 | Pass, inclusief merge-integriteit en herstelbare statusovergangen. |
| Providers | 18 | 0 | 6 | Pass met lokale read-only fixtures; aanvullende echte-providerfoutmatrices niet uitgevoerd. |
| Topologie | 18 | 0 | 5 | Pass voor beheer, verwijderen, toetsenbord, verbergen en undo; enkele geavanceerde drag/bulk/tooltipproeven blijven Not run. |
| DNS en proxy | 5 | 0 | 0 | Pass; handmatige CRUD en read-only herkomst zijn duidelijk gescheiden. |
| Speedtest | 3 | 2 | 2 | Parser, instellingen en foutstatus Pass; echte CLI- en concurrencyproeven vragen Docker/runtime. |
| Back-up en herstel | 7 | 0 | 0 | Pass, inclusief foutinjectie en atomaire portable restore. |
| Configuratie-uitwisseling | 5 | 0 | 0 | Pass, inclusief cross-table foreign keys en rollback. |
| Systeem/deployment | 13 | 4 | 3 | Statische hardening en migraties Pass; echte Docker-/upgrade-/volumeproeven ontbreken. |
| Logica en integriteit | 8 | 0 | 0 | Pass; alle eerdere dataverlies- en atomaire foutpaden blijven groen. |
| Applicatiebrede UX | 11 | 0 | 1 | Pass na R2-herstel; fysieke touch/long-press blijft buiten deze host. |

## 4. Applicatiebrede UX-conclusie

De primaire structuur **Patch → Apps → Topologie → Admin** is begrijpelijk en
blijft op desktop en mobiel consistent. Primaire taken zijn zichtbaar, lege
toestanden geven een volgende stap, destructieve acties gebruiken dezelfde
bevestigingsdialoog en topologiebeheer is niet meer afhankelijk van een
pixelnauwkeurige muisklik.

De grootste resterende complexiteit zit functioneel terecht in Admin. De R2-
wijzigingen halen de meest technische drempel weg: normale databroninstellingen
kunnen nu zonder JSON-kennis worden ingevuld en vóór opslaan worden getest.
Geavanceerde JSON blijft beschikbaar voor uitzonderingen zonder de hoofdtaak te
domineren.

## 5. Resterende omgevingsproeven

Voor definitieve productieacceptatie is nog een aparte omgeving nodig voor:

1. Docker Compose-build, healthcheck, volumepermissies, herstart en upgrade.
2. LibreSpeed CLI, gelijktijdige run en echte netwerkinterface-/serverfouten.
3. Fysieke touch en long-press op een telefoon of tablet.
4. Veilige ARP/ping-scan tegen een expliciet testsubnet.
5. Echte testinstanties van de externe providers en hun uitgebreide foutmatrix.
6. Printpreview/A4 en enkele geavanceerde topologie-drag-/bulkgrenzen.

Deze punten zijn geen bekende codefouten. Ze blijven expliciet open zodat een
groene geautomatiseerde suite niet als bewijs voor niet-uitgevoerde hardware-
of integratieproeven wordt gebruikt.
