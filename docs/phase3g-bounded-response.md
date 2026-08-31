# Phase 3G bounded gain/noise response experiment

This experiment measures the local RFsim response from scalar gain and corrected
effective-noise commands to UE relative RSRP and SINR. It does not replay the
measured UPV trace and does not access the final Test 6 payload.

## Frozen design

- One gNB and one UE.
- AWGN channel with one unit-energy tap.
- Attachment at 0 dB gain and −60 dB noise.
- 45 clean UE execution units from
  `etc/phase3g-bounded-response-plan.csv`.
- Five seconds of settling and 15 seconds of usable telemetry per execution.
- Stop on attachment loss, insufficient telemetry, ping failure, radio failure,
  container restart, channel-identity change, or command/telemetry mismatch.
- Restore the original UE image and verify attachment before exit.

The execution plan must have SHA-256
`63b8d0e8880c1ccc418923d10e375f43f3c71c99b80ff75b0fedb57cbc7e21e7`.

## Reservation

Use the `oai-5g-ric-v2` profile with one cell, one UE, AWGN, a d430 core node,
and a d740 cell node. Reserve four hours to allow image construction, preflight,
the campaign, verification, and artifact transfer.

## Image construction

On the cell node, obtain the pinned OAI source at
`70508ebaf52f2aae420566d380c6537f2efb9f0c`, then build a uniquely tagged UE
image:

```bash
sudo /local/repository/bin/build-ue-radio-image.sh \
  /local/oai-src \
  oai-nr-ue-rfsim-phase3g:70508eb
```

Record the resulting image ID and verify its revision label before execution.

## Preflight and execution

Calculate the runtime identities on the cell node:

```bash
PROFILE_REVISION=$(git -C /local/repository rev-parse HEAD)
RUNNER_SHA256=$(sha256sum /local/repository/bin/run-phase3g-bounded-response.py | awk '{print $1}')
PLAN_SHA256=$(sha256sum /local/repository/etc/phase3g-bounded-response-plan.csv | awk '{print $1}')
COMPOSE_SHA256=$(sha256sum /local/repository/etc/docker-compose-cell1.yaml | awk '{print $1}')
CHANNEL_SHA256=$(sha256sum /local/repository/etc/channelmod-cell1.conf | awk '{print $1}')
UE_CONFIG_SHA256=$(sha256sum /local/repository/etc/nr-ue-cell1-1.conf | awk '{print $1}')
DEBUG_IMAGE_ID=$(sudo docker image inspect oai-nr-ue-rfsim-phase3g:70508eb --format '{{.Id}}')
```

Run the campaign only after all values have been recorded:

```bash
sudo python3 /local/repository/bin/run-phase3g-bounded-response.py \
  --debug-image oai-nr-ue-rfsim-phase3g:70508eb \
  --expected-debug-image-id "$DEBUG_IMAGE_ID" \
  --expected-profile-revision "$PROFILE_REVISION" \
  --expected-runner-sha256 "$RUNNER_SHA256" \
  --expected-plan-sha256 "$PLAN_SHA256" \
  --expected-compose-sha256 "$COMPOSE_SHA256" \
  --expected-channel-config-sha256 "$CHANNEL_SHA256" \
  --expected-ue-config-sha256 "$UE_CONFIG_SHA256"
```

Preserve the printed output directory in full. The primary artifacts are
`execution_state.json`, `phase3g_bounded_response_telemetry.csv`, per-execution
summaries, attachment and ping checks, and raw UE/gNB logs.
