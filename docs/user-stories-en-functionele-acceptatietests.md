# Network Patch Manager — user stories en functionele acceptatietests

> Status: **goedgekeurd en uitgevoerd op 20 augustus 2026**
> Applicatieversie: **0.4.0**
> Referentiecommit: `2a1afb3cae0d37bdd2abcc804299640d75d14bf1`
> Opgesteld: 20 augustus 2026
> Oorspronkelijke resultaten: [functionele-acceptatietest-rapport-0.4.0.md](./functionele-acceptatietest-rapport-0.4.0.md)<br>
> Laatste post-mergehertest: [functionele-acceptatietest-rapport-0.4.0-r2.md](./functionele-acceptatietest-rapport-0.4.0-r2.md)

## 1. Doel en afbakening

Dit document is het vooraf af te spreken contract voor de functionele acceptatietest (FAT) van de hele applicatie. Het dekt:

- alle zichtbare gebruikersfuncties in **Aanmelden**, **Setup-wizard**, **Patch**, **Apps**, **Topologie** en **Admin**;
- alle 69 HTTP-routes van de backend;
- de zeven provideradapters: DHCP/ARP, Uptime Kuma, Glances, AdGuard Home, Nginx Proxy Manager, Portainer en Proxmox VE;
- achtergrondverwerking, historie, migraties, back-up/herstel, configuratie-uitwisseling en containerdeployment;
- positieve paden, foutpaden, grenswaarden, rechten, dataconsistentie en herstelbaarheid;
- bediening met muis, toetsenbord en touch waar de interface dat ondersteunt.

De traceerbare omvang is **68 user stories**, **186 testscripts** (49 P0, 125 P1 en 12 P2) en **69 backendroutes**.

Niet in scope zonder apart akkoord:

- mutaties in echte externe providers; alle adapters behoren read-only te blijven;
- een restore op een productie- of gebruikersdatabase;
- echte LAN-scans buiten expliciet opgegeven `PATCH_TRUSTED_SUBNETS`;
- load-, penetratie- of langdurige duurtesten boven de hieronder beschreven controles.

## 2. Uitvoeringsstop en akkoord

Er wordt **geen enkele test uit dit document uitgevoerd voordat de eigenaar expliciet akkoord geeft**. Inspectie van broncode voor het opstellen van dit plan is geen testuitvoering.

Met akkoord op dit document worden de volgende uitgangspunten goedgekeurd:

1. Alle destructieve scenario's draaien uitsluitend op een nieuwe, geïsoleerde testdatabase en tijdelijke back-upmap.
2. Providerintegraties worden eerst tegen lokale fixtures/stubs getest. Een aanvullende proef tegen echte providers gebeurt alleen als daarvoor afzonderlijk bereikbare testgegevens beschikbaar zijn.
3. Bevindingen krijgen ernst **P0** (dataverlies/beveiliging/onbruikbaar), **P1** (kernfunctie stuk), **P2** (belangrijke beperking) of **P3** (klein/visueel).
4. De voorgestelde betekenis van verwijderen in de topologie is:
   - een handmatige **groep** kan worden verwijderd; kinderen worden ontgroepeerd;
   - een handmatige **relatie** krijgt een zichtbare, toetsenbordbedienbare verwijderactie;
   - een gewone node krijgt een zichtbare keuze **Verbergen uit topologie**;
   - voor een handmatig device of netwerkapparaat staat daarnaast **Bronobject verwijderen**, met impactoverzicht en naambevestiging;
   - een geïmporteerde discovery kan niet lokaal als bronobject worden verwijderd en verwijst naar **Negeren**, **Archiveren** of de provider.
5. De FAT slaagt alleen als alle P0/P1-scenario's slagen, er geen onverklaarde datamutaties zijn en iedere mislukte P2/P3 als geaccepteerde afwijking of vervolgactie is geregistreerd.
6. Iedere user story wordt afzonderlijk beoordeeld op **logische correctheid** én **gebruikersgemak**. `Niet van toepassing` is alleen toegestaan met een schriftelijke reden in het testrapport.
7. Een functie die technisch via een verborgen, onvindbare of onredelijk moeilijke handeling werkt, krijgt geen Pass. De handeling moet voor de beoogde gebruiker vindbaar en betrouwbaar bedienbaar zijn.

### 2.1 Verplichte beoordelingslens voor iedere functie

De onderstaande twee controlelagen gelden voor **iedere** user story en ieder onderliggend scherm, formulier, knop en endpoint.

#### Logische correctheid

- Het beoogde resultaat en de toegestane toestandsovergangen zijn eenduidig.
- De begin-, tussen- en eindtoestand zijn consistent in UI, API en database.
- Validaties, grenswaarden, ontbrekende objecten en strijdige invoer hebben voorspelbaar gedrag.
- Afhankelijkheden, cascades, `SET NULL`, afgeleide data en tellingen blijven correct.
- Opnieuw klikken, herladen, opnieuw importeren of dezelfde provider opnieuw synchroniseren veroorzaakt geen onbedoelde duplicaten.
- Gelijktijdige acties en fouten halverwege laten geen gedeeltelijke toestand achter.
- De uitkomst blijft na refresh en, waar bedoeld, na herstart bestaan.
- Audit, undo, read-only-grenzen en autorisatie sluiten aan op de domeinregel.

#### Gebruikersgemak

- De functie is zonder broncodekennis vindbaar en gebruikt herkenbare, consequente termen.
- De primaire taak kost een logisch aantal stappen en heeft bruikbare defaults.
- De gebruiker ziet wat geselecteerd is, wat er gaat gebeuren en of een actie bezig, geslaagd of mislukt is.
- Foutmeldingen zeggen wat misging en hoe de gebruiker verder kan.
- Annuleren, teruggaan en herstel laten geen onverwachte wijziging achter.
- Destructieve acties tonen concrete impact en vragen bevestiging op het juiste moment.
- Lege, laad-, fout-, disabled- en gedeeltelijke toestanden zijn begrijpelijk.
- De functie is bedienbaar met muis en toetsenbord en, waar relevant, touch; targets en focus zijn zichtbaar.
- De bediening blijft bruikbaar op desktop en mobiel zonder essentiële verborgen acties.
- Een actie mag niet afhangen van een onbenoemde pixelnauwkeurige klik, zoals uitsluitend klikken op een dunne topologielijn.

## 3. Rollen

| Rol | Omschrijving |
|---|---|
| Beheerder | De enige interactieve rol. Beheert inventaris, patching, topologie, providers, apps, DNS en herstel. |
| Niet-aangemelde bezoeker | Mag alleen de publieke titel, aanmeldstatus en healthstatus zien; geen inventaris of mutaties. |
| Systeem | Voert providerpolling, sampling, retentie, speedtests en automatische back-ups uit. |
| Externe provider | Levert uitsluitend observaties; mag handmatige waarheid in Network Patch Manager niet overschrijven. |

## 4. Testomgevingen en testdata

### 4.1 Omgevingen

| ID | Omgeving | Gebruik |
|---|---|---|
| ENV-01 | Lokale FastAPI-app met nieuwe tijdelijke SQLite-database | API-, UI- en dataconsistentietests |
| ENV-02 | Lokale HTTP-fixtures voor alle zeven providers | Deterministische provider-happy- en foutpaden |
| ENV-03 | Docker Compose-build met tijdelijke volumes | Container, healthcheck, rechten, persistentie en LibreSpeed |
| ENV-04 | Desktop Chromium en mobiele/touch-viewport | Browseracceptatie en responsiviteit |

### 4.2 Basistestdata

| Object | Waarde |
|---|---|
| Beheerder | `fat-admin`; wachtwoord via tijdelijke testsecret, minimaal 12 tekens |
| Handmatige devices | `FAT-Laptop`, `FAT-NAS`, `FAT-Camera`, `FAT-Host` |
| Netwerkapparaten | `FAT-Switch` (4 poorten), `FAT-AP` (2 poorten), `FAT-Patchpanel` (2 front/rear-paren) |
| Kabels | `FAT-C-001` blauw, `FAT-C-002` geel, met herkenbare notities |
| Apps | `FAT-Grafana`, `FAT-Home Assistant` in twee groepen |
| DNS | `fat-a.home.arpa`, `fat-v6.home.arpa`, `fat-alias.home.arpa` |
| Topologie | groep `FAT-Groep`, relatie `FAT-relatie`, geplande node en uitfaseernode |
| Providerfixtures | minimaal één host, container, VM, LXC, monitor, DNS-client, rewrite en proxyhost; plus 401, timeout en ongeldig payloadscenario |
| Back-ups | geldige `.pmbackup`, geldige legacy `.db`, beschadigd bestand, verkeerd-sleutelpakket en padmanipulatiepakket |

Iedere test die data maakt, gebruikt de prefix `FAT-` en ruimt die data na bewijsregistratie op, tenzij de volgende test die data expliciet nodig heeft.

## 5. User stories en acceptatiecriteria

### 5.1 Toegang, sessies en algemene bediening

