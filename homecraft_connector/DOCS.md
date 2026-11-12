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

## Ingress do doplňků

Homecraft nastavuje Node-RED, Grafanu, InfluxDB a go2rtc přes jejich ingress.
Relaci ingressu vytvoří Home Assistant jen správci, proto doplněk potřebuje
**Token správce Home Assistantu**: v profilu správce → **Zabezpečení** →
**Dlouhodobé přístupové tokeny** → vytvořit, vložit do panelu a **Uložit**.
Jde jen zadat a smazat, nikdy zobrazit, a doplněk ho používá jen na vytvoření
relace ingressu (`/ingress/session`); Homecraftu ho nikdy nepošle. Bez něj
funguje vše ostatní, jen požadavky do doplňků skončí chybou `no_ha_token`.
Doplněk se kvůli tomu připojuje na `ws://homeassistant:8123`. Má-li Home
Assistant certifikát přímo v `http:` (`ssl_certificate`), ingress přes Connector
zatím nefunguje.

## Možnosti

| Volba | Výchozí | Význam |
|---|---|---|
| `server_url` | `wss://agent.homecraft.cz/api/agent/ws` | Adresa Homecraftu. Jen `wss://`, výjimka viz níže. |
| `paused` | `false` | `true` zapne pauzu při každém startu (vypnout ji pak jde jen v panelu do dalšího restartu). |

Přístupový token se do možností **nezadává** (byl by v nich čitelný).
Zadává se v panelu doplňku.

**Jen pro test v LAN:** `server_url` smí být `ws://` (nešifrované), jen když je
adresou privátní IP (`192.168.x.x`, `10.x.x.x`, `172.16–31.x.x`, IPv6 `fd…`
nebo `fe80::…`), např. `ws://192.168.1.20:8000/api/agent/ws` pro vývojový
Homecraft bez Cloudflare. Název hostitele ani `127.0.0.1` nejde. Doplněk při
startu varuje v logu: **přístupový token jde po síti nešifrovaně**.

## Panel

Panel je dostupný jen přes Home Assistant (ingress), nikde jinde neposlouchá.

- **Spojení**: připojeno/odpojeno, adresa Homecraftu, čas posledního spojení,
  poslední chyba a tlačítko **Připojit znovu** (zkusí to hned, bez čekání;
  otevřené spojení ukončí a naváže znovu).
- **Pozastavit Homecraft**: dokud je zapnuto, doplněk odmítne každý požadavek
  z Homecraftu chybou `paused` (Plan i Apply v Homecraftu skončí chybou)
  a hned zavře všechna otevřená sezení Homecraftu do Home Assistantu. Spojení
  s Homecraftem zůstává. Pauza přežije restart doplňku.
- **Párovací kód** s tlačítkem **Kopírovat**.
- **Přístupový token Homecraftu**: jde jen zadat, změnit a smazat. Po uložení
  panel ukazuje jen „Token nastaven ✓“; token není vidět v panelu, v nastavení
  doplňku ani v jeho YAML a nepíše se do logu. Uložení i smazání tokenu
  ukončí současné spojení s Homecraftem (nové použije nový token).

  Při výměně tokenu (rotace) pošle Homecraft doplňku nový token sám, po už
  ověřeném spojení (operace `access_token.set`, i během pauzy: týká se jen
  spojení s Homecraftem, ne Home Assistantu). Doplněk ho nejdřív jen odloží
  (panel: „Nový token od Homecraftu čeká na ověření“). Znovu se připojí, až
  doběhnou rozběhnuté požadavky a otevřená sezení, nejpozději ale za 15 minut.
  Každé připojení pak zkusí nejdřív čekající token. Když ho Cloudflare pustí,
  platí (panel: „aktualizován <datum> Homecraftem“, audit
  `access_token.commit`). Když ho odmítne, doplněk se v tomtéž pokusu připojí
  s původním tokenem a čekající zkusí znovu při dalším připojení; odmítnutí
  zapíše do logu a auditu jen jednou. Čekající token nahradí jen další token
  od Homecraftu nebo uložení či smazání tokenu v panelu. Homecraft vidí jen
  otisk tokenu (prvních 16 znaků SHA-256), nikdy token. Nemůže nastavit nic
  jiného než token (adresu serveru ani možnosti ne). Spojení s Homecraftem
  neukládá cookies, takže cookie od Cloudflare Access nikdy nenahradí token.
