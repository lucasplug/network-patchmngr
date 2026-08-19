# Feedback uit gebruik

Losse observaties uit het echte gebruik op de homelab-VM, met wat eraan is
gedaan. Nieuwe punten onderaan toevoegen. Bedoeld als geheugensteun, niet als
issue-tracker — één regel per punt volstaat.

| # | Waargenomen | Oorzaak | Opgelost in |
|---|---|---|---|
| 1 | Scan over `192.168.100.0/22` meldt 237 apparaten, terwijl er ~40 zijn | De ARP-parser controleerde alleen of kolom 4 op een MAC leek. Een ping-sweep laat voor elk adres zonder antwoord een buur achter met vlaggen `0x0` en MAC `00:00:00:00:00:00`, en die telden mee | `parse_arp_table()` eist nu `ATF_COM`; `normalize_mac()` weigert de nul-MAC |
| 2 | Proxmox-provider geeft `HTTP 401` en er is geen veld voor het token-ID | De wizard vroeg alleen de basis-URL en het tokengeheim uit. `user` en `token_name` bleven op de sjabloonwaarden `readonly@pve` / `patchmanager` uit `db.py`, dus de kop `PVEAPIToken=<user>!<token_name>=<geheim>` klopte nooit | `PROVIDER_CONFIG_FIELDS` in `main.py`; wizard rendert en verstuurt die velden |
| 3 | Glances draait op meerdere machines, maar er kan er maar één in | De adapter kon al een lijst endpoints aan; alleen de UI propte er één in en koppelde op hostnaam | Endpointlijst in het providerdialoog, met per endpoint een expliciet device (`entity_id`) |
| 4 | Gevonden apparaten zijn niet aan een Deco te koppelen | Koppelen was uitsluitend "kabel van poort naar device". Een Deco heeft drie poorten en tientallen wifi-clients, dus daar paste niets | `entities.uplink_device_id`: hangt aan een netwerkapparaat zónder poort. Kabel wint als die er is |
| 5 | De rol van een apparaat is niet af te lezen | `type` was een vrij tekstveld met twee losse keuzelijsten die geen van beide dekten wat providers opleveren | Eén lijst in `patch_manager/categories.py`, gebruikt voor keuzelijsten, iconen en labels |
| 6 | Kan ik een Glances- of Uptime Kuma-entity aan switch 1 of 2 hangen? | Deels. De poortloze uplink accepteerde ook containers, VM's en monitors — die hebben geen netwerkpoort. En een overgenomen (handmatig) device kon helemaal geen uplink meer krijgen, want het toewijsscherm toont alleen discoveries | `attachable` per categorie, afgedwongen in `PUT /uplink`; uplinkveld in het devicedialoog |
| 7 | Een SG108E kan wél in Uptime Kuma gemonitord worden of hij online is | `physical_devices` had helemaal geen status. Switches en Deco's stonden altijd kleurloos in beeld, terwijl de ping-observatie ernaast als losse entity rondzweefde | `physical_devices.monitor_entity_id`: het apparaat leent de status van de observatie die erover gaat |
| 8 | De glasvezel-ONT ontbreekt; die verzorgt het internet en gaat bedraad naar een Deco | Er was geen ONT-categorie, en erger: een kabel tússen twee netwerkapparaten werd niet in de topologie getekend. `trace_entity()` gaf `None` zodra een kabel op een poort eindigde in plaats van op een device | Categorie `ont` + geseed apparaat; `trace_far_port()` tekent apparaat-naar-apparaat als `trunk:`-relatie |
| 9 | Er is een tweede Portainer en een tweede AdGuard | `providers.type` was `UNIQUE`: exact één bron per soort | Die beperking eruit, plus toevoegen/hernoemen/verwijderen van bronnen. De wizard leest nu de inventaris in plaats van een vaste lijst |
| 10 | Graag een licht thema | De CSS had een tokenblok, maar 121 harde kleuren daarbuiten — waaronder donkere vlakken en lichte tekstkleuren | Alle overlays via `--tint`, vlakken en merkkleuren naar tokens; licht thema is nu één `[data-theme="light"]`-blok |
| 11 | Een pagina zoals Homarr: links naar apps met status uit Uptime Kuma | Bestond niet | Tabblad **Apps** met tegels per groep; status via dezelfde monitorkoppeling als netwerkapparaten |
| 12 | Kan de DHCP/ARP-scan beter? | Drie dingen: niets werd ooit op `down` gezet, de reverse lookups liepen serieel (200 apparaten x 2s > het pollinterval), en een enkel ping-pakket laat apparaten flapperen | `_mark_absent()` na een scan, parallelle lookups, `ping -c 2`, en een harde tijdslimiet op de sweep |