| ID | User story | Acceptatiecriteria |
|---|---|---|
| US-ACC-01 | Als eerste beheerder wil ik éénmalig een account maken, zodat de installatie beveiligd wordt. | Setup verschijnt alleen zonder gebruiker; gebruikersnaam is 2–64 tekens; wachtwoord minimaal 12 tekens; na setup is de sessie actief; tweede setup wordt geweigerd. |
| US-ACC-02 | Als beheerder wil ik veilig in- en uitloggen. | Correcte gegevens geven toegang; foute gegevens geven geen informatie over het bestaan van de gebruiker; logout maakt de sessie ongeldig en verwijdert de cookie. |
| US-ACC-03 | Als beheerder wil ik dat sessies en mutaties beschermd zijn. | Sessies verlopen na zeven dagen; cookie is HttpOnly, SameSite=Lax en optioneel Secure; alle mutaties eisen een geldig CSRF-token; vijf mislukte logins blokkeren het client-IP tijdelijk. |
| US-GEN-01 | Als beheerder wil ik tussen alle hoofdschermen navigeren en data verversen. | Actief tabblad is zichtbaar; verversen haalt de actuele volledige toestand op; de lichte 30-secondenpoll werkt alleen wanneer het document zichtbaar is. |
| US-GEN-02 | Als beheerder wil ik de applicatietitel aanpassen. | Titel wordt getrimd/samengevouwen, is 2–80 tekens en verschijnt op login, kop en browsertab; blijft na herstart behouden. |
| US-GEN-03 | Als beheerder wil ik licht/donker kiezen. | Systeemkeuze geldt zonder lokale voorkeur; knop wisselt thema zonder flits; keuze blijft per browser in `localStorage`. |
| US-GEN-04 | Als beheerder wil ik zoeken over devices, netwerkapparaten en apps. | Vanaf twee tekens; treft naam/IP/MAC/hostname/model/locatie/app-URL/groep; opent de juiste context; gearchiveerde entities ontbreken; resultaten zijn begrensd. |
| US-GEN-05 | Als beheerder wil ik duidelijke succes- en foutfeedback. | Mutaties tonen toast of veldfout; knoppen met langlopende acties krijgen busy-status en worden hersteld; fouten laten de bestaande data intact. |

### 5.2 Setup-wizard

| ID | User story | Acceptatiecriteria |
|---|---|---|
| US-WIZ-01 | Als nieuwe beheerder wil ik automatisch de vierstappenwizard zien. | Wizard toont Scannen, Databronnen, Toewijzen en Klaar; vooruit, terug, overslaan en sluiten werken; status wordt als dismissed opgeslagen; Admin kan de wizard heropenen. |
| US-WIZ-02 | Als beheerder wil ik alleen toegestane subnetten scannen. | Suggestie is alleen een klein vertrouwd lokaal subnet; maximaal 8 subnetten en 1024 adressen per subnet; buiten `PATCH_TRUSTED_SUBNETS` wordt geweigerd; voortgang en tussentijdse vondsten zijn zichtbaar. |
| US-WIZ-03 | Als beheerder wil ik een provider testen vóór opslaan. | Test gebruikt ingevoerde maar nog niet opgeslagen waarden; slaat niets op; toont een betekenisvolle samenvatting; Opslaan blijft uitgeschakeld na een mislukte test. |
| US-WIZ-04 | Als beheerder wil ik discoveries in bulk plaatsen of verwerken. | Per discovery: later, overnemen, samenvoegen of negeren; indien attachable: vrije poort of poortloze uplink; fouten per rij blokkeren andere rijen niet; overzicht telt acties correct. |

### 5.3 Patch, inventaris, kabels en historie

| ID | User story | Acceptatiecriteria |
|---|---|---|
| US-PAT-01 | Als beheerder wil ik een actuele samenvatting van poorten, status, ongekoppelde discoveries en conflicten. | Tellers komen overeen met de database en werken na mutaties en lichte polls direct bij. |
| US-PAT-02 | Als beheerder wil ik handmatige devices aanmaken en bewerken. | Naam, categorie, hostname, IP, MAC en notities worden bewaard; categorie komt uit de centrale lijst; validatiegrenzen gelden; providerobservaties mogen handmatige identiteit niet overschrijven. |
| US-PAT-03 | Als beheerder wil ik handmatige devices veilig verwijderen. | Impact noemt kabels, kinderen, provider-, DNS-, proxy- en topologiekoppelingen; naambevestiging is verplicht; kabel/node verdwijnen; SET NULL-koppelingen blijven als los record; geïmporteerde entities zijn niet lokaal verwijderbaar. |
| US-PAT-04 | Als beheerder wil ik netwerkapparaten aanmaken en bewerken. | Ondersteunt switch, mesh-AP, AP, patchpanel en ONT; 1–96 poorten; model, locatie, notities en statusmonitor; poorten kunnen veilig groeien en alleen krimpen wanneer de te verwijderen poorten vrij zijn. |
| US-PAT-05 | Als beheerder wil ik netwerkapparaten veilig verwijderen. | Impact toont poorten, kabels en relaties; naambevestiging verplicht; afhankelijke poorten/kabels/nodes verdwijnen; bewust verwijderde seedapparaten komen na herstart niet terug. |
| US-PAT-06 | Als beheerder wil ik poortmetadata beheren. | Label, snelheid 10–100000 Mbps of leeg en notities worden opgeslagen; onbekende poort geeft 404. |
| US-PAT-07 | Als beheerder wil ik een device aan precies één poort koppelen. | Eén kabel per poort en per entity; kabel bevat label, kleur en notities; koppeling wist een eventuele poortloze uplink; dubbele of ongeldige koppelingen worden atomair geweigerd. |
| US-PAT-08 | Als beheerder wil ik twee fysieke poorten verbinden. | Zelfkoppeling is onmogelijk; beide poorten moeten bestaan en vrij zijn; apparaat-naar-apparaatverbinding verschijnt één keer als fysieke topologierelatie. |
| US-PAT-09 | Als beheerder wil ik een patchpanel als front/rear-doorsteek gebruiken. | Elk nummer heeft exact één gekoppeld front/rearpaar; trace loopt door het paneel; paneel wordt niet als extra eindnode in de topologie getekend; hoplimiet voorkomt lussen. |
| US-PAT-10 | Als beheerder wil ik kabels verplaatsen, overschrijven en loskoppelen. | Bezet doel vraagt bevestiging; vervangen gebeurt in één consistente stap; loskoppelen werkt via lade en slepen naar ongekoppeld; bron/target blijven niet half gekoppeld. |
| US-PAT-11 | Als beheerder wil ik koppelen via muis, touch of keuzelijst. | Drag/drop, 350 ms touch-drag en klik+poortselectie leiden tot hetzelfde resultaat; virtuele categorieën zijn niet rechtstreeks bekabelbaar. |
| US-PAT-12 | Als beheerder wil ik een wifi/poort-onbekend-device zonder poort aan netwerkapparatuur hangen. | Alleen attachable categorieën; kabel en uplink bestaan nooit tegelijk; loskoppelen werkt; draadloze/poortloze lijn is onderscheidbaar in topologie. |
| US-PAT-13 | Als beheerder wil ik een statusmonitor aan een netwerkapparaat koppelen. | Apparaat leent status/historie van gekozen entity; monitor wordt niet dubbel getekend; bij ontkoppelen verschijnt de monitor weer als eigen node; onbekende monitor wordt geweigerd. |
| US-PAT-14 | Als beheerder wil ik detail en historie van een device zien. | Lade toont identiteit, kabel, laatste waarneming, 30-daagse uptime, flips en 48-uurs cpu/mem/ping; ontbrekende historie wordt begrijpelijk getoond. |
| US-PAT-15 | Als beheerder wil ik devices via CSV importeren. | UTF-8/BOM; minimaal kolom `name`; create/update op MAC anders handmatige naam; tweede import dupliceert niet; ongeldige rijen worden gemeld; bestanden boven 1 MB worden zonder gedeeltelijke import geweigerd en import verwijdert nooit. |
| US-PAT-16 | Als beheerder wil ik een bruikbaar patchschema printen. | Alleen patchinhoud wordt op wit afgedrukt; navigatie, lades en beheeracties ontbreken; namen, poortnummers, labels en kleuren blijven leesbaar. |

### 5.4 Apps, discovery en providers

| ID | User story | Acceptatiecriteria |
|---|---|---|
| US-APP-01 | Als beheerder wil ik app-snelkoppelingen beheren. | CRUD voor naam, http(s)-URL, beschrijving, icoon, groep, positie en monitor; `javascript:` en andere schema's worden geweigerd; openen gebeurt in nieuw tabblad met `noopener noreferrer`. |
| US-APP-02 | Als beheerder wil ik apps gegroepeerd, geordend en met status zien. | Groep en positie bepalen volgorde; lege groep heet Overig; status komt van monitor of is unknown; verwijderen van monitor verwijdert app niet. |
| US-DIS-01 | Als beheerder wil ik nieuwe en verdwenen apparaten zien. | Periode 1/7/30 dagen; nieuw gebruikt first_seen; verdwenen gebruikt last_seen en niet-up; grenzen 1–90 dagen; maximaal 50 per lijst. |
| US-DIS-02 | Als beheerder wil ik een discovery overnemen. | Origin wordt manual; naam/categorie kunnen gekozen worden; providerrecord blijft gekoppeld en observaties blijven status leveren. |
| US-DIS-03 | Als beheerder wil ik discoveries negeren, archiveren en herstellen. | Alleen discoveries; genegeerd/gearchiveerd verdwijnt uit actieve lijst/topologie; herstellen maakt zichtbaar; providerrecord blijft bestaan. |
| US-DIS-04 | Als beheerder wil ik een discovery samenvoegen met een bestaand device. | Providerrecords, observaties, conflicten, kinderen, DNS/proxy, fysieke kabel en geldige handmatige relaties verhuizen; bron verdwijnt; twee fysieke kabels of self-merge worden geweigerd; geen self-cycle ontstaat. |
| US-DIS-05 | Als beheerder wil ik providerrecords expliciet koppelen en ontkoppelen. | Geldig doel wordt gebruikt bij volgende sync; `Niet gekoppeld` zet null; onbekend record/doel geeft 404. |
| US-DIS-06 | Als beheerder wil ik conflicten zien en oplossen zonder mijn handmatige waarheid kwijt te raken. | Naam/MAC-afwijking maakt één open conflict; handmatige waarden blijven staan; oplossen bewaart resolutie; identieke vervolgobservatie maakt niet opnieuw hetzelfde conflict. |
| US-PRO-01 | Als beheerder wil ik meerdere omgevingen per providertype beheren. | Bekend type kan extra instantie krijgen, naam/config/secrets blijven gescheiden; instantie kan worden hernoemd; alleen niet-laatste instantie is na naambevestiging verwijderbaar. |
| US-PRO-02 | Als beheerder wil ik providerconfiguratie en geheimen veilig beheren. | Enable, interval 15–86400 s en JSON-config worden bewaard; geheimen zijn versleuteld en nooit teruggestuurd; leeg veld behoudt; wissen verwijdert; onbekend credentialveld wordt geweigerd. |
| US-PRO-03 | Als beheerder wil ik handmatig en automatisch synchroniseren. | Eén sync per provider tegelijk; busy bij overlap; succes/records/tijden worden opgeslagen; fout wordt begrensd opgeslagen en blokkeert andere providers niet. |
| US-PRO-04 | Als beheerder wil ik DHCP/ARP-discovery. | Alleen complete niet-nul-MAC-buren; normaliseert MAC; vertrouwde IP's; reverse DNS parallel; twee pings; ontbrekend apparaat binnen gescand subnet wordt down, erbuiten blijft ongemoeid; lokale OUI-herkenning. |
| US-PRO-05 | Als beheerder wil ik Uptime Kuma read-only importeren. | Publieke statuspagina plus heartbeat; status 1/0/2 wordt up/down/degraded; monitornaam/service ontstaat; test meldt monitoraantal; ontbrekende URL/slug en HTTP-fouten zijn duidelijk. |
| US-PRO-06 | Als beheerder wil ik meerdere Glances-machines importeren. | Ieder endpoint heeft URL en gekozen device; host wordt expliciet gebonden; system/IP/containers/quicklook/mem worden verwerkt; gedeeltelijke test rapporteert per endpoint; geen werkend endpoint faalt. |
| US-PRO-07 | Als beheerder wil ik AdGuard Home read-only importeren. | Basic auth/TLS-keuze; clients en auto-clients; MAC/IP/hostname matching; rewrites als A/AAAA/CNAME; koppeling op doel; geïmporteerde DNS blijft read-only. |
| US-PRO-08 | Als beheerder wil ik Nginx Proxy Manager read-only importeren. | Token of login; proxyhosts, domeinen, doel, poort en status; doelmatching op IP/hostname/naam; service-entity genest onder doel; geïmporteerde host blijft read-only. |
| US-PRO-09 | Als beheerder wil ik Portainer read-only importeren. | API-key/TLS; endpoints als hosts; alle containers; running→up en overige→down; native container-ID voorkomt duplicaten over relevante bronnen. |
| US-PRO-10 | Als beheerder wil ik Proxmox VE read-only importeren. | URL, user, token-ID en geheim; correcte Authorization-header; nodes, QEMU-VM's en LXC's met parent; online/running-status; 401 noemt geprobeerd token-ID maar nooit geheim. |