- **Audit**: posledních 200 operací (čas, operace, cíl, výsledek, trvání).
  Cíl je typ příkazu WebSocketu, metoda a cesta HTTP nebo cesta souboru,
  nikdy obsah souborů, těla požadavků ani tokeny. Soubor `/data/audit.jsonl`
  drží posledních 1000 záznamů.

Změny v panelu (token, pauza, připojení) přijme doplněk jen s tajnou hodnotou,
kterou při každém startu vloží do stránky panelu. Cizí stránka ji nezná.
Skript ze stejného původu (cokoli, co běží v Home Assistantu) si ale stránku
panelu načíst a hodnotu přečíst může.

## Co smí Homecraft dělat

Doplněk provede jen operace z pevného seznamu v jeho kódu (ne z možností ani ze
serveru): příkazy WebSocketu, které features potřebují (registry, pomocníci,
dashboardy, `reload` služby, `scheduler.edit`, doplňky Node-RED, go2rtc,
InfluxDB a Grafana), několik REST adres (kontrola konfigurace, integrace
InfluxDB) a soubory jen v `packages/`, `custom_templates/` a `themes/`
(`configuration.yaml` jen ke čtení). Cokoli jiného odmítne s chybou `not_allowed`.

## Bezpečnost

**Kam** (allowlist, viz výše) a **co**: doplněk před zápisem zkontroluje obsah
a odmítne s chybou `refused_content` vše, co by spouštělo příkazy nebo posílalo
data ven (se záznamem v auditu panelu). Seznamy jsou pevně v kódu
a nastavení je nerozšíří. Je to denylist: díru zužuje, nezavírá (viz limity).

- **Soubory v `packages/`** se rozparsují jako YAML. Odmítne se:
  - klíč `shell_command`, `command_line`, `python_script`, `pyscript`,
    `rest_command`, `rest`, `scrape`, `panel_custom`, `panel_iframe`, `http`,
    `frontend`, `lovelace`, `ffmpeg`, `logger`, i s příponou
    (`shell_command 2:`);
  - `influxdb:` s čímkoli kromě `include`, `exclude` a `ignore_attributes`
    (kam data jdou, nastavuje config flow integrace);
  - `homeassistant:` s `auth_providers`, `packages`, `allowlist_external_dirs`,
    `allowlist_external_urls`, `media_dirs` nebo `customize_glob`
    (`customize` je v pořádku);
  - položka s `platform: command_line|shell_command|rest|scrape`;
  - akce (`action`/`service`/`perform_action`) v doménách `shell_command`,
    `rest_command`, `python_script`, `pyscript`, `hassio`, `downloader`,
    `backup`, služby `update.install`, `homeassistant.stop|restart`,
    `recorder.purge*` a `recorder.disable`, i akce, kterou může šablona na
    zakázanou změnit;
  - jakýkoli tag HA (`!include*`, `!secret`, `!env_var`, `!input`): načetly by
    obsah, který kontrolou neprošel. Knihovna žádný nepoužívá. Neplatný YAML.
  `recorder:` povolený zůstává, knihovna ho potřebuje (`purge_keep_days`).
- **`themes/`**: kód nespouští (CSS proměnné), ale loader HA v nich tagy
  vyhodnocuje, proto se odmítnou stejné tagy. `custom_templates/` jsou soubory
  Jinja, ne YAML. Soubor `secrets.yaml` nelze zapsat nikam.