## Bijvangst bij deze punten

- **Nul-MAC's vielen op één apparaat samen.** `_store_record` koppelt vondsten
  op MAC-adres. Alle spookvondsten uit punt 1 deelden `00:00:00:00:00:00` en
  belandden dus op dezelfde entity, die zichzelf steeds overschreef. Daarom
  zit de weigering in `normalize_mac()` zelf en niet alleen in de ARP-parser.
- **Uptime Kuma had hetzelfde gat als Proxmox.** De adapter heeft
  `status_page_slug` nodig, en ook dat veld was in de wizard onbereikbaar.
  Meegenomen in punt 2.
- **Een 401 van Proxmox noemt nu het token dat is geprobeerd** (`root@pam!typfout`),
  omdat het token-ID uit twee losse velden wordt samengesteld en een kale
  "controleer inloggegevens" je daar niet verder helpt. Het geheim staat niet
  in de melding.
- **`relation_type: "uplink"` was al bezet.** De internet→router-lijn gebruikt
  dat type. De poortloze koppeling heet daarom `portless` (of `wireless` op een
  access point); anders werd de internetlijn óók gestippeld. Gevonden door de
  topologie in een browser te bekijken, niet door de tests.
- **`special:router` was een spookknoop.** Een losse topologieknoop met
  subtitel "Deco XE75 Pro" die nergens aan vastzat — dezelfde dubbeling als bij
  de monitors. Vervangen door de echte ONT, waar het internet ook binnenkomt.
- **Een patchpaneel wordt doorlopen, niet getekend.** De eerste versie van de
  apparaat-naar-apparaat-relatie tekende ook een lijn naar het paneel zelf.
  Een poort met een `peer_port_id` is een doorsteek en telt niet als eindpunt.
- **Uplink én statusmonitor tegelijk liet de topologie omvallen.** Een entity
  die de status van een apparaat levert krijgt geen eigen knoop meer; de
  uplinkrelatie ernaartoe bleef wel staan en botste op een foreign key.
  Gevonden door twee browsercontroles achter elkaar op dezelfde database.
- **Het thema volgt standaard je systeem.** De keuze staat in `localStorage`,
  niet in de database: het is een voorkeur van deze browser, niet van de
  installatie. Een klein inline script zet hem vóór de eerste render, anders
  flitst bij elke reload eerst het donkere thema voorbij.
- **De laatste bron van een soort is niet te verwijderen.** Dan blijft de app
  zonder die adapter achter; uitzetten doet hetzelfde zonder de instellingen
  kwijt te raken.
- **Alles was voor altijd 'up'.** De ARP-adapter meldde alleen wat hij vond;
  een uitgezet apparaat viel hooguit terug op `unknown` als de observatie
  verliep. Daarmee was de uptime-balk betekenisloos. Actief zoeken en niets
  vinden is bewijs van `down`, maar alleen binnen de gescande subnetten --
  daarbuiten hebben we niet gekeken. `last_seen_at` blijft staan: dat is
  wanneer het ding er nog wel was.
- **Een app-tegel is een `<a href>`.** Daarom accepteert de API alleen http(s):
  `javascript:` in je eigen dashboard draait met je sessie erbij.
- **Glances koppelt nu op endpoint in plaats van op hostnaam.** De sleutel van
  een providerrecord is `host:<entity_id>` geworden: verandert de hostnaam van
  een machine, dan blijft het dezelfde rij. Een afwijkende naam levert bij een
  expliciete koppeling ook geen conflict meer op — dat is juist de bedoeling.

## Schema gewijzigd

Punt 3, 4, 7, 8, 9 en 11 wijzigen `db.py`: `entities.uplink_device_id`,
`physical_devices.monitor_entity_id`, `entity_id` per Glances-endpoint, de
`UNIQUE` van `providers.type` eraf, en de tabel `app_links`. Er is geen
migratiepad: gooi het datavolume weg en begin opnieuw.