### 5.5 Topologie

| ID | User story | Acceptatiecriteria |
|---|---|---|
| US-TOP-01 | Als beheerder wil ik een actuele samengestelde topologie zien. | Fysieke apparaten, entities, speciale internetnode, parents, status en metrics verschijnen; provider- en patchsync behouden handmatige layout/labels/parents. |
| US-TOP-02 | Als beheerder wil ik fysieke, virtuele en servicelagen filteren. | Elke checkbox verbergt uitsluitend de bedoelde relaties; nodes blijven consistent; opnieuw inschakelen herstelt de laag. |
| US-TOP-03 | Als beheerder wil ik de topologie verkennen. | Hover toont label/subtitel/status/IP/cpu/respons/bronnen; pan werkt buiten nodes; wiel en +/- zoomen 40–400%; Passend herstelt 100%. |
| US-TOP-04 | Als beheerder wil ik node-eigenschappen beheren. | Label, subtitel, parent, lifecycle active/planned/phase_out en collapsed blijven behouden; self-parent, ontbrekende parent en iedere indirecte cirkel worden geweigerd. |
| US-TOP-05 | Als beheerder wil ik één of meerdere nodes verplaatsen. | Klik opent editor zonder verplaatsing; drag bewaart coördinaten; shift selecteert/deselecteert; multiselectie beweegt als één groep; ongeldige bulkpositie wordt geweigerd. |
| US-TOP-06 | Als beheerder wil ik groepen maken en beheren. | Lege groep of geselecteerde nodes groeperen; bestaande groep als parent; groep inklappen; groep verwijderen ontgroepeert kinderen; ongeldige selectie/cirkel wordt geweigerd. |
| US-TOP-07 | Als beheerder wil ik handmatige relaties tekenen en verwijderen. | Van/naar verschillend en bestaand; type dependency/network/service/uplink; label zichtbaar; verwijderen via zichtbare knop en toetsenbord; alleen manual is verwijderbaar. |
| US-TOP-08 | Als beheerder wil ik nodes veilig uit de topologie verwijderen of verbergen. | Gedrag volgt beslispunt 4 in hoofdstuk 2; actie is zichtbaar in node-editor; impact en bevestiging zijn correct; verborgen node blijft na catalogussync verborgen; verwijderd bronobject keert niet terug. |
| US-TOP-09 | Als beheerder wil ik automatische layout herstellen. | Alle handmatige posities worden gewist, parents/labels/lifecycle blijven; nieuwe auto-indeling is bruikbaar. |
| US-TOP-10 | Als beheerder wil ik topologiewijzigingen ongedaan maken. | Laatste 50 snapshots; node, positie, groep, relatie, reset en verwijdering herstelbaar; actieomschrijving klopt; zonder historie duidelijke fout; afgeleide patchrelaties worden niet als handmatig teruggezet. |

### 5.6 DNS, speedtest, back-up, configuratie en systeem

| ID | User story | Acceptatiecriteria |
|---|---|---|
| US-DNS-01 | Als beheerder wil ik handmatige DNS-records beheren. | CRUD voor A/AAAA/CNAME, naam zonder afsluitende punt, waarde, TTL 30–86400/leeg en optionele entity; geïmporteerde records zijn niet lokaal wijzigbaar/verwijderbaar. |
| US-DNS-02 | Als beheerder wil ik proxyhosts raadplegen. | Domeinen, scheme, host, poort, enabled en gekoppeld device/service zijn zichtbaar; beheer gebeurt uitsluitend in NPM. |
| US-SPD-01 | Als beheerder wil ik speedtestinstellingen beheren. | Aan/uit, interval 15 min–7 dagen, duur 5–30 s, server en interface; telemetry blijft technisch 0. |
| US-SPD-02 | Als beheerder wil ik handmatig en gepland snelheid meten. | Eén run tegelijk; busy bij overlap; timeout/CLI-/JSON-fout wordt opgeslagen; succes toont download/upload/ping/jitter/server en historie; stale running wordt na herstart failed. |
| US-BCK-01 | Als beheerder wil ik draagbare, integere back-ups maken en downloaden. | Online SQLite-copy, integrity check, database+sandboxsleutel+manifest, mode 0600, lijst maximaal 50, retentie volgens instelling en veilige bestandsnaam. |
| US-BCK-02 | Als beheerder wil ik geldige back-ups importeren en herstellen. | `.pmbackup` en legacy `.db`; upload ≤512 MB; pad-/zip-/manifest-/sleutelvalidatie; vóór restore veiligheidsback-up; naambevestiging; database en secrets werken na restore; corrupte invoer wijzigt actief systeem niet. |
| US-CFG-01 | Als beheerder wil ik configuratie als JSON exporteren/importeren. | Formaat/versionering; inventaris, poorten, providers zonder secrets, manual DNS, topologie en speedtest; import maakt eerst back-up, upsert op ID, herstelt FK-relaties onafhankelijk van recordvolgorde en rolt volledig terug bij fout. |
| US-AUD-01 | Als beheerder wil ik beheeracties kunnen herleiden. | Relevante mutaties schrijven actor, actie, type, id, details en tijd; UI toont laatste 200; database houdt maximaal 5000. |
| US-SYS-01 | Als operator wil ik health, versie en containerstatus controleren. | `/health` zonder login geeft ok+0.4.0; Docker-healthcheck volgt `PATCH_PORT`; container draait als UID 10001, read-only, met alleen benodigde NET_RAW; data/back-ups blijven in volumes. |
| US-SYS-02 | Als operator wil ik veilig upgraden en terugrollen. | Verse DB krijgt actuele `user_version`; oude DB migreert zonder verlies en idempotent; nieuwere DB wordt geweigerd; seeddata komt eenmaal; release-tag moet bij codeversie passen. |
| US-SYS-03 | Als operator wil ik begrensde achtergronddata. | Sessions/observaties worden opgeschoond; 5-minutensampling; 48 uur detail, 730 dagen dagaggregaten, 5000 auditregels; automatische providerpoll, speedtest en dagelijkse back-up volgen instellingen. |
| US-SYS-04 | Als eigenaar wil ik dat providerdata read-only en secrets vertrouwelijk blijven. | Geen adapter schrijft naar externe bron; handmatige identiteit blijft leidend; secrets zijn Fernet-versleuteld, niet in config-export/log/foutmelding; draagbare back-up valideert dat sleutel en data horen bij elkaar. |

## 6. Functionele testscripts

Legenda: **UI** = browser, **API** = HTTP plus databasecontrole, **INT** = providerfixture/integratie, **OPS** = container of migratie. Iedere regel bevat een uitvoerbaar script; bewijs bestaat uit screenshot of response, relevante databasecontrole en logreferentie.

Naast de functiespecifieke scripts wordt bij ieder resultaat vastgelegd: `Logica: Pass/Fail/N.v.t.` en `UX: Pass/Fail/N.v.t.`, met toelichting bij iedere Fail of N.v.t.

### 6.1 Toegang en algemene bediening

