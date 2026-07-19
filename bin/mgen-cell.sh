#!/usr/bin/env bash
# Cell-side MGEN helper for the distributed POWDER profile.
# Run on a cell node through sudo. It only operates on cell-local UE containers.

set -euo pipefail

DN_IP="${MGEN_DN_IP:-192.168.72.135}"
DN_SUBNET="${MGEN_DN_SUBNET:-192.168.72.128/26}"
LOG_DIR_HOST="${MGEN_HOST_LOG_DIR:-/local/logs/mgen}"
LOG_DIR_CONTAINER="${MGEN_CONTAINER_LOG_DIR:-/logs/mgen}"

usage() {
    cat <<'EOF'
Usage:
  sudo bash bin/mgen-cell.sh check CELL UES_PER_CELL [UE]
  sudo bash bin/mgen-cell.sh send-ul RUN_ID CELL UE PORT RATE SIZE DURATION
  sudo bash bin/mgen-cell.sh listen-dl RUN_ID CELL UE PORT DURATION
  sudo bash bin/mgen-cell.sh count-dl RUN_ID CELL UE
EOF
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

require_uint() {
    [[ "$2" =~ ^[0-9]+$ ]] || die "$1 must be an unsigned integer: $2"
}

require_run_id() {
    [[ "$1" =~ ^[A-Za-z0-9_.-]+$ ]] || die "invalid run ID: $1"
}

ue_container() {
    printf 'ric5g-ue-cell%s-%s' "$1" "$2"
}

container_running() {
    [ "$(docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null || true)" = "running" ]
}

mgen_runs() {
    local output
    output=$(docker exec "$1" /usr/bin/mgen version 2>&1 || true)
    grep -q "invalid command" <<<"$output"
}

ue_ip() {
    docker exec "$1" ip -4 -o addr show oaitun_ue1 2>/dev/null |
        awk '$4 ~ /^12\.1\.1\./ {sub(/\/.*/, "", $4); print $4; exit}'
}

ensure_ready() {
    local container=$1 ip route
    container_running "$container" || die "$container is not running"
    mgen_runs "$container" || die "MGEN is not runnable in $container"
    ip=$(ue_ip "$container")
    [ -n "$ip" ] || die "$container has no 12.1.1.x address on oaitun_ue1"
    docker exec "$container" ip route replace "$DN_SUBNET" dev oaitun_ue1
    route=$(docker exec "$container" ip route get "$DN_IP" 2>/dev/null || true)
    grep -q 'dev oaitun_ue1' <<<"$route" || die "$container routes the DN outside oaitun_ue1"
    printf '%s' "$ip"
}

case "${1:-}" in
check)
    [ "$#" -ge 3 ] && [ "$#" -le 4 ] || { usage; exit 2; }
    CELL=$2 UES_PER_CELL=$3 UE_ONLY=${4:-}
    require_uint CELL "$CELL"
    require_uint UES_PER_CELL "$UES_PER_CELL"
    [ -z "$UE_ONLY" ] || require_uint UE "$UE_ONLY"

    mkdir -p "$LOG_DIR_HOST"
    if [ -n "$UE_ONLY" ]; then
        UES=$UE_ONLY
    else
        UES=$(seq 1 "$UES_PER_CELL")
    fi

    failures=0
    for UE in $UES; do
        C=$(ue_container "$CELL" "$UE")
        if IP=$(ensure_ready "$C"); then
            echo "OK: $C attached as $IP; MGEN works; DN route uses oaitun_ue1"
        else
            failures=$((failures + 1))
        fi
    done
    [ "$failures" -eq 0 ] || die "$failures UE readiness check(s) failed"
    ;;

