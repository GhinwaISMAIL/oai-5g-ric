#!/bin/bash
# =============================================================================
# node-setup.sh — dispatcher, called by profile.py on every node
#
# Usage: node-setup.sh <role> <index> <num_cells> <ues_per_cell> [channel_mode] [channel_type]
#   role = core | cell
# =============================================================================

set +e

ROLE="$1"
INDEX="$2"
NUM_CELLS="$3"
UES_PER_CELL="$4"
CHANNEL_MODE="${5:-uniform}"
CHANNEL_TYPE="${6:-AWGN}"

mkdir -p /local/logs
chmod +x /local/repository/bin/*.sh

echo "[NODE-SETUP] role=${ROLE} index=${INDEX} num_cells=${NUM_CELLS} ues=${UES_PER_CELL} channel=${CHANNEL_MODE}/${CHANNEL_TYPE}"

case "$ROLE" in
    core)
        exec bash /local/repository/bin/core-setup.sh "10.10.1.1" "$NUM_CELLS" "$UES_PER_CELL"
        ;;
    cell)
        exec env CHANMOD_MODE="$CHANNEL_MODE" CHANMOD_TYPE="$CHANNEL_TYPE" \
            bash /local/repository/bin/cell-setup.sh "$INDEX" "$NUM_CELLS" "$UES_PER_CELL"
        ;;
    *)
        echo "[NODE-SETUP] ERROR: unknown role '${ROLE}'"
        exit 1
        ;;
esac