| Test-ID | P | Laag | Voorwaarde | Stappen | Verwacht resultaat |
|---|---:|---|---|---|---|
| FAT-ACC-001 | P0 | UI/API | Nieuwe DB | Open app; controleer status; maak `fat-admin` met geldig wachtwoord. | Setup zichtbaar; account en sessie ontstaan; Patch opent; wizard verschijnt. |
| FAT-ACC-002 | P1 | UI/API | Nieuwe DB | Probeer gebruikersnaam van 1 teken en wachtwoord van 11 tekens. | Client/server weigeren; geen user/sessie. |
| FAT-ACC-003 | P0 | API | Setup voltooid | POST setup met tweede gebruiker. | 409; bestaande beheerder onveranderd. |
| FAT-ACC-004 | P0 | UI/API | Uitgelogd | Login correct, herlaad pagina. | Sessiecookie werkt; gebruiker en data zichtbaar. |
| FAT-ACC-005 | P0 | UI/API | Uitgelogd | Login met onbekende gebruiker en fout wachtwoord. | Beide geven generiek 401; vergelijkbare respons zonder gebruikerslek. |
| FAT-ACC-006 | P1 | API | Uitgelogd | Doe vijf foutieve pogingen vanaf één client en daarna een zesde/correcte poging. | Zesde en correcte poging binnen venster geven 429; ander client-IP blijft bruikbaar; na venster vervalt blokkade. |
| FAT-ACC-007 | P0 | API | Ingelogd | Roep alle write-methoden representatief aan zonder, met fout en met correct CSRF-token. | 403 zonder/fout; alleen correct token muteert. |
| FAT-ACC-008 | P0 | UI/API | Ingelogd | Logout; hergebruik oude cookie en CSRF. | Loginweergave; oude sessie geeft 401 en is uit DB verwijderd. |
| FAT-ACC-009 | P1 | API | Ingelogde verlopen sessiefixture | Vraag bootstrap en probeer mutatie. | Beide 401; housekeeping verwijdert verlopen sessie. |
| FAT-ACC-010 | P1 | API | Secure false/true varianten | Inspecteer Set-Cookie na login. | HttpOnly, SameSite=Lax, path=/, max-age 7 dagen; Secure volgt instelling; ruwe token staat niet in DB. |
| FAT-GEN-001 | P1 | UI | Ingelogd | Navigeer Patch→Apps→Topologie→Admin→Patch. | Exact één actief tabblad; juiste inhoud; geen statecorruptie. |
| FAT-GEN-002 | P1 | UI/API | Externe DB-mutatiefixture | Klik Verversen. | Nieuwe data verschijnt en tijdstempel wordt bijgewerkt. |
| FAT-GEN-003 | P2 | UI/API | Statuswijzigingfixture | Laat summarypoll lopen zichtbaar en daarna verborgen. | Zichtbaar werkt status/tellers bij zonder bootstrap; verborgen doet geen poll. |
| FAT-GEN-004 | P1 | UI/API | Ingelogd | Sla titel met extra spaties op; herlaad en herstart. | Samengevouwen titel op login/header/tab en persistent; auditrecord aanwezig. |
| FAT-GEN-005 | P2 | UI | Geen lokale themakeuze | Controleer systeemthema; wissel tweemaal; herlaad. | Correct initieel thema, wissel, persistentie en geen contentverlies. |
| FAT-GEN-006 | P1 | UI/API | Volledige basistestdata | Zoek op entitynaam, IP, MAC en hostname. | Correcte entitytreffers; selectie opent entitylade. |
| FAT-GEN-007 | P1 | UI/API | Volledige basistestdata | Zoek op apparaatnaam/model/locatie en appnaam/URL/groep. | Juiste tab; apparaatkaart highlight; appresultaat zichtbaar. |
| FAT-GEN-008 | P2 | UI/API | Basistestdata | Zoek 0/1 teken, onbekende term, >25 treffers en gearchiveerde entity. | Leeg onder 2; nette lege status; begrensd; archief niet gevonden. |
| FAT-GEN-009 | P1 | UI | Simuleer API-fout | Voer create/update/sync uit. | Fouttoast/veldfeedback; busy-knop herstelt; dialog/data blijven herstelbaar. |

### 6.2 Setup-wizard

| Test-ID | P | Laag | Voorwaarde | Stappen | Verwacht resultaat |
|---|---:|---|---|---|---|
| FAT-WIZ-001 | P1 | UI/API | Eerste login | Controleer vier stappen; ga vooruit/terug; sla stap over; sluit. | Navigatie, titels en knopstatus kloppen; sluiten zet dismissed=true. |
| FAT-WIZ-002 | P1 | UI/API | Wizard dismissed | Herlaad; open Admin→Setup-wizard. | Niet automatisch opnieuw; handmatig opent stap 1 en reset tellers. |
| FAT-WIZ-003 | P1 | API | Trusted subnetfixture | Vraag wizardinfo met lokaal IP binnen/buiten netwerk en netwerk >1024. | Suggestie alleen voor vertrouwd netwerk ≤1024; volledige trusted lijst zichtbaar. |
| FAT-WIZ-004 | P0 | UI/INT | Scanfixture | Scan één toegestaan /30-netwerk. | Provider wordt geconfigureerd; voortgang; alleen echte complete buren; resultaat en teller correct. |
| FAT-WIZ-005 | P0 | UI/API | Scanfixture | Probeer onbetrouwbaar subnet, >1024 adressen, 9 subnetten en ongeldige CIDR. | Geen ongeautoriseerde scan; duidelijke fout; bestaande config/data intact. |
| FAT-WIZ-006 | P1 | UI/INT | Providerfixtures | Vul providerwaarden in; test succesvol. | Samenvatting; Opslaan actief; vóór opslaan config/secrets/records onveranderd. |
| FAT-WIZ-007 | P1 | UI/INT | Providerfoutfixture | Test 401, timeout en onvolledige config. | Begrijpelijke fout; Opslaan blijft disabled; geheim niet in melding. |
| FAT-WIZ-008 | P1 | UI/INT | Geslaagde providertest | Klik Opslaan en ophalen. | Config versleuteld opgeslagen, provider enabled, sync uitgevoerd en recordtelling zichtbaar. |
| FAT-WIZ-009 | P1 | UI/API | Vier open discoveries | Kies respectievelijk later, promote+poort, merge en ignore; toepassen. | Iedere rij volgt keuze; tellingen kloppen; fouten isoleren zich per rij. |
| FAT-WIZ-010 | P1 | UI/API | Attachable en niet-attachable discovery | Kies poortloze uplink voor attachable; inspecteer VM/container/service. | Uplink opgeslagen; niet-attachable heeft geen plekselectie. |
| FAT-WIZ-011 | P2 | UI/API | Eerder geplaatste uplink | Open toewijsscherm opnieuw. | Bestaande uplink is vooringevuld; vrije poortenlijst bevat geen bezette poorten. |
| FAT-WIZ-012 | P2 | UI | Wizard stap 4 | Controleer samenvatting; open links naar Patch/Topologie. | Cijfers en resterend aantal kloppen; wizard sluit en juiste tab opent. |

### 6.3 Patch, inventaris en kabels

| Test-ID | P | Laag | Voorwaarde | Stappen | Verwacht resultaat |
|---|---:|---|---|---|---|
| FAT-PAT-001 | P1 | UI/API | Verse DB | Vergelijk summarychips, kaarten, poorten en ongekoppelde lijst met DB/bootstrap. | Aantallen, seedapparaten en 26 voorpoorten kloppen. |
| FAT-PAT-002 | P1 | UI/API | Ingelogd | Maak `FAT-NAS` met alle velden; open/bewerk; herlaad. | Velden/categorie persistent; create/update-audit aanwezig. |
| FAT-PAT-003 | P1 | API | Handmatig device | Test naam/type/IP/MAC/hostname/notitiegrenzen en onbekende categorie. | Pydanticgrenzen gelden; onbekende API-categorie blijft leesbaar zonder crash. |
| FAT-PAT-004 | P0 | INT/API | Handmatig device plus providerfixture | Sync met afwijkende naam/MAC/IP. | Handmatige naam/MAC blijven; status/last_seen wijzigen; conflict ontstaat volgens regels. |
| FAT-PAT-005 | P1 | UI/API | Discovered entity | Probeer lokaal bewerken. | UI biedt dit niet; API 409; brondata intact. |
| FAT-PAT-006 | P1 | UI/API | Handmatig device met afhankelijkheden | Open Verwijder; controleer impact; annuleer; bevestig daarna. | Impact volledig; annuleren muteert niet; bevestiging verwijdert device/kabel/node en laat DNS/proxy losgekoppeld. |
| FAT-PAT-007 | P0 | API | Discovered entity | Vraag impact en probeer correcte naamdelete. | deletable=false en 409; providerrecord/entity blijven. |
| FAT-PAT-008 | P1 | UI/API | Ingelogd | Maak `FAT-Switch` met 4 poorten, alle metadata en monitor; bewerk naar 6. | Apparaat en 6 unieke frontpoorten; monitorstatus; metadata persistent. |
| FAT-PAT-009 | P1 | UI/API | FAT-Switch 6 poorten | Krimp naar 4 wanneer 5/6 vrij; daarna bezet 4 en probeer krimp naar 3. | Vrije poorten verdwijnen; bezette poort blokkeert met nummer; transactie intact. |
| FAT-PAT-010 | P1 | API | Bestaand switchtype | Probeer wisselen switch↔patchpanel. | 409 met instructie nieuw apparaat te maken; poorten intact. |
| FAT-PAT-011 | P0 | UI/API | Apparaat met kabels/relaties | Controleer impact; annuleer; delete met foute en juiste naam. | Foute naam 422; juist verwijdert apparaat, poorten, kabels en node; audit klopt. |
| FAT-PAT-012 | P1 | OPS/API | Verwijder seeded testkopie | Herstart tweemaal. | Bewust verwijderd seedapparaat keert niet terug. |
| FAT-PAT-013 | P1 | API | Vrije poort | PATCH label, snelheid en notities; test 9, 10, 100000, 100001 en onbekend id. | Geldige grenzen opgeslagen; ongeldige 422; onbekend 404. |
| FAT-PAT-014 | P0 | UI/API | FAT-Switch en FAT-Laptop | Koppel via poortlade met label/kleur/notities. | Exact één kabel; poort bezetweergave, legenda, entitylade en trace kloppen. |
| FAT-PAT-015 | P0 | API | Twee entities/twee poorten | Probeer twee kabels op dezelfde poort en één entity op twee poorten. | 409; bestaande kabel niet gewijzigd; geen half record. |
| FAT-PAT-016 | P1 | API | Vrije poorten | Maak port↔port; probeer self-link en ontbrekende target. | Geldige verbinding bezet beide; self 422; ontbrekend 404. |
| FAT-PAT-017 | P0 | UI/API | Bezet bron- en doelpoort | Sleep kabel naar vrije poort en daarna bezette poort; annuleer/bevestig overschrijven. | Vrije move behoudt metadata; annulering intact; bevestiging vervangt atomair. |
| FAT-PAT-018 | P1 | UI/API | Bezet devicepoort | Los via lade en via slepen naar zijlijst. | Kabel verwijderd; beide uiteinden vrij; device verschijnt ongekoppeld. |
| FAT-PAT-019 | P1 | UI/API | Ongekoppeld device | Klik chip en kies poort zonder muis. | Zelfde kabelresultaat; bij geen vrije poort duidelijke optie/geen mutatie. |
| FAT-PAT-020 | P1 | UI/touch | Touchviewport | Lang indrukken >350 ms en sleep naar poort; kort tikken; loslaten buiten doel. | Lang slepen koppelt; korte tik opent kiezer; buiten doel muteert niet; ghost-click onderdrukt. |
| FAT-PAT-021 | P0 | API | Device met bestaande uplink | Maak kabel naar device. | Uplink wordt automatisch null; topologie toont alleen fysieke kabel. |
| FAT-PAT-022 | P1 | UI/API | FAT-Camera | Stel poortloze uplink in en wis. | Uplinklijst/topologie werken; wissen verwijdert relatie. |
| FAT-PAT-023 | P1 | API | VM, LXC, container, service en host | Probeer uplink op elk type. | Virtuele typen 409; host/attachable toegestaan; onbekend apparaat 404. |
| FAT-PAT-024 | P1 | UI/API | Monitorentity met historie | Koppel aan FAT-Switch; open statusknop; ontkoppel. | Status/historie geleend; monitornode niet dubbel; na ontkoppelen eigen node terug. |
| FAT-PAT-025 | P1 | API | Geen/ongeldige monitor | Vraag fysieke historie en stel onbekend monitor-id in. | Zonder monitor lege historie; onbekend 404; apparaat intact. |
| FAT-PAT-026 | P1 | UI/API | Entity met samples | Open detaildrawer en gekoppelde poort vanuit drawer. | Facts, uptime, flips, drie sparklines en kabelpad; poortlade opent correct. |
| FAT-PAT-027 | P0 | UI/API | FAT-Patchpanel 2 poorten | Controleer front/rear-paren; verbind switch→front en rear→entity; vraag trace beide kanten. | Exact vier poorten, symmetrische peers en complete trace zonder paneel-eindnode. |
| FAT-PAT-028 | P1 | API | Kunstmatige kabelcyclus/hopketenfixture | Vraag trace vanaf begin. | Geen oneindige lus; stopt uiterlijk na 10 hops/los eind. |
| FAT-PAT-029 | P1 | UI/API | CSV met create/update/BOM/fouten | Importeer tweemaal; inspecteer melding en data. | Eerste create, tweede update, geen duplicaat; maximaal 20 problemen terug; geldige rijen blijven. |
| FAT-PAT-030 | P1 | API | CSV-fixtures | Upload zonder name-header, ongeldige MAC, lege naam, >1 MB en onbekende kolommen. | Header 422; rijfouten benoemd; onbekende kolommen genegeerd; >1 MB geeft 413 zonder gedeeltelijke import; geen deletes. |
| FAT-PAT-031 | P2 | UI | Gevulde Patchview | Start printpreview desktop en A4. | Alleen bruikbaar patchschema; geen nav/knoppen/lades; geen afgekapt essentieel veld. |

