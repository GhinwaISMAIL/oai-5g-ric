# E2 / KPM — root cause and working recipe

Status: SOLVED. Per-UE KPM indications stream from a real OAI gNB.

## Root cause

The nearRT-RIC and the xApp MUST be built from the same source tree as the gNB's
E2 agent — from OAI's submodule at `openair2/E2AP/flexric`, NOT from the standalone
FlexRIC repository.

Even at identical versions (E2AP_V3 / KPM_V3_00), a RIC built from
gitlab.eurecom.fr/mosaic5g/flexric sends subscription requests that OAI's embedded
E2 agent silently ignores. The association is up, the request goes out, the gNB
never answers.

### The A/B that proved it
Same gNB, same UEs, same machine, minutes apart:
- `~/flexric` (standalone master): SUBSCRIPTION-REQUEST tx -> "Pending event
  timeout. Communication with E2 Node lost?" -> xApp aborts at e42_xapp.c:280
- `~/oai-src/openair2/E2AP/flexric` (OAI submodule): SUBSCRIPTION RESPONSE rx ->
  KPM indications flowing

## NOT the cause (ruled out with evidence)
- 28_552_kpm_meas.txt missing: cosmetic only (unit lookup when printing)
- E2AP_V2/KPM_V2_03 vs V3: rebuilt at V3, identical failure
- Multi-cell: failed identically at num_cells=1
- No UEs / no traffic: failed with 12 UEs attached and pings running
- Networking: RIC pinged cell nodes; routes resolved in both directions

## Working recipe (core node)

```bash
sudo apt-get install -y git cmake ninja-build build-essential swig libsctp-dev \
     python3-dev pkg-config libconfig-dev libconfig++-dev bison flex libpcre2-dev \
     autoconf automake libtool gcc-10 g++-10
sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-10 100
sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-10 100
sudo update-alternatives --set gcc /usr/bin/gcc-10
sudo update-alternatives --set g++ /usr/bin/g++-10

cd /tmp && git clone https://github.com/mouse07410/asn1c asn1c-src && cd asn1c-src
git checkout 940dd5fa9f3917913fd487b13dfddfacd0ded06e
autoreconf -iv
CFLAGS="-O2 -fno-strict-aliasing" ./configure --prefix /opt/asn1c/
make -j$(nproc) && sudo make install && sudo ldconfig

# KEY STEP: FlexRIC from the OAI submodule
cd ~
git clone https://gitlab.eurecom.fr/oai/openairinterface5g.git oai-src
cd oai-src && git checkout develop && git submodule update --init --recursive
cd openair2/E2AP/flexric && mkdir build && cd build
cmake -DKPM_VERSION=KPM_V3_00 -DE2AP_VERSION=E2AP_V3 -DXAPP_DB=SQLITE3_XAPP ..
make -j$(nproc) && sudo make install

# make install RESETS flexric.conf to 127.0.0.1 -- fix it AFTER installing
sudo sed -i 's/^NEAR_RIC_IP.*/NEAR_RIC_IP = 10.10.1.1/' /usr/local/etc/flexric/flexric.conf
```

gNB config: near_ric_ip_addr = "10.10.1.1" (core's LAN IP, where the native RIC binds).

## Bring-up order (hard rule)
core -> RIC -> gNB -> UEs -> traffic -> xApp

## Operational rules
- Never restart the RIC without restarting every gNB after it. OAI's E2 agent does
  not recover: it heartbeats the dead association and the RIC replies SCTP ABORT.
  Restarting a gNB drops all of its UEs.
- Never Ctrl+C an xApp: it leaves a zombie SCTP association with full socket buffers,
  and subsequent xApps then time out on E42 SETUP.
- Never auto-start an xApp from compose. The old kpm-xapp service started before any
  gNB had completed E2 setup, crashed (exit 139), and poisoned the RIC from boot.
- FlexRIC block-buffers stdout when not on a TTY: docker logs returns nothing and the
  RIC looks dead when it is fine. Use tty: true, stdbuf -oL, or a terminal.

## Open item
The containerized flexric-kpm:v2 image is built from the OAI submodule yet still
failed, while a fresh native build from develop worked -- likely the image was built
from an older clone and the submodule moved. Pin the OAI commit explicitly in both
Dockerfiles so the E2 agent and the RIC come from provably the same source.

## The dataset
xapp_db_* (sqlite) contains: KPM_MeasRecord (per-UE KPM), MAC_UE (per-UE per-slot:
pusch_snr, pucch_snr, wb_cqi, dl/ul_mcs, dl/ul_bler, HARQ rounds, PRBs, BSR, PHR),
RLC_bearer, PDCP_bearer, GTP_NGUT, SLICE, RC_MEAS_REPORT (rsrp/rsrq/sinr).
MAC_UE is the table that responds to channel-model changes.
