#!/usr/bin/env bash
# Coordinate a bounded, bidirectional MGEN validation from the operator laptop.
# This script intentionally does not auto-start traffic during profile setup.

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  bash bin/mgen-run-distributed.sh CORE_SSH CELL_SSH CELL [UE|all] [quick|full]

Examples:
  bash bin/mgen-run-distributed.sh ghinwa@pc816.emulab.net ghinwa@pc03-meb.emulab.net 1 1 full
  bash bin/mgen-run-distributed.sh ghinwa@pc816.emulab.net ghinwa@pc03-meb.emulab.net 1 all quick

Optional environment variables:
  UES_PER_CELL=12  RATE=10  SIZE=1000  DURATION=5  WARMUP=2

RATE is packets/second and SIZE is bytes/packet. The defaults generate about
80 kbit/s per active flow and are intended as a safe preflight load.
EOF
}

[ "$#" -ge 3 ] && [ "$#" -le 5 ] || { usage; exit 2; }

CORE_HOST=$1
CELL_HOST=$2
CELL=$3
UE_SPEC=${4:-all}
MODE=${5:-full}

UES_PER_CELL=${UES_PER_CELL:-12}
RATE=${RATE:-10}
SIZE=${SIZE:-1000}
DURATION=${DURATION:-5}
WARMUP=${WARMUP:-2}
RUN_ID="mgen-$(date +%Y%m%d-%H%M%S)-cell${CELL}"
EXPECT=$((RATE * DURATION))
PASS_MIN=$((EXPECT * 7 / 10))

for value in "$CELL" "$UES_PER_CELL" "$RATE" "$SIZE" "$DURATION" "$WARMUP"; do
    [[ "$value" =~ ^[0-9]+$ ]] || { echo "ERROR: numeric argument expected: $value" >&2; exit 2; }
done
case "$MODE" in quick|full) ;; *) usage; exit 2 ;; esac
if [ "$UE_SPEC" = all ]; then
    UES=$(seq 1 "$UES_PER_CELL")
elif [[ "$UE_SPEC" =~ ^[0-9]+$ ]] && [ "$UE_SPEC" -ge 1 ] && [ "$UE_SPEC" -le "$UES_PER_CELL" ]; then
    UES=$UE_SPEC
else
    echo "ERROR: UE must be 1..$UES_PER_CELL or all" >&2
    exit 2
fi

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15)
core() {
    ssh "${SSH_OPTS[@]}" "$CORE_HOST" \
        sudo bash /local/repository/bin/mgen-core.sh "$@"
}
cell() {
    ssh "${SSH_OPTS[@]}" "$CELL_HOST" \
        sudo bash /local/repository/bin/mgen-cell.sh "$@"
}
value_from() {
    local key=$1 text=$2
    awk -v key="$key" '{for (i=1; i<=NF; i++) if ($i ~ ("^" key "=")) {sub("^" key "=", "", $i); print $i}}' <<<"$text" | tail -1
}

echo "Run ID: $RUN_ID"
echo "Core:   $CORE_HOST"
echo "Cell:   $CELL_HOST (cell $CELL)"
echo "Mode:   $MODE"
echo

core check
if [ "$UE_SPEC" = all ]; then
    cell check "$CELL" "$UES_PER_CELL"
else
    cell check "$CELL" "$UES_PER_CELL" "$UE_SPEC"
fi
[ "$MODE" = quick ] && { echo "QUICK PREFLIGHT PASSED"; exit 0; }

failures=0
for UE in $UES; do
    UL_PORT=$((5000 + CELL * 100 + UE))
    DL_PORT=$((6000 + CELL * 100 + UE))
    echo
    echo "=== cell${CELL}/UE${UE} ==="

    core listen-ul "$RUN_ID" "$CELL" "$UE" "$UL_PORT" "$DURATION"
    sleep "$WARMUP"
    UL_TX=$(cell send-ul "$RUN_ID" "$CELL" "$UE" "$UL_PORT" "$RATE" "$SIZE" "$DURATION")
    echo "$UL_TX"
    UE_IP=$(value_from UE_IP "$UL_TX")
    TX_DELTA=$(value_from TX_DELTA "$UL_TX")
    sleep 1
    UL_RX=$(core count-ul "$RUN_ID" "$CELL" "$UE")
    echo "$UL_RX"
    UL_RECV=$(value_from UL_RECV "$UL_RX")

    DL_READY=$(cell listen-dl "$RUN_ID" "$CELL" "$UE" "$DL_PORT" "$DURATION")
    echo "$DL_READY"
    [ -n "$UE_IP" ] || UE_IP=$(value_from UE_IP "$DL_READY")
    sleep "$WARMUP"
    core send-dl "$RUN_ID" "$CELL" "$UE" "$UE_IP" "$DL_PORT" "$RATE" "$SIZE" "$DURATION"
    sleep 1
    DL_RX=$(cell count-dl "$RUN_ID" "$CELL" "$UE")
    echo "$DL_RX"
    DL_RECV=$(value_from DL_RECV "$DL_RX")
    RX_DELTA=$(value_from RX_DELTA "$DL_RX")

    if [ "${UL_RECV:-0}" -ge "$PASS_MIN" ] && [ "${TX_DELTA:-0}" -ge "$PASS_MIN" ] &&
       [ "${DL_RECV:-0}" -ge "$PASS_MIN" ] && [ "${RX_DELTA:-0}" -ge "$PASS_MIN" ]; then
        echo "PASS: UL ${UL_RECV}/${EXPECT}, DL ${DL_RECV}/${EXPECT}; tunnel counters agree"
    else
        echo "FAIL: UL recv=${UL_RECV:-0} tx_delta=${TX_DELTA:-0}; DL recv=${DL_RECV:-0} rx_delta=${RX_DELTA:-0}"
        failures=$((failures + 1))
    fi
done

echo
echo "Logs are under /local/logs/mgen on the core and selected cell."
if [ "$failures" -eq 0 ]; then
    echo "MGEN DISTRIBUTED PREFLIGHT PASSED"
else
    echo "MGEN DISTRIBUTED PREFLIGHT FAILED: $failures UE(s)"
    exit 1
fi