### 6.4 Apps, discovery en providerbeheer

| Test-ID | P | Laag | Voorwaarde | Stappen | Verwacht resultaat |
|---|---:|---|---|---|---|
| FAT-APP-001 | P1 | UI/API | Monitorfixture | Maak app met alle velden; open kaartlink en bewerk. | Waarden persistent; link nieuw tabblad+noopener; audit create/update. |
| FAT-APP-002 | P0 | API/UI | Appformulier | Probeer `javascript:`, `data:`, relatieve URL en geldige HTTP/HTTPS. | Alleen HTTP/HTTPS geaccepteerd; script wordt nooit uitgevoerd. |
| FAT-APP-003 | P1 | UI/API | Meerdere apps | Varieer groep, lege groep en positie. | Gegroepeerd/geordend; leeg onder Overig; teller per groep klopt. |
| FAT-APP-004 | P1 | UI/API | Monitor up/down/unknown | Poll status; verwijder monitor. | Tegelstatus volgt; zonder monitor unknown; app blijft bestaan. |
| FAT-APP-005 | P1 | UI/API | Bestaande app | Annuleer delete en bevestig daarna. | Eerst intact; daarna weg; 404 op herhaalde API-delete. |
| FAT-DIS-001 | P1 | UI/API | Entities met first/last_seen | Kies 1, 7 en 30 dagen; roep API met 0 en 999 aan. | Juiste nieuw/weg-lijsten; clamp 1–90; up niet als verdwenen; max 50. |
| FAT-DIS-002 | P1 | UI/API | Discoveryfixture | Promote met bestaande en aangepaste naam/categorie. | Manual, actief, providerlink behouden; lege naam 422; tweede promote 409. |
| FAT-DIS-003 | P1 | UI/API | Drie discoveries | Negeer, archiveer en herstel. | Lijsten, counts en topology hidden correct; manual entity statechange 409. |
| FAT-DIS-004 | P0 | UI/API | Discovery met providerdata en handmatig target | Merge; controleer alle FK's, kabel en relaties. | Alles verhuist zoals US-DIS-04; bron weg; audit aanwezig. |
| FAT-DIS-005 | P0 | API | Bron en doel beide bekabeld | Merge; test ook self/ontbrekend target en bron manual. | 409/404 zonder enige gedeeltelijke verhuizing. |
| FAT-DIS-006 | P0 | API | Bronrelatie naar doel | Merge dat een self-relation zou maken. | Geen self-relation/cyclus; overige geldige data verhuist of hele transactie wordt veilig geweigerd volgens implementatie. |
| FAT-DIS-007 | P1 | UI/API | Providerrecord + twee entities | Koppel aan A, sync, koppel aan B, ontkoppel. | Record gebruikt expliciet doel; UI-naam volgt; null na ontkoppeling; ongeldige ids 404. |
| FAT-DIS-008 | P0 | INT/API | Manual entity met providerafwijking | Sync tweemaal, los conflict op, sync opnieuw identiek. | Eén conflict; manual intact; resolved blijft resolved; geen duplicaat. |
| FAT-PRO-001 | P1 | UI/API | Seedproviders | Voeg tweede Portainer toe; hernoem en configureer beide verschillend. | Twee ids; settings/secrets/records geïsoleerd. |
| FAT-PRO-002 | P1 | UI/API | Eén en twee instanties per type | Probeer laatste te verwijderen; verwijder tweede met foute/juiste naam. | Laatste 409/geen UI-knop; foute naam 422; juiste delete cascadeert providerdata. |
| FAT-PRO-003 | P1 | API | Providercreate | Probeer onbekend type en naamgrenzen. | 422; geen record. |
| FAT-PRO-004 | P0 | UI/API/DB | Provider met secrets | Sla secrets op, heropen, laat leeg, wijzig één, wis één; exporteer config. | Secrets versleuteld; nooit terug in bootstrap/export; behoud/wijzig/wis correct. |
| FAT-PRO-005 | P1 | UI/API | Providerconfig | Test interval 14/15/86400/86401, ongeldige JSON en onbekend credentialveld. | Alleen geldige waarden; JSON-fout clientmelding; 422 onbekend veld; data intact. |
| FAT-PRO-006 | P1 | INT/API | Providerfixture | Test verbinding met niet-opgeslagen waarden en vergelijk DB voor/na. | Alleen samenvatting; geen config/secrets/records/tijdstempels gewijzigd. |
| FAT-PRO-007 | P1 | INT/API | Providerfixture | Start twee gelijktijdige syncs. | Eén verwerkt; andere busy; geen dubbele records. |
| FAT-PRO-008 | P1 | INT/API | Eén succes- en één foutprovider | Sync beide en background sync. | Succesvelden/foutvelden correct; fout begrensd; één fout blokkeert andere niet. |
| FAT-PRO-009 | P0 | INT | ARP-fixtures | Parse complete/incomplete/nul-/uppercase-/malformed MAC-regels. | Alleen complete geldige buren; lowercase; geen nul-MAC. |
| FAT-PRO-010 | P0 | INT | DHCP-scanfixture | Scan trusted /30 met present/absent/out-of-range devices. | Present up; eerder device in scope down; buiten scope/status/last_seen intact; observatie TTL vernieuwd. |
| FAT-PRO-011 | P1 | INT | OUI-filefixture | Discover bekende en onbekende MAC. | Bekende vendor lokaal ingevuld; onbekend leeg; geen internetlookup. |
| FAT-PRO-012 | P1 | INT | Kuma fixture | Test en sync statuses 1,0,2,onbekend. | Count en up/down/degraded/unknown correct; her-sync upsert zonder duplicaat. |
| FAT-PRO-013 | P1 | INT | Kuma foutfixtures | Ontbrekende URL/slug, 401, timeout, malformed JSON. | Test/sync geeft bruikbare fout; secrets niet relevant; bestaande records intact. |
| FAT-PRO-014 | P1 | INT | Twee Glances-endpoints | Test en sync met gekozen entities, metrics en containers. | Per-endpoint verslag; hosts expliciet gebonden; metrics/parents/containers correct. |
| FAT-PRO-015 | P1 | INT | Eén goed/één fout/geen entity Glances | Test partial; sync. | Partial test noemt beide en slaagt bij ≥1; sync zonder entity weigert; geen verkeerde hostmatch. |
| FAT-PRO-016 | P1 | INT | AdGuardfixture | Test/sync clients, auto-clients, A/AAAA/CNAME rewrites. | Tellingen, normalization, types, entitymatching en upserts correct. |
| FAT-PRO-017 | P1 | INT | AdGuard auth/TLS/foutpayload | Test 401, TLS true/false en lege rewrite. | Betekenisvolle fout; lege rewrite genegeerd; geen manual DNS-mutatie. |
| FAT-PRO-018 | P1 | INT | NPM tokenfixture | Test/sync enabled/disabled hosts met doelen op IP/hostname/naam. | Proxy- en serviceentities up/down, domeinen/parent/koppelingen correct. |
| FAT-PRO-019 | P1 | INT | NPM loginfixture | Zonder token met identity/secret; daarna ontbrekende creds en 401. | Tokenflow werkt; ontbrekend/401 duidelijk; geheim niet gemeld. |
| FAT-PRO-020 | P1 | INT | Portainerfixture | Test/sync twee endpoints en running/stopped containers. | Hosts/containers/parents/status/counts correct; her-sync geen duplicaten. |
| FAT-PRO-021 | P1 | INT | Portainer foutfixture | Ontbrekende URL/key, 401 endpoint en containerlistingfout. | Fout opgeslagen; geen half-onjuiste successstatus. |
| FAT-PRO-022 | P1 | INT | Proxmoxfixture | Test/sync node, QEMU en LXC. | Header correct; nodes/children/status/counts/metrics correct; her-sync upsert. |
| FAT-PRO-023 | P0 | INT | Proxmox 401fixture | Gebruik herkenbaar token-ID en geheimmarker. | Melding bevat user!token-ID maar nergens geheimmarker. |
| FAT-PRO-024 | P1 | INT | Matchingfixtures | Zelfde MAC, native container-ID, discovered hostname en manual hostname. | Matchingvolgorde expliciet→record→MAC→native ID→discovered host; manual host niet op naam gemerged. |

