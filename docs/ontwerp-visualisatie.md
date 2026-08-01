# Ontwerp — levend homelab-overzicht (fase 0)

*1 augustus 2026 · basis: `main` v0.3.0 · vervangt de prioritering uit `research-en-roadmap.md`*

Uitgangspunten uit de opdracht: één gebruiker, ~40 entities, geen productiedata (schema mag in één keer goed, datavolume is weggooibaar), harde retentiegrens op historie, simpel wint van generiek.

---

## 1. Doelschema (fase 1, in één keer)

### Wat vervalt of verandert

| Tabel | Wat |
|---|---|
| `port_assignments` | **Vervalt.** Vervangen door `cables` (een assignment was een kabel met maar één zichtbaar uiteinde). |
| `ports` | Krijgt `side TEXT NOT NULL DEFAULT 'front'` (`front`/`rear`) en `peer_port_id` (1:1-koppeling front↔rear binnen een patchpanel). Voor switches/AP's blijft alles `front` met `peer_port_id NULL`. |
| `entities` | Krijgt `vendor TEXT` (OUI-lookup, alleen gevuld bij discoveries met MAC). |
| `topology_relations` (`source='patch'`) | Blijft, maar wordt afgeleid uit de kabeltrace (device-poort → eind-entity) in plaats van uit assignments. |
| `observations` | Blijft zoals hij is (TTL-gedreven statusreset). Sparkline-data komt níét hieruit maar uit `entity_samples`. |

### Nieuw

```sql
CREATE TABLE cables (
  id TEXT PRIMARY KEY,
  a_port_id   TEXT NOT NULL REFERENCES ports(id) ON DELETE CASCADE,
  -- Precies één van beide: het andere uiteinde is een poort óf een eind-device.
  b_port_id   TEXT REFERENCES ports(id) ON DELETE CASCADE,
  b_entity_id TEXT REFERENCES entities(id) ON DELETE CASCADE,
  label TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL, updated_by TEXT REFERENCES users(id),
  CHECK ((b_port_id IS NULL) != (b_entity_id IS NULL)),
  CHECK (b_port_id IS NULL OR a_port_id != b_port_id)
);
-- Eén kabel per poort en (bewust, zoals nu) één kabel per entity.
CREATE UNIQUE INDEX cables_a ON cables(a_port_id);
CREATE UNIQUE INDEX cables_b_port ON cables(b_port_id) WHERE b_port_id IS NOT NULL;
CREATE UNIQUE INDEX cables_b_entity ON cables(b_entity_id) WHERE b_entity_id IS NOT NULL;
-- "Poort bezet?" moet beide kolommen checken (a_port_id én b_port_id);
-- dat doet het endpoint, met deze indexen als vangnet binnen één kolom.

CREATE TABLE entity_samples (          -- fijne resolutie, laatste 48 uur
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  sampled_at TEXT NOT NULL,            -- ISO, 5-minutenraster
  status TEXT NOT NULL,
  cpu_percent REAL, memory_percent REAL, latency_ms REAL,
  PRIMARY KEY(entity_id, sampled_at)
);

CREATE TABLE entity_days (             -- dag-aggregaten, max 730 dagen
  entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  day TEXT NOT NULL,                   -- YYYY-MM-DD
  samples_total INTEGER NOT NULL, samples_up INTEGER NOT NULL,
  flips INTEGER NOT NULL DEFAULT 0,    -- statuswissels die dag
  last_change_at TEXT,
  PRIMARY KEY(entity_id, day)
);
```

**Patchpanel:** `physical_devices.type='patch_panel'`; aanmaken met N poorten genereert N front- én N rear-poorten, paarsgewijs via `peer_port_id`. Bewust simpeler dan NetBox (1:1 in plaats van N:1 met posities) — voor koperpanelen is 1:1 de werkelijkheid.

**Kabeltrace** (code, geen SQL-recursie nodig): volg vanaf een device-poort de kabel; kom je op een poort uit, spring dan via `peer_port_id` naar de andere zijde en volg de volgende kabel; stop bij een entity-uiteinde of een los eind. Dieptelimiet 10 hops.

**Sampling & vulling:** `_maintenance_housekeeping` (draait per 30 s) schrijft eens per 5 minuten per entity één rij in `entity_samples` (status uit `entities`, cpu/mem/latency uit de laatste `provider_records`-payload via de bestaande `entity_metrics`-logica) en werkt de `entity_days`-rij van vandaag bij. Opruimen gebeurt in dezelfde tick.

## 2. Retentiebudget (harde grens, vóór de bouw)

| Tabel | Volume bij 40 entities | Grootte (±100 B/rij incl. index) | Grens |
|---|---|---|---|
| `entity_samples` | 40 × 12/uur × 48 uur = **23.040 rijen, constant** | ≈ 2,5 MB, constant | `DELETE WHERE sampled_at < now-48h` per housekeeping-tick |
| `entity_days` | 40 × 365 = **14.600 rijen/jaar** | ≈ 1,5 MB/jaar | cap **730 dagen** → max 29.200 rijen ≈ 3 MB |
| Totaal historie | | **< 6 MB, plafond onafhankelijk van looptijd** | |

