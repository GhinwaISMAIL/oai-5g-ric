# oai-5g-ric

Multi-cell OAI 5G SA testbed on POWDER, with a FlexRIC near-RT RIC and per-UE radio
channel modelling. The RAN is simulated at the PHY layer (RFsim) with a configurable
channel; the core network, control plane and user plane are real.

Instantiating the POWDER profile brings up the whole system: core network, RIC, one
or more cells, and their UEs.

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

`bin/mgen-preflight.sh` gates data collection on registration, tunnel presence, and
routing, and detects traffic that bypasses the 5G stack.

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
./build/examples/xApp/c/monitor/xapp_kpm_moni
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
- **Do not interrupt an xApp.** A killed xApp leaves an SCTP association behind on the
  RIC with undrained buffers; subsequent xApps then time out. The example xApps have
  a fixed run duration — let them exit.
- **Do not start an xApp automatically.** Started before the gNBs have completed E2
  setup, it crashes and leaves the RIC's xApp-facing state unusable.

Bring-up order, whether automatic or by hand: **core → RIC → gNB → UEs → traffic →
xApp.**

## Limits

The UPF allocates PDU addresses from `12.1.1.0/24`, bounding the total UE count.
Per-cell UE count is limited by the single `nr-softmodem` process serving the cell;
scale by adding cells rather than overloading one gNB. A small number of UEs may fail
to attach on first boot (a registration race) and recover on restart — `cell-setup.sh`
handles this.

## Layout

```
profile.py               POWDER profile: 1 core node + N cell nodes
BUILD.md                 image builds and the constraints that apply
bin/node-setup.sh        dispatcher (core | cell)
bin/core-setup.sh        core: 5G core network, then FlexRIC built and run natively
bin/cell-setup.sh        cell: routes, wait for AMF, gNB + UEs, data-network routes
bin/gen-channelmod.sh    per-UE channel models for a cell
bin/gen-subscribers.sh   subscriber database for num_cells × ues_per_cell
bin/mgen-preflight.sh    readiness gate before data collection
etc/gnb-cell.conf.tmpl   gNB template: E2 agent, channel model, per-cell identities
etc/nr-ue.conf.tmpl      UE template
Dockerfile.flexric       reference build for a containerised RIC (see BUILD.md)
```
