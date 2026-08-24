#!/usr/bin/env python3
"""Add fail-closed CIRDB timing and tap-energy telemetry to pinned OAI."""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "VRTSIM_CIRDB_DEBUG_V1"
STATE_ANCHOR = "  int64_t last_step_applied; /* -1 until first update */\n} cirdb_g;\n"
HELPER_ANCHOR = "/* Load snapshot index s into the next publication buffer and flip */\n"
UPDATE_ANCHOR = '''  if (step != G->last_step_applied) {
    uint32_t s = (uint32_t)(step % G->S);
    load_snapshot_and_publish(G, s);
    G->snap_idx = s;
    G->last_step_applied = step;
  }
'''

STATE_REPLACEMENT = '''  int64_t last_step_applied; /* -1 until first update */

  /* Phase 3C diagnostic counters. These do not alter replay timing. */
  uint64_t applied_updates;
  uint64_t skipped_snapshots;
  uint64_t maximum_consecutive_skipped_snapshots;
  int64_t last_debug_second;
} cirdb_g;
'''

HELPERS = r'''static double cirdb_published_tap_energy(const cirdb_g *G)
{
  if (!G || !G->channel_desc_out || !*G->channel_desc_out)
    return -1.0;
  const channel_desc_t *ch = *G->channel_desc_out;
  double energy = 0.0;
  for (int tx = 0; tx < ch->nb_tx; tx++) {
    for (int rx = 0; rx < ch->nb_rx; rx++) {
      const struct complexf *taps = ch->ch_ps[rx + ch->nb_rx * tx];
      if (!taps)
        return -1.0;
      for (int tap = 0; tap < ch->channel_length; tap++)
        energy += taps[tap].r * taps[tap].r + taps[tap].i * taps[tap].i;
    }
  }
  return energy;
}

static void cirdb_log_debug(cirdb_g *G, uint64_t ns_since_start, int64_t expected_step)
{
  const int64_t elapsed_second = (int64_t)(ns_since_start / 1000000000ULL);
  if (elapsed_second == G->last_debug_second)
    return;
  const double tap_energy = cirdb_published_tap_energy(G);
  if (tap_energy < 0.0)
    return;
  G->last_debug_second = elapsed_second;
  LOG_I(HW,
        "VRTSIM_CIRDB_DEBUG_V1 elapsed_second=%lld expected_cirdb_step=%lld "
        "current_cirdb_snapshot_index=%u applied_cirdb_updates=%" PRIu64 " "
        "skipped_cirdb_snapshots=%" PRIu64 " "
        "maximum_consecutive_skipped_cirdb_snapshots=%" PRIu64 " "
        "current_tap_energy_linear=%.17g\n",
        (long long)elapsed_second,
        (long long)expected_step,
        G->snap_idx,
        G->applied_updates,
        G->skipped_snapshots,
        G->maximum_consecutive_skipped_snapshots,
        tap_energy);
}

'''

UPDATE_REPLACEMENT = '''  if (step != G->last_step_applied) {
    uint64_t skipped_now = 0;
    if (G->last_step_applied >= 0 && step > G->last_step_applied + 1)
      skipped_now = (uint64_t)(step - G->last_step_applied - 1);
    G->skipped_snapshots += skipped_now;
    if (skipped_now > G->maximum_consecutive_skipped_snapshots)
      G->maximum_consecutive_skipped_snapshots = skipped_now;
    uint32_t s = (uint32_t)(step % G->S);
    load_snapshot_and_publish(G, s);
    G->snap_idx = s;
    G->last_step_applied = step;
    G->applied_updates++;
  }
  cirdb_log_debug(G, ns_since_start, step);
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
            "cirdb_published_tap_energy",
            "expected_cirdb_step=",
            "current_cirdb_snapshot_index=",
            "applied_cirdb_updates=",
            "skipped_cirdb_snapshots=",
            "maximum_consecutive_skipped_cirdb_snapshots=",
            "current_tap_energy_linear=",
            "cirdb_log_debug(G, ns_since_start, step);",
        )
        missing = [value for value in required if value not in text]
        if missing:
            raise RuntimeError(f"partial CIRDB telemetry patch: missing {missing}")
        print(f"Already patched: {path}")
        return False

    text = replace_once(
        text,
        STATE_ANCHOR,
        STATE_REPLACEMENT,
        "CIRDB telemetry state",
    )
    text = replace_once(
        text,
        HELPER_ANCHOR,
        HELPERS + HELPER_ANCHOR,
        "CIRDB telemetry helpers",
    )
    text = replace_once(
        text,
        UPDATE_ANCHOR,
        UPDATE_REPLACEMENT,
        "CIRDB telemetry update",
    )
    path.write_text(text)
    print(f"Patched {path}")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} CIRDB_PROVIDER_C", file=sys.stderr)
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