### 6.5 Topologie

| Test-ID | P | Laag | Voorwaarde | Stappen | Verwacht resultaat |
|---|---:|---|---|---|---|
| FAT-TOP-001 | P1 | UI/API | Basistestdata + providers | Open Topologie en vergelijk nodes/relaties/status/metrics met brondata. | Volledige consistente catalogus; internet→ONT; geen verweesde relatie. |
| FAT-TOP-002 | P1 | UI/API | Devicekabel, trunk, patchpanel en uplink | Controleer afgeleide relaties. | Entitykabel, apparaat-trunk en poortloze/wifi-relatie éénmaal; patchpanel doorgelopen. |
| FAT-TOP-003 | P2 | UI | Alle relatietypen | Schakel fysiek, virtueel en services afzonderlijk uit/in. | Alleen betreffende lijnen wijzigen; geen data- of layoutmutatie. |
| FAT-TOP-004 | P2 | UI | Node met metrics | Hover normale node; verlaat canvas; activeer editmode. | Tooltipwaarden correct; verdwijnt; geen tooltip tijdens edit. |
| FAT-TOP-005 | P1 | UI | Grote topologie | Pan, wielzoom, +/- tot grenzen en Passend. | Pan buiten node; 40–400%; cursorgericht; passend 100%; editdrag niet als pan. |
| FAT-TOP-006 | P1 | UI/API | Editmode | Klik node zonder bewegen; wijzig label/subtitel/lifecycle/collapsed/parent; herlaad en sync provider. | Editor opent; waarden persistent en niet overschreven. |
| FAT-TOP-007 | P0 | API | Parentketen A→B→C | Probeer A→A, A→C/descendant en onbekende parent. | 422/404; geen cyclus of gedeeltelijke update. |
| FAT-TOP-008 | P1 | UI/API | Editmode | Sleep één node >4 px en klik één node <4 px; verplaats een tweede node rechtstreeks via het single-position-endpoint. | UI-drag en single endpoint bewaren positie; klik opent editor en verplaatst niet; onbekend id geeft 404. |
| FAT-TOP-009 | P1 | UI/API | Editmode | Shift-selecteer drie nodes, deselecteer één en sleep selectie. | Selectiestatus correct; twee nodes bewegen met gelijke delta; derde blijft. |
| FAT-TOP-010 | P1 | API | Bulkposities | Stuur 1, 200, 201, ontbrekende x/y, niet-numeriek en onbekend id. | Geldige set opgeslagen; modelgrens/format 422; onbekende ids leiden niet tot corrupte data. |
| FAT-TOP-011 | P1 | UI/API | Editmode | Maak lege `FAT-Groep`; open en klap in/uit. | Groep zichtbaar; collapsed gedrag; eigenschappen persistent. |
| FAT-TOP-012 | P1 | UI/API | Twee geselecteerde nodes | Klik Selectie groeperen en maak groep. | Beide parents nieuwe groep; selectie leeg; groepering zichtbaar. |
| FAT-TOP-013 | P0 | API | Groep plus descendant | Test group-selection met self, onbekende node/groep en indirecte cirkel. | 422/404; parents ongewijzigd. |
| FAT-TOP-014 | P1 | UI/API | Groep met kinderen | Verwijder via zichtbare knop; annuleer en bevestig. | Annulering intact; groep weg; kinderen parent null; audit/history. |
| FAT-TOP-015 | P1 | UI/API | Twee nodes | Maak dependency/network/service/uplink relaties met labels. | Relaties zichtbaar in juiste laag/stijl; self-link 409; ontbrekende node/FK 409. |
| FAT-TOP-016 | P1 | UI/API | Handmatige relatie | Selecteer relatie en verwijder met zichtbare knop via muis en toetsenbord. | Betrouwbaar selecteerbaar; bevestiging; relatie weg; toast/audit/history. |
| FAT-TOP-017 | P0 | API | Afgeleide patchrelatie | Probeer delete-endpoint. | 409; afgeleide relatie intact. |
| FAT-TOP-018 | P1 | UI/API | Gewone physical/manual/discovered nodes | Open nodeactie en volg Verbergen/Bronobject verwijderen/Discoverybeheer. | Gedrag exact volgens hoofdstuk 2 punt 4; geen terugkerende verborgen/verwijderde node. |
| FAT-TOP-019 | P1 | UI/API | Handmatige posities + custom parents/labels | Klik Auto-indeling en bevestig resultaat. | Posities/manual flag reset; overige customisatie blijft; layout bruikbaar. |
| FAT-TOP-020 | P0 | UI/API | Reeks node/group/relation/move/reset/delete wijzigingen | Gebruik na iedere wijziging Ongedaan; test lege historie. | Iedere stap exact terug; beschrijving klopt; leeg geeft duidelijke 409. |
| FAT-TOP-021 | P1 | API | 55 topologiemutaties | Inspecteer historie en undo. | Alleen laatste 50 snapshots; geen onbeperkte groei. |
| FAT-TOP-022 | P0 | INT/API | Handmatige layout plus providersync | Sync alle fixtures tweemaal. | Geen verloren label/parent/positie/groep/relation; afgeleide data wel bijgewerkt. |
| FAT-TOP-023 | P1 | UI | Lifecycledata | Vergelijk active/planned/phase_out visueel in licht/donker. | Onderscheid herkenbaar zonder uitsluitend kleur; labels niet afgekapt tot onbruikbaar. |

### 6.6 DNS, speedtest, back-up en configuratie

| Test-ID | P | Laag | Voorwaarde | Stappen | Verwacht resultaat |
|---|---:|---|---|---|---|
| FAT-DNS-001 | P1 | UI/API | Ingelogd | Maak A, AAAA en CNAME met/zonder trailing dot en entity. | Types/waarden correct; trailing dot verwijderd; koppeling zichtbaar. |
| FAT-DNS-002 | P1 | API | DNS-formulier | Test lege naam/waarde, verkeerd type, TTL 29/30/86400/86401 en onbekende entity. | Model/FK-validatie; geen gedeeltelijke create/update. |
| FAT-DNS-003 | P1 | UI/API | Manual DNS | Bewerk; annuleer delete en bevestig. | Update persistent; annulering intact; delete weg/audit. |
| FAT-DNS-004 | P0 | UI/API | AdGuard DNS | Controleer read-only badge; probeer API update/delete. | Geen UI-acties; API 409; record intact. |
| FAT-DNS-005 | P1 | UI | NPM-fixture | Controleer proxyhostlijst en doelstatus. | Domeinen, URL-doel, status en koppeling correct; geen edit/delete. |
| FAT-SPD-001 | P1 | UI/API | Ingelogd | Wijzig enabled, interval, duur, server en interface; herlaad. | Waarden persistent; telemetry altijd false/0; audit. |
| FAT-SPD-002 | P1 | API | Settingsendpoint | Test intervallen 899/900/604800/604801 en duur 4/5/30/31. | Alleen grenzen geldig; data intact bij 422. |
| FAT-SPD-003 | P1 | INT/UI | Succesvolle CLI-fixture | Start Nu testen; inspecteer header, KPI's en historie. | Busyweergave; successrecord en parsing Mbps/ping/jitter/server/IP correct. |
| FAT-SPD-004 | P1 | INT/API | Langlopende CLI | Start twee runs. | Tweede busy; exact één running/success. |
| FAT-SPD-005 | P1 | INT/API | CLI ontbreekt/exit!=0/timeout/ongeldige JSON/leeg array | Start per fixture. | Failed met begrensde duidelijke fout; running gesloten; settings.last_error gezet. |
| FAT-SPD-006 | P1 | OPS/API | Running record | Herstart app. | Record wordt failed met herstartmelding. |
| FAT-SPD-007 | P2 | UI/API | Speedhistorie | Klik speedindicator en inspecteer 24/120 historiegrenzen. | Springt naar Topologiehistorie; grafiek/KPI/age correct; API max 120. |
| FAT-BCK-001 | P0 | UI/API/DB | Testdata + secret | Maak back-up via knop. | `.pmbackup`, integrity ok, manifest+DB+sleutel precies eenmaal, 0600 en lijstentry/audit. |
| FAT-BCK-002 | P1 | UI/API | Bestaande back-up | Download en valideer bestandsnaam/media-type/inhoud. | Exact bestand; portable zip of legacy sqlite; traversalnaam 400; onbekend 404. |
| FAT-BCK-003 | P0 | UI/API | Geldige portable/legacy files | Importeer beide. | Nieuwe unieke naam, valide metadata, 0600, geen actieve DB-mutatie. |
| FAT-BCK-004 | P0 | API | Ongeldige back-ups | Import corrupt, verkeerd manifest, ontbrekend/dubbel bestand, traversal, verkeerde sleutel en >512 MB. | 413/422; tempbestand opgeruimd; actieve DB/key intact. |
| FAT-BCK-005 | P0 | UI/API | Geldige portable backup A; actieve staat B | Annuleer restore; foute naam; daarna juiste restore. | Eerst intact; foute naam 422; juiste restore maakt safetybackup, zet A+key terug en reloadt veilig. |
| FAT-BCK-006 | P0 | API | Legacy DB met passende/niet-passende key | Restore beide varianten. | Passend werkt; verkeerde key 422 met instructie portable backup; actief systeem intact. |
| FAT-BCK-007 | P1 | OPS | Meer back-ups dan retentie | Trigger handmatig/automatisch en prune. | Nieuwste N blijven over volgens instelling; andere bestanden ongemoeid. |
| FAT-CFG-001 | P1 | UI/API | Volledige testconfig | Exporteer JSON; controleer format/version/tables/settings. | Alle bedoelde tabellen, alleen manual DNS, geen users/sessions/audit/secrets/provider secretwaarden. |
| FAT-CFG-002 | P0 | UI/API | Lege doel-DB + export | Importeer; vergelijk alle records, parents, peers en relaties. | Eerst safetybackup; volledige equivalente config; secrets niet verzonnen. |
| FAT-CFG-003 | P0 | API | Export met kind vóór parent, poort peers en topology parents willekeurig geordend | Importeer. | FK-relaties na tweede fase correct; geen orderafhankelijkheid. |
| FAT-CFG-004 | P0 | API | Ongeldig format/version/table/record/FK | Importeer per variant. | 422 en volledige transaction rollback; safetybackup bestaat; actieve configuratie onveranderd. |
| FAT-CFG-005 | P1 | API | Bestaande doelrecords met dezelfde IDs | Importeer gewijzigde export. | Upsert zonder duplicaten; expliciet gedocumenteerde merge semantics. |

