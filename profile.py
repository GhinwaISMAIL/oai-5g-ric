#!/usr/bin/env python3
"""
POWDER profile: oai-5g-ric
Multi-cell OAI 5G SA RFsim scale-out with FlexRIC near-RT RIC + channel model.

Topology:
  1 x core node  : CN NFs + FlexRIC nearRT-RIC + KPM xApp        (default d430)
  N x cell nodes : one E2-enabled, chanmod-capable gNB + K UEs   (default d740)

Shared experimental LAN 10.10.1.0/26: core = .1, cell-k = .(10+k)

INVARIANT: each cell is a colocated island -- its gNB and its UEs run on the
SAME node, so the RFsim IQ path stays local and the channel model behaves as in
the validated single-node baseline. Only NGAP/N3/E2 cross the LAN.
"""

import geni.portal as portal
import geni.rspec.pg as rspec
import geni.rspec.igext as IG

pc = portal.context

pc.defineParameter("num_cells", "Number of cell nodes (gNBs)",
                   portal.ParameterType.INTEGER, 2)
pc.defineParameter("ues_per_cell", "UEs per cell",
                   portal.ParameterType.INTEGER, 12)
pc.defineParameter("core_type", "Core node hardware type",
                   portal.ParameterType.NODETYPE, "d430")
pc.defineParameter("cell_type", "Cell node hardware type",
                   portal.ParameterType.NODETYPE, "d740")

params = pc.bindParameters()

if params.num_cells < 1:
    pc.reportError(portal.ParameterError("num_cells must be >= 1", ["num_cells"]))
if params.ues_per_cell < 1:
    pc.reportError(portal.ParameterError("ues_per_cell must be >= 1", ["ues_per_cell"]))
pc.verifyParameters()

request = pc.makeRequestRSpec()

IMAGE = "urn:publicid:IDN+emulab.net+image+emulab-ops:UBUNTU22-64-STD"
MASK = "255.255.255.192"
CORE_LAN_IP = "10.10.1.1"

lan = request.LAN("ran-lan")

def add_iface(node, ip):
    iface = node.addInterface("if0")
    iface.addAddress(rspec.IPv4Address(ip, MASK))
    lan.addInterface(iface)

def setup(node, role, index):
    node.addService(rspec.Execute(
        shell="bash",
        command=("sudo mkdir -p /local/logs && "
                 "sudo bash /local/repository/bin/node-setup.sh %s %d %d %d "
                 ">> /local/logs/setup.log 2>&1"
                 % (role, index, params.num_cells, params.ues_per_cell))))

core = request.RawPC("core")
core.hardware_type = params.core_type
core.disk_image = IMAGE
add_iface(core, CORE_LAN_IP)
setup(core, "core", 0)

for k in range(1, params.num_cells + 1):
    cell = request.RawPC("cell%d" % k)
    cell.hardware_type = params.cell_type
    cell.disk_image = IMAGE
    add_iface(cell, "10.10.1.%d" % (10 + k))
    setup(cell, "cell", k)

tour = IG.Tour()
tour.Description(IG.Tour.TEXT,
    "Multi-cell OAI 5G SA RFsim scale-out with FlexRIC near-RT RIC and channel "
    "modelling. One core node runs the CN + RIC + KPM xApp; each cell node runs "
    "one E2-enabled, chanmod-capable gNB with its UEs colocated. "
    "PLMN 208/99, TAC=1, SST=1, DNN=oai.")
tour.Instructions(IG.Tour.TEXT,
    "Bring-up is staged: the core node comes up first, then each cell node waits "
    "for the AMF over the LAN before starting its gNB. Allow ~20-30 min. Check "
    "/local/logs/setup.log on each node.")
request.addTour(tour)

pc.printRequestRSpec()
