#!/usr/bin/env bash
# =============================================================================
# mgen-preflight.sh — readiness gate for the RFsim MGEN testbed
#
# Per UE, verifies the full chain before a data-collection session:
#   (1) UE container is running
#   (2) UE registered: oaitun_ue1 has an IP in 12.1.1.0/24
#   (3) mgen binary executes in the UE container
#   (4) DN traffic routes via oaitun_ue1, not eth0  (kernel routing check)
#   (5) [full] bidirectional flow passes with counter-proven path integrity:
#       uplink via oaitun_ue1 tx counter, downlink via oaitun_ue1 rx counter
#       — a Docker-bridge bypass cannot pass both counters
# Shared: mgen executes in ext-dn and UPF.
#
# Usage (run as root or via sudo):
#   sudo bash bin/mgen-preflight.sh            # all 12 UEs, full
#   sudo bash bin/mgen-preflight.sh quick      # all 12 UEs, checks 1-4 only (fast)
#   sudo bash bin/mgen-preflight.sh 3          # UE 3 only, full
#
# Exit 0 iff every check passes — use as a deploy gate:
#   sudo bash bin/mgen-preflight.sh quick || exit 1
# =============================================================================
set -u

# ---- configuration --------------------------------------------------------
DN_C="rfsim5g-oai-ext-dn"
UPF_C="rfsim5g-oai-upf"
UL_PORT=5000
DL_PORT=5001
RATE=10
DUR=5
EXPECT=$(( RATE * DUR ))
PASS_MIN=$(( EXPECT * 7 / 10 ))   # 70 %: real failures read ~0; cold-start jitter ~10-15 %
WARMUP=2                           # seconds for receiver to bind before sender starts

# ---- arg parsing ----------------------------------------------------------
MODE="full"
UES="$(seq 1 12)"
case "${1:-}" in
  quick)       MODE="quick" ;;
  ''|*[!0-9]*) : ;;
  *)           UES="$1" ;;
esac

# ---- output helpers -------------------------------------------------------
ok()  { printf "    %-6s %s\n" "OK"   "$*"; }
err() { printf "    %-6s %s\n" "FAIL" "$*"; }

# ---- verify stack is up ---------------------------------------------------
DN_IP=$(docker exec "$DN_C" ip -4 addr show 2>/dev/null \
        | awk '/inet 192\.168\.72\./{print $2}' | cut -d/ -f1 | head -1)
[ -z "$DN_IP" ] && { echo "FATAL: $DN_C not up or has no traffic_net IP — is the stack running?"; exit 1; }

# ---- mgen executable check ------------------------------------------------
# "mgen version" is not a valid mgen command; mgen prints "invalid command: version"
# and exits non-zero. Seeing that string proves the binary ran. A missing binary
# or wrong architecture yields a docker exec error, which does not contain it.
mgen_runs() { docker exec "$1" /usr/bin/mgen version 2>&1 | grep -q "invalid command"; }

# ---- temp file cleanup on exit --------------------------------------------
cleanup() {
    for c in "$DN_C" "$UPF_C"; do
        docker exec "$c" rm -f /tmp/pf_rx_ul.mgn /tmp/pf_rx_ul.log \
                               /tmp/pf_tx_ul.mgn /tmp/pf_rx_dl.mgn \
                               /tmp/pf_rx_dl.log /tmp/pf_tx_dl.mgn 2>/dev/null ||:
    done
    for u in $UES; do
        docker exec "rfsim5g-oai-nr-ue${u}" \
            rm -f /tmp/pf_rx_ul.mgn /tmp/pf_rx_ul.log \
                  /tmp/pf_tx_ul.mgn /tmp/pf_rx_dl.mgn \
                  /tmp/pf_rx_dl.log /tmp/pf_tx_dl.mgn 2>/dev/null ||:
    done
}
trap cleanup EXIT

echo "======================================================================"
echo " MGEN PREFLIGHT    mode=${MODE}    DN=${DN_IP}"
echo "======================================================================"

