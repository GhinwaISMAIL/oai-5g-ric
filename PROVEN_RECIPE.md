# oai-5g-ric — Proven Recipe (validated on POWDER d740, July 2026)

Everything below was demonstrated end-to-end on a single build node. This is the
ground truth the Dockerfiles, compose files, and per-cell configs must reproduce.
It corrects earlier assumptions in BUILD_GUIDE.md (notably the KPM version and the
FlexRIC source).

OAI version proven: `develop`, commit `70508ebaf5` (Jul 10 2026).

---

## 1. Build OAI gNB + UE from source (the one custom build)

```bash
git clone https://gitlab.eurecom.fr/oai/openairinterface5g.git
cd openairinterface5g && git checkout develop
git submodule update --init --recursive          # pulls the FlexRIC E2 agent submodule
cd cmake_targets
sudo ./build_oai -I --gNB --nrUE -w SIMU --build-e2 --build-lib telnetsrv --ninja
```

- `-w SIMU` → RFSim device (chanmod apply path lives here) — **required for chanmod**
- `--build-lib telnetsrv` → telnet server — **required to drive/verify the channel**
- `--build-e2` → E2 agent compiled into the gNB — **required for FlexRIC**

Result: `nr-softmodem`, `nr-uesoftmodem`, `librfsimulator.so`, `libtelnetsrv*.so`
in `cmake_targets/ran_build/build/`.

---

## 2. chanmod — PROVEN working (the thing the stock image can't do)

### Config (both gNB and UE need an rfsimulator block + the channel include)

gNB config already has an `rfsimulator = ( { ... } )` block; set inside it:
```
serveraddr = "server";
options    = ("chanmod");
```
The UE config (`ue.conf`) ships **without** an rfsimulator block — it must be
**added**:
```
rfsimulator = (
{
  serveraddr = "127.0.0.1";     # the gNB's IP
  serverport = 4043;
  options    = ("chanmod");
  modelname  = "AWGN";
});
```
Both configs end with the channel model include, by **bare filename**, with
`channelmod_rfsimu.conf` copied into the **same directory** as the config
(libconfig resolves includes relative to the config's dir — an absolute path
fails with "cannot open include file"):
```
@include "channelmod_rfsimu.conf"
```

### Launch (phy-test, no core needed for the chanmod proof)

```bash
# gNB
sudo .../nr-softmodem -O gnb-chan.conf --rfsim --phy-test -E --gNBs.[0].min_rxtxtime 3
# UE
sudo .../nr-uesoftmodem -O ue-chan.conf --rfsim --phy-test -E --telnetsrv
```

### Gotchas learned the hard way
- **Do NOT pass `--rfsimulator.serveraddr` / `--rfsimulator.options` on the command
  line** — this build rejects the flat form ("unknown option"); it wants them in
  the config file (or the array form `--rfsimulator.[0].serveraddr`). Config is cleanest.
- **`min_rxtxtime` must be 3.** The default `minTXRXTIME 2` triggers
  `Slot offset K2 (2) needs to be higher than DURATION_RX_TO_TX (3)` and the UE exits.
  Override on the CLI as above, or set `min_rxtxtime = 3` in the gNB config.

### Proof observed
- `[OCM] Model rfsimu_channel_enB0 type AWGN allocated from config file`
- `[OCM] Model rfsimu_channel_ue0 type AWGN allocated from config file`
- `[HW] Random channel rfsimu_channel_ue0 in rfsimulator activated`
- UE synced through the channel: `SINR = 17 dB, CQI = 13` baseline

### Runtime tuning over telnet (this is how you drive per-UE channels)
```bash
telnet 127.0.0.1 9090          # server runs in the UE (DL channel is UE-side)
  channelmod show current      # model 0 = enB0 (UL), model 1 = ue0 (DL)
  channelmod modify 1 noise_power_dB 30
```
Valid `<param>` names for `channelmod modify` (from `channelmod help`):
`riceanf`, `aoa`, `randaoa`, `ploss`, `noise_power_dB`, `offset`, `forgetf`.
(The config-file names `ploss_dB`/`noise_power_dB`; the telnet noise param is
`noise_power_dB`, NOT `noise`.) Verified `noise: 0.000000 → 30.000000` on model 1.

