# oai-5g-ric

Multi-cell OAI 5G SA testbed on POWDER, with a FlexRIC near-RT RIC and per-UE radio
channel modelling. The RAN is simulated at the PHY layer (RFsim) with a configurable
channel; the core network, control plane and user plane are real.

Instantiating the POWDER profile brings up the whole system: core network, RIC,
one to three reserved cell nodes, and their UEs.

## Architecture

```
                     experimental LAN 10.10.1.0/26
  core (.1) ──────────────────┬──────────────────┬────────── ...
  ┌──────────────────────┐    │                  │
  │ mysql udr udm ausf   │  cell1 (.11)       cell2 (.12)
  │ amf smf upf ext-dn   │  ┌─────────────┐   ┌─────────────┐
  │ near-RT RIC (host)   │  │    gNB      │   │    gNB      │
  └──────────────────────┘  │   + K UEs   │   │   + K UEs   │
   Docker bridges:          └─────────────┘   └─────────────┘
     public  192.168.71.128/26
     traffic 192.168.72.128/26
```

One core node runs the 5G core and the RIC. Each cell node runs one gNB with its UEs
colocated.

Each cell also receives a unique default Docker bridge
(`172.30.<cell>.1/24`). FlexRIC advertises every host address in its SCTP
association, so reusing Docker's `172.17.0.1` on every node can make the RIC send a
heartbeat to its own local bridge and receive an SCTP abort instead of reaching the
cell.

**A cell's gNB and its UEs must remain on the same node.** The RFsim IQ path between
them is local, which is what keeps the channel model the only source of impairment.
Splitting UEs onto a different node from their gNB would push IQ samples over the
network and contaminate the channel being modelled.

Only control and user plane cross the LAN, all of which tolerate network latency:

| flow | from → to | reachability |
|------|-----------|--------------|
| N2 (NGAP) | cell gNB → AMF `192.168.71.132` | route via core `.1`, forwarded by the core node |
| N3 (GTP-U) | cell gNB ↔ UPF `192.168.71.134` | same route; the gNB advertises its LAN IP for NGU |
| E2 | cell gNB → RIC `10.10.1.1` | direct on the LAN |

## Components

The gNB and UE run from custom images. Stock OAI images will not work: they do not
apply file-based channel models and carry no E2 agent.

| image | contents |
|-------|----------|
| `ghinwa555/oai-gnb-e2-chan:v2` | gNB with RFsim, channel model, telnet server, E2 agent |
| `ghinwa555/oai-nr-ue-chan:v2` | UE with RFsim, channel model, telnet server |

The near-RT RIC runs as a host process on the core node, built from OAI's FlexRIC
submodule at boot. It is not containerised: a RIC built from the standalone FlexRIC
repository sends subscription requests that OAI's embedded E2 agent does not answer,
and no indications are produced.

The setup pins the validated OAI revision
`70508ebaf52f2aae420566d380c6537f2efb9f0c` and FlexRIC revision
`ef6d722f22191eea74089966983da1f5ec1fedd4`. Before building, it applies a
checked, idempotent patch that replaces the fixed 2048-byte MAC, RLC, PDCP and GTP
SQLite aggregation buffers with capacity proportional to the number of records.
This prevents the `out_len >= max` assertions seen with multiple attached UEs.

`BUILD.md` documents how the images are built and the constraints that apply.

## Instantiating

Progress is in `/local/logs/setup.log` on every node, and the RIC's output is in
`/local/logs/nearRT-RIC.log` on the core.

Subscribers are generated for `num_cells × ues_per_cell` before MySQL starts, so UE
count is a profile parameter rather than a hand-edited database.

## Verifying the deployment

On the core:

```bash
# the RIC is up and has loaded its service models
grep "Loading SM ID" /local/logs/nearRT-RIC.log

# every gNB has registered over E2
grep "E2 SETUP-REQUEST rx" /local/logs/nearRT-RIC.log

# every gNB has registered with the AMF
sudo docker logs ric5g-oai-amf 2>&1 | grep -A3 "gNBs' information" | tail
```

On a cell node:

```bash
# the channel model is active (absent on stock images)
sudo docker logs ric5g-gnb-cell1 2>&1 | grep OCM
#   Model rfsimu_channel_ue0 type AWGN allocated from config file

# UEs are attached
for u in $(seq 1 12); do
  sudo docker exec ric5g-ue-cell1-$u ip -4 addr show oaitun_ue1 2>/dev/null \
    | grep -o 'inet [0-9.]*'
done

# the data path is intact end to end
sudo docker exec ric5g-ue-cell1-1 ping -I oaitun_ue1 -c3 192.168.72.135
```

### Distributed MGEN preflight

The external DN is on the core node while UE containers are on their respective
cell nodes. Run the MGEN coordinator from the operator workstation, where SSH can
reach both POWDER hosts. Do not run the old single-Docker-host workflow on a node.

First validate one UE with a short bidirectional flow:

```bash
cd oai-5g-ric

bash bin/mgen-run-distributed.sh \
  ghinwa@CORE_POWDER_HOST \
  ghinwa@CELL1_POWDER_HOST \
  1 1 full
```

Then validate all UEs on each cell:

```bash
# Registration, MGEN binary, and tunnel route only
bash bin/mgen-run-distributed.sh \
  ghinwa@CORE_POWDER_HOST ghinwa@CELL1_POWDER_HOST 1 all quick

# Bounded uplink and downlink MGEN flows
bash bin/mgen-run-distributed.sh \
  ghinwa@CORE_POWDER_HOST ghinwa@CELL1_POWDER_HOST 1 all full

bash bin/mgen-run-distributed.sh \
  ghinwa@CORE_POWDER_HOST ghinwa@CELL2_POWDER_HOST 2 all full
```

