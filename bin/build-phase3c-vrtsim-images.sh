#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${1:?usage: build-phase3c-vrtsim-images.sh OAI_SOURCE UE_IMAGE_TAG GNB_IMAGE_TAG}"
UE_IMAGE_TAG="${2:?usage: build-phase3c-vrtsim-images.sh OAI_SOURCE UE_IMAGE_TAG GNB_IMAGE_TAG}"
GNB_IMAGE_TAG="${3:?usage: build-phase3c-vrtsim-images.sh OAI_SOURCE UE_IMAGE_TAG GNB_IMAGE_TAG}"
EXPECTED_OAI_COMMIT="70508ebaf52f2aae420566d380c6537f2efb9f0c"
RUNNER_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MEASUREMENT_SOURCE="$SOURCE_DIR/openair1/PHY/NR_UE_ESTIMATION/nr_ue_measurements.c"
APPLY_CHANNELMOD_SOURCE="$SOURCE_DIR/radio/rfsimulator/apply_channelmod.c"
CIRDB_SOURCE="$SOURCE_DIR/radio/vrtsim/cirdb_provider.c"
VRTSIM_SOURCE="$SOURCE_DIR/radio/vrtsim/vrtsim.c"
DOCKERFILE="$RUNNER_DIR/../etc/Dockerfile.phase3c-vrtsim"

[ "$(uname -m)" = "x86_64" ] || {
    echo "the Phase 3C VRTSIM images must be built on an x86_64 host" >&2
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
python3 "$RUNNER_DIR/patch-oai-rfsim-noise-scaling.py" "$APPLY_CHANNELMOD_SOURCE"
python3 "$RUNNER_DIR/patch-oai-vrtsim-cirdb-telemetry.py" "$CIRDB_SOURCE"
python3 "$RUNNER_DIR/patch-oai-vrtsim-runtime-telemetry.py" "$VRTSIM_SOURCE"
python3 "$RUNNER_DIR/patch-oai-vrtsim-split-telemetry.py" "$VRTSIM_SOURCE"
git -C "$SOURCE_DIR" diff --check -- \
    "$MEASUREMENT_SOURCE" \
    "$APPLY_CHANNELMOD_SOURCE" \
    "$CIRDB_SOURCE" \
    "$VRTSIM_SOURCE"

docker build \
    --target ran-base \
    --tag ran-base:latest \
    --file "$SOURCE_DIR/docker/Dockerfile.base.ubuntu" \
    "$SOURCE_DIR"

docker build \
    --target phase3c-vrtsim-ue \
    --tag "$UE_IMAGE_TAG" \
    --build-arg "BASE_UE_IMAGE=ghinwa555/oai-nr-ue-chan:v2" \
    --label "org.opencontainers.image.revision=$ACTUAL_OAI_COMMIT" \
    --label "org.opencontainers.image.title=OAI NR UE Phase 3C VRTSIM" \
    --file "$DOCKERFILE" \
    "$SOURCE_DIR"

docker build \
    --target phase3c-vrtsim-gnb \
    --tag "$GNB_IMAGE_TAG" \
    --build-arg "BASE_GNB_IMAGE=ghinwa555/oai-gnb-e2-chan:v2" \
    --label "org.opencontainers.image.revision=$ACTUAL_OAI_COMMIT" \
    --label "org.opencontainers.image.title=OAI gNB Phase 3C VRTSIM" \
    --file "$DOCKERFILE" \
    "$SOURCE_DIR"

for image in "$UE_IMAGE_TAG" "$GNB_IMAGE_TAG"; do
    docker image inspect "$image" --format \
        'image={{.Id}} size={{.Size}} architecture={{.Architecture}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
done

docker run --rm --entrypoint /bin/bash "$UE_IMAGE_TAG" -c \
    'test -x /opt/oai-nr-ue/bin/nr-uesoftmodem && test -f /usr/local/lib/libvrtsim.so && ! ldd /opt/oai-nr-ue/bin/nr-uesoftmodem | grep -q "not found" && ! ldd /usr/local/lib/libvrtsim.so | grep -q "not found"'
docker run --rm --entrypoint /bin/bash "$GNB_IMAGE_TAG" -c \
    'test -x /opt/oai-gnb/bin/nr-softmodem && test -f /usr/local/lib/libvrtsim.so && ! ldd /opt/oai-gnb/bin/nr-softmodem | grep -q "not found" && ! ldd /usr/local/lib/libvrtsim.so | grep -q "not found"'
