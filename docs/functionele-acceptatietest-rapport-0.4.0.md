# Network Patch Manager — functionele acceptatietest en code-reviewrapport 0.4.0

> Oorspronkelijke uitkomst: **AFGEKEURD / niet releaseklaar**
> Referentiecommit: `2a1afb3cae0d37bdd2abcc804299640d75d14bf1`
> Uitgevoerd: 20 augustus 2026
> Goedgekeurd testcontract: [user-stories-en-functionele-acceptatietests.md](./user-stories-en-functionele-acceptatietests.md)

> Hertest na herstel: **alle 15 vastgelegde codebevindingen opgelost; geautomatiseerde releasepoort groen**
> Remediatiebranch: `codex/fat-0.4.0-complete-fixes`
> Hertest uitgevoerd: 20 augustus 2026

## 0. Hertest na herstel

De oorspronkelijke matrix hieronder blijft ongewijzigd als nulmeting. Na herstel zijn alle vijftien concrete P0/P1/P2-codebevindingen opnieuw getest. De volledige lokale suite eindigt op **138 passed**, waaronder **12 echte Chromium-acceptatiescenario's**. JavaScript-syntaxcontrole en Python-compilecontrole slagen eveneens. In de ingebouwde applicatiebrowser is bovendien handmatig bevestigd dat een topologierelatie vindbaar, benoemd, verwijderbaar en na bevestiging werkelijk verdwenen is.

| Hersteld gebied | Hertestbewijs |
|---|---|
| Migratie zonder verlies | Migratie- en regressietests groen. |
| Atomaire kabelvervanging/verplaatsing | Foutinjectie rolt terug; UI gebruikt één server-side mutatie. |
| Atomaire portable restore | Geïnjecteerde fout bij sleutelwissel herstelt zowel database als sleutel. |
| Config-import met cross-table-FK's | Parents, uplinks en monitors worden in een tweede fase gekoppeld. |
| Discovery-merge zonder self-cycle | Descendant-target wordt veilig losgemaakt; regressietest groen. |
| Topologiebeheer | Brede klikzone, zichtbare beheerdialoog, Enter/Spatie, bevestiging en werkende delete. |
| Patchpanel bewerken | Twee opeenvolgende bewerkingen behouden exact twee poortparen. |
| CSV-limiet en MAC-validatie | Oversize wordt geheel geweigerd; ingebedde/ongeldige/dubbele MAC voorspelbaar afgehandeld. |
| Whitespace en unieke velden | Invoer wordt vóór validatie getrimd; lege waarden en dubbele MAC leveren 4xx, geen 500. |
| Mobiele hoofdschermen | Patch, Apps, Topologie en Admin hebben bij 390 px geen document-overflow. |
| Toegankelijkheid en CRUD-labels | SVG-acties en iconbuttons hebben namen/focus; DNS-delete en opslaanknoppen zijn expliciet. |
| CI-testgate | Nieuwe workflow draait alle pytest- en Chromiumtests vóór acceptatie; image-workflow gebruikt actuele action-major. |
| Speedtestinstellingen | UI biedt het volledige ondersteunde bereik van 15 minuten tot wekelijks. |
| Destructieve UX | Eén consistente, toegankelijke bevestigingsdialoog; typed confirmation voor databronnen. |

Nog niet omgezet in een lokale pass zijn uitsluitend de al als **Blocked/Not run** geregistreerde omgevingsproeven, zoals echte Docker-runtime, werkelijk externe providers, fysieke touch/long-press en een live ARP/ping-netwerkfixture. Dat zijn geen resterende bekende codebevindingen; de nieuwe GitHub-testpoort voorkomt wel dat de geautomatiseerde scope voortaan ongemerkt regresseert.

## 1. Managementsamenvatting

De bestaande regressieset is groen, maar de functionele acceptatie is **niet geslaagd**. Van de 186 vooraf afgesproken scripts zijn **132 Pass**, **26 Fail**, **9 Blocked** en **19 Not run**. Er zijn **24 falende P0/P1-scripts**; daarmee wordt niet voldaan aan de FAT-exitcriteria.

Het expliciete voorbeeld van de eigenaar is bevestigd: verwijderen in de topologie is technisch als API aanwezig, maar functioneel niet acceptabel. Een relatie kan alleen via een dun, niet-focusbaar SVG-pad worden aangeklikt. Gewone nodes hebben in de editor geen zichtbare actie voor verbergen of bronobject verwijderen.

