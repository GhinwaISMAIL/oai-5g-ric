#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:?usage: build-ue-radio-image.sh OAI_SOURCE UE_IMAGE_TAG [GNB_IMAGE_TAG]}"
IMAGE_TAG="${2:?usage: build-ue-radio-image.sh OAI_SOURCE UE_IMAGE_TAG [GNB_IMAGE_TAG]}"
GNB_IMAGE_TAG="${3:-}"
EXPECTED_OAI_COMMIT="70508ebaf52f2aae420566d380c6537f2efb9f0c"
RUNNER_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MEASUREMENT_SOURCE="$SOURCE_DIR/openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c"
RFSIMULATOR_SOURCE="$SOURCE_DIR/radio/rfsimulator/simulator.cpp"
RANDOM_CHANNEL_SOURCE="$SOURCE_DIR/openair1/SIMULATION/TOOLS/random_channel.c"

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
python3 "$RUNNER_DIR/patch-oai-rfsim-rng-init.py" "$RFSIMULATOR_SOURCE"
python3 "$RUNNER_DIR/patch-oai-rfsim-rsrp-calibration.py" "$RFSIMULATOR_SOURCE"
python3 "$RUNNER_DIR/patch-oai-rfsim-debug-telemetry.py" "$RFSIMULATOR_SOURCE"
python3 "$RUNNER_DIR/patch-oai-channelmod-scalar-control.py" "$RANDOM_CHANNEL_SOURCE"
python3 "$RUNNER_DIR/patch-oai-tdl-model.py" "$RANDOM_CHANNEL_SOURCE"
git -C "$SOURCE_DIR" diff --check -- \
    "$MEASUREMENT_SOURCE" \
    "$RFSIMULATOR_SOURCE" \
    "$RANDOM_CHANNEL_SOURCE"

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

if [ -n "$GNB_IMAGE_TAG" ]; then
    docker build \
        --target oai-gnb-rfsim \
        --tag "$GNB_IMAGE_TAG" \
        --build-arg "BASE_GNB_IMAGE=ghinwa555/oai-gnb-e2-chan:v2" \
        --label "org.opencontainers.image.revision=$ACTUAL_OAI_COMMIT" \
        --label "org.opencontainers.image.title=OAI gNB RFsim fading channels" \
        --file "$RUNNER_DIR/../etc/Dockerfile.nrUE-radio" \
        "$SOURCE_DIR"
    docker image inspect "$GNB_IMAGE_TAG" --format 'image={{.Id}} size={{.Size}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
fi
