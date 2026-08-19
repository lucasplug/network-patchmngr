# Network Patch Manager

Lokale webapp voor handmatige fysieke patchadministratie en een automatisch verrijkte homelabtopologie.

De kernregel is technisch afgedwongen: providers kunnen status, IP-adressen, hostnames en virtuele inventaris observeren, maar kunnen handmatige namen, poortkeuzes en kabelinformatie niet overschrijven. Afwijkingen worden als conflict opgeslagen.

## Functies

- **Setup-wizard** — na het aanmaken van de beheerder loodst een wizard je in vier stappen door de eerste inrichting: netwerk scannen, databronnen koppelen (met testverbinding vóór opslaan), gevonden apparaten in bulk toewijzen en een slotoverzicht. Herstartbaar via **Admin → Setup-wizard**; het bulkscherm is ook los bereikbaar voor apparaten die later verschijnen.
- **Patch** — elk fysiek apparaat wordt getekend als poortenrij met live status en kabelkleur per poort. Koppelen kan door slepen (muis of touch), of muisloos door een device aan te klikken en een poort te kiezen.
- **Kabels en patchpanelen** — een kabel is een record tussen twee uiteinden: poort↔poort of poort↔device. Patchpanelen krijgen gepaarde voor- en achterpoorten, zodat een verbinding via een paneel als één trace zichtbaar blijft (switch → paneel → wandcontactdoos → device).
- **Uptime en live stats** — per device een uptime-balk over 30 dagen met percentage en aantal statuswissels, plus sparklines van cpu, geheugen en respons over de laatste 48 uur. De historie heeft een harde retentiegrens (48 uur fijn, 730 dagen dagtotalen) en blijft daarmee onder ~6 MB.
- **Veilig verwijderen** — handmatige devices en fysieke netwerkapparaten tonen eerst hun afhankelijkheden. Poortkoppelingen en topologierelaties worden opgeruimd; DNS/proxykoppelingen blijven als losgekoppelde records behouden.
- **Inventarisbeheer** — handmatige devices en fysieke netwerkapparaten kunnen worden bewerkt; het poortaantal kan veilig groeien of krimpen zolang te verwijderen poorten vrij zijn.
- **Topologie** — geneste hosts/VM's/containers/services, fysieke en virtuele relaties, status en live metrics. Pan met slepen, zoom met het scrollwiel of de knoppen; elke node toont één kerngetal (cpu of respons). In bewerkmodus kun je nodes verslepen, groeperen, hernoemen, plannen en handmatige relaties tekenen.
- **DNS & reverse proxy** — handmatige A/AAAA/CNAME-records, read-only import van AdGuard Home-rewrites en Nginx Proxy Manager-hosts, inclusief koppeling aan bekende devices en services.
- **Speedtest** — LibreSpeed CLI in de container, automatische historie en download/upload/ping permanent bovenin. Telemetry staat technisch uit.
- **Admin** — providerconfiguratie, handmatige synchronisatie, ongekoppelde discoveries, conflicten, DNS, proxyhosts, speedtestinstellingen en back-ups.
- **Discoverybeheer** — discoveries negeren, archiveren, herstellen of samenvoegen met een bestaand device; providerrecords kunnen ook expliciet worden gekoppeld en ontkoppeld.
- **Uitwisseling & herstel** — SQLite-back-ups downloaden, importeren en terugzetten, plus configuratie exporteren/importeren als JSON. Voor restore/import wordt automatisch een veiligheidsback-up gemaakt.
- **Audit & undo** — de laatste 200 beheeracties zijn zichtbaar; topologiewijzigingen hebben een server-side undo-geschiedenis van maximaal 50 stappen.
- Eerste-run beheerder, scrypt-wachtwoordhashing, server-side sessies, HttpOnly-cookie en CSRF-controle.
- SQLite in WAL-modus met auditlog en consistente online-back-ups.
- Read-only adapters voor DHCP/ARP, Uptime Kuma, Glances, Portainer, Proxmox VE, AdGuard Home en Nginx Proxy Manager, elk met een testverbinding die vóór opslaan laat zien wat hij zou vinden.
- Vendorherkenning van gevonden apparaten via een lokaal IEEE OUI-bestand in de image — geen enkele externe lookup.

## Starten via Portainer (aanbevolen)

De image wordt bij elke push naar `main` automatisch gepubliceerd naar
`ghcr.io/lucasplug/network-patchmngr:latest`. In Portainer hoef je dus niets te
bouwen: **Stacks → Add stack → Web editor**, plak de inhoud van
[`docker-compose.portainer.yml`](docker-compose.portainer.yml) en pas de
omgevingsvariabelen aan (in elk geval `PATCH_TRUSTED_SUBNETS`).

Is het GHCR-pakket privé, zet het dan eenmalig op *Public* onder
**GitHub → Packages → network-patchmngr → Package settings**, of geef Portainer
een registry-token mee.

## Starten met Docker Compose

```bash
cp .env.example .env
```