Daarnaast zijn dataverlies-/integriteitsproblemen gevonden bij migratie, kabelvervanging, portable restore, config-import en discovery-merge. Een groene `pytest`-run is daardoor geen vrijgavebewijs.

## 2. Testbasis en bewijs

- Geïsoleerde tijdelijke SQLite-databases en back-upmappen; geen gebruikers- of productiegegevens gewijzigd.
- Bestaande suite: **112 passed in 8.05s**.
- `pip check`: geen gebroken dependencies.
- `python -m compileall -q patch_manager tests`: geslaagd.
- OpenAPI: **69 operaties** (19 GET, 24 POST, 15 PATCH, 9 DELETE, 2 PUT); health geeft `0.4.0`.
- Extra auth/import/config/migratie-harness: **15 Pass, 5 Fail**.
- Zeven providerfixtures: **10/10 controles Pass**; testcalls waren read-only en resync was idempotent.
- Back-up/security-harness: **7/7 controles Pass** voor geldige bundel, mode 0600, zip-slip, verkeerde sleutel, corrupt bestand, restore en retentie.
- Browseracceptatie: setup/login, wizard, navigatie, zoeken, thema, titel, device, kabel, patchpanel, apps, topologie, DNS, providers, back-up en mobiel uitgevoerd.
- Browserconsole: geen runtime-errors tijdens de doorlopen flows.
- Docker-runtimeproeven konden niet worden uitgevoerd omdat Docker op deze host ontbreekt; Dockerfile en Compose zijn wel statisch beoordeeld.

## 3. Bevindingen uit de code review

### P0 — direct blokkeren

1. **Migratie 1→2 wist providerafhankelijke data.** Het hernoemen en droppen van `providers_old` laat SQLite-cascades `provider_secrets` en `provider_records` verwijderen. Reproducer: tellingen `[1,1,1]` worden `[1,0,0]`. Zie `patch_manager/db.py:396`.
2. **Kabelvervanging is niet atomair.** De bestaande kabel wordt in een aparte transactie verwijderd vóór de nieuwe insert. Een geïnjecteerde insertfout geeft 500 en laat de poort leeg. Zie `patch_manager/main.py:1326`.
3. **Portable restore kan half slagen.** De database wordt eerst overschreven en de sleutel pas daarna atomair vervangen. Bij een sleutelvervangingsfout rapporteert restore een fout, maar de actieve database staat al op de back-upstaat. Zie `patch_manager/db.py:826`.
4. **Geldige config-import faalt op foreign keys.** `physical_devices` wordt vóór `entities` geïmporteerd, terwijl `monitor_entity_id` naar `entities` verwijst. Resultaat: 422 `FOREIGN KEY constraint failed`. Zie `patch_manager/main.py:1614`.
5. **Discovery-merge kan een self-cycle creëren.** Als het doel een kind van de bron is, zet de bulkupdate `target.parent_id` op het target zelf. Reproducer eindigt met `target.parent_id == target.id`. Zie `patch_manager/main.py:1050`.

### P1 — kernfunctie of acceptatieflow stuk

6. **Topologie-verwijderen/verbergen ontbreekt in de node-editor.** Alleen groepen krijgen een verwijderknop; gewone nodes missen alle afgesproken acties. Handmatige relaties hebben geen zichtbare of toetsenbordbedienbare deleteactie. Zie `static/index.html:231` en `static/app.js:943`.
7. **Patchpanel verdubbelt bij bewerken.** De UI gebruikt het aantal fysieke front+rear-poortrijen als aantal paren. Een 2-poorts patchpanel wordt na ongewijzigd opslaan 4 paren/8 poorten. Zie `static/app.js:848`.
8. **CSV-limiet veroorzaakt gedeeltelijke import.** De server leest stil precies 1.000.000 bytes in plaats van oversize te detecteren; een bestand boven de limiet geeft 200 en maakt de eerste rij aan. Zie `patch_manager/main.py:766`.
9. **Whitespace passeert minimumlengtes.** Setup met gebruikersnaam `"  "` maakt een beheerder met lege naam; hetzelfde geldt voor titel, device, netwerkapparaat en app, terwijl DNS een blanco naam bewaart. Zie de modellen vanaf `patch_manager/main.py:74`.
10. **Dubbele MAC geeft 500.** De unieke index werkt, maar create/update vertaalt `sqlite3.IntegrityError` niet naar een bruikbare 409/422. De eerste rij blijft intact, maar de UX en API-contracten falen. Zie `patch_manager/main.py:944`.
11. **Mobiele layout verliest inhoud horizontaal.** Bij 390 px was de documentbreedte 454 px in Patch/Topologie en 566 px in Admin; providerkolommen en acties vielen buiten beeld.
12. **Topologie is niet toetsenbordtoegankelijk.** SVG-nodes en -relaties hebben geen `role` of `tabindex`; relatie verwijderen vereist een muis/pixelklik. Ook enkele CRUD-iconbuttons heten alleen `✎` of `×`.

