#!/bin/bash
# =============================================================================
# cell-setup.sh — cell node: one E2-enabled, chanmod-capable gNB + K UEs
#
# Usage: cell-setup.sh <cell_idx> <num_cells> <ues_per_cell>
#
# The gNB runs on the host network (N2/N3/E2 originate from this node's LAN IP).
# Its UEs are colocated on a cell-local bridge so the RFsim IQ path stays local
# and the channel model behaves as in the validated single-node baseline.
#
# Cross-node reachability: the core node's Docker bridges (192.168.71/72.128/26)
# are reached via a route through the core node's LAN IP, with ip_forward enabled
# on the core. This is the same hop pattern validated on the OTA track.
# =============================================================================

set +e

CELL_IDX="${1:-1}"
NUM_CELLS="${2:-2}"
UES_PER_CELL="${3:-12}"

CORE_LAN_IP="10.10.1.1"
CELL_LAN_IP="10.10.1.$((10 + CELL_IDX))"

AMF_IP="192.168.71.132"
UPF_IP="192.168.71.134"
RIC_IP="192.168.71.142"
DN_SUBNET="192.168.72.128/26"

# Per-cell identities. These MUST be unique across cells or the AMF/RIC will
# reject or confuse the second gNB.
GNB_ID=$((0xe00 + CELL_IDX - 1))
PCI=$((CELL_IDX - 1))
NR_CELL_ID=$((12345678 + CELL_IDX - 1))
UE_SUBNET="172.$((20 + CELL_IDX)).0.0/24"

# IMSI slice for this cell: base + (cell-1)*K .. + K-1
IMSI_BASE=208990100001100
IMSI_START=$((IMSI_BASE + (CELL_IDX - 1) * UES_PER_CELL))

mkdir -p /local/logs

echo "============================================"
echo "[CELL${CELL_IDX}] started at $(date)"
echo "[CELL${CELL_IDX}] lan=${CELL_LAN_IP} gnb_id=${GNB_ID} pci=${PCI}"
echo "[CELL${CELL_IDX}] imsi ${IMSI_START}..$((IMSI_START + UES_PER_CELL - 1))"
echo "============================================"

# ------------------------------------------------------------------ #
# 1. Install Docker
# ------------------------------------------------------------------ #
apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# ------------------------------------------------------------------ #
# 2. Route the core's Docker bridges via the core node.
#    Without this the gNB cannot reach the AMF, UPF or RIC.
# ------------------------------------------------------------------ #
echo "[CELL${CELL_IDX}] Adding routes to core bridges via ${CORE_LAN_IP}..."
sysctl -w net.ipv4.ip_forward=1
ip route replace 192.168.71.128/26 via ${CORE_LAN_IP}
ip route replace 192.168.72.128/26 via ${CORE_LAN_IP}
ip route show | grep 192.168.7

# ------------------------------------------------------------------ #
# 3. Wait for the core: AMF must be reachable before the gNB starts.
# ------------------------------------------------------------------ #
echo "[CELL${CELL_IDX}] Waiting for AMF at ${AMF_IP}..."
MAX_WAIT=900
ELAPSED=0
until ping -c1 -W2 "${AMF_IP}" >/dev/null 2>&1; do
    if [ "$ELAPSED" -ge "$MAX_WAIT" ]; then
        echo "[CELL${CELL_IDX}] WARNING: AMF unreachable after ${MAX_WAIT}s."
        break
    fi
    echo "[CELL${CELL_IDX}] waiting for core... (${ELAPSED}s)"
    sleep 15
    ELAPSED=$((ELAPSED + 15))
done
echo "[CELL${CELL_IDX}] Core reachable."

# ------------------------------------------------------------------ #
# 4. Pull images
# ------------------------------------------------------------------ #
docker pull ghinwa555/oai-gnb-e2-chan:v1
docker pull ghinwa555/oai-nr-ue-chan:v1

# ------------------------------------------------------------------ #
# 5. Generate this cell's gNB config
#    - e2_agent block pointing at the RIC on the core node
#    - rfsimulator block with options=("chanmod")  [NOT a CLI flag]
#    - @include by BARE FILENAME, file co-located (absolute paths fail)
#    - NGU advertises this node's LAN IP so downlink GTP-U comes back here
# ------------------------------------------------------------------ #
cd /local/repository/etc

