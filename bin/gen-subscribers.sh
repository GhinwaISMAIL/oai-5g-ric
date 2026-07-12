#!/bin/bash
# =============================================================================
# gen-subscribers.sh — generate the subscriber DB for num_cells x ues_per_cell
#
# Usage: gen-subscribers.sh <num_cells> <ues_per_cell> [base_sql] [out_sql]
#
# MySQL reinitializes from oai_db.sql on every container start (no persistence),
# so every UE that will ever attach must be present in this file BEFORE the core
# comes up. Called by core-setup.sh.
#
# Each UE needs TWO rows:
#   AuthenticationSubscription        -> lets it authenticate (5G_AKA/milenage)
#   SessionManagementSubscriptionData -> lets it establish a PDU session
# A UE with only the first will register and then fail PDU setup — which looks
# like a data-plane bug and isn't. Both are emitted here.
#
# No staticIpAddress: the UPF assigns PDU addresses from its pool at attach time.
# Hardcoding them does not scale and breaks the "query IPs live" invariant.
# =============================================================================

set -e

NUM_CELLS="${1:-2}"
UES_PER_CELL="${2:-12}"
BASE_SQL="${3:-/local/repository/etc/oai_db.sql}"
OUT_SQL="${4:-$BASE_SQL}"

IMSI_BASE=208990100001100
KEY="fec86ba6eb707ed08905757b1bb44b8f"
OPC="c42449363bbad02b66d16bc975d77cc1"
PLMN="20899"
TOTAL=$((NUM_CELLS * UES_PER_CELL))

echo "[SUBS] generating ${TOTAL} subscribers (${NUM_CELLS} cells x ${UES_PER_CELL} UEs)"
echo "[SUBS] IMSI range: ${IMSI_BASE} .. $((IMSI_BASE + TOTAL - 1))"

TMP=$(mktemp)

# Keep everything up to our generated block; drop any previously generated one
# so this script is idempotent across reboots.
sed '/-- BEGIN GENERATED SUBSCRIBERS/,/-- END GENERATED SUBSCRIBERS/d' \
    "$BASE_SQL" > "$TMP"

{
  echo ""
  echo "-- BEGIN GENERATED SUBSCRIBERS"
  echo "-- ${NUM_CELLS} cells x ${UES_PER_CELL} UEs = ${TOTAL} subscribers"
  echo "-- PLMN 208/99, SST=1 SD=0xffffff, DNN=oai, dynamic PDU addressing"

  for i in $(seq 0 $((TOTAL - 1))); do
      IMSI=$((IMSI_BASE + i))

      # Remove any stale row for this IMSI from the base dump, then insert.
      echo "DELETE FROM \`AuthenticationSubscription\` WHERE \`ueid\` = '${IMSI}';"
      echo "DELETE FROM \`SessionManagementSubscriptionData\` WHERE \`ueid\` = '${IMSI}';"

      cat <<SQLEOF
INSERT INTO \`AuthenticationSubscription\` (\`ueid\`, \`authenticationMethod\`, \`encPermanentKey\`, \`protectionParameterId\`, \`sequenceNumber\`, \`authenticationManagementField\`, \`algorithmId\`, \`encOpcKey\`, \`encTopcKey\`, \`vectorGenerationInHss\`, \`n5gcAuthMethod\`, \`rgAuthenticationInd\`, \`supi\`) VALUES
('${IMSI}', '5G_AKA', '${KEY}', '${KEY}', '{"sqn": "000000000020", "sqnScheme": "NON_TIME_BASED", "lastIndexes": {"ausf": 0}}', '8000', 'milenage', '${OPC}', NULL, NULL, NULL, NULL, '${IMSI}');
INSERT INTO \`SessionManagementSubscriptionData\` (\`ueid\`, \`servingPlmnid\`, \`singleNssai\`, \`dnnConfigurations\`) VALUES
('${IMSI}', '${PLMN}', '{\\"sst\\": 1, \\"sd\\": \\"16777215\\"}', '{\\"oai\\":{\\"pduSessionTypes\\":{\\"defaultSessionType\\": \\"IPV4\\"},\\"sscModes\\":{\\"defaultSscMode\\": \\"SSC_MODE_1\\"},\\"5gQosProfile\\":{\\"5qi\\": 6,\\"arp\\":{\\"priorityLevel\\": 1,\\"preemptCap\\": \\"NOT_PREEMPT\\",\\"preemptVuln\\":\\"PREEMPTABLE\\"},\\"priorityLevel\\":1},\\"sessionAmbr\\":{\\"uplink\\":\\"1000Mbps\\",\\"downlink\\":\\"1000Mbps\\"}}}');
SQLEOF
  done

  echo "-- END GENERATED SUBSCRIBERS"
} >> "$TMP"

mv "$TMP" "$OUT_SQL"
chmod 644 "$OUT_SQL"

echo "[SUBS] wrote ${OUT_SQL}"
echo "[SUBS] AuthenticationSubscription rows:        $(grep -c 'INSERT INTO `AuthenticationSubscription`' "$OUT_SQL")"
echo "[SUBS] SessionManagementSubscriptionData rows: $(grep -c 'INSERT INTO `SessionManagementSubscriptionData`' "$OUT_SQL")"
