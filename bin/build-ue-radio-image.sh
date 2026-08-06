#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:?usage: build-ue-radio-image.sh OAI_SOURCE IMAGE_TAG}"
IMAGE_TAG="${2:?usage: build-ue-radio-image.sh OAI_SOURCE IMAGE_TAG}"
EXPECTED_OAI_COMMIT="70508ebaf52f2aae420566d380c6537f2efb9f0c"
RUNNER_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MEASUREMENT_SOURCE="$SOURCE_DIR/openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c"
RFSIMULATOR_SOURCE="$SOURCE_DIR/radio/rfsimulator/simulator.cpp"

[ "$(uname -m)" = "x86_64" ] || {
    echo "the OAI radio image must be built on an x86_64 host" >&2
    exit 1
}
[ -d "$SOURCE_DIR/.git" ] || {
    echo "not an OAI Git checkout: $SOURCE_DIR" >&2
    exit 1
}

ACTUAL_OAI_COMMIT=$(git -C "$SOURCE_DIR" rev-parse HEAD)
[ "$ACTUAL_OAI_COMMIT" = "$EXPECTED_OAI_COMMIT" ] || {
    echo "OAI commit $ACTUAL_OAI_COMMIT does not match $EXPECTED_OAI_COMMIT" >&2
    exit 1
}

python3 "$RUNNER_DIR/patch-oai-ue-radio-measurements.py" "$MEASUREMENT_SOURCE"
python3 "$RUNNER_DIR/patch-oai-rfsim-rsrp-calibration.py" "$RFSIMULATOR_SOURCE"
git -C "$SOURCE_DIR" diff --check -- "$MEASUREMENT_SOURCE" "$RFSIMULATOR_SOURCE"

docker build \
    --target ran-base \
    --tag ran-base:latest \
    --file "$SOURCE_DIR/docker/Dockerfile.base.ubuntu" \
    "$SOURCE_DIR"

docker build \
    --target oai-nr-ue-radio \
    --tag "$IMAGE_TAG" \
    --build-arg "BASE_UE_IMAGE=ghinwa555/oai-nr-ue-chan:v2" \
    --label "org.opencontainers.image.revision=$ACTUAL_OAI_COMMIT" \
    --label "org.opencontainers.image.title=OAI NR UE RFsim radio measurements" \
    --file "$RUNNER_DIR/../etc/Dockerfile.nrUE-radio" \
    "$SOURCE_DIR"

docker image inspect "$IMAGE_TAG" --format 'image={{.Id}} size={{.Size}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