Ter vergelijking: Uptime Kuma bewaart ruwe heartbeats onbegrensd en eindigt bij gigabytes. Hier bestaat er per ontwerp geen rij ouder dan 48 uur op sample-niveau.

## 3. Wizard-flow (fase 2) — vier schermen

Geen server-side wizardstatus: elke stap doet gewone, losse mutaties via de normale API, dus afbreken laat nooit halve toestand achter. Herstartbaar via een knop in Admin. Na `auth/setup` opent hij automatisch zolang `app_meta.wizard_dismissed` niet gezet is.

| Stap | Doet | Endpoints (bestaand tenzij *nieuw*) |
|---|---|---|
| **1. Scannen** | Subnet-invoer, vooringevuld met het subnet van de Docker-VM (*nieuw veld `suggested_subnet` in een klein `GET /api/wizard/info`*; moet binnen `PATCH_TRUSTED_SUBNETS` liggen — foutmelding van de bestaande validatie wordt getoond). Start scan; resultaten verschijnen live omdat `_store_record` per record commit (frontend pollt tijdens de scan elke 2 s *nieuw `GET /api/discoveries`*: id, naam, ip, mac, **vendor**, hostname, first_seen). Banner: "alleen apparaten die aan staan en recent verkeer hadden". | `PATCH /api/providers/dhcp-arp` (subnets+scan aan), `POST /api/providers/dhcp-arp/sync` |
| **2. Providers** (overslaanbaar, per provider overslaanbaar) | Per provider URL+credentials, **Test verbinding**-knop vóór opslaan: *nieuw `POST /api/providers/{id}/test`* — accepteert config+credentials zonder op te slaan, doet per adapter één lees-call en vat samen ("Proxmox: 1 node, 4 VM's, 6 LXC's"). Bij opslaan: bestaande `PATCH` + directe `sync`. | `PATCH /api/providers/{id}`, `POST /api/providers/{id}/sync`, *`POST /api/providers/{id}/test`* |
| **3. Toewijzen** (bulk) | Eén tabel met álle open discoveries (stap 1 + 2). Per rij: **Later** (default, doet niets) / **Overnemen** / **Samenvoegen met…** / **Negeren**. Overnemen = *nieuw `POST /api/entities/{id}/promote`*: origin wordt `manual` (naam/type worden van jou; status blijft geobserveerd — dat doet `_store_record` op manual entities al), providerkoppeling blijft via `provider_records`. Bij overnemen verschijnt inline een apparaat/poort-dropdown → `POST /api/cables`. Eén "Toepassen"-knop voert de aangevinkte keuzes uit. | `POST /api/entities/{id}/merge`, `PATCH /api/entities/{id}/discovery-state`, *`POST /api/entities/{id}/promote`*, *`POST /api/cables`* |
| **4. Klaar** | Telling aangemaakt/samengevoegd/genegeerd/open, links naar patchview en topologie. Client-side samenvatting, geen endpoint. | — |

Het bulk-scherm van stap 3 is een gewone view die ook los uit Admin opent ("Nieuwe apparaten toewijzen") — de wizard rendert hem alleen als stap.

## 4. Wireframes (tekst)

**Patchview — apparaatfronten** (klik poort → drawer; kleurstreep = kabelkleur; bolletje = live status van het getraceerde eind-device):

```
┌─ TP-Link SG108E 01 · switch · Kast ────────────────────────────┐
│  ▢1●  ▢2●  ▢3○  ▢4·   ▢5·   ▢6·   ▢7·   ▢8●                    │
│  ▔▔blauw▔geel                                    ▔▔rood        │
│  1 NAS↑  2 Deco-01↑  3 Printer?  4-7 vrij  8 → Panel-A:p3      │
└────────────────────────────────────────────────────────────────┘
┌─ Patchpanel A · patch_panel ── front ──────────────────────────┐
│  ▢1●  ▢2·  ▢3●  …    (rear-zijde uitklapbaar eronder)          │
└────────────────────────────────────────────────────────────────┘
Zijlijst rechts: "Niet-gekoppelde devices" → sleep op een poort.
< 768 px: elke poort wordt een kaartrij (poortnr · device · status), ≥44 px hoog.
```

**Topologie** (bestaande SVG + pan/zoom, geen herbouw):

```
                    [ internet ]
                         │
                  [ router · Deco ]───────────┐
                   │       │                  │ (virtueel, gestippeld)
   [SG108E-01]──[NAS ● 42d 99,8%]      [docker-vm ● cpu 31%]
        │                                   ├─[jellyfin ●]
   [SG108E-02]                              └─[adguard ●]
Node: statusbolletje + één kerngetal (cpu% voor hosts, uptime% voor devices,
ping voor services). Groepen blijven compound-kaders. Undo-knop blijft.
```

