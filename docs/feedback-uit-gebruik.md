# Feedback uit gebruik

Losse observaties uit het echte gebruik op de homelab-VM, met wat eraan is
gedaan. Nieuwe punten onderaan toevoegen. Bedoeld als geheugensteun, niet als
issue-tracker — één regel per punt volstaat.

| # | Waargenomen | Oorzaak | Opgelost in |
|---|---|---|---|
| 1 | Scan over `192.168.100.0/22` meldt 237 apparaten, terwijl er ~40 zijn | De ARP-parser controleerde alleen of kolom 4 op een MAC leek. Een ping-sweep laat voor elk adres zonder antwoord een buur achter met vlaggen `0x0` en MAC `00:00:00:00:00:00`, en die telden mee | `parse_arp_table()` eist nu `ATF_COM`; `normalize_mac()` weigert de nul-MAC |
| 2 | Proxmox-provider geeft `HTTP 401` en er is geen veld voor het token-ID | De wizard vroeg alleen de basis-URL en het tokengeheim uit. `user` en `token_name` bleven op de sjabloonwaarden `readonly@pve` / `patchmanager` uit `db.py`, dus de kop `PVEAPIToken=<user>!<token_name>=<geheim>` klopte nooit | `PROVIDER_CONFIG_FIELDS` in `main.py`; wizard rendert en verstuurt die velden |

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
