#!/bin/bash
# =============================================================================
# core-setup.sh — core node: 5G core NFs + FlexRIC near-RT RIC + KPM xApp
#
# Runs on the single "core" node. Brings up the CN, the RIC, and the monitoring
# xApp, then enables forwarding so cell nodes can reach the Docker bridges
# (192.168.71.128/26 and 192.168.72.128/26) across the experimental LAN.
#
# Cell nodes wait for the AMF to be reachable before starting their gNBs.
# =============================================================================

set +e

CORE_LAN_IP="${1:-10.10.1.1}"

mkdir -p /local/logs /local/logs/xapp
chmod 777 /local/logs/xapp

echo "============================================"
echo "[CORE] started at $(date)  lan_ip=${CORE_LAN_IP}"
echo "============================================"

# ------------------------------------------------------------------ #
# 1. Install Docker
# ------------------------------------------------------------------ #
echo "[CORE] Installing Docker..."
apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
docker --version

# ------------------------------------------------------------------ #
# 2. IP forwarding — REQUIRED so cell nodes can reach the CN bridges.
#    Each cell node routes 192.168.71/72.128/26 via this node's LAN IP;
#    without forwarding those packets die here.
# ------------------------------------------------------------------ #
echo "[CORE] Enabling IP forwarding..."
sysctl -w net.ipv4.ip_forward=1
echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
iptables -P FORWARD ACCEPT

# ------------------------------------------------------------------ #
# 3. Pull images
# ------------------------------------------------------------------ #
echo "[CORE] Pulling images..."
docker pull mysql:8.0
docker pull oaisoftwarealliance/oai-udr:v2.0.0
docker pull oaisoftwarealliance/oai-udm:v2.0.0
docker pull oaisoftwarealliance/oai-ausf:v2.0.0
docker pull oaisoftwarealliance/oai-amf:v2.0.0
docker pull oaisoftwarealliance/oai-smf:v2.0.0
docker pull oaisoftwarealliance/oai-upf:v2.0.0
docker pull oaisoftwarealliance/trf-gen-cn5g:focal
docker pull ghinwa555/flexric-kpm:v1
echo "[CORE] All images pulled."

# ------------------------------------------------------------------ #
# 4. mgen must be present (bind-mounted into upf + ext-dn)
# ------------------------------------------------------------------ #
MGEN_BIN="/local/repository/bin/mgen"
if [ ! -f "$MGEN_BIN" ] || [ ! -x "$MGEN_BIN" ]; then
    echo "[CORE] ERROR: $MGEN_BIN missing or not executable. Aborting."
    exit 1
fi

# ------------------------------------------------------------------ #
# 5. Generate the subscriber DB for this topology.
#    MySQL reinitializes from oai_db.sql on every start, so every UE across
#    every cell must be present BEFORE the stack comes up. A UE missing here
#    will fail authentication, or (if only half-present) register and then fail
#    PDU session establishment.
# ------------------------------------------------------------------ #
NUM_CELLS="${2:-2}"
UES_PER_CELL="${3:-12}"
bash /local/repository/bin/gen-subscribers.sh "${NUM_CELLS}" "${UES_PER_CELL}"

# ------------------------------------------------------------------ #
# 6. Point the RIC config at this node's bridge IP
# ------------------------------------------------------------------ #
cd /local/repository/etc
sed -i "s/^NEAR_RIC_IP.*/NEAR_RIC_IP = 192.168.71.142/" flexric.conf
echo "[CORE] flexric.conf:"; cat flexric.conf

# ------------------------------------------------------------------ #
# 7. Start the stack
# ------------------------------------------------------------------ #
echo "[CORE] Starting core + RIC..."
docker compose -f docker-compose-core.yaml up -d

# ------------------------------------------------------------------ #
# 7. Wait for AMF
# ------------------------------------------------------------------ #
echo "[CORE] Waiting for AMF..."
MAX_WAIT=600
ELAPSED=0
until docker logs ric5g-oai-amf 2>&1 | grep -q "HTTP2 server started\|Waiting for"; do
    if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
        echo "[CORE] WARNING: AMF timeout, continuing..."
        break
    fi
    echo "[CORE] Waiting for AMF... (${ELAPSED}s)"
    sleep 10
    ELAPSED=$((ELAPSED + 10))
done
echo "[CORE] AMF ready."

# ------------------------------------------------------------------ #
# 8. Wait for the RIC to load its service models
# ------------------------------------------------------------------ #
echo "[CORE] Waiting for near-RT RIC..."
MAX_WAIT=120
ELAPSED=0
until docker logs ric5g-flexric 2>&1 | grep -q "Loading SM ID = 2"; do
    if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
        echo "[CORE] WARNING: RIC did not report SM loading."
        break
    fi
    sleep 5
    ELAPSED=$((ELAPSED + 5))
done
docker logs ric5g-flexric 2>&1 | grep -E "nearRT-RIC IP|Loading SM ID"
echo "[CORE] RIC ready — E2 termination on 192.168.71.142:36421"

# ------------------------------------------------------------------ #
# Done. Cell nodes poll the AMF and attach on their own.
# ------------------------------------------------------------------ #
echo "============================================"
echo "[CORE] completed at $(date)"
echo "============================================"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