- **Automatizace a skripty přes REST** projdou stejnou kontrolou, ale proti
  živé konfiguraci stejného id: odmítne se jen zakázaná akce, kterou tam
  uživatel ještě nemá (přejmenování entity v jeho automatizaci projde).
- **Dashboardy** (`lovelace/config/save`): akce karet (`tap_action`,
  `hold_action`…) se zakázanými službami, které v živém dashboardu ještě
  nejsou. Vlastní karty uživatele Homecraft nikdy neblokují.
- **Node-RED** (`POST /flows` posílá všechny flow): kontrolují se nové a změněné
  uzly a vše, kam vedou (dráty, instance subflow i jejich vnitřek). Smí být jen
  typu, který používá knihovna Homecraftu, uzel `exec` nikdy. `api-call-service`
  jen ve verzi 7, se `blockInputOverrides` a službou pevně v uzlu (ne ze
  zprávy), na kterou se nevztahuje denylist. `function` bez modulů (`libs`).
  Záložky a subflow uživatele i všechny jejich uzly musí přijít přesně jako
  v živém Node-RED (nic změněného, smazaného ani přidaného dovnitř).
  Instalace modulů (`POST /nodes`) povolená není.
- **go2rtc** (`POST /api/config` přepisuje celý soubor): Homecraft smí změnit jen
  klíč `webrtc`, a to jen ve tvaru, jaký zapisuje knihovna (`listen`,
  `candidates`, `ice_servers[].urls`). Ostatní klíče (`api`, `streams` i
  s `exec:`, `echo:`, `expr:`…) musí zůstat přesně jak jsou: nesmí se změnit,
  smazat ani vynulovat. Nová proměnná `${…}` (go2rtc ji doplní z prostředí)
  se odmítne.

**AppArmor** (druhá vrstva pod allowlistem): vlastní profil `apparmor.txt`
omezuje agenta i v případě chyby v allowlistu nebo v kódu doplňku. Agent běží
ve vlastním podprofilu a jádro HA OS mu dovolí jen:

- Python a jeho knihovny, kód doplňku (`/app`) jen ke čtení;
- `/data` (identita, token, stav, audit, `options.json`);
- `configuration.yaml` ke čtení a `packages/`, `custom_templates/`, `themes/`
  ke čtení i zápisu;
- síť (Homecraft, Supervisor, Home Assistant) a certifikáty a DNS v `/etc`.

Odepřeno je vše ostatní, hlavně spuštění shellu nebo jiného programu, zápis do
`custom_components/` a jinam do `/homeassistant`, čtení i zápis `secrets.yaml`
a `.storage/`. Spouštěcí skripty S6 mají pravidla podle příkladu
v dokumentaci Home Assistantu. Do vyladění na živém HA OS je profil v režimu
`complain` (porušení jen zapisuje do logu).

*Pro vývoj:* každá nová funkce, která sahá na nové cesty nebo spouští nový
program, musí ve stejném PR upravit `apparmor.txt` a ověřit ho na HA OS
(`journalctl _TRANSPORT=audit` bez `DENIED`/`ALLOWED` pro tento profil).

**Známé limity** (uzavře až úroveň 3, podepsané balíčky):

- Uzel Node-RED `function` spouští JavaScript; jeho `vm` není hranice.
  Kontrola se týká typů uzlů, ne jejich kódu.
- Uzel `http request` (hlídání bran Shelly ve feature Topení) posílá požadavky
  na adresu ze zprávy.
- Nové uzly mohou na flow uživatele působit nepřímo: přes uzly `catch`
  a `status`, vyvolané události a změny stavů entit.
- JavaScript v šablonách vlastních karet dashboardu (`[[[ ]]]` v button-card,
  HTML karty) se nekontroluje.
- Šablony Jinja v YAML se nekontrolují (běží v sandboxu HA).

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
