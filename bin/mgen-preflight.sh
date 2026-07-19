#!/usr/bin/env bash
# Compatibility entry point. The profile is distributed, so a full MGEN run
# must be coordinated from the operator workstation rather than one Docker host.

set -euo pipefail

cat <<'EOF'
The OAI 5G RIC profile now places the core and each cell on different POWDER
nodes. Run the distributed preflight from your workstation:

  bash bin/mgen-run-distributed.sh CORE_SSH CELL_SSH CELL [UE|all] [quick|full]

Examples:

  bash bin/mgen-run-distributed.sh \
    ghinwa@CORE_POWDER_HOST ghinwa@CELL1_POWDER_HOST 1 all quick

  bash bin/mgen-run-distributed.sh \
    ghinwa@CORE_POWDER_HOST ghinwa@CELL1_POWDER_HOST 1 1 full

Do not replace this with only container-name changes: the external DN is on
the core Docker daemon, while UE containers are on their corresponding cell.
EOF

exit 2
