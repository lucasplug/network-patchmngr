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
- **Glances koppelt nu op endpoint in plaats van op hostnaam.** De sleutel van
  een providerrecord is `host:<entity_id>` geworden: verandert de hostnaam van
  een machine, dan blijft het dezelfde rij. Een afwijkende naam levert bij een
  expliciete koppeling ook geen conflict meer op — dat is juist de bedoeling.

## Schema gewijzigd

Punt 3 en 4 wijzigen `db.py` (`entities.uplink_device_id`, `entity_id` per
Glances-endpoint). Er is geen migratiepad: gooi het datavolume weg en begin
opnieuw.