The default full test sends ten 1000-byte packets per second in each direction for
five seconds per UE. Override the controlled load with environment variables:

```bash
RATE=100 SIZE=1200 DURATION=60 \
  bash bin/mgen-run-distributed.sh \
  ghinwa@CORE_POWDER_HOST ghinwa@CELL1_POWDER_HOST 1 1 full
```

MGEN logs persist under `/local/logs/mgen` on the core and selected cell. The
coordinator also checks `oaitun_ue1` packet counters, so Docker-bridge traffic
cannot be mistaken for valid 5G user-plane traffic.

## The channel model

Each cell has its own model file, `etc/channelmod-cell<N>.conf`, generated at boot
with one model per UE (`rfsimu_channel_enB0` for the uplink, and
`rfsimu_channel_ue0 .. ue(K-1)` for each UE's downlink). Cells can therefore carry
different channel conditions, and UEs within a cell can differ from one another.

`bin/gen-channelmod.sh` produces the file. It supports a `uniform` mode (all UEs
identical) and a `gradient` mode (path loss increasing from cell centre to cell
edge), and any of AWGN, TDL-A/B/C, EPA, EVA or ETU.

The default is a quiet channel, deliberately. **Noise gates random access**: at
`noise_power_dB` of −4/−2 no UE completes RACH — they synchronise, decode SIB1, and
then loop on `RAR reception failed`. The generated baseline uses −30, at which UEs
attach reliably. Let the UEs attach first, then impair the channel.

Models can be changed at runtime over telnet. The server runs in the UE, since the
downlink channel is applied UE-side:

```
telnet <ue> 9090
  channelmod show current              # model 0 = uplink, 1..K = per-UE downlink
  channelmod modify 1 noise_power_dB 30
```

Parameters: `riceanf`, `aoa`, `randaoa`, `ploss`, `noise_power_dB`, `offset`,
`forgetf`.

## Collecting measurements

Run an xApp on the core, **after** the gNBs have registered and the UEs are attached
and carrying traffic. KPM reports per-UE throughput and resource usage; an idle cell
reports nothing.

```bash
cd /opt/oai-src/openair2/E2AP/flexric
./build/examples/xApp/c/monitor/xapp_gtp_mac_rlc_pdcp_moni
```

Measurements are written to a SQLite database at `/tmp/xapp_db_*`:

| table | contents |
|-------|----------|
| `KPM_MeasRecord` | per-UE KPM: throughput, PDCP volume, RLC delay, PRB usage |
| `MAC_UE` | per-UE, per-slot: PUSCH/PUCCH SNR, CQI, MCS, BLER, HARQ rounds, PRBs, BSR, PHR |
| `RLC_bearer`, `PDCP_bearer`, `GTP_NGUT` | per-bearer counters |
| `RC_MEAS_REPORT` | RSRP, RSRQ, SINR |
| `SLICE`, `UE_SLICE` | slice state |

`MAC_UE` is the table that responds to changes in the channel model.

## Operational constraints

- **Restarting the RIC orphans every gNB.** OAI's E2 agent does not re-establish: it
  heartbeats the dead association and the RIC replies with an SCTP abort. Every gNB
  must be restarted after the RIC.
- **Restarting a gNB drops all of its UEs.** They need an explicit restart, and their
  data-network routes re-applied.
- **Stop an xApp with `Ctrl-C`/`SIGINT`, never `SIGKILL`.** A clean interrupt sends
  subscription-delete requests and waits for their responses. Confirm
  `Successfully stopped` and `Test xApp run SUCCESSFULLY` in the log. Force-killing
  can leave the RIC's xApp-facing SCTP state with undrained buffers.
- **Do not start an xApp automatically.** Started before the gNBs have completed E2
  setup, it crashes and leaves the RIC's xApp-facing state unusable.

Bring-up order, whether automatic or by hand: **core → RIC → gNB → UEs → traffic →
xApp.**

## Limits

The profile accepts `num_cells=1..3`; each selected cell consumes one POWDER raw PC.
The UPF allocates PDU addresses from `12.1.1.0/24`, bounding the total UE count.
Per-cell UE count is limited by the single `nr-softmodem` process serving the cell;
scale by adding cells rather than overloading one gNB. A small number of UEs may fail
to attach on first boot (a registration race) and recover on restart — `cell-setup.sh`
handles this.

## Layout

```
profile.py               POWDER profile: 1 core node + 1–3 cell nodes
BUILD.md                 image builds and the constraints that apply
bin/node-setup.sh        dispatcher (core | cell)
bin/core-setup.sh        core: 5G core network, then FlexRIC built and run natively
bin/cell-setup.sh        cell: routes, wait for AMF, gNB + UEs, data-network routes
bin/gen-channelmod.sh    per-UE channel models for a cell
bin/gen-subscribers.sh   subscriber database for num_cells × ues_per_cell
bin/mgen-core.sh         core-local external-DN MGEN endpoint helper
bin/mgen-cell.sh         cell-local UE MGEN and tunnel-counter helper
bin/mgen-run-distributed.sh
                         workstation-side SSH coordinator for core + one cell
bin/mgen-preflight.sh    compatibility notice for the removed single-host flow
bin/patch-flexric-sqlite-buffers.py
                         checked MAC/RLC/PDCP/GTP SQLite buffer fix
etc/gnb-cell.conf.tmpl   gNB template: E2 agent, channel model, per-cell identities
etc/nr-ue.conf.tmpl      UE template
Dockerfile.flexric       reference build for a containerised RIC (see BUILD.md)
```
