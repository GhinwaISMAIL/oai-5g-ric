#!/bin/bash
# =============================================================================
# core-setup.sh — core node: 5G core NFs + FlexRIC near-RT RIC (built natively)
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
# 6. Build FlexRIC natively, from OAI's submodule.
#
#    The RIC and the xApps MUST come from the same source tree as the gNB's E2
#    agent (openair2/E2AP/flexric). A RIC built from the standalone FlexRIC
#    repository sends subscription requests that OAI's embedded agent does not
#    answer: the SCTP association is up, the request is sent, and the gNB never
#    responds. No indications are ever produced, for any service model.
#
#    The RIC therefore runs as a host process, not a container.
# ------------------------------------------------------------------ #
echo "[CORE] Installing FlexRIC build dependencies..."
apt-get install -y git cmake ninja-build build-essential swig libsctp-dev \
    python3-dev pkg-config libconfig-dev libconfig++-dev bison flex \
    libpcre2-dev autoconf automake libtool gcc-10 g++-10

# gcc-11 will not build FlexRIC
update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-10 100
update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-10 100
update-alternatives --set gcc /usr/bin/gcc-10
update-alternatives --set g++ /usr/bin/g++-10
gcc --version | head -1

# asn1c pinned to OAI's commit; vlm_master generates code that will not link
echo "[CORE] Building asn1c..."
rm -rf /tmp/asn1c-src
git clone https://github.com/mouse07410/asn1c /tmp/asn1c-src
cd /tmp/asn1c-src
git checkout 940dd5fa9f3917913fd487b13dfddfacd0ded06e
autoreconf -iv
CFLAGS="-O2 -fno-strict-aliasing" ./configure --prefix /opt/asn1c/
make -j"$(nproc)" && make install && ldconfig

echo "[CORE] Building FlexRIC from the OAI submodule..."
rm -rf /opt/oai-src
git clone https://gitlab.eurecom.fr/oai/openairinterface5g.git /opt/oai-src
cd /opt/oai-src
git checkout develop
git submodule update --init --recursive
cd /opt/oai-src/openair2/E2AP/flexric
mkdir -p build && cd build
cmake -DKPM_VERSION=KPM_V3_00 -DE2AP_VERSION=E2AP_V3 -DXAPP_DB=SQLITE3_XAPP ..
make -j"$(nproc)"
make install
ls -1 /usr/local/lib/flexric/

# make install OVERWRITES flexric.conf and resets NEAR_RIC_IP to 127.0.0.1.
# Rewrite it afterwards, or the cell nodes cannot reach the RIC.
sed -i "s|^NEAR_RIC_IP.*|NEAR_RIC_IP = ${CORE_LAN_IP}|" /usr/local/etc/flexric/flexric.conf
echo "[CORE] flexric.conf:"; cat /usr/local/etc/flexric/flexric.conf

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
# 8. Start the native RIC.
#
#    Runs under systemd-style nohup so it survives this script exiting. Its log
#    is at /local/logs/nearRT-RIC.log.
#
#    Do NOT auto-start an xApp here. An xApp started before the gNBs have
#    completed E2 setup crashes and leaves an orphaned SCTP association that
#    poisons the RIC's E42 state from boot. Run xApps by hand, after the cells
#    have registered:
#
#      cd /opt/oai-src/openair2/E2AP/flexric
#      ./build/examples/xApp/c/monitor/xapp_kpm_moni
# ------------------------------------------------------------------ #
echo "[CORE] Starting nearRT-RIC on ${CORE_LAN_IP}:36421..."
cd /opt/oai-src/openair2/E2AP/flexric
nohup ./build/examples/ric/nearRT-RIC > /local/logs/nearRT-RIC.log 2>&1 &
sleep 10

if grep -q "Loading SM ID = 2" /local/logs/nearRT-RIC.log; then
    echo "[CORE] RIC ready — E2 on ${CORE_LAN_IP}:36421, E42 on ${CORE_LAN_IP}:36422"
    grep -E "nearRT-RIC IP|Loading SM ID" /local/logs/nearRT-RIC.log
else
    echo "[CORE] WARNING: RIC did not report SM loading. See /local/logs/nearRT-RIC.log"
    tail -20 /local/logs/nearRT-RIC.log
fi

# ------------------------------------------------------------------ #
# Done. Cell nodes poll the AMF and attach on their own.
# ------------------------------------------------------------------ #
echo "============================================"
echo "[CORE] completed at $(date)"
echo "============================================"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
