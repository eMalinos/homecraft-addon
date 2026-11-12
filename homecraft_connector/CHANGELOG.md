# Changelog

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