### 6.7 Systeem, historie, beveiliging en deployment

| Test-ID | P | Laag | Voorwaarde | Stappen | Verwacht resultaat |
|---|---:|---|---|---|---|
| FAT-SYS-001 | P1 | API | App gestart | Vraag `/`, public settings, auth status, health en beschermde endpoints zonder login. | Index/titel/status/health toegankelijk; inventaris/mutaties 401; health versie 0.4.0. |
| FAT-SYS-002 | P0 | OPS | Nieuwe DB | Start app tweemaal. | Schema/user_version correct; seedinventaris/providers/topologie/speedsettings éénmaal; geen duplicaten. |
| FAT-SYS-003 | P0 | OPS/DB | Kopie van iedere ondersteunde oude schemaversie met data | Upgrade; vergelijk alle records/FK's/secrets. | Migratie zonder verlies, correcte constraints/ONT, herhalen idempotent. |
| FAT-SYS-004 | P0 | OPS/DB | DB user_version hoger dan app | Start app. | Start weigert duidelijk; databasebytes/schema/data niet gewijzigd. |
| FAT-SYS-005 | P0 | DB | Providersecretfixture | Inspecteer DB, bootstrap, configexport, logs en foutmeldingen. | Geen plaintext geheim; decryptie met juiste key; verkeerde key geeft veilige fout. |
| FAT-SYS-006 | P1 | DB | Verlopen en dubbele observaties | Run housekeeping. | Verlopen weg; per entity/provider/field nieuwste over; status zonder actuele observatie unknown. |
| FAT-SYS-007 | P1 | DB/API | Entities/metrics over meerdere 5-minutenslots | Record samples en vraag history. | Eén sample per slot/entity; memory% afgeleid; uptime/flips/dagen/API-volgorde correct. |
| FAT-SYS-008 | P1 | DB | Oude samples/dagen en >5000 auditregels | Prune. | Detail >48 uur, dagen >730 en oudste audit >5000 verwijderd; grensrecords behouden. |
| FAT-SYS-009 | P1 | INT/OPS | Enabled providers met verschillende intervals | Laat maintenancecycli lopen met tijdfixture. | Alleen due providers; fout geïsoleerd; tijden correct. |
| FAT-SYS-010 | P1 | INT/OPS | Speed auto enabled/due en disabled/not due | Laat maintenancecyclus lopen. | Alleen enabled+due start backgroundrun; geen overlap. |
| FAT-SYS-011 | P1 | OPS | Backupuur/tijdfixture | Laat meerdere 30s-cycli binnen dezelfde dag en volgende dag lopen. | Maximaal één automatische back-up per dag op uur; retentie toegepast. |
| FAT-SYS-012 | P1 | UI/API/DB | Reeks mutaties | Controleer audit UI en DB. | Actor/action/type/id/details/tijd correct; UI max 200 en nieuwste eerst. |
| FAT-SYS-013 | P0 | OPS | Docker build/start tijdelijke volumes | Inspecteer proces, mounts, rootfs, caps, tmpfs en health. | UID/GID 10001, read-only, NET_RAW/ping, volumes schrijfbaar, `/tmp` begrensd, healthy. |
| FAT-SYS-014 | P1 | OPS | Afwijkende `PATCH_PORT` | Start container. | Uvicorn en healthcheck gebruiken dezelfde poort; hostnetworking vereist geen `ports:`. |
| FAT-SYS-015 | P0 | OPS | Container met data | Restart/recreate/update image. | Inventaris, secrets, sessiebeleid en back-ups volgens volumes behouden; init zet alleen rechten. |
| FAT-SYS-016 | P1 | OPS | CI-workflowfixture | Simuleer main, tag gelijk aan versie en tag ongelijk. | Main publiceert latest+sha; geldige tag versie+sha zonder latest; mismatch stopt vóór push. |
| FAT-SYS-017 | P0 | INT | HTTP-capturefixtures voor alle providers | Sync/test alle adapters. | Uitsluitend GET/read-only authenticatiecalls, behalve NPM login-tokenrequest; geen provider-mutatie-endpoints. |
| FAT-SYS-018 | P1 | UI | Desktop + mobiel/touch + toetsenbord | Doorloop nav, dialogs, forms, drawers, patchen en topology-acties. | Geen onbereikbare functie; zichtbare focus; labels; targets bruikbaar; geen horizontaal verlies behalve bedoelde topology-scroll. |
| FAT-SYS-019 | P1 | UI | Licht/donker en statusvarianten | Controleer tekst/status/lifecycle/conflict/fouten. | Leesbaar contrast; betekenis niet uitsluitend kleur; statuslabels beschikbaar. |
| FAT-SYS-020 | P1 | API | Gelijktijdige niet-conflicterende en conflicterende mutaties | Parallel create/update/kabelacties. | SQLite busy-timeout/transactions voorkomen gedeeltelijke toestand; consistente 2xx/409/5xx-afhandeling. |

### 6.8 Verplichte logica- en gebruiksgemakcontrole per user story

Deze scripts zijn geen eenmalige algemene smoke. Ze worden voor **iedere toepasselijke `US-*`** herhaald en in het rapport gekoppeld aan die user story. Bijvoorbeeld: `FAT-UX-001 × US-TOP-07` controleert of relatie verwijderen daadwerkelijk vindbaar is.