Start daarna de applicatie. De beveiligingssleutel wordt bij de eerste start automatisch in het blijvende datavolume gemaakt:

```bash
docker compose up -d --build
```

Open `http://<docker-vm-ip>:8080`. Is die poort op de VM al bezet, zet dan `PATCH_PORT` op een vrije poort — de app én de healthcheck volgen die variabele. Host networking negeert `ports:`, dus dit is de enige manier om de poort te wijzigen. De eerste bezoeker maakt het eenmalige beheeraccount aan. Daarna verdwijnt de setup-route automatisch. De container gebruikt host networking op de Linux Docker-VM, zodat DHCP/ARP-discovery de LAN-burentabel kan gebruiken; de gekozen poort moet daarom vrij zijn op die VM.

De zichtbare applicatietitel wijzig je onder **Admin → Applicatie**. Zo kan dezelfde openbare code onder een eigen naam worden gebruikt. Providergeheimen staan niet in de repository of configuratie-export. Admin-back-ups zijn draagbare `.pmbackup`-pakketten met de database en bijbehorende encryptiesleutel. Behandel zo'n bestand daarom als een wachtwoord en bewaar het versleuteld.

Bij een upgrade vanaf een oudere versie zet een korte, netwerkloze initialisatiestap automatisch de juiste rechten op het bestaande datavolume. Bestaande `.db`-bestanden blijven in de lokale map `./backups` staan en kun je via **Admin → Back-ups → Importeren** blijven gebruiken. Nieuwe back-ups worden in het afgeschermde Docker-volume `patch-backups` bewaard.

### HTTPS

Logingegevens zijn op onbeveiligd HTTP zichtbaar voor iemand die het LAN-verkeer kan onderscheppen. Plaats de app daarom bij voorkeur achter Caddy, Traefik of Nginx met een intern certificaat en zet vervolgens:

```env
PATCH_SESSION_SECURE=true
```

## Beveiligingsnotities

- **Achter een reverse proxy**: de login-rate-limiting werkt per client-IP. Uvicorn vertrouwt `X-Forwarded-For` standaard alleen vanaf `127.0.0.1`. Draait de proxy op een andere host, geef dan `--forwarded-allow-ips` met het proxy-IP mee aan uvicorn; anders delen alle gebruikers achter de proxy één limiet.
- **Netwerktoegang beperken**: `PATCH_TRUSTED_SUBNETS` begrenst actieve discovery-scans, maar is geen toegangsfilter voor de webinterface. Beperk webtoegang via de firewall van de Docker-VM of via de reverse proxy.
- **Containerrechten**: de app draait als niet-root met alleen `NET_RAW` voor ICMP-discovery. Ping krijgt die capability via een file capability op het binary; daarom staat `no-new-privileges` bewust niet op de app-container (dat zou file capabilities bij execve blokkeren). Host networking blijft nodig om de LAN-burentabel te kunnen lezen; gebruik daarom een dedicated, vertrouwde Docker-VM.

## Providers configureren

Providers staan bij de eerste start uit. Vul in **Admin → Configureren** de URL's en inloggegevens in en zet de provider daarna aan. Inloggegevens worden met een automatisch gemaakte sleutel versleuteld opgeslagen; de interface geeft alleen aan welke velden zijn ingesteld.

| Provider | Inloggegevens in Admin | Opmerking |
|---|---|---|
| DHCP/ARP | geen | Leest de ARP-tabel en pingt geconfigureerde `/24`-subnets; maximaal 1024 adressen per subnet |
| Uptime Kuma | geen bij publieke statuspagina | Gebruikt statuspagina- en heartbeat-endpoints; de slug van de statuspagina hoort erbij |
| Glances | gebruikersnaam en wachtwoord | Ondersteunt meerdere API-v4-endpoints |
| Portainer | API-key | Gebruik een aparte gebruiker met minimale environmentrechten |
| Proxmox | API-tokengeheim | Gebruik een read-only API-token; vul ook de gebruiker en het token-ID in |
| AdGuard Home | gebruikersnaam en wachtwoord | Importeert clients en DNS-rewrites via `/control/clients` en `/control/rewrite/list` |
| Nginx Proxy Manager | API-token of gebruikersnaam en wachtwoord | Importeert proxyhosts; de adapter schrijft niets terug naar NPM |

De meegeleverde configuratie bevat alvast de adressen uit het ontwerp voor Docker VM (`192.168.1.12`) en Proxmox (`192.168.1.100`). Controleer die voordat je providers inschakelt.

Proxmox stelt zijn tokenkop samen uit drie delen: `PVEAPIToken=<gebruiker>!<token-ID>=<geheim>`. In Proxmox staat dat onder *Datacenter → Permissions → API Tokens* als bijvoorbeeld `root@pam!patchmanager`. De wizard vraagt de eerste twee los uit; alleen het geheim wordt versleuteld opgeslagen.

Bij zelfondertekende certificaten kan `verify_tls` tijdelijk op `false`. Een eigen lokale CA en `true` is veiliger.

