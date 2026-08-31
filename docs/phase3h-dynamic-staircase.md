# Phase 3H dynamic staircase validation

This campaign validates the gain/noise translator needed to replay the designated
UPV Test 1 trace. The measured trace is the realism source. The translator is a
supporting tool and is not treated as the research objective.

The campaign uses one gNB, one UE, and the one-tap AWGN RFsim path. It recreates
the UE only three times. During each attachment, it visits the seven frozen outer
states dynamically, returning to the `(-10, -25)` anchor between states. Each
state receives five seconds of settling and ten seconds of usable telemetry.

The complete staircase after a clean UE recreation is the independent execution
unit. Individual one-second radio samples are not independent repetitions.

The runner stops on attachment loss, insufficient telemetry, packet loss beyond
the frozen limit, critical radio failure, container restart, channel-identity
change, or a mismatch between commanded and applied controls. It restores the
original UE image before exit. It cannot authorize trace replay or access Test 6.

Use the `oai-5g-ric-v2` profile with one cell, one UE, AWGN, a d430 core node,
and a d740 cell node. Reserve three hours for preflight, image verification, the
three short staircases, artifact transfer, and rollback verification.

Build the pinned instrumented UE image on the cell node:

```bash
sudo /local/repository/bin/build-ue-radio-image.sh \
  /local/oai-src \
  oai-nr-ue-rfsim-phase3h:70508eb
```

Record all runtime identities before execution:

```bash
PROFILE_REVISION=$(git -C /local/repository rev-parse HEAD)
RUNNER_SHA256=$(sha256sum /local/repository/bin/run-phase3h-dynamic-staircase.py | awk '{print $1}')
PLAN_SHA256=$(sha256sum /local/repository/etc/phase3h-dynamic-staircase-plan.csv | awk '{print $1}')
COMPOSE_SHA256=$(sha256sum /local/repository/etc/docker-compose-cell1.yaml | awk '{print $1}')
CHANNEL_SHA256=$(sha256sum /local/repository/etc/channelmod-cell1.conf | awk '{print $1}')
UE_CONFIG_SHA256=$(sha256sum /local/repository/etc/nr-ue-cell1-1.conf | awk '{print $1}')
DEBUG_IMAGE_ID=$(sudo docker image inspect oai-nr-ue-rfsim-phase3h:70508eb --format '{{.Id}}')
```

Run only after the profile commit and checksums have been frozen in the research
execution manifest:

```bash
sudo python3 /local/repository/bin/run-phase3h-dynamic-staircase.py \
  --debug-image oai-nr-ue-rfsim-phase3h:70508eb \
  --expected-debug-image-id "$DEBUG_IMAGE_ID" \
  --expected-profile-revision "$PROFILE_REVISION" \
  --expected-runner-sha256 "$RUNNER_SHA256" \
  --expected-plan-sha256 "$PLAN_SHA256" \
  --expected-compose-sha256 "$COMPOSE_SHA256" \
  --expected-channel-config-sha256 "$CHANNEL_SHA256" \
  --expected-ue-config-sha256 "$UE_CONFIG_SHA256"
```

Preserve the printed output directory in full. The offline research evaluator,
not the hardware runner, makes the translation-validation decision.
