#!/bin/sh
# Start the agent. Runs under the add-on's top-level AppArmor profile: it reads
# HA's install uuid (.storage/core.uuid, in no API) for the pairing request's
# fingerprint and hands it over in the environment. The agent's own profile
# (connector) denies .storage entirely, so the agent itself never reads it.
HA_INSTALL_UUID="$(jq -r '.data.uuid // empty' "${CONFIG_ROOT:-/homeassistant}/.storage/core.uuid" 2>/dev/null)" || HA_INSTALL_UUID=""
export HA_INSTALL_UUID
exec python3 -m connector
