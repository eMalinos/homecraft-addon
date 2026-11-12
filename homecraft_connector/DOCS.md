# Homecraft Connector

Připojí tento Home Assistant k Homecraftu **odchozím** spojením (WebSocket přes
Cloudflare). Home Assistant nepotřebuje veřejnou adresu, přesměrování portů ani VPN.

## Spárování

1. Spusť doplněk a otevři jeho panel **Homecraft** v postranním menu HA
   (nebo **Otevřít webové rozhraní** u doplňku).
2. Do **Přístupový token Homecraftu** vlož token, který ti dal Homecraft, a dej **Uložit**.
3. Párovací kód zkopíruj tlačítkem **Kopírovat** (je i v **Logu** na řádku `Párovací kód: …`).
4. V Homecraftu otevři projekt → připojení HA → **Homecraft Connector**, vlož kód do **Pairing code** a dej **Pair**.
5. V panelu dej **Připojit znovu**, ať doplněk nečeká na další pokus.

Kód obsahuje jen identifikátor doplňku a jeho veřejný klíč, žádné tajemství.
Soukromý klíč zůstává v doplňku (`/data`).

## Možnosti

| Volba | Výchozí | Význam |
|---|---|---|
| `server_url` | `wss://agent.homecraft.cz/api/agent/ws` | Adresa Homecraftu. |
| `paused` | `false` | `true` zapne pauzu při každém startu (vypnout ji pak jde jen v panelu do dalšího restartu). |

Přístupový token se do možností **nezadává** (byl by v nich čitelný).
Zadává se v panelu doplňku.

## Panel

Panel je dostupný jen přes Home Assistant (ingress), nikde jinde neposlouchá.

- **Spojení**: připojeno/odpojeno, adresa Homecraftu, čas posledního spojení,
  poslední chyba a tlačítko **Připojit znovu** (zkusí to hned, bez čekání).
- **Pozastavit Homecraft**: dokud je zapnuto, doplněk odmítne každý požadavek
  z Homecraftu chybou `paused` (Plan i Apply v Homecraftu skončí chybou).
  Spojení zůstává. Pauza platí okamžitě a přežije restart doplňku.
- **Párovací kód** s tlačítkem **Kopírovat**.
- **Přístupový token Homecraftu**: jde jen zadat, změnit a smazat. Po uložení
  panel ukazuje jen „Token nastaven ✓“; token není vidět v panelu, v nastavení
  doplňku ani v jeho YAML a nepíše se do logu.
- **Audit**: posledních 200 operací (čas, operace, cíl, výsledek, trvání).
  Cíl je typ příkazu WebSocketu, metoda a cesta HTTP nebo cesta souboru,
  nikdy obsah souborů, těla požadavků ani tokeny. Soubor `/data/audit.jsonl`
  drží posledních 1000 záznamů.

## Co smí Homecraft dělat

Doplněk provede jen operace z pevného seznamu v jeho kódu (ne z možností ani ze
serveru): příkazy WebSocketu, které features potřebují (registry, pomocníci,
dashboardy, `reload` služby, `scheduler.edit`, doplňky Node-RED, go2rtc,
InfluxDB a Grafana), několik REST adres (kontrola konfigurace, integrace
InfluxDB) a soubory jen v `packages/`, `custom_templates/` a `themes/`
(`configuration.yaml` jen ke čtení). Cokoli jiného odmítne s chybou `not_allowed`.

## Log při startu (self-check)

Doplněk při startu ověří, k čemu má přístup, a do logu zapíše `OK` / `FAIL` /
`unsupported` pro: WebSocket Home Assistantu a jeho příkazy, API Supervisoru a
dosah na Home Assistant. Nic nemění.

## Odpojení

- Na chvíli: **Pozastavit Homecraft** v panelu.
- Úplně: zruš spárování v Homecraftu, nebo doplněk odinstaluj (smaže se
  i jeho identita, token a audit v `/data`).

Bez spárování se doplněk připojuje čím dál řidčeji (1, 5, 15, 60 minut, dál
jednou za hodinu), dokud ho znovu nespáruješ nebo neodinstaluješ.