sed -e "s/@CELL_IDX@/${CELL_IDX}/g" \
    -e "s/@GNB_ID@/${GNB_ID}/g" \
    -e "s/@PCI@/${PCI}/g" \
    -e "s/@NR_CELL_ID@/${NR_CELL_ID}/g" \
    -e "s/@AMF_IP@/${AMF_IP}/g" \
    -e "s/@RIC_IP@/${RIC_IP}/g" \
    -e "s/@CELL_LAN_IP@/${CELL_LAN_IP}/g" \
    gnb-cell.conf.tmpl > gnb-cell${CELL_IDX}.conf

# One channel model per UE. RFsim needs rfsimu_channel_ue0..ue(K-1); with only
# ue0 defined, every UE after the first logs "Model rfsimu_channel_ueN not
# found" and runs with NO channel at all. CHANMOD_MODE/TYPE are overridable.
CHANMOD_MODE="${CHANMOD_MODE:-uniform}"
CHANMOD_TYPE="${CHANMOD_TYPE:-AWGN}"
bash /local/repository/bin/gen-channelmod.sh "${UES_PER_CELL}" \
     "channelmod-cell${CELL_IDX}.conf" "${CHANMOD_MODE}" "${CHANMOD_TYPE}"

echo "[CELL${CELL_IDX}] gNB config generated:"
grep -E "gNB_ID|physCellId|nr_cellid|amf_ip|near_ric|options|min_rxtxtime|GNB_IPV4" \
     gnb-cell${CELL_IDX}.conf

# ------------------------------------------------------------------ #
# 6. Generate the compose file (gNB + K UEs)
# ------------------------------------------------------------------ #
COMPOSE="docker-compose-cell${CELL_IDX}.yaml"

cat > "${COMPOSE}" <<EOF
services:
  oai-gnb:
    container_name: ric5g-gnb-cell${CELL_IDX}
    image: ghinwa555/oai-gnb-e2-chan:v1
    network_mode: host
    cap_drop:
      - ALL
    environment:
      TZ: Europe/Paris
      ASAN_OPTIONS: detect_leaks=0
      USE_ADDITIONAL_OPTIONS: "-E --rfsim --gNBs.[0].min_rxtxtime 3 --log_config.global_log_options level,nocolor,time"
    volumes:
      - ./gnb-cell${CELL_IDX}.conf:/opt/oai-gnb/etc/gnb.conf
      - ./channelmod-cell${CELL_IDX}.conf:/opt/oai-gnb/etc/channelmod_rfsimu.conf
    healthcheck:
      test: /bin/bash -c "pgrep nr-softmodem"
      interval: 10s
      timeout: 5s
      retries: 10
EOF

for u in $(seq 1 "${UES_PER_CELL}"); do
    IMSI=$((IMSI_START + u - 1))
    UE_IP="172.$((20 + CELL_IDX)).0.$((10 + u))"

    # Per-UE config: the rfsimulator block stock ue.conf does NOT have, with
    # chanmod enabled and serveraddr pointing at the gNB on the host.
    sed -e "s/@IMSI@/${IMSI}/g" \
        -e "s/@GNB_ADDR@/${CELL_LAN_IP}/g" \
        nr-ue.conf.tmpl > nr-ue-cell${CELL_IDX}-${u}.conf

    cat >> "${COMPOSE}" <<EOF

  oai-nr-ue${u}:
    container_name: ric5g-ue-cell${CELL_IDX}-${u}
    image: ghinwa555/oai-nr-ue-chan:v1
    cap_drop:
      - ALL
    cap_add:
      - NET_ADMIN
      - NET_RAW
    devices:
      - /dev/net/tun:/dev/net/tun
    environment:
      TZ: Europe/Paris
      ASAN_OPTIONS: detect_leaks=0
      USE_ADDITIONAL_OPTIONS: "-E --rfsim -r 106 --numerology 1 --uicc0.imsi ${IMSI} -C 3319680000 --log_config.global_log_options level,nocolor,time"
    volumes:
      - ./nr-ue-cell${CELL_IDX}-${u}.conf:/opt/oai-nr-ue/etc/nr-ue.conf
      - ./channelmod-cell${CELL_IDX}.conf:/opt/oai-nr-ue/etc/channelmod_rfsimu.conf
      - ../bin/mgen:/usr/bin/mgen:ro
    depends_on:
      - oai-gnb
    networks:
      ue_net:
        ipv4_address: ${UE_IP}
