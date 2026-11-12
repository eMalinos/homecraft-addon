# Changelog

## 0.5.0

MINE_PLACEHOLDER
## 0.4.0

- Kontrola obsahu: doplněk odmítne zápis, který by v Home Assistantu spouštěl
  příkazy nebo posílal data ven (chyba `refused_content`, záznam v auditu a logu).
  - Soubory v `packages/`: zakázané integrace (`shell_command`, `command_line`,
    `rest`, `python_script`, `frontend`, `lovelace`…), platformy
    `command_line`/`rest`/`scrape` a akce `shell_command.*`, `hassio.*`,
    `homeassistant.restart` a další. Žádné tagy HA (`!include`, `!secret`,
    `!env_var`), ani v `themes/`. `secrets.yaml` zapsat nelze.
  - Stejná kontrola akcí pro automatizace a skripty přes REST a pro akce karet
    v dashboardech, vždy proti živé konfiguraci: obsah uživatele Homecraft
    neblokuje.
  - Node-RED: nové a změněné uzly a vše, kam vedou, jen typů, které používá
    knihovna (nikdy `exec`). Volání služeb pevně v uzlu, flow uživatele beze
    změny.
  - go2rtc: Homecraft smí měnit jen klíč `webrtc` ve tvaru knihovny, ostatní
    klíče (streamy s `exec:`, `echo:`, `expr:`, `api`…) zůstávají přesně
    uživatele.

## 0.3.0

- Panel doplňku v postranním menu HA (ingress): stav spojení, párovací kód
  s tlačítkem Kopírovat, tlačítko **Připojit znovu** (bez čekání na další pokus).
- Přístupový token Homecraftu se zadává v panelu. Jde jen uložit, změnit nebo
  smazat, zpátky ho panel nikdy neukáže.
- Přepínač **Pozastavit Homecraft**: každý požadavek pak skončí chybou `paused`.
  Pauza hned zavře otevřená sezení do HA a přežije restart; volba `paused`
  ji při startu zapne.
- Audit posledních 200 operací (čas, operace, cíl, výsledek, trvání) bez obsahu
  souborů a tokenů; `/data/audit.jsonl` drží posledních 1000.

## 0.2.0

- Provádí operace z Homecraftu: WebSocket sezení do Home Assistantu (`ws.open`,
  `ws.send`, `ws.close`), REST a ingress doplňků (`http`) a soubory
  (`file.read`, `file.write`, `file.delete`, `file.list`).
- Vše jen podle seznamu povolených operací v kódu doplňku; ostatní odmítne
  s chybou `not_allowed`.

## 0.1.0

- První verze: identita doplňku, párovací kód, připojení k Homecraftu
  (výzva a podpis), udržování spojení a opětovné připojení, self-check při startu.
  Požadavky z Homecraftu zatím odmítá (`not_implemented`).