# ---- shared infra: mgen in ext-dn and UPF --------------------------------
echo "[infra] mgen in shared containers"
n_fail=0
for c in "$DN_C" "$UPF_C"; do
    if mgen_runs "$c"; then ok "$c"
    else err "$c — not runnable (binary missing or glibc mismatch)"; n_fail=$(( n_fail + 1 )); fi
done
echo

# ---- per-UE checks --------------------------------------------------------
ready=0; total=0
for u in $UES; do
    total=$(( total + 1 ))
    UE_C="rfsim5g-oai-nr-ue${u}"
    ufail=0
    echo "[UE${u}] ${UE_C}"

    # (1) container running — skip remaining checks if down to avoid misleading errors
    STATE=$(docker inspect -f '{{.State.Status}}' "$UE_C" 2>/dev/null)
    if [ "$STATE" = "running" ]; then
        ok "container running"
    else
        err "container not running (state=${STATE:-missing}) — skipping remaining checks"
        echo "    => UE${u} NOT READY"; echo
        n_fail=$(( n_fail + 1 )); continue
    fi

    # (2) oaitun_ue1 registered with IP in 12.1.1.0/24
    # Validating the subnet catches a misconfigured PDU session that assigns
    # a wrong IP — the interface would exist but routing would still be broken.
    UE_IP=$(docker exec "$UE_C" ip -4 addr show oaitun_ue1 2>/dev/null \
            | awk '/inet /{print $2}' | cut -d/ -f1)
    if [ -z "$UE_IP" ]; then
        err "oaitun_ue1 has no IP (UE not attached)"; ufail=1
    elif echo "$UE_IP" | grep -qE '^12\.1\.1\.'; then
        ok "registered, oaitun_ue1=${UE_IP}"
    else
        err "oaitun_ue1 IP=${UE_IP} outside 12.1.1.0/24 — misconfigured PDU session"; ufail=1
    fi

    # (3) mgen runs in UE
    if mgen_runs "$UE_C"; then ok "mgen executable"
    else err "mgen not runnable (binary missing or glibc mismatch)"; ufail=1; fi

    # (4) DN routes via tunnel, not eth0
    RGET=$(docker exec "$UE_C" ip route get "$DN_IP" 2>/dev/null)
    if echo "$RGET" | grep -q "oaitun_ue1"; then
        ok "DN (${DN_IP}) routes via oaitun_ue1"
    else
        DEV=$(echo "$RGET" | grep -o 'dev [^ ]*' | head -1)
        err "DN routes via ${DEV:-unknown} — bypass risk; fix: docker exec ${UE_C} ip route replace 192.168.72.128/26 dev oaitun_ue1"
        ufail=1
    fi

    # (5) bidirectional traffic with counter-proven path integrity
    if [ "$MODE" = "full" ] && [ "$ufail" -eq 0 ]; then

        # uplink: UE → ext-dn, proven by oaitun_ue1 tx counter
        docker exec "$DN_C" bash -c \
            "printf '0.0 LISTEN UDP ${UL_PORT}\n' > /tmp/pf_rx_ul.mgn"
        docker exec -d "$DN_C" bash -c \
            "timeout $(( DUR + WARMUP + 2 )) mgen input /tmp/pf_rx_ul.mgn output /tmp/pf_rx_ul.log"
        sleep "$WARMUP"

        TX0=$(docker exec "$UE_C" cat /sys/class/net/oaitun_ue1/statistics/tx_packets 2>/dev/null); TX0=${TX0:-0}
        docker exec "$UE_C" bash -c \
            "printf '0.0 ON 1 UDP DST ${DN_IP}/${UL_PORT} PERIODIC [${RATE} 1000]\n${DUR}.0 OFF 1\n' > /tmp/pf_tx_ul.mgn; \
             timeout $(( DUR + 2 )) mgen input /tmp/pf_tx_ul.mgn" >/dev/null 2>&1
        TX1=$(docker exec "$UE_C" cat /sys/class/net/oaitun_ue1/statistics/tx_packets 2>/dev/null); TX1=${TX1:-0}
        sleep 1

        UL_RECV=$(docker exec "$DN_C" sh -c \
            "grep -c RECV /tmp/pf_rx_ul.log 2>/dev/null" | tr -dc '0-9'); UL_RECV=${UL_RECV:-0}
        TXD=$(( TX1 - TX0 ))

        if   [ "$UL_RECV" -ge "$PASS_MIN" ] && [ "$TXD" -ge "$PASS_MIN" ]; then
            ok "uplink ${UL_RECV}/${EXPECT} pkt, oaitun_ue1 tx +${TXD} (tunnel confirmed)"
        elif [ "$UL_RECV" -ge "$PASS_MIN" ]; then
            err "uplink arrived but bypassed tunnel (oaitun tx +${TXD}) — INVALID PATH"; ufail=1
        else
            err "uplink ${UL_RECV}/${EXPECT} pkt (oaitun tx +${TXD})"; ufail=1
        fi

        # downlink: ext-dn → UE, proven by oaitun_ue1 rx counter
        # 12.1.1.x is only assigned to oaitun_ue1, so any arriving packet
        # necessarily traversed the 5G stack; the rx counter makes it explicit
        # and symmetric with the uplink check.
        docker exec "$UE_C" bash -c \
            "printf '0.0 LISTEN UDP ${DL_PORT}\n' > /tmp/pf_rx_dl.mgn"
        docker exec -d "$UE_C" bash -c \
            "timeout $(( DUR + WARMUP + 2 )) mgen input /tmp/pf_rx_dl.mgn output /tmp/pf_rx_dl.log"
        sleep "$WARMUP"

        RX0=$(docker exec "$UE_C" cat /sys/class/net/oaitun_ue1/statistics/rx_packets 2>/dev/null); RX0=${RX0:-0}
        docker exec "$DN_C" bash -c \
            "printf '0.0 ON 1 UDP DST ${UE_IP}/${DL_PORT} PERIODIC [${RATE} 1000]\n${DUR}.0 OFF 1\n' > /tmp/pf_tx_dl.mgn; \
             timeout $(( DUR + 2 )) mgen input /tmp/pf_tx_dl.mgn" >/dev/null 2>&1
        RX1=$(docker exec "$UE_C" cat /sys/class/net/oaitun_ue1/statistics/rx_packets 2>/dev/null); RX1=${RX1:-0}
        sleep 1

        DL_RECV=$(docker exec "$UE_C" sh -c \
            "grep -c RECV /tmp/pf_rx_dl.log 2>/dev/null" | tr -dc '0-9'); DL_RECV=${DL_RECV:-0}
        RXD=$(( RX1 - RX0 ))

        if   [ "$DL_RECV" -ge "$PASS_MIN" ] && [ "$RXD" -ge "$PASS_MIN" ]; then
            ok "downlink ${DL_RECV}/${EXPECT} pkt, oaitun_ue1 rx +${RXD} (tunnel confirmed)"
        elif [ "$DL_RECV" -ge "$PASS_MIN" ]; then
            err "downlink arrived but rx counter mismatch (oaitun rx +${RXD})"; ufail=1
        else
            err "downlink ${DL_RECV}/${EXPECT} pkt (oaitun rx +${RXD})"; ufail=1
        fi
    fi

    if [ "$ufail" -eq 0 ]; then
        echo "    => UE${u} READY"; ready=$(( ready + 1 ))
    else
        echo "    => UE${u} NOT READY"; n_fail=$(( n_fail + 1 ))
    fi
    echo
done

echo "======================================================================"
if [ "$n_fail" -eq 0 ]; then
    echo " RESULT: ${ready}/${total} UEs READY — ALL PASS"
else
    echo " RESULT: ${ready}/${total} UEs ready — ATTENTION NEEDED (${n_fail} item(s) failed)"
fi
echo "======================================================================"
exit $(( n_fail > 0 ? 1 : 0 ))
