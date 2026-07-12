#!/bin/bash
# node-setup.sh — dispatcher, called by profile.py on every node.
# Usage: node-setup.sh <role> <index> <num_cells> <ues_per_cell>

set +e
ROLE="$1"; INDEX="$2"; NUM_CELLS="$3"; UES_PER_CELL="$4"

mkdir -p /local/logs
chmod +x /local/repository/bin/*.sh

echo "[NODE-SETUP] role=${ROLE} index=${INDEX} num_cells=${NUM_CELLS} ues=${UES_PER_CELL}"

case "$ROLE" in
    core) exec bash /local/repository/bin/core-setup.sh ;;
    cell) exec bash /local/repository/bin/cell-setup.sh "$INDEX" "$NUM_CELLS" "$UES_PER_CELL" ;;
    *)    echo "[NODE-SETUP] ERROR: unknown role '${ROLE}'"; exit 1 ;;
esac
