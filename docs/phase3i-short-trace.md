# Phase 3I representative short trace

This campaign replays one frozen 60-second portion of the UPV Test 1 development
trace through dynamic RFsim gain and effective-noise controls. It uses one gNB,
one UE, and the one-tap AWGN path. It does not access Test 6 and does not claim
physical multipath reconstruction.

The selected source interval is Test 1 rows 154 through 213. All targets lie
inside the validated Phase 3G/3H output hull; no command is extrapolated or
clipped. The primary comparison is fixed at zero lag. Other lags are diagnostic
only and cannot be used to select a passing alignment.

The runner performs one clean UE recreation, records start and end anchors,
applies 60 command pairs at one-second intervals, verifies command completion
before each sample midpoint, and restores the original UE image on every exit.
It stops on excessive command lateness, attachment loss, insufficient paired
telemetry, critical radio failures, container restarts, or identity mismatches.

Use the active `oai-5g-ric-v2` reservation with one cell, one UE, AWGN, a d430
core node, and a d740 cell node. The offline evaluator alone decides whether a
complete Test 1 development-trace protocol may be frozen.
