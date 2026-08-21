# Persona-hertest en bevindingenopvolging

Datum: 20 augustus 2026
Branch: `codex/fat-0.4.0-r2-improvements`

## Resultaat

De negen bevindingen uit de persona-usabilitytest zijn technisch opgelost en opnieuw getest. De volledige geautomatiseerde suite eindigt op **163 geslaagde tests**, waaronder **31 echte Chromium-tests** voor functionele flows, WCAG-controles, mobiele reflow en de nieuwe regressies.

| Bevinding | Oplossing | Hertest |
|---|---|---|
| Geen veilige kijkersrol | Rollen `beheerder` en `kijker`, accountbeheer in Admin, server-side blokkade van mutaties én beheergegevens | Kijker ziet alleen Patch, Apps en Topologie; mutatie geeft 403; uitloggen werkt |
| Ongeldige provider kon actief worden | Bij inschakelen is vóór opslag een read-only verbindingstest verplicht | Portainer zonder API-key blijft uit; dialoog blijft open met concrete fout |
| Verwijderen vanuit topologie liep dood | `Apparaat beheren` opent het bronobject met zichtbare verwijderknop en gevolgenbevestiging | Handmatig device vanuit topologie verwijderd; node verdwijnt mee |
| Internetstatus en speedtest liepen door elkaar | Aparte beschikbaarheidsmonitor, expliciet `niet gemonitord`, speedtestfouten los benoemd | Online monitor blijft online als LibreSpeed ontbreekt; UI zegt dat een speedtest geen live status is |
| Tab en bewerkmodus bleven na logout staan | Volledige sessie- en UI-reset; statuspoll stopt buiten een ingelogde app | Herlogin begint op Patch, topologie-bewerkmodus is uit |
| Verse installatie bevatte persoonlijke hardware/adressen | Productie start leeg; generieke providerconfig; optionele demo-seed via omgevingsvariabele | Verse database: 0 apparaten, 0 poorten, geen `192.168.*`-providerdefaults |
| Technische Engelstalige statuslabels | `online`, `offline`, `storing`, `niet gemonitord` in kaarten, zoeken, detail en topologie | Browsertest controleert uitsluitend de Nederlandse labels |
| Wizard toonde alle providerformulieren tegelijk | Eerst zes compacte, optionele keuzes; per bron pas na openen de velden; jargon toegelicht | Op verse installatie zijn alle keuzes dicht en interne type-ID's onzichtbaar |
| Headercontext verdween rond zoeken | Zoekveld heeft stabiele breedte en verliest focus na selectie; compacte internetstatus blijft zichtbaar | Titel, speedstatus en profiel blijven na een zoekselectie zichtbaar |

## Persona-uitkomst

- **Lucas — eigenaar:** beheeracties blijven snel bereikbaar; providerfouten worden vóór opslag gestopt; verwijderen is vanuit Patch en Topologie vindbaar.
- **Sanne — kijker:** ziet een herkenbaar `kijker`-label, geen Admin-tab of bewerkknoppen en een begrijpelijke internetstatus; uitloggen is toegestaan.
- **Ravi — nieuwe zelfhoster:** begint met een lege eigen inventaris, krijgt direct de wizard en ziet bij databronnen eerst keuzes in plaats van zes formulieren.

## Bewijs en resterende processtap

De regressies staan in `tests/test_roles_and_usability.py`, `tests/test_e2e_browser.py` en `tests/test_migrations.py`. Daarnaast zijn de drie persona's opnieuw in een lokale browser doorlopen.

Dit is een persona-simulatie en geen vervanging voor observatie van drie echte mensen. Voor volledige toepassing van ISO 9241-210 blijft een volgende ronde met echte gebruikers in hun eigen context nodig; hun taakduur, fouten, hulpvragen en opmerkingen moeten als nieuw onderzoeksbewijs worden vastgelegd.