send-ul)
    [ "$#" -eq 8 ] || { usage; exit 2; }
    RUN_ID=$2 CELL=$3 UE=$4 PORT=$5 RATE=$6 SIZE=$7 DURATION=$8
    require_run_id "$RUN_ID"
    require_uint CELL "$CELL"
    require_uint UE "$UE"
    require_uint PORT "$PORT"
    require_uint RATE "$RATE"
    require_uint SIZE "$SIZE"
    require_uint DURATION "$DURATION"
    C=$(ue_container "$CELL" "$UE")
    IP=$(ensure_ready "$C")
    FLOW_ID=$((CELL * 100 + UE))
    BASE="$LOG_DIR_CONTAINER/$RUN_ID-ul-cell${CELL}-ue${UE}-tx"
    TX0=$(docker exec "$C" cat /sys/class/net/oaitun_ue1/statistics/tx_packets)
    docker exec "$C" bash -lc \
        "mkdir -p '$LOG_DIR_CONTAINER'; \
         printf '0.0 ON %s UDP DST %s/%s PERIODIC [%s %s]\\n%s.0 OFF %s\\n' \
         '$FLOW_ID' '$DN_IP' '$PORT' '$RATE' '$SIZE' '$DURATION' '$FLOW_ID' > '${BASE}.mgn'; \
         timeout '$((DURATION + 5))' /usr/bin/mgen input '${BASE}.mgn' output '${BASE}.log'; \
         rc=\$?; [ \"\$rc\" -eq 0 ] || [ \"\$rc\" -eq 124 ]"
    TX1=$(docker exec "$C" cat /sys/class/net/oaitun_ue1/statistics/tx_packets)
    echo "UE_IP=$IP TX_DELTA=$((TX1 - TX0))"
    ;;

listen-dl)
    [ "$#" -eq 6 ] || { usage; exit 2; }
    RUN_ID=$2 CELL=$3 UE=$4 PORT=$5 DURATION=$6
    require_run_id "$RUN_ID"
    require_uint CELL "$CELL"
    require_uint UE "$UE"
    require_uint PORT "$PORT"
    require_uint DURATION "$DURATION"
    C=$(ue_container "$CELL" "$UE")
    IP=$(ensure_ready "$C")
    BASE="$LOG_DIR_CONTAINER/$RUN_ID-dl-cell${CELL}-ue${UE}-rx"
    RX0=$(docker exec "$C" cat /sys/class/net/oaitun_ue1/statistics/rx_packets)
    printf '%s\n' "$RX0" > "$LOG_DIR_HOST/$RUN_ID-dl-cell${CELL}-ue${UE}.rx0"
    docker exec "$C" bash -lc \
        "mkdir -p '$LOG_DIR_CONTAINER'; printf '0.0 LISTEN UDP %s\\n' '$PORT' > '${BASE}.mgn'; : > '${BASE}.log'"
    docker exec -d "$C" bash -lc \
        "timeout '$((DURATION + 5))' /usr/bin/mgen input '${BASE}.mgn' output '${BASE}.log'"
    echo "UE_IP=$IP"
    ;;

count-dl)
    [ "$#" -eq 4 ] || { usage; exit 2; }
    RUN_ID=$2 CELL=$3 UE=$4
    require_run_id "$RUN_ID"
    require_uint CELL "$CELL"
    require_uint UE "$UE"
    C=$(ue_container "$CELL" "$UE")
    BASE="$LOG_DIR_CONTAINER/$RUN_ID-dl-cell${CELL}-ue${UE}-rx"
    RX0_FILE="$LOG_DIR_HOST/$RUN_ID-dl-cell${CELL}-ue${UE}.rx0"
    [ -f "$RX0_FILE" ] || die "missing receive-counter baseline: $RX0_FILE"
    RX0=$(<"$RX0_FILE")
    RX1=$(docker exec "$C" cat /sys/class/net/oaitun_ue1/statistics/rx_packets)
    COUNT=$(docker exec "$C" sh -lc "grep -c RECV '${BASE}.log' 2>/dev/null || true")
    COUNT=${COUNT//[^0-9]/}
    echo "DL_RECV=${COUNT:-0} RX_DELTA=$((RX1 - RX0))"
    ;;

*)
    usage
    exit 2
    ;;
esac