EOF
done

cat >> "${COMPOSE}" <<EOF

networks:
  ue_net:
    driver: bridge
    name: ric5g-ue-cell${CELL_IDX}
    ipam:
      config:
        - subnet: ${UE_SUBNET}
EOF

echo "[CELL${CELL_IDX}] compose generated: ${COMPOSE}"

# ------------------------------------------------------------------ #
# 7. mgen must be present (bind-mounted into every UE)
# ------------------------------------------------------------------ #
MGEN_BIN="/local/repository/bin/mgen"
if [ ! -f "$MGEN_BIN" ] || [ ! -x "$MGEN_BIN" ]; then
    echo "[CELL${CELL_IDX}] ERROR: $MGEN_BIN missing or not executable. Aborting."
    exit 1
fi

# ------------------------------------------------------------------ #
# 8. Start the cell
# ------------------------------------------------------------------ #
docker compose -f "${COMPOSE}" up -d

# ------------------------------------------------------------------ #
# 9. Verify the gNB: chanmod active + E2 connected
# ------------------------------------------------------------------ #
sleep 45
echo "[CELL${CELL_IDX}] chanmod check (expect [OCM] model allocated):"
docker logs ric5g-gnb-cell${CELL_IDX} 2>&1 | grep -i "OCM" | head -4
echo "[CELL${CELL_IDX}] E2 check:"
docker logs ric5g-gnb-cell${CELL_IDX} 2>&1 | grep -iE "E2 SETUP|E2 agent" | head -4

# ------------------------------------------------------------------ #
# 10. Wait for UEs, restart any stuck in REG-INITIATED (known race)
# ------------------------------------------------------------------ #
echo "[CELL${CELL_IDX}] Waiting for UEs to attach (180s)..."
sleep 180

STUCK=""
for u in $(seq 1 "${UES_PER_CELL}"); do
    IMSI=$((IMSI_START + u - 1))
    if ! docker exec ric5g-ue-cell${CELL_IDX}-${u} ip link show oaitun_ue1 >/dev/null 2>&1; then
        echo "[CELL${CELL_IDX}] UE${u} (${IMSI}) has no tunnel, restarting..."
        docker restart ric5g-ue-cell${CELL_IDX}-${u}
        STUCK="$STUCK UE${u}"
    fi
done
[ -n "$STUCK" ] && { echo "[CELL${CELL_IDX}] restarted:$STUCK — waiting 90s..."; sleep 90; }

# ------------------------------------------------------------------ #
# 11. DN route per UE. Without this the kernel sends app traffic out eth0
#     (the control bridge), bypassing the 5G stack entirely and producing
#     traffic that looks fine but never touched the RAN.
# ------------------------------------------------------------------ #
echo "[CELL${CELL_IDX}] Adding DN route (${DN_SUBNET}) via oaitun_ue1..."
for u in $(seq 1 "${UES_PER_CELL}"); do
    C="ric5g-ue-cell${CELL_IDX}-${u}"
    W=0
    until docker exec "$C" ip link show oaitun_ue1 >/dev/null 2>&1; do
        if [ "$W" -ge 120 ]; then
            echo "[CELL${CELL_IDX}] WARNING: UE${u}: no oaitun_ue1 after 120s — skipping"
            break
        fi
        sleep 5
        W=$((W + 5))
    done
    if docker exec "$C" ip link show oaitun_ue1 >/dev/null 2>&1; then
        docker exec "$C" ip route replace ${DN_SUBNET} dev oaitun_ue1 \
            && echo "[CELL${CELL_IDX}] UE${u}: DN route added" \
            || echo "[CELL${CELL_IDX}] WARNING: UE${u}: DN route failed"
    fi
done

echo "============================================"
echo "[CELL${CELL_IDX}] completed at $(date)"
echo "============================================"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