> Note: in `--phy-test` the reported SINR is largely static, so the live SINR
> *swing* is muted — that's a phy-test artifact, not chanmod. The dramatic
> SINR/MCS/throughput drop appears in full SA mode with the adaptive scheduler
> (i.e. in the real stack). The build property — models load, attach to the live
> sample stream, and are tunable — is what's proven here.

---

## 3. FlexRIC + KPM xApp — PROVEN working

### Dependencies + compiler
```bash
sudo apt-get install -y swig libsctp-dev python3-dev cmake-curses-gui pkg-config \
     libconfig-dev libconfig++-dev bison flex libpcre2-dev autoconf automake \
     libtool gcc-10 g++-10
sudo update-alternatives --set gcc /usr/bin/gcc-10      # default gcc-11 will NOT build FlexRIC
sudo update-alternatives --set g++ /usr/bin/g++-10
```
SWIG 4.0.2 (Ubuntu 22.04 default) was **sufficient** — the C xApps build without
the 4.1 requirement (that's only for the Python multi-language bindings).

### CRITICAL: asn1c must be pinned to OAI's commit
FlexRIC's xApps (and the sqlite DB layer) generate C from NR-RRC ASN.1 via asn1c.
OAI pins it deliberately — `build_helper` line ~470 says `vlm_master` **breaks
F1/NG**, so it checks out the commit *before*:

```bash
git clone https://github.com/mouse07410/asn1c /tmp/asn1c
cd /tmp/asn1c
git checkout 940dd5fa9f3917913fd487b13dfddfacd0ded06e   # NOT vlm_master
autoreconf -iv
CFLAGS="-O2 -fno-strict-aliasing" ./configure --prefix /opt/asn1c/
make -j$(nproc) && sudo make install
```
Installs asn1c v0.9.29 at `/opt/asn1c/bin/asn1c` — exactly where FlexRIC's
`find_program(ASN1C_EXEC_PATH asn1c HINTS /opt/asn1c/bin)` looks.

> Using `vlm_master` instead produces generated headers where RRC members become
> pointers and the code calls CBOR functions that don't exist in FlexRIC's
> bundled runtime. Symptoms: `'ul_msg->message' is a pointer; did you mean ->`
> and `undefined reference to BOOLEAN_decode_cbor` / `asn_check_INTEGER_range`.
> These are NOT bugs in FlexRIC — do not patch the source. Fix the asn1c commit.

On a node where `build_oai -I` has already run, asn1c is present at
`/opt/asn1c/bin/asn1c` and native FlexRIC builds just work. A clean container
must install it explicitly (above).

### Build — from the OAI submodule (guarantees version match with the gNB agent)
```bash
cd ~/openairinterface5g/openair2/E2AP/flexric
mkdir build && cd build
cmake ..                     # NO version flags, NO XAPP_DB flag — take defaults
make -j$(nproc)
sudo make install            # SMs → /usr/local/lib/flexric, conf → /usr/local/etc/flexric
```
- Building from the submodule (not a fresh `br-flexric` clone) means the RIC and
  the gNB's E2 agent share the **exact same** E2AP/KPM versions.
- **Versions this submodule uses: E2AP_V2 / KPM_V2_03.** (Earlier notes said
  KPM_V3_00 — that was wrong for this OAI commit. Match the agent, don't assume V3.)
- `XAPP_DB`: the accepted token is `SQLITE3_XAPP` (not `SQLITE3`). We built with the
  default (no DB) for the proof; use `-DXAPP_DB=SQLITE3_XAPP` later if you want the
  `/tmp/xapp_db` persistence for offline ML/analysis.
- Installs 8 service models: `libkpm_sm.so librc_sm.so libmac_sm.so librlc_sm.so
  libpdcp_sm.so libslice_sm.so libtc_sm.so libgtp_sm.so`.

### gNB E2 agent block (in the gNB config)
```
e2_agent = {
  near_ric_ip_addr = "127.0.0.1";           # RIC IP (loopback here; core-node IP in the real stack)
  sm_dir           = "/usr/local/lib/flexric/";
};
```
> The gNB asserts at startup (`plugin_agent.c ... Error opening the input
> directory`) if this block is present but `sm_dir` doesn't exist yet. Install
> FlexRIC first, or comment the block out. Order matters.

### Proof observed
- Emulator 3-node test (`nearRT-RIC` + `emu_agent_gnb` + `xapp_kpm_moni`): RIC
  accepted RAN functions, xApp streamed KPM metrics (`DRB.UEThpDl/Ul`,
  `DRB.PdcpSduVolumeDL/UL`, `DRB.RlcSduDelayDl`, `RRU.PrbTotDl/Ul`).
- **Real gNB → RIC:** `E2 SETUP-REQUEST rx from PLMN 1, Node ID 3584, ngran_gNB`
  → `Accepting RAN function ID 2 (KPM), 3 (RC), 142–148`. The real E2-enabled
  gNB connected and registered all service models over E2.

Ports: nearRT-RIC E2 = **36421**, internal iApp/E42 = **36422**.
Start order: **RIC first, then gNB.**

Benign artifacts (not failures): the example `xapp_kpm_moni` segfaults on exit
with emulated random data; `SCTP_SEND_FAILED` prints when an xApp disconnects.

---

## 4. Slices

The gNB binary is slice-capable out of the box (UE requested `NSSAI 1.ffffff` in
these runs). But **slicing is a full-stack, control-plane property** — it needs
the AMF/SMF/UPF/subscriber-DB, so it can't be exercised in phy-test and is a
Phase-2 task in the real multi-node stack. FlexRIC provides `SLICE_STATS_V0`
(RAN function 145), so a slice-monitoring/control xApp is available later.

---

## 5. The three images (BUILT, VERIFIED, PUSHED)

All on Docker Hub under `ghinwa555`, all E2AP_V2 / KPM_V2_03:

| image | contents | verified by |
|-------|----------|-------------|
| `ghinwa555/oai-gnb-e2-chan:v1` | gNB: rfsim+chanmod, telnetsrv, E2 agent, 8 SMs | `--help` shows `--rfsim`/`--telnetsrv`; `/usr/local/lib/flexric/` has 8 SMs |
| `ghinwa555/oai-nr-ue-chan:v1` | UE: rfsim+chanmod, telnetsrv | `librfsimulator.so` + `libtelnetsrv*.so` linked |
| `ghinwa555/flexric-kpm:v1` | nearRT-RIC, xapp_kpm_moni, xapp_rc_moni, xapp_gtp_mac_rlc_pdcp_moni, 8 SMs, **sqlite3 xApp DB** | RIC starts, loads 8 SMs, prints "run SUCCESSFULLY" |

Build notes:
- **gNB/UE:** built from OAI's own `docker/Dockerfile.{build,gNB,nrUE}.ubuntu`,
  which ALREADY include `--build-e2` and `--build-lib "telnetsrv ..."`. The only
  required override is the version args, since the Dockerfile defaults to V3:
  `--build-arg E2AP_VERSION=E2AP_V2 --build-arg KPM_VERSION=KPM_V2_03`.
- **`.dockerignore` must exclude host build dirs** (`cmake_targets/ran_build/build/`,
  `openair2/E2AP/flexric/build/`) or `COPY . .` drags in stale CMakeCache files
  and the in-container build fails with "CMakeCache.txt directory is different".
- **FlexRIC image:** Ubuntu 22.04, gcc-10, asn1c pinned as in section 3,
  `-DXAPP_DB=SQLITE3_XAPP` for metric persistence (`DB_DIR=/tmp/`).
  Ubuntu 22.04 runtime package is `libconfig++9v5` (not `libconfig++9`).
- **Version invariant:** gNB agent and FlexRIC must share E2AP_V2/KPM_V2_03.

Runtime override needed at deploy: `flexric.conf` ships with
`NEAR_RIC_IP = 127.0.0.1`; on the core node it must be the core node's address so
cell-node gNBs can reach it. Bind-mount a modified conf or pass `-c`.
