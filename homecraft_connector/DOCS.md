# Homecraft Connector

Připojí tento Home Assistant k Homecraftu **odchozím** spojením (WebSocket přes
Cloudflare). Home Assistant nepotřebuje veřejnou adresu, přesměrování portů ani VPN.

## Spárování

1. Spusť doplněk a otevři jeho **Log**.
2. Najdi řádek `Párovací kód: …` a kód zkopíruj.
3. V Homecraftu otevři projekt → připojení HA → **Homecraft Connector**, vlož kód do **Pairing code** a dej **Pair**.

Kód obsahuje jen identifikátor doplňku a jeho veřejný klíč, žádné tajemství.
Soukromý klíč zůstává v doplňku (`/data`).

## Možnosti

| Volba | Výchozí | Význam |
|---|---|---|
| `server_url` | `wss://agent.homecraft.cz/api/agent/ws` | Adresa Homecraftu. |
| `paused` | `false` | Pozastaví provádění požadavků z Homecraftu. |

Přístupový token Cloudflare se do možností **nezadává** (byl by v nich čitelný).
Zadává se v panelu doplňku.

## Log při startu (self-check)

Doplněk při startu ověří, k čemu má přístup, a do logu zapíše `OK` / `FAIL` /
`unsupported` pro: WebSocket Home Assistantu a jeho příkazy, API Supervisoru a
dosah na Home Assistant. Nic nemění.

## Odpojení

Spárování zrušíš v Homecraftu. Doplněk se pak připojuje čím dál řidčeji
(1, 5, 15, 60 minut, dál jednou za hodinu), dokud ho znovu nespáruješ nebo neodinstaluješ.