**Entity-drawer** (klik op node of poort):

```
┌─ NAS · nas · handmatig ─────────────────────────── × ─┐
│ ● up · 192.168.1.20 · aa:bb:… · vendor: Synology      │
│ Uptime 30d  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▁▇▇▇▇▇▇▇▇  99,2 %    │
│              (dagblokjes uit entity_days, rood=uitval)│
│ Laatste 48 u:  cpu ▁▂▁▁▃▂▁  mem ▅▅▅▅▅▅▅  ping ▁▁▂▁    │
│              (sparklines uit entity_samples)          │
│ Kabel: SG108E-01 p1 ──(blauw · C-001)── NAS           │
│ Trace: SG108E-01 p8 → Panel-A f3 ⇄ r3 → wandcontact…  │
│ Bronnen: dhcp-arp · glances     [Bewerken] [Poort…]   │
└───────────────────────────────────────────────────────┘
```

## 5. Live metrics per entity-type (bestaande adapters)

| Entity-type | Metric | Bron (raw-veld) | Status |
|---|---|---|---|
| host (Glances) | cpu %, mem used/total, uptime | `/quicklook`.cpu, `/mem`.used/.total, `/system`.uptime | **Geverifieerd** — adapter haalt dit al op, `entity_metrics` ontsluit het al |
| host/VM/LXC (Proxmox) | cpu %, mem/maxmem, disk/maxdisk, uptime | `cluster/resources` velden `cpu,mem,maxmem,disk,maxdisk,uptime` | **Geverifieerd** — idem |
| service (Uptime Kuma) | latency ms, laatste bericht | heartbeat `ping`, `msg` | **Geverifieerd** — idem |
| container (Portainer) | alléén status (running/exited) + image-naam | `containers/json` `State`, `Image` | Status **geverifieerd**; `Image`-veld is standaard Docker-API — **gok**, checken bij bouw |
| container (Glances) | cpu %, mem per container | `/containers`-items | Raw wordt opgeslagen (**geverifieerd**) maar veldnamen (`cpu.total`, `memory.usage`?) zijn **gok** — verifiëren tegen echte Glances v4-payload; zo niet: alleen status |
| device (ARP) | up/down + last_seen, vendor (OUI) | ARP/ping + lokaal OUI-bestand | Presence **geverifieerd**; méér is er niet zonder SNMP |
| proxyhost (NPM) | actief/uit; cert-verloopdatum | `enabled`; `expand=certificate` | Enabled **geverifieerd**; certveldnamen **gok** — zit al in raw_json, ontsluiten kan later |
| temperatuur (Glances) | — | `/sensors` wordt **niet** opgehaald | Vergt kleine adapteruitbreiding (+1 GET); doe ik alleen in fase 4 als de payload-check meevalt |

## 6. Bewust niet

- **Meerdere kabels per entity** (multi-NIC/LACP): unique index houdt het op één, zoals nu. Loslaten is later één index droppen.
- **NetBox-volledigheid**: geen N:1 front/rear-posities, geen kabelprofielen, geen racks/units, geen VLAN's.
- **Cytoscape.js**: pan/zoom en betere routing op de eigen SVG volstaan (fase 4); multiselect/drag/undo blijven ongemoeid.
- **Server-side wizardstatus of aparte wizard-API**: de wizard is een frontend-flow over gewone endpoints.
- **Ruwe metric-historie voorbij 48 uur**, instelbare retentie, of een tijdreeksdatabase: de grens is hardgecodeerd met een comment.
- **Temperatuur/S.M.A.R.T./disk-I/O-grafieken** in deze fasen; hooguit temperatuur als de Glances-check in fase 4 meevalt.
- **Notificaties, routers-refactor, providertests, passphrase-back-ups** — expliciet buiten scope verklaard in de opdracht; staan in de roadmap.

## 7. Faseplan zoals ik het ga uitvoeren

1. **Fase 1**: schema hierboven in `db.py` (schoon volume), `/api/cables`-endpoints vervangen de assignment-endpoints, `/api/summary`, OUI-bestand in de image + `vendor`-vulling. Tests: kabelmodel (poort↔poort, poort↔entity, bezet-checks, trace), summary, OUI.
2. **Fase 2**: wizard (4 schermen), `promote`-, `test`- en `discoveries`-endpoints, bulk-toewijsscherm ook los bereikbaar. Tests: promote, provider-test-endpoint (gemockt), bulkflow.
3. **Fase 3**: apparaatfronten-view, drawer met trace, daarna drag-and-drop (met select-dialoog als toets/touch-alternatief). Gesplitst opleverbaar.
4. **Fase 4**: sampling + retentie in housekeeping, uptime-balk, sparklines, pan/zoom op de SVG. Tests: sampling-cadans, dag-aggregatie, retentie-opruiming.
5. **Fase 5**: migratieregister — pas op jouw teken.
