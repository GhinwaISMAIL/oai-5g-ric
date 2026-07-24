#!/bin/bash
# =============================================================================
# core-setup.sh — core node: 5G core NFs + FlexRIC near-RT RIC (built natively)
#
# Runs on the single "core" node. Brings up the CN and the RIC, then enables
# forwarding so cell nodes can reach the Docker bridges (192.168.71.128/26 and
# 192.168.72.128/26) across the experimental LAN.
#
# Cell nodes wait for the AMF to be reachable before starting their gNBs.
# =============================================================================

set +e

CORE_LAN_IP="${1:-10.10.1.1}"
OAI_COMMIT="70508ebaf52f2aae420566d380c6537f2efb9f0c"
FLEXRIC_COMMIT="ef6d722f22191eea74089966983da1f5ec1fedd4"

mkdir -p /local/logs /local/logs/xapp /local/logs/mgen
chmod 777 /local/logs/xapp /local/logs/mgen

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
git clone https://gitlab.eurecom.fr/oai/openairinterface5g.git /opt/oai-src \
    || exit 1
cd /opt/oai-src
git checkout --detach "${OAI_COMMIT}" || exit 1
git submodule update --init --recursive || exit 1

ACTUAL_OAI_COMMIT="$(git rev-parse HEAD)"
ACTUAL_FLEXRIC_COMMIT="$(git -C openair2/E2AP/flexric rev-parse HEAD)"
if [ "${ACTUAL_OAI_COMMIT}" != "${OAI_COMMIT}" ]; then
    echo "[CORE] ERROR: OAI ${ACTUAL_OAI_COMMIT}, expected ${OAI_COMMIT}"
    exit 1
fi
if [ "${ACTUAL_FLEXRIC_COMMIT}" != "${FLEXRIC_COMMIT}" ]; then
    echo "[CORE] ERROR: FlexRIC ${ACTUAL_FLEXRIC_COMMIT}, expected ${FLEXRIC_COMMIT}"
    exit 1
fi
echo "[CORE] OAI=${ACTUAL_OAI_COMMIT} FlexRIC=${ACTUAL_FLEXRIC_COMMIT}"

python3 /local/repository/bin/patch-flexric-sqlite-buffers.py \
    /opt/oai-src/openair2/E2AP/flexric/src/xApp/db/sqlite3/sqlite3_wrapper.c \
    || exit 1
python3 /local/repository/bin/patch-flexric-receipt-timestamps.py \
    /opt/oai-src/openair2/E2AP/flexric/src/xApp/db/sqlite3/sqlite3_wrapper.c \
    || exit 1
python3 /local/repository/bin/patch-flexric-iapp-log.py \
    /opt/oai-src/openair2/E2AP/flexric/src/ric/iApps/stdout.c \
    || exit 1
# The patch scripts above insert SQL schema strings with trailing whitespace.
# git diff --check exits non-zero on whitespace errors, so normalise first or
# the check below fails on every run and the FlexRIC build never starts.
sed -i 's/[[:space:]]*$//' \
    /opt/oai-src/openair2/E2AP/flexric/src/xApp/db/sqlite3/sqlite3_wrapper.c \
    /opt/oai-src/openair2/E2AP/flexric/src/ric/iApps/stdout.c

git -C /opt/oai-src/openair2/E2AP/flexric diff --check || exit 1

cd /opt/oai-src/openair2/E2AP/flexric
mkdir -p build && cd build
cmake -DKPM_VERSION=KPM_V3_00 -DE2AP_VERSION=E2AP_V3 -DXAPP_DB=SQLITE3_XAPP .. \
    || exit 1
make -j"$(nproc)" || exit 1
make install || exit 1
ls -1 /usr/local/lib/flexric/

# make install OVERWRITES flexric.conf and resets NEAR_RIC_IP to 127.0.0.1.
# Rewrite it afterwards, or the cell nodes cannot reach the RIC.
sed -i "s|^NEAR_RIC_IP.*|NEAR_RIC_IP = ${CORE_LAN_IP}|" /usr/local/etc/flexric/flexric.conf
echo "[CORE] flexric.conf:"; cat /usr/local/etc/flexric/flexric.conf

# ------------------------------------------------------------------ #
# 7. Start the core network.
#    MUST cd back to etc/ first. The FlexRIC build above leaves the shell in
#    /opt/oai-src/openair2/E2AP/flexric/build, where docker-compose-core.yaml
#    does not exist. Without this cd, compose fails with "no configuration file
#    provided", set +e swallows it, and the entire core silently never starts --
#    the AMF wait below then burns its full timeout against containers that were
#    never created.
# ------------------------------------------------------------------ #
echo "[CORE] Starting core network..."
cd /local/repository/etc
docker compose -f docker-compose-core.yaml up -d

# ------------------------------------------------------------------ #
# 8. Wait for AMF
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
# 9. Start the native RIC.
#
#    Runs under nohup so it survives this script exiting. Its log is at
#    /local/logs/nearRT-RIC.log.
#
#    stdbuf -oL -eL is REQUIRED: FlexRIC block-buffers stdout when it is not
#    writing to a TTY (here it writes to a file). Without line-buffering the log
#    stays empty for a long time after the RIC is already up, and the readiness
#    grep below reports a false "did not report SM loading" warning on a healthy
#    RIC. Line-buffering makes the log reflect the RIC's real state immediately.
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
nohup stdbuf -oL -eL ./build/examples/ric/nearRT-RIC > /local/logs/nearRT-RIC.log 2>&1 &
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
