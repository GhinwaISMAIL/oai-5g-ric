# oai-5g-ric

Multi-cell OAI 5G SA RFsim testbed on POWDER: a 5G core plus a FlexRIC near-RT
RIC on one node, and N cell nodes each running an E2-enabled, chanmod-capable
gNB with its UEs colocated.

## Topology

```
                     experimental LAN 10.10.1.0/26
  core (.1) ──────────────────┬──────────────────┬────────── ...
  ┌──────────────────────┐    │                  │
  │ mysql udr udm ausf   │  cell1 (.11)       cell2 (.12)
  │ amf smf upf ext-dn   │  ┌─────────────┐   ┌─────────────┐
  │ FlexRIC + KPM xApp   │  │ gNB + K UEs │   │ gNB + K UEs │
  └──────────────────────┘  └─────────────┘   └─────────────┘
   Docker bridges:
     public  192.168.71.128/26
     traffic 192.168.72.128/26
```

A cell's gNB and its UEs stay on the same node — the RFsim IQ path must remain
local for the channel model to behave correctly. Only N2, N3 and E2 cross the
LAN, routed via the core node.

Key addresses: AMF `192.168.71.132`, UPF `192.168.71.134`,
FlexRIC `192.168.71.142`, ext-dn `192.168.72.135`.

## Instantiate

POWDER → Experiments → Create Experiment Profile → source **Git Repo** →
`https://github.com/GhinwaISMAIL/oai-5g-ric`, then Instantiate with:

| parameter | default | notes |
|---|---|---|
| `num_cells` | 2 | one gNB per cell node |
| `ues_per_cell` | 12 | UEs colocated with their gNB |
| `core_type` | d430 | core + RIC |
| `cell_type` | d740 | gNB + UEs (chanmod is CPU-heavy) |

Allow ~20–30 min. The core comes up first; each cell node adds its routes, waits
for the AMF, then starts its gNB and UEs. Progress: `/local/logs/setup.log` on
each node.

UE count is a parameter — the subscriber DB is generated for
`num_cells × ues_per_cell` before MySQL starts. Total UEs ≲ 250 (the UPF
allocates from `12.1.1.0/24`). Scale by adding cells rather than overloading one
gNB.

## Validate

```bash
# core — UEs registered
sudo docker logs ric5g-oai-amf 2>&1 | grep 5GMM-REGISTERED

# core — gNBs connected to the RIC, KPM metrics flowing
sudo docker logs ric5g-flexric  2>&1 | grep -E 'E2 SETUP|Accepting RAN function'
sudo docker logs ric5g-kpm-xapp 2>&1 | grep -E 'DRB.UEThp|RRU.PrbTot'

# cell — channel model active
sudo docker logs ric5g-gnb-cell1 2>&1 | grep OCM
#   expect: [OCM] Model rfsimu_channel_ue0 type AWGN allocated from config file

# cell — data path clean (gates all data collection)
sudo bash /local/repository/bin/mgen-preflight.sh quick
```

KPM metrics persist to sqlite under `/local/logs/xapp/` on the core node.

## Channel model

Per cell: `etc/channelmod-cell<N>.conf` — each cell has its own file, so cells
can differ. Per UE within a cell: the model list defines
`rfsimu_channel_ue0..ueN`.

Live, over telnet on a UE (the downlink channel is applied UE-side):

```
telnet <ue> 9090
  channelmod show current
  channelmod modify 1 noise_power_dB 30
```

Parameters: `riceanf aoa randaoa ploss noise_power_dB offset forgetf`.

## Images

Nodes pull these; nothing is compiled at boot.

| image | contents |
|---|---|
| `ghinwa555/oai-gnb-e2-chan:v1` | gNB: RFsim + chanmod, telnet, E2 agent |
| `ghinwa555/oai-nr-ue-chan:v1` | UE: RFsim + chanmod, telnet |
| `ghinwa555/flexric-kpm:v1` | nearRT-RIC, KPM xApp, 8 service models, sqlite DB |

The stock OAI images will not work: `oai-gnb:develop` does not apply file-based
channel models and has no E2 agent. To rebuild, see `PROVEN_RECIPE.md` and
`Dockerfile.flexric`.

## Layout

```
profile.py              POWDER profile: 1 core + N cell nodes
PROVEN_RECIPE.md        build recipe and verified configuration details
Dockerfile.flexric      rebuild recipe for the RIC image
bin/node-setup.sh       dispatcher (core | cell)
bin/core-setup.sh       core: CN + FlexRIC + KPM xApp
bin/cell-setup.sh       cell: routes -> wait for AMF -> gNB + UEs -> DN routes
bin/gen-subscribers.sh  subscriber DB for num_cells x ues_per_cell
bin/mgen-preflight.sh   gates data collection
etc/gnb-cell.conf.tmpl  gNB template (per-cell IDs substituted at setup)
etc/nr-ue.conf.tmpl     UE template (per-UE IMSI substituted at setup)
```
