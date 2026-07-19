#!/usr/bin/env bash
# Core-side MGEN endpoint helper for the distributed POWDER profile.
# Run on the core node through sudo. It only operates on core-local containers.

set -euo pipefail

DN_C="${MGEN_DN_CONTAINER:-ric5g-oai-ext-dn}"
UPF_C="${MGEN_UPF_CONTAINER:-ric5g-oai-upf}"
LOG_DIR="${MGEN_CONTAINER_LOG_DIR:-/logs/mgen}"

usage() {
    cat <<'EOF'
Usage:
  sudo bash bin/mgen-core.sh check
  sudo bash bin/mgen-core.sh listen-ul RUN_ID CELL UE PORT DURATION
  sudo bash bin/mgen-core.sh count-ul RUN_ID CELL UE
  sudo bash bin/mgen-core.sh send-dl RUN_ID CELL UE UE_IP PORT RATE SIZE DURATION

The external-DN container stores logs under /logs/mgen, bind-mounted from
/local/logs/mgen on the core host.
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

require_ue_ip() {
    [[ "$1" =~ ^12\.1\.1\.[0-9]+$ ]] || die "invalid UE PDU address: $1"
}

container_running() {
    [ "$(docker inspect -f '{{.State.Status}}' "$1" 2>/dev/null || true)" = "running" ]
}

mgen_runs() {
    local output
    output=$(docker exec "$1" /usr/bin/mgen version 2>&1 || true)
    grep -q "invalid command" <<<"$output"
}

ul_base() {
    printf '%s/%s-ul-cell%s-ue%s' "$LOG_DIR" "$1" "$2" "$3"
}

case "${1:-}" in
check)
    for container in "$DN_C" "$UPF_C"; do
        container_running "$container" || die "$container is not running"
        mgen_runs "$container" || die "MGEN is not runnable in $container"
        echo "OK: $container is running and MGEN executes"
    done

    DN_IP=$(docker exec "$DN_C" ip -4 -o addr show 2>/dev/null |
        awk '$4 ~ /^192\.168\.72\./ {sub(/\/.*/, "", $4); print $4; exit}')
    [ "$DN_IP" = "192.168.72.135" ] || die "unexpected external-DN address: ${DN_IP:-missing}"
    docker exec "$DN_C" mkdir -p "$LOG_DIR"
    echo "OK: external DN is $DN_IP"
    ;;

listen-ul)
    [ "$#" -eq 6 ] || { usage; exit 2; }
    RUN_ID=$2 CELL=$3 UE=$4 PORT=$5 DURATION=$6
    require_run_id "$RUN_ID"
    require_uint CELL "$CELL"
    require_uint UE "$UE"
    require_uint PORT "$PORT"
    require_uint DURATION "$DURATION"
    container_running "$DN_C" || die "$DN_C is not running"

    BASE=$(ul_base "$RUN_ID" "$CELL" "$UE")
    CFG="${BASE}.mgn"
    LOG="${BASE}.log"
    docker exec "$DN_C" bash -lc \
        "mkdir -p '$LOG_DIR'; printf '0.0 LISTEN UDP %s\\n' '$PORT' > '$CFG'; : > '$LOG'"
    docker exec -d "$DN_C" bash -lc \
        "timeout '$((DURATION + 5))' /usr/bin/mgen input '$CFG' output '$LOG'"
    echo "OK: uplink receiver armed on UDP $PORT; log=$LOG"
    ;;

count-ul)
    [ "$#" -eq 4 ] || { usage; exit 2; }
    RUN_ID=$2 CELL=$3 UE=$4
    require_run_id "$RUN_ID"
    require_uint CELL "$CELL"
    require_uint UE "$UE"
    BASE=$(ul_base "$RUN_ID" "$CELL" "$UE")
    COUNT=$(docker exec "$DN_C" sh -lc "grep -c RECV '${BASE}.log' 2>/dev/null || true")
    COUNT=${COUNT//[^0-9]/}
    echo "UL_RECV=${COUNT:-0}"
    ;;

send-dl)
    [ "$#" -eq 9 ] || { usage; exit 2; }
    RUN_ID=$2 CELL=$3 UE=$4 UE_IP=$5 PORT=$6 RATE=$7 SIZE=$8 DURATION=$9
    require_run_id "$RUN_ID"
    require_uint CELL "$CELL"
    require_uint UE "$UE"
    require_ue_ip "$UE_IP"
    require_uint PORT "$PORT"
    require_uint RATE "$RATE"
    require_uint SIZE "$SIZE"
    require_uint DURATION "$DURATION"
    container_running "$DN_C" || die "$DN_C is not running"

    BASE="$LOG_DIR/$RUN_ID-dl-cell${CELL}-ue${UE}-tx"
    CFG="${BASE}.mgn"
    LOG="${BASE}.log"
    FLOW_ID=$((CELL * 100 + UE))
    docker exec "$DN_C" bash -lc \
        "mkdir -p '$LOG_DIR'; printf '0.0 ON %s UDP DST %s/%s PERIODIC [%s %s]\\n%s.0 OFF %s\\n' \
         '$FLOW_ID' '$UE_IP' '$PORT' '$RATE' '$SIZE' '$DURATION' '$FLOW_ID' > '$CFG'; \
         timeout '$((DURATION + 5))' /usr/bin/mgen input '$CFG' output '$LOG'; \
         rc=\$?; [ \"\$rc\" -eq 0 ] || [ \"\$rc\" -eq 124 ]"
    echo "OK: downlink sender completed for cell${CELL}/UE${UE}; log=$LOG"
    ;;

*)
    usage
    exit 2
    ;;
esac