### P2 — belangrijke kwaliteitsbeperking

13. **MAC-normalisatie accepteert tekst rondom een MAC.** `re.search` accepteert bijvoorbeeld `prefix-AA:BB:CC:DD:EE:12-suffix` en importeert de rij. Gebruik een volledige match. Zie `patch_manager/providers.py:44`.
14. **Publicatieworkflow heeft geen testgate.** Na checkout/tagcontrole volgt direct build+push; de 112 tests hoeven niet groen te zijn om een image te publiceren. Zie `.github/workflows/publish-image.yml:45`.
15. **Terminologie en destructieve patronen zijn inconsistent.** Een bewerkdialoog gebruikt de knoptekst `Toevoegen`; delete varieert tussen benoemde knop, kale ×, browserprompt en onzichtbare lijnklik.

## 4. Logica en gebruikersgemak per domein

| Domein | Logica | UX | Kernconclusie |
|---|---|---|---|
| Toegang/sessies | Fail | Fail | Sessies/CSRF/rate limit werken, maar whitespace maakt een lege beheerder. |
| Wizard | Gedeeltelijk | Gedeeltelijk | Structuur en provider-testflow zijn bruikbaar; echte scan en gevulde eindstap niet volledig uitgevoerd. |
| Patch/inventaris | Fail | Fail | Basis-CRUD en kabels werken; patchpanel, duplicate-MAC, oversize-CSV en atomaire replace falen. |
| Apps | Pass | Pass | CRUD, veilige URL's, groepering/status en nieuw-tabbeleid werken. |
| Discovery | Fail | Pass | Promote/state/mapping/conflict werken; merge kan een self-parent maken. |
| Providers | Pass | Pass | Alle zeven happy paths, read-only tests, encryptie en idempotentie slagen met fixtures. |
| Topologie | Gedeeltelijk | Fail | Catalogus/groepen/zoom/undo werken; verwijdersemantiek, vindbaarheid en toetsenbord falen. |
| DNS/proxy | Pass | Gedeeltelijk | CRUD/read-only/import werken; delete-iconen zijn niet consequent benoemd. |
| Speedtest | Gedeeltelijk | Gedeeltelijk | Parser/stale/missing-binary werken; echte CLI-, busy- en foutmatrix geblokkeerd/onvolledig. |
| Back-up/herstel | Fail | Pass | Bundelvalidatie is sterk; systeemfout tussen DB- en sleutelrestore geeft gedeeltelijke toestand. |
| Config-uitwisseling | Fail | Pass | Export/rollback/upsert werken; geldige FK-roundtrip faalt. |
| Deployment/operations | Fail | Fail | Statische hardening oogt goed; migratie en mobiel falen, Docker-runtime is geblokkeerd. |

## 5. Volledige testmatrix

