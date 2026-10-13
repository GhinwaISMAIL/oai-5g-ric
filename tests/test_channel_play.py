import importlib.util
import json
import socketserver
import threading
import time
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "bin" / "channel-cell.py"
SPEC = importlib.util.spec_from_file_location("channel_cell_play", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

PROMPT = "softmodem_5Gue> "


class FakeUe(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, ignore_modify=False, drop_after=None):
        super().__init__(("127.0.0.1", 0), Handler)
        self.values = {"ploss": 0.0, "noise_power_dB": -30.0}
        self.ignore_modify = ignore_modify
        self.drop_after = drop_after
        self.commands = []
        self.connections = 0
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.server_address[1]

    def show(self):
        return (f"model 0 rfsimu_channel_enB0 type AWGN:\n"
                f"path loss: {self.values['ploss']:.6f}  noise: {self.values['noise_power_dB']:.6f} "
                f"rchannel offset: 0    forget factor; 0.000000\n----------------\n")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        server.connections += 1
        self.wfile.write(PROMPT.encode())
        for raw in self.rfile:
            line = raw.decode().strip()
            server.commands.append(line)
            if server.drop_after is not None and len(server.commands) == server.drop_after:
                return
            if line == "channelmod show current":
                self.wfile.write((server.show() + PROMPT).encode())
            elif line.startswith("channelmod modify 0 "):
                _, _, _, name, value = line.split()
                if not server.ignore_modify:
                    server.values[name] = float(value)
                self.wfile.write(("ok\n" + PROMPT).encode())
            else:
                self.wfile.write(PROMPT.encode())


def cell_for(*servers):
    ports = {index: server.port for index, server in enumerate(servers, start=1)}
    return MODULE.Cell(1, resolve=lambda cell, ue: (f"ric5g-ue-cell{cell}-{ue}", f"127.0.0.1:{ports[ue]}"))


def events(*rows):
    return [{"event_index": i, "at_s": at, "ue": ue, "parameter": parameter, "value": value,
             "target": f"ue{ue}", "cell": 1, "direction": "dl"}
            for i, (at, ue, parameter, value) in enumerate(rows)]


def test_events_are_applied_in_time_order_with_readback():
    ue = FakeUe()
    cell = cell_for(ue)
    now = [100.0]
    slept = []
    records = []
    ok = MODULE.play(
        cell, events((2.0, 1, "ploss", 6.0), (0.0, 1, "noise_power_dB", -20.0), (2.0, 1, "noise_power_dB", -10.0)),
        start_epoch=100.0, report=records.append, clock=lambda: now[0],
        sleep=lambda seconds: (slept.append(seconds), now.__setitem__(0, now[0] + seconds)))
    cell.close()
    assert ok
    assert [r["event_index"] for r in records] == [1, 0, 2]
    assert slept == [2.0]
    assert all(r["status"] == "verified" and r["verified"] for r in records)
    assert ue.values == {"ploss": 6.0, "noise_power_dB": -10.0}
    assert ue.connections == 1


def test_late_event_is_applied_and_lag_is_recorded():
    ue = FakeUe()
    cell = cell_for(ue)
    records = []
    MODULE.play(cell, events((0.0, 1, "ploss", 3.0)), start_epoch=time.time() - 5.0,
                report=records.append)
    cell.close()
    assert records[0]["status"] == "verified"
    assert records[0]["lag_s"] == pytest.approx(5.0, abs=0.5)


def test_unverified_change_stops_the_timeline():
    ue = FakeUe(ignore_modify=True)
    cell = cell_for(ue)
    records = []
    ok = MODULE.play(cell, events((0.0, 1, "ploss", 3.0), (0.0, 1, "ploss", 4.0)),
                     start_epoch=time.time(), report=records.append)
    cell.close()
    assert not ok
    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert json.loads(records[0]["error"])["verified"] is False


def test_lost_connection_is_reopened_once():
    ue = FakeUe(drop_after=1)
    cell = cell_for(ue)
    records = []
    ok = MODULE.play(cell, events((0.0, 1, "ploss", 7.0)), start_epoch=time.time(), report=records.append)
    cell.close()
    assert ok
    assert ue.values["ploss"] == 7.0
    assert ue.connections == 2


def test_many_simultaneous_changes_finish_quickly():
    servers = [FakeUe() for _ in range(12)]
    cell = cell_for(*servers)
    rows = [(0.0, ue, parameter, float(ue))
            for ue in range(1, 13) for parameter in ("ploss", "noise_power_dB")]
    started = time.perf_counter()
    ok = MODULE.play(cell, events(*rows), start_epoch=time.time(), report=lambda record: None)
    elapsed = time.perf_counter() - started
    cell.close()
    assert ok
    assert elapsed < 1.0
    assert [server.values["ploss"] for server in servers] == [float(ue) for ue in range(1, 13)]


def test_check_reads_each_target_once():
    ue = FakeUe()
    cell = cell_for(ue)
    targets = [{"target": "ue1", "ue": 1, "parameter": "ploss"},
               {"target": "ue1", "ue": 1, "parameter": "ploss"},
               {"target": "ue1", "ue": 1, "parameter": "noise_power_dB"}]
    result = MODULE.check_targets(cell, targets)
    cell.close()
    assert result["checked"] == 2
    assert {item["model_type"] for item in result["targets"]} == {"AWGN"}
    assert ue.commands.count("channelmod show current") == 2
