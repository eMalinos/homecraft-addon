import asyncio
import logging
import sys

import aiohttp

from . import audit, fingerprint, identity, panel, selfcheck, settings, state
from .client import Client

logger = logging.getLogger("connector")


async def main() -> None:
    opts = settings.options()
    server_url = opts["server_url"]
    if settings.lan_ws(server_url):
        logger.warning(
            "Spojení s Homecraftem není šifrované (ws:// na adresu v LAN): jen pro test, "
            "přístupový token jde po síti nešifrovaně"
        )
    elif not server_url.startswith("wss://") and not settings.DEV:
        logger.error(
            "server_url musí začínat wss:// (ws:// jen na privátní IP v LAN nebo s CONNECTOR_DEV=1)"
        )
        sys.exit(1)
    agent_id, key = identity.load_or_create()
    code = identity.pairing_code(agent_id, key)
    logger.info("Homecraft Connector %s, agent %s", settings.version(), agent_id)
    logger.info("Párovací kód: %s", code)
    verification = identity.verification_code(key)
    logger.info("Ověřovací kód: %s", verification)
    audit.load()
    # The option can only switch the pause on; the panel's switch is the rest.
    paused = opts["paused"] or state.load_paused()
    if paused:
        logger.info("Homecraft je pozastaven")
    async with aiohttp.ClientSession(timeout=selfcheck.TIMEOUT) as session:
        hello_extra = await fingerprint.collect(session)
    client = Client(server_url, agent_id, key, settings.version(), paused, hello_extra)
    await panel.start(client, code, verification)
    try:
        await selfcheck.run()
    except Exception:
        logger.exception("self-check selhal, pokračuji bez něj")
    await client.run()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())