| Test-ID | P | Laag | Status | Logica | UX | Bewijs / afwijking |
|---|---:|---|---|---|---|---|
| FAT-ACC-001 | P0 | UI/API | Pass | Pass | Pass | Pass: extended auth-harness + browser login. |
| FAT-ACC-002 | P1 | UI/API | Fail | Fail | Fail | Fail: invoer met uitsluitend spaties wordt 200 en levert een lege beheerder op. |
| FAT-ACC-003 | P0 | API | Pass | Pass | N.v.t. | Pass: extended auth-harness + browser login. |
| FAT-ACC-004 | P0 | UI/API | Pass | Pass | Pass | Pass: extended auth-harness + browser login. |
| FAT-ACC-005 | P0 | UI/API | Pass | Pass | Pass | Pass: extended auth-harness + browser login. |
| FAT-ACC-006 | P1 | API | Pass | Pass | N.v.t. | Pass: extended auth-harness + browser login. |
| FAT-ACC-007 | P0 | API | Pass | Pass | N.v.t. | Pass: extended auth-harness + browser login. |
| FAT-ACC-008 | P0 | UI/API | Pass | Pass | Pass | Pass: extended auth-harness + browser login. |
| FAT-ACC-009 | P1 | API | Pass | Pass | N.v.t. | Pass: extended auth-harness + browser login. |
| FAT-ACC-010 | P1 | API | Pass | Pass | N.v.t. | Pass: extended auth-harness + browser login. |
| FAT-GEN-001 | P1 | UI | Pass | Pass | Pass | Pass: browser + search/app regressietests. |
| FAT-GEN-002 | P1 | UI/API | Pass | Pass | Pass | Pass: browser + search/app regressietests. |
| FAT-GEN-003 | P2 | UI/API | Not run | N.v.t. | N.v.t. | Not run: echte 30-seconden visible/hidden pollcyclus niet tijdgestuurd uitgevoerd. |
| FAT-GEN-004 | P1 | UI/API | Pass | Pass | Pass | Pass: browser + search/app regressietests. |
| FAT-GEN-005 | P2 | UI | Pass | Pass | Pass | Pass: browser + search/app regressietests. |
| FAT-GEN-006 | P1 | UI/API | Pass | Pass | Pass | Pass: browser + search/app regressietests. |
| FAT-GEN-007 | P1 | UI/API | Pass | Pass | Pass | Pass: browser + search/app regressietests. |
| FAT-GEN-008 | P2 | UI/API | Pass | Pass | Pass | Pass: browser + search/app regressietests. |
| FAT-GEN-009 | P1 | UI | Pass | Pass | Pass | Pass: browser + search/app regressietests. |
| FAT-WIZ-001 | P1 | UI/API | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-002 | P1 | UI/API | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-003 | P1 | API | Pass | Pass | N.v.t. | Pass: wizard-regressietests + browser. |
| FAT-WIZ-004 | P0 | UI/INT | Blocked | N.v.t. | N.v.t. | Blocked: geen veilige, controleerbare ARP/ping-netwerkfixture voor een echte UI-scan in deze host. |
| FAT-WIZ-005 | P0 | UI/API | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-006 | P1 | UI/INT | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-007 | P1 | UI/INT | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-008 | P1 | UI/INT | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-009 | P1 | UI/API | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-010 | P1 | UI/API | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-011 | P2 | UI/API | Pass | Pass | Pass | Pass: wizard-regressietests + browser. |
| FAT-WIZ-012 | P2 | UI | Not run | N.v.t. | N.v.t. | Not run: wizard-stap 4 met gevulde bulkresultaten niet visueel uitgevoerd. |
| FAT-PAT-001 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-002 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-003 | P1 | API | Fail | Fail | N.v.t. | Fail: lege namen na trim worden opgeslagen; dubbele MAC geeft 500 in plaats van voorspelbare validatie/conflict. |
| FAT-PAT-004 | P0 | INT/API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-005 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-006 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-007 | P0 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-008 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-009 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-010 | P1 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-011 | P0 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-012 | P1 | OPS/API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-013 | P1 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-014 | P0 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-015 | P0 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-016 | P1 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-017 | P0 | UI/API | Fail | Fail | Fail | Fail: foutinjectie tussen delete en insert wist de bestaande kabel; vervanging is niet atomair. |
| FAT-PAT-018 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-019 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-020 | P1 | UI/touch | Blocked | N.v.t. | N.v.t. | Blocked: gekozen browseromgeving biedt viewportcontrole maar geen echte touch/long-press-emulatie. |
| FAT-PAT-021 | P0 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-022 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-023 | P1 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-024 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-025 | P1 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-026 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-027 | P0 | UI/API | Fail | Fail | Fail | Fail: patchpanel start met 2 front/rear-paren, maar ongewijzigd bewerken verdubbelt naar 4 paren. |
| FAT-PAT-028 | P1 | API | Pass | Pass | N.v.t. | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-029 | P1 | UI/API | Pass | Pass | Pass | Pass: cable/app regressietests + browser + fault injection. |
| FAT-PAT-030 | P1 | API | Fail | Fail | N.v.t. | Fail: >1 MB geeft 200 en importeert FAT-OVERSIZE gedeeltelijk; ingebedde MAC-string wordt geaccepteerd. |
| FAT-PAT-031 | P2 | UI | Blocked | N.v.t. | N.v.t. | Blocked: geen printpreview-/A4-emulatie in de beschikbare browserbesturing. |
| FAT-APP-001 | P1 | UI/API | Pass | Pass | Pass | Pass: app-regressietests + browser. |
| FAT-APP-002 | P0 | API/UI | Pass | Pass | Pass | Pass: app-regressietests + browser. |
| FAT-APP-003 | P1 | UI/API | Pass | Pass | Pass | Pass: app-regressietests + browser. |
| FAT-APP-004 | P1 | UI/API | Pass | Pass | Pass | Pass: app-regressietests + browser. |
| FAT-APP-005 | P1 | UI/API | Pass | Pass | Pass | Pass: app-regressietests + browser. |
| FAT-DIS-001 | P1 | UI/API | Pass | Pass | Pass | Pass: discovery/merge regressietests + extended harness. |
| FAT-DIS-002 | P1 | UI/API | Pass | Pass | Pass | Pass: discovery/merge regressietests + extended harness. |
| FAT-DIS-003 | P1 | UI/API | Pass | Pass | Pass | Pass: discovery/merge regressietests + extended harness. |
| FAT-DIS-004 | P0 | UI/API | Pass | Pass | Pass | Pass: discovery/merge regressietests + extended harness. |
| FAT-DIS-005 | P0 | API | Pass | Pass | N.v.t. | Pass: discovery/merge regressietests + extended harness. |
| FAT-DIS-006 | P0 | API | Fail | Fail | N.v.t. | Fail: merge resulteert in target.parent_id == target. |
| FAT-DIS-007 | P1 | UI/API | Pass | Pass | Pass | Pass: discovery/merge regressietests + extended harness. |
| FAT-DIS-008 | P0 | INT/API | Pass | Pass | N.v.t. | Pass: discovery/merge regressietests + extended harness. |
| FAT-PRO-001 | P1 | UI/API | Pass | Pass | Pass | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-002 | P1 | UI/API | Pass | Pass | Pass | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-003 | P1 | API | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-004 | P0 | UI/API/DB | Pass | Pass | Pass | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-005 | P1 | UI/API | Pass | Pass | Pass | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-006 | P1 | INT/API | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-007 | P1 | INT/API | Not run | N.v.t. | N.v.t. | Not run: twee werkelijk gelijktijdige provider-HTTP-syncs niet gestart. |
| FAT-PRO-008 | P1 | INT/API | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-009 | P0 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-010 | P0 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-011 | P1 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-012 | P1 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-013 | P1 | INT | Not run | N.v.t. | N.v.t. | Not run: volledige Kuma timeout/malformed-matrix niet uitgevoerd. |
| FAT-PRO-014 | P1 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-015 | P1 | INT | Not run | N.v.t. | N.v.t. | Not run: gemengde multi-endpoint Glances-partial-failurematrix niet uitgevoerd. |
| FAT-PRO-016 | P1 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-017 | P1 | INT | Not run | N.v.t. | N.v.t. | Not run: volledige AdGuard TLS/401/foutpayloadmatrix niet uitgevoerd. |
| FAT-PRO-018 | P1 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-019 | P1 | INT | Not run | N.v.t. | N.v.t. | Not run: volledige NPM loginfallback/401-matrix niet uitgevoerd. |
| FAT-PRO-020 | P1 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-021 | P1 | INT | Not run | N.v.t. | N.v.t. | Not run: volledige Portainer endpoint/containerfoutmatrix niet uitgevoerd. |
| FAT-PRO-022 | P1 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-023 | P0 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-PRO-024 | P1 | INT | Pass | Pass | N.v.t. | Pass: 7-provider HTTP-fixtures + providerregressie. |
| FAT-TOP-001 | P1 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-002 | P1 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-003 | P2 | UI | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-004 | P2 | UI | Not run | N.v.t. | N.v.t. | Not run: hovertooltipwaarden niet met een metricsfixture visueel vergeleken. |
| FAT-TOP-005 | P1 | UI | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-006 | P1 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-007 | P0 | API | Pass | Pass | N.v.t. | Pass: topologieregressie + browser. |
| FAT-TOP-008 | P1 | UI/API | Not run | N.v.t. | N.v.t. | Not run: echte SVG-dragdrempel <4/>4 px niet geautomatiseerd. |
| FAT-TOP-009 | P1 | UI/API | Not run | N.v.t. | N.v.t. | Not run: shift-multiselect plus groepsdrag niet geautomatiseerd. |
| FAT-TOP-010 | P1 | API | Not run | N.v.t. | N.v.t. | Not run: volledige 1/200/201 bulkgrensmatrix niet uitgevoerd. |
| FAT-TOP-011 | P1 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-012 | P1 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-013 | P0 | API | Pass | Pass | N.v.t. | Pass: topologieregressie + browser. |
| FAT-TOP-014 | P1 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-015 | P1 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-016 | P1 | UI/API | Fail | Pass | Fail | Fail: verwijderen vereist een pixelklik op een dun SVG-pad; geen zichtbare knop, rol of tabindex. |
| FAT-TOP-017 | P0 | API | Pass | Pass | N.v.t. | Pass: topologieregressie + browser. |
| FAT-TOP-018 | P1 | UI/API | Fail | Pass | Fail | Fail: node-editor bevat geen Verbergen uit topologie, Bronobject verwijderen of discovery-verwijzing. |
| FAT-TOP-019 | P1 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-020 | P0 | UI/API | Pass | Pass | Pass | Pass: topologieregressie + browser. |
| FAT-TOP-021 | P1 | API | Pass | Pass | N.v.t. | Pass: topologieregressie + browser. |
| FAT-TOP-022 | P0 | INT/API | Pass | Pass | N.v.t. | Pass: topologieregressie + browser. |
| FAT-TOP-023 | P1 | UI | Not run | N.v.t. | N.v.t. | Not run: alle lifecyclevarianten niet als gevulde licht/donker-visual vergeleken. |
| FAT-DNS-001 | P1 | UI/API | Pass | Pass | Pass | Pass: API/regressie + browser + AdGuardfixture. |
| FAT-DNS-002 | P1 | API | Pass | Pass | N.v.t. | Pass: API/regressie + browser + AdGuardfixture. |
| FAT-DNS-003 | P1 | UI/API | Pass | Pass | Pass | Pass: API/regressie + browser + AdGuardfixture. |
| FAT-DNS-004 | P0 | UI/API | Pass | Pass | Pass | Pass: API/regressie + browser + AdGuardfixture. |
| FAT-DNS-005 | P1 | UI | Pass | Pass | Pass | Pass: API/regressie + browser + AdGuardfixture. |
| FAT-SPD-001 | P1 | UI/API | Pass | Pass | Pass | Pass: speedtest unit/regressie + browser. |
| FAT-SPD-002 | P1 | API | Pass | Pass | N.v.t. | Pass: speedtest unit/regressie + browser. |
| FAT-SPD-003 | P1 | INT/UI | Blocked | N.v.t. | N.v.t. | Blocked: librespeed-cli ontbreekt buiten de niet-beschikbare Docker-image; parser is wel groen. |
| FAT-SPD-004 | P1 | INT/API | Blocked | N.v.t. | N.v.t. | Blocked: geen langlopende LibreSpeed-fixture/binary in deze omgeving. |
| FAT-SPD-005 | P1 | INT/API | Not run | N.v.t. | N.v.t. | Not run: exit/timeout/lege-array foutmatrix niet compleet; ontbrekend binary-pad is wel vastgesteld. |
| FAT-SPD-006 | P1 | OPS/API | Pass | Pass | N.v.t. | Pass: speedtest unit/regressie + browser. |
| FAT-SPD-007 | P2 | UI/API | Not run | N.v.t. | N.v.t. | Not run: 24/120 gevulde speedhistorie niet in de browser doorlopen. |
| FAT-BCK-001 | P0 | UI/API/DB | Pass | Pass | Pass | Pass: portable/legacy security-harness + regressie + browser. |
| FAT-BCK-002 | P1 | UI/API | Pass | Pass | Pass | Pass: portable/legacy security-harness + regressie + browser. |
| FAT-BCK-003 | P0 | UI/API | Pass | Pass | Pass | Pass: portable/legacy security-harness + regressie + browser. |
| FAT-BCK-004 | P0 | API | Pass | Pass | N.v.t. | Pass: portable/legacy security-harness + regressie + browser. |
| FAT-BCK-005 | P0 | UI/API | Fail | Fail | Fail | Fail: fout bij vervangen van de sleutel laat de database al hersteld achter; mislukte restore muteert actief systeem. |
| FAT-BCK-006 | P0 | API | Pass | Pass | N.v.t. | Pass: portable/legacy security-harness + regressie + browser. |
| FAT-BCK-007 | P1 | OPS | Pass | Pass | N.v.t. | Pass: portable/legacy security-harness + regressie + browser. |
| FAT-CFG-001 | P1 | UI/API | Pass | Pass | Pass | Pass: roundtripregressie + extended FK/rollback-harness. |
| FAT-CFG-002 | P0 | UI/API | Pass | Pass | Pass | Pass: roundtripregressie + extended FK/rollback-harness. |
| FAT-CFG-003 | P0 | API | Fail | Fail | N.v.t. | Fail: geldige monitor-FK in export geeft 422 FOREIGN KEY constraint failed. |
| FAT-CFG-004 | P0 | API | Pass | Pass | N.v.t. | Pass: roundtripregressie + extended FK/rollback-harness. |
| FAT-CFG-005 | P1 | API | Pass | Pass | N.v.t. | Pass: roundtripregressie + extended FK/rollback-harness. |
| FAT-SYS-001 | P1 | API | Pass | Pass | N.v.t. | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-002 | P0 | OPS | Pass | Pass | N.v.t. | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-003 | P0 | OPS/DB | Fail | Fail | N.v.t. | Fail: migratie 1→2 houdt provider zelf, maar wist provider_secrets en provider_records via cascade. |
| FAT-SYS-004 | P0 | OPS/DB | Pass | Pass | N.v.t. | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-005 | P0 | DB | Pass | Pass | N.v.t. | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-006 | P1 | DB | Pass | Pass | N.v.t. | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-007 | P1 | DB/API | Pass | Pass | N.v.t. | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-008 | P1 | DB | Pass | Pass | N.v.t. | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-009 | P1 | INT/OPS | Not run | N.v.t. | N.v.t. | Not run: onderhoudscycli niet met een bestuurbare klokfixture doorlopen. |
| FAT-SYS-010 | P1 | INT/OPS | Not run | N.v.t. | N.v.t. | Not run: automatische speedtest due/not-due-cycli niet met klokfixture doorlopen. |
| FAT-SYS-011 | P1 | OPS | Not run | N.v.t. | N.v.t. | Not run: dagovergang voor automatische backup niet met klokfixture doorlopen. |
| FAT-SYS-012 | P1 | UI/API/DB | Pass | Pass | Pass | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-013 | P0 | OPS | Blocked | N.v.t. | N.v.t. | Blocked: Docker is niet geïnstalleerd; Dockerfile/Compose zijn wel statisch gecontroleerd. |
| FAT-SYS-014 | P1 | OPS | Blocked | N.v.t. | N.v.t. | Blocked: Docker ontbreekt; PATCH_PORT-koppeling is statisch consistent. |
| FAT-SYS-015 | P0 | OPS | Blocked | N.v.t. | N.v.t. | Blocked: Docker/volume-recreate niet uitvoerbaar op deze host. |
| FAT-SYS-016 | P1 | OPS | Blocked | N.v.t. | N.v.t. | Blocked: geen Docker/GitHub Actions runner; workflowlogica alleen statisch beoordeeld. |
| FAT-SYS-017 | P0 | INT | Pass | Pass | N.v.t. | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-018 | P1 | UI | Fail | Pass | Fail | Fail: mobiele viewport 390 px heeft documentbreedte 454–566 px; acties/kolommen vallen buiten beeld. |
| FAT-SYS-019 | P1 | UI | Pass | Pass | Pass | Pass: migratie/history/security-regressie + statische OPS-review. |
| FAT-SYS-020 | P1 | API | Fail | Fail | N.v.t. | Fail: foutinjectie bewijst gedeeltelijke kabelmutatie; geen enkelvoudige transactionele eindstaat. |
| FAT-LOG-001 | P0 | UI/API/DB | Fail | Fail | Fail | Fail: meerdere hoofd- en eindtoestanden wijken af (merge, import, patchpanel, restore). |
| FAT-LOG-002 | P0 | UI/API/DB | Fail | Fail | Fail | Fail: foutpaden bij kabelvervanging/restore voorspellen en bewaren afhankelijkheden niet atomair. |
| FAT-LOG-003 | P1 | UI/API | Fail | Fail | Fail | Fail: ongewijzigd opslaan van patchpanel verdubbelt poorten; CSV-limiet is niet idempotent veilig. |
| FAT-LOG-004 | P1 | UI/API/DB | Fail | Fail | Fail | Fail: merge kan een verboden self-parent-toestand creëren. |
| FAT-LOG-005 | P1 | API/UI | Fail | Fail | Fail | Fail: whitespace, dubbele MAC, CSV-grootte en MAC-substring leveren inconsistente validatie/500 op. |
| FAT-LOG-006 | P1 | API/DB | Fail | Fail | N.v.t. | Fail: geïnjecteerde fouten laten gedeeltelijke kabel- en restoretoestand achter. |
| FAT-LOG-007 | P1 | UI/API | Pass | Pass | Pass | Pass: cross-cutting fault-/grenswaardeharness. |
| FAT-LOG-008 | P0 | INT/API | Pass | Pass | N.v.t. | Pass: cross-cutting fault-/grenswaardeharness. |
| FAT-UX-001 | P1 | UI | Fail | Pass | Fail | Fail: topologie-verwijderen is niet vindbaar zonder broncode/pixelklik. |
| FAT-UX-002 | P1 | UI | Fail | Pass | Fail | Fail: topologie-acties missen een logische, expliciete taakroute; Admin wordt mobiel horizontaal versnipperd. |
| FAT-UX-003 | P1 | UI | Pass | Pass | Pass | Pass: desktop/mobile browseracceptatie. |
| FAT-UX-004 | P1 | UI | Pass | Pass | Pass | Pass: desktop/mobile browseracceptatie. |
| FAT-UX-005 | P1 | UI | Fail | Pass | Fail | Fail: topologiebron-acties ontbreken en relatie/groep/DNS gebruiken alleen kale browserconfirmaties. |
| FAT-UX-006 | P1 | UI | Pass | Pass | Pass | Pass: desktop/mobile browseracceptatie. |
| FAT-UX-007 | P1 | UI | Pass | Pass | Pass | Pass: desktop/mobile browseracceptatie. |
| FAT-UX-008 | P1 | UI | Fail | Pass | Fail | Fail: SVG-nodes/relaties hebben geen rol/tabindex; diverse iconbuttons heten alleen ✎ of ×. |
| FAT-UX-009 | P1 | UI/touch | Fail | Pass | Fail | Fail: mobiel horizontaal verlies en afgesneden provider-/topologie-inhoud. |
| FAT-UX-010 | P2 | UI | Fail | Pass | Fail | Fail: patchpanel-editor heet Bewerken maar primaire knop blijft Toevoegen; delete-termen/patronen wisselen. |
| FAT-UX-011 | P2 | UI | Fail | Pass | Fail | Fail: gevaarlijke acties variëren tussen benoemde knop, kale ×, lijnklik en browserprompt. |
| FAT-UX-012 | P2 | UI/API | Not run | N.v.t. | N.v.t. | Not run: geen aparte grote-dataset/prestatiemeting; normale dataset voelde direct. |

## 6. Vrijgaveadvies

**Niet vrijgeven als 0.4.0 zonder herstel en hertest.** Minimale volgorde:

1. Herstel eerst de vier dataverlies-/atomiciteitsproblemen: migratie, kabelreplace, portable restore en config-import.
2. Blokkeer self-cycles en normaliseer/valideer alle getrimde namen en MAC-adressen; vertaal unieke conflicten naar 409/422.
3. Bouw de afgesproken topologie-acties als zichtbare knoppen met impactdialoog, focus en toetsenbordbediening.
4. Herstel patchpanel-edit en mobiele overflow.
5. Voeg regressietests voor iedere bevinding toe en maak `pytest` een verplichte CI-gate vóór image-push.
6. Voer daarna alle Fail-, Blocked- en Not-run-scripts opnieuw uit in Docker met touch/print/LibreSpeed-capaciteit.

De volledige bestaande testsuite moet groen blijven, maar is op zichzelf niet voldoende: de FAT slaagt pas wanneer alle P0/P1-cases Pass zijn.