| Test-ID | P | Laag | Voorwaarde | Stappen | Verwacht resultaat |
|---|---:|---|---|---|---|
| FAT-LOG-001 | P0 | UI/API/DB | Een toepasselijke user story | Leg vooraf de geldige beginstaat en verwachte eindstaat vast; voer het hoofdpad uit; vergelijk UI, response en DB. | Eindstaat voldoet volledig aan de domeinregel; alle drie lagen zijn gelijk en tellingen/afgeleide data kloppen. |
| FAT-LOG-002 | P0 | UI/API/DB | Functie met afhankelijkheden | Maak alle relevante afhankelijkheden; wijzig of verwijder het bronobject; controleer cascades, loskoppelingen en afgeleide records. | Geen verweesde, dubbel getelde of onverwacht verwijderde data; impactmelding voorspelde exact de uitkomst. |
| FAT-LOG-003 | P1 | UI/API | Herhaalbare functie | Voer dezelfde create/import/sync/save/deletehandeling tweemaal uit en herlaad tussen beide pogingen. | Gedocumenteerde idempotentie; geen onbedoelde duplicaten of tweede neveneffecten; herhaalde delete heeft veilige fout. |
| FAT-LOG-004 | P1 | UI/API/DB | Object met meerdere statussen | Doorloop alle toegestane en verboden toestandsovergangen. | Alleen geldige overgangen slagen; verboden overgang is duidelijk en atomair geweigerd. |
| FAT-LOG-005 | P1 | API/UI | Formulier of endpoint | Test leeg, null, minimum, maximum, net buiten grens, verkeerd type, onbekend id en strijdige combinatie. | Consistente validatie en foutcodes; geen 500; geen gedeeltelijke wijziging. |
| FAT-LOG-006 | P1 | API/DB | Muterende functie | Start twee gelijke en twee strijdige acties gelijktijdig; injecteer waar mogelijk een fout halverwege. | Transacties en locks leveren één verklaarbare eindstaat; geen half record of stille overschrijving. |
| FAT-LOG-007 | P1 | UI/API | Muterende/herstelbare functie | Voer mutatie uit; refresh en herstart; controleer audit; gebruik undo indien aangeboden. | Persistentie, auditdetails en undo sluiten exact aan op de zichtbare actie. |
| FAT-LOG-008 | P0 | INT/API | Provider-, auth- of read-onlyfunctie | Probeer de functie buiten rol-, CSRF-, origin- of providergrens te gebruiken. | Grens wordt server-side afgedwongen; handmatige waarheid en externe provider blijven beschermd. |
| FAT-UX-001 | P1 | UI | Een toepasselijke user story, tester kent code niet | Start op het logische hoofdscherm en zoek de functie zonder URL, DOM-inspectie of documentatie. | Primaire actie is door naam, plaatsing of context vindbaar; geen verborgen gebaar of pixelnauwkeurige klik vereist. |
| FAT-UX-002 | P1 | UI | Hoofdpad van user story | Tel stappen en beslissingen vanaf het logische startpunt; voer taak volledig uit. | Volgorde is logisch, essentiële keuzes staan bij elkaar, defaults zijn veilig en er zijn geen onnodige omwegen/dubbele invoer. |
| FAT-UX-003 | P1 | UI | Langlopende of muterende actie | Start de actie en observeer selectie, disabled/busy-state, voortgang, succes en eindstaat. | Geen twijfel of dubbelklikrisico; feedback verschijnt tijdig en benoemt het resultaat concreet. |
| FAT-UX-004 | P1 | UI | Foutfixture per functie | Veroorzaak validatie-, conflict-, netwerk-, autorisatie- en serverfout. | Melding staat in context, is begrijpelijk, bevat herstelactie en lekt geen technische geheimen; invoer blijft waar nuttig behouden. |
| FAT-UX-005 | P1 | UI | Destructieve of overschrijvende functie | Open actie; lees impact; annuleer; heropen en bevestig met juiste/foute bevestigingswaarde. | Impact is specifiek, annuleren volledig veilig, bevestiging proportioneel en succes duidelijk; geen browserprompt als een rijk impactoverzicht nodig is. |
| FAT-UX-006 | P1 | UI | Dialog, wizard, drawer of editmode | Gebruik Sluiten, Annuleren, Terug, Escape, backdrop en herstel/undo waar aangeboden. | Geen onverwachte save; terugkeerpositie en selectie zijn logisch; gebruiker raakt niet opgesloten of verdwaald. |
| FAT-UX-007 | P1 | UI | Lege, ladende, disabled, gedeeltelijke en fouttoestandfixtures | Open ieder toestandstype voor de functie. | Iedere toestand legt uit wat er aan de hand is en, indien mogelijk, wat de volgende stap is; layout springt niet onnodig. |
| FAT-UX-008 | P1 | UI | Toetsenbord | Bedien functie met Tab/Shift+Tab/Enter/Spatie/Escape; controleer focusvolgorde, focusbehoud en namen. | Alle acties bereikbaar; focus zichtbaar en logisch; iconbuttons/graphics hebben naam; dialog houdt focus en geeft die terug. |
| FAT-UX-009 | P1 | UI/touch | Mobiele viewport en touch | Bedien dezelfde functie met touch, rotatie en vergrote tekst; controleer targets en scroll. | Geen essentiële hover-only actie; targets bruikbaar; geen overlappende of onbereikbare controls; data blijft leesbaar. |
| FAT-UX-010 | P2 | UI | Hele taakflow | Vergelijk labels, knoppen, bevestigingen en fouttermen met dezelfde objecten elders. | Consequente Nederlandse termen en werkwoorden; geen wisseling tussen technisch id, bronterm en gebruikerstaal zonder uitleg. |
| FAT-UX-011 | P2 | UI | Vergelijkbare CRUD- en selectieflows | Vergelijk knopplaatsing, kleur, iconen, formulieropbouw en bevestigingspatroon. | Vergelijkbare handelingen gedragen zich voorspelbaar; gevaarlijke en primaire acties zijn consistent onderscheiden. |
| FAT-UX-012 | P2 | UI/API | Normale en grotere testdataset | Meet zichtbare reactie op openen, zoeken, opslaan, refresh en render; observeer blokkering. | Directe feedback binnen circa 100 ms; normale actie voelt niet vastgelopen; langere actie toont busy/progress; grote dataset blijft praktisch bedienbaar. |

## 7. API-traceerbaarheid

Deze tabel bewijst dat iedere backendroute minimaal één testscripteigenaar heeft.

| Route | Tests |
|---|---|
| `GET /health` | FAT-SYS-001, FAT-SYS-013 |
| `GET /` | FAT-SYS-001 |
| `GET /api/public/settings` | FAT-GEN-004, FAT-SYS-001 |
| `GET /api/auth/status` | FAT-ACC-001, FAT-SYS-001 |
| `POST /api/auth/setup` | FAT-ACC-001–003 |
| `POST /api/auth/login` | FAT-ACC-004–006, FAT-ACC-010 |
| `POST /api/auth/logout` | FAT-ACC-008 |
| `GET /api/bootstrap` | FAT-GEN-002, FAT-PAT-001, FAT-SYS-001 |
| `GET /api/summary` | FAT-GEN-003, FAT-PAT-001 |
| `GET /api/physical-devices/{id}/history` | FAT-PAT-024–025 |
| `GET /api/entities/{id}/history` | FAT-PAT-026, FAT-SYS-007 |
| `GET/PATCH /api/wizard/info` | FAT-WIZ-001–003 |
| `POST /api/entities/import-csv` | FAT-PAT-029–030 |
| `GET /api/search` | FAT-GEN-006–008 |
| `GET /api/changes` | FAT-DIS-001 |
| `GET /api/discoveries` | FAT-WIZ-009–011 |
| `POST /api/entities/{id}/promote` | FAT-DIS-002, FAT-WIZ-009 |
| `POST /api/entities` | FAT-PAT-002–003 |
| `PATCH /api/entities/{id}` | FAT-PAT-002–005 |
| `PUT /api/entities/{id}/uplink` | FAT-PAT-021–023 |
| `PATCH /api/entities/{id}/discovery-state` | FAT-DIS-003, FAT-WIZ-009 |
| `POST /api/entities/{id}/merge` | FAT-DIS-004–006 |
| `GET /api/entities/{id}/deletion-impact` | FAT-PAT-006–007 |
| `DELETE /api/entities/{id}` | FAT-PAT-006–007 |
| `POST /api/physical-devices` | FAT-PAT-008 |
| `PATCH /api/physical-devices/{id}` | FAT-PAT-008–010 |
| `GET /api/physical-devices/{id}/deletion-impact` | FAT-PAT-011 |
| `DELETE /api/physical-devices/{id}` | FAT-PAT-011–012 |
| `PATCH /api/ports/{id}` | FAT-PAT-013 |
| `GET /api/ports/{id}/trace` | FAT-PAT-014, FAT-PAT-027–028 |
| `POST /api/cables` | FAT-PAT-014–016 |
| `DELETE /api/cables/{id}` | FAT-PAT-018 |
| `PUT /api/ports/{id}/cable` | FAT-PAT-014–017, FAT-PAT-021 |
| `DELETE /api/ports/{id}/cable` | FAT-PAT-018 |
| `POST/PATCH/DELETE /api/app-links...` | FAT-APP-001–005 |
| `POST /api/providers` | FAT-PRO-001, FAT-PRO-003 |
| `DELETE /api/providers/{id}` | FAT-PRO-002 |
| `PATCH /api/providers/{id}` | FAT-PRO-001, FAT-PRO-004–005, FAT-WIZ-008 |
| `PATCH /api/settings` | FAT-GEN-004 |
| `POST /api/providers/{id}/test` | FAT-WIZ-006–007, FAT-PRO-006, FAT-PRO-012–023 |
| `POST /api/providers/{id}/sync` | FAT-WIZ-008, FAT-PRO-007–024 |
| `PATCH /api/provider-records/{id}/mapping` | FAT-DIS-007 |
| `POST /api/conflicts/{id}/resolve` | FAT-DIS-008 |
| `GET /api/backups` | FAT-BCK-001 |
| `POST /api/backups` | FAT-BCK-001 |
| `GET /api/backups/{name}/download` | FAT-BCK-002 |
| `POST /api/backups/import` | FAT-BCK-003–004 |
| `POST /api/backups/{name}/restore` | FAT-BCK-005–006 |
| `GET /api/config/export` | FAT-CFG-001 |
| `POST /api/config/import` | FAT-CFG-002–005 |
| `PATCH /api/topology/nodes/{id}` | FAT-TOP-006–007 |
| `PATCH /api/topology/nodes/{id}/position` | FAT-TOP-008 |
| `PATCH /api/topology/positions` | FAT-TOP-009–010 |
| `POST /api/topology/groups` | FAT-TOP-011–012 |
| `PATCH /api/topology/group-selection` | FAT-TOP-013 |
| `POST /api/topology/relations` | FAT-TOP-015 |
| `DELETE /api/topology/relations/{id}` | FAT-TOP-016–017 |
| `POST /api/topology/layout/reset` | FAT-TOP-019 |
| `DELETE /api/topology/groups/{id}` | FAT-TOP-014 |
| `POST /api/topology/undo` | FAT-TOP-020–021 |
| `GET /api/speedtest` | FAT-SPD-003, FAT-SPD-007 |
| `POST /api/speedtest/run` | FAT-SPD-003–006 |
| `PATCH /api/speedtest/settings` | FAT-SPD-001–002 |
| `POST/PATCH/DELETE /api/dns-records...` | FAT-DNS-001–004 |

## 8. Uitvoervolgorde na akkoord

1. Bevries commit en maak geïsoleerde ENV-01/ENV-02 aan.
2. Voer P0 API- en dataconsistentietests uit.
3. Voer P1 UI-happy- en foutpaden uit.
4. Voer alle providerfixtures uit.
5. Voer back-up/restore en migraties uit op kopieën.
6. Voer Docker-, browser-, touch-, print- en toegankelijkheidssmokes uit.
7. Hertoets mislukte cases éénmaal op een schone omgeving.
8. Lever een rapport met per Test-ID: Pass/Fail/Blocked/Not run, bewijs, bevinding, ernst en reproduceerstappen.

## 9. Goedkeuringsblok

Akkoord kan worden gegeven met:

> **AKKOORD FAT-PLAN 0.4.0** — voer alle tests uit volgens dit document, inclusief de verwijdersemantiek uit hoofdstuk 2 punt 4, uitsluitend op geïsoleerde testdata.

Afwijkingen kunnen bij het akkoord per Test-ID of hoofdstuk worden genoemd. Zonder deze expliciete akkoordtekst blijft de uitvoering gestopt.
