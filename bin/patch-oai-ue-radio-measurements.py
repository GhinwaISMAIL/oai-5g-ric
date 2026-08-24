#!/usr/bin/env python3
"""Add one-second serving-cell radio measurements to the pinned OAI NR UE."""

from __future__ import annotations

from pathlib import Path
import sys


MARKER = "UE_RADIO_V1"
DEBUG_MARKER = "UE_RADIO_DEBUG_V1"
INCLUDE_ANCHOR = '#include "PHY/NR_UE_ESTIMATION/nr_estimation.h"\n'
HELPER_ANCHOR = "// Send SSB RSRP measurement to MAC\n"
CALL_ANCHOR = "  LOG_D(PHY,\n        \"[UE %d] ssb %d SS-RSRP:"

HELPERS = r'''
#define UE_RADIO_SSB_RBS 20

typedef struct {
  int64_t utc_second;
  double rsrp_sum;
  double rsrq_sum;
  double sinr_sum;
  double rsrp_digital_power_sum;
  double rsrp_db_per_re_sum;
  unsigned int samples;
  int module_id;
  uint16_t cell_id;
  int ssb_index;
  int ss_rsrp_dbm_integer;
} ue_radio_second_t;

static ue_radio_second_t ue_radio_second = {.utc_second = -1};

static double nr_ue_calculate_ssb_rsrq(const NR_DL_FRAME_PARMS *fp,
                                       const c16_t rxdataF[][fp->ofdm_symbol_size],
                                       int ssb_start_subcarrier,
                                       uint32_t rsrp_avg)
{
  const unsigned int ssb_offset = fp->first_carrier_offset + ssb_start_subcarrier;
  uint64_t rssi_sum = 0;

  for (int aarx = 0; aarx < fp->nb_antennas_rx; aarx++) {
    const c16_t *rxF_ssb = rxdataF[aarx];
    for (int k = 0; k < UE_RADIO_SSB_RBS * NR_NB_SC_PER_RB; k++) {
      const int re = (ssb_offset + k) % fp->ofdm_symbol_size;
      rssi_sum += squaredMod(rxF_ssb[re]);
    }
  }

  if (rsrp_avg == 0 || rssi_sum == 0 || fp->nb_antennas_rx == 0)
    return NAN;

  const double rssi = (double)rssi_sum / fp->nb_antennas_rx;
  return 10.0 * log10((UE_RADIO_SSB_RBS * (double)rsrp_avg) / rssi);
}

static void record_ue_radio_second(const PHY_VARS_NR_UE *ue,
                                   int ssb_index,
                                   double rsrp_dbm,
                                   double rsrq_db,
                                   double sinr_db,
                                   uint32_t rsrp_digital_power,
                                   double rsrp_db_per_re)
{
  if (!isfinite(rsrp_dbm) || !isfinite(rsrq_db) || !isfinite(sinr_db) || !isfinite(rsrp_db_per_re))
    return;

  struct timespec now = {0};
  if (clock_gettime(CLOCK_REALTIME, &now) != 0)
    return;

  const int64_t utc_second = now.tv_sec;
  const int64_t emitted_epoch_us = utc_second * 1000000LL + now.tv_nsec / 1000;

  if (ue_radio_second.utc_second >= 0 && utc_second != ue_radio_second.utc_second && ue_radio_second.samples > 0) {
    LOG_I(PHY,
          "UE_RADIO_V1 utc_second=%lld emitted_epoch_us=%lld ue=%d cell=%u ssb=%d samples=%u "
          "ss_rsrp_dbm=%.3f ss_rsrq_db=%.3f ss_sinr_db=%.3f\n",
          (long long)ue_radio_second.utc_second,
          (long long)emitted_epoch_us,
          ue_radio_second.module_id,
          ue_radio_second.cell_id,
          ue_radio_second.ssb_index,
          ue_radio_second.samples,
          ue_radio_second.rsrp_sum / ue_radio_second.samples,
          ue_radio_second.rsrq_sum / ue_radio_second.samples,
          ue_radio_second.sinr_sum / ue_radio_second.samples);
    LOG_I(PHY,
          "UE_RADIO_DEBUG_V1 utc_second=%lld emitted_epoch_us=%lld ue=%d cell=%u ssb=%d samples=%u "
          "rsrp_digital_power_linear=%.9f rsrp_db_per_re_unquantized=%.6f "
          "ss_rsrp_dbm_integer=%d ss_sinr_db=%.6f\n",
          (long long)ue_radio_second.utc_second,
          (long long)emitted_epoch_us,
          ue_radio_second.module_id,
          ue_radio_second.cell_id,
          ue_radio_second.ssb_index,
          ue_radio_second.samples,
          ue_radio_second.rsrp_digital_power_sum / ue_radio_second.samples,
          ue_radio_second.rsrp_db_per_re_sum / ue_radio_second.samples,
          ue_radio_second.ss_rsrp_dbm_integer,
          ue_radio_second.sinr_sum / ue_radio_second.samples);
  }

  if (utc_second != ue_radio_second.utc_second) {
    ue_radio_second = (ue_radio_second_t){
        .utc_second = utc_second,
        .module_id = ue->Mod_id,
        .cell_id = ue->frame_parms.Nid_cell,
        .ssb_index = ssb_index,
    };
  }

  ue_radio_second.rsrp_sum += rsrp_dbm;
  ue_radio_second.rsrq_sum += rsrq_db;
  ue_radio_second.sinr_sum += sinr_db;
  ue_radio_second.rsrp_digital_power_sum += rsrp_digital_power;
  ue_radio_second.rsrp_db_per_re_sum += rsrp_db_per_re;
  ue_radio_second.ss_rsrp_dbm_integer = (int)rsrp_dbm;
  ue_radio_second.samples++;
}

'''

CALL = '''  if (ssb_index == fp->ssb_index) {
    const double ss_rsrq_dB = nr_ue_calculate_ssb_rsrq(fp, rxdataF, fp->ssb_start_subcarrier, rsrp_avg);
    record_ue_radio_second(ue,
                           ssb_index,
                           ue->measurements.ssb_rsrp_dBm[ssb_index],
                           ss_rsrq_dB,
                           ue->measurements.ssb_sinr_dB[ssb_index],
                           rsrp_avg,
                           rsrp_db_per_re);
  }

'''


def replace_once(text: str, old: str, new: str, context: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{context}: expected one occurrence of {old!r}, found {count}"
        )
    return text.replace(old, new, 1)


def patch_file(path: Path) -> bool:
    text = path.read_text()
    if MARKER in text:
        required = (
            "nr_ue_calculate_ssb_rsrq",
            "record_ue_radio_second",
            "CLOCK_REALTIME",
            "ss_rsrq_db=",
            DEBUG_MARKER,
            "rsrp_digital_power_linear=",
            "rsrp_db_per_re_unquantized=",
            "ss_rsrp_dbm_integer=",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial UE radio patch: missing {missing}")
        print(f"Already patched: {path}")
        return False

    text = replace_once(
        text,
        INCLUDE_ANCHOR,
        INCLUDE_ANCHOR + "#include <stdint.h>\n#include <time.h>\n",
        "time include",
    )
    text = replace_once(text, HELPER_ANCHOR, HELPERS + HELPER_ANCHOR, "helpers")
    text = replace_once(text, CALL_ANCHOR, CALL + CALL_ANCHOR, "record call")
    path.write_text(text)
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} NR_UE_MEASUREMENTS_C", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 2
    try:
        patch_file(path)
    except (OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