AdGuard- en NPM-data zijn geïmporteerde observaties: wijzigingen doe je in de bron. Handmatige DNS-records kun je volledig in Plugnet beheren. NPM-doelen worden op IP, hostname of naam aan bestaande entities gekoppeld; de proxyhost verschijnt als geneste service in de topologie.

## Topologie en relaties

- Een fysieke poortkoppeling tekent automatisch een fysieke relatie.
- Proxmox, Portainer en Nginx Proxy Manager leveren automatisch parent/child-relaties wanneer de bron die informatie kent.
- Relaties die niet betrouwbaar zijn af te leiden — bijvoorbeeld de onderlinge bekabeling van Deco-units zonder SNMP — teken je zelf in **Topologie → Bewerken → Relatie**.
- Handmatige posities, groepen en parents blijven behouden bij een volgende providersynchronisatie.
- In bewerkmodus selecteer je meerdere nodes met shift-klik. De selectie kan samen worden versleept of direct in een nieuwe groep worden geplaatst.
- Handmatige groepen zijn verwijderbaar; kinderen worden daarbij uit de groep gehaald. Met **Ongedaan** herstel je de laatste topologiewijziging.

## Discoveries en bronkoppelingen

- **Negeren** verbergt ruis uit de actieve discoverylijst en topologie, maar laat providersynchronisatie intact.
- **Archiveren** bewaart een discovery als inactief historisch item.
- **Herstellen** maakt een genegeerd of gearchiveerd item weer actief.
- **Samenvoegen** verhuist providerrecords, observaties, DNS/proxykoppelingen en eventuele fysieke koppeling naar het gekozen doeldevice.
- Onder **Expliciete bronkoppelingen** kan ieder afzonderlijk providerrecord handmatig aan een entity worden gekoppeld of losgemaakt.

## Configuratie en herstel

Configuratie-export bevat inventaris, poorten, providers zonder secrets, DNS, topologie en speedtestinstellingen. Import voegt records op stabiele ID samen en maakt eerst een back-up. Een volledige SQLite-restore vervangt de database; daarom wordt ook daar eerst een veiligheidskopie gemaakt en kan de huidige sessie daarna verlopen.

## Speedtest

De Docker-image bouwt `librespeed-cli` mee. Standaard wordt elke zes uur vanaf de Docker-VM getest; dit is dus de internetsnelheid gezien vanaf die VM. Via **Admin → Internetspeedtest** stel je interval, testduur, server en netwerkinterface in. Een test kan ook direct vanuit de topologie worden gestart.

## Back-ups

- Dagelijks op het uur uit `PATCH_BACKUP_SCHEDULE_HOUR` (standaard 03:00).
- Consistente SQLite online-back-up, gevolgd door `PRAGMA integrity_check`.
- Standaard 14 dagelijkse kopieën; instelbaar met `PATCH_BACKUP_RETENTION_DAILY`.
- Bestanden staan in het blijvende Docker-volume `patch-backups` en zijn via Admin te downloaden.
- Een handmatige back-up kan vanuit Admin worden gestart.

Neem de volumes `patch-data` en `patch-backups` op in de normale Proxmox/VM-back-upstrategie. Back-ups staan nooit in het actieve databasevolume.

## Lokaal ontwikkelen

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn patch_manager.main:app --reload --port 8080
```

Tests:

```bash
.venv/bin/pytest -q
```

## Datamodel

- `physical_devices`, `ports` en `port_assignments`: handmatige fysieke waarheid.
- `entities`: handmatige en ontdekte hosts, devices, VM's, LXC's, containers en services.
- `provider_records` en `observations`: tijdgebonden providerdata.
- `conflicts`: afwijkingen tussen handmatige identiteit en observaties.
- `topology_nodes` en `topology_relations`: persistente indeling, groepen en afgeleide/handmatige relaties.
- `dns_records` en `proxy_hosts`: handmatige records en geïmporteerde AdGuard/NPM-data.
- `speedtest_settings` en `speedtest_runs`: planning en lokale meetgeschiedenis.
- `audit_log`: beheerwijzigingen.

Containers en VM's worden niet samengevoegd met hun host. Ze blijven aparte entities met een `parent_id`, zodat de topologie een zuivere virtuele laag kan tekenen.

Geïmporteerde entities worden niet lokaal verwijderd: verwijder of deactiveer ze in hun provider. Handmatig aangemaakte entities zijn verwijderbaar vanuit **Patch → Handmatige devices**. Ook de vooraf ingestelde switches en Deco-units zijn blijvend verwijderbaar en worden na een herstart niet opnieuw aangemaakt.

## Bekende hardwarebeperking

De TP-Link SG108E en Deco XE75 Pro hebben in deze app geen automatische poortprovider. Hun fysieke poorten en aansluitingen blijven handmatig. Algemene bereikbaarheid kan wel via DHCP/ARP of Uptime Kuma worden verrijkt.
