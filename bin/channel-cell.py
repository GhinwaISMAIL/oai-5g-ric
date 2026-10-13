#!/usr/bin/env python3
"""Read and modify live RFsim channel parameters on one POWDER cell node.

Run this helper as root on the cell node.  Each UE container is an independent
RFsim receiver for downlink and activates ``rfsimu_channel_enB0`` at model index
0.  Selecting the UE container therefore provides per-UE DL control; selecting
an index based on the UE number only modifies an inactive table entry.

The gNB activates ``rfsimu_channel_ueN`` models for uplink connections, but
their connection-to-UE identity is not stable after reconnects.  UL mutation is
rejected until that identity can be proven rather than mislabeled as cell-wide
or per-UE control.

Only parameters whose value can be read back unambiguously are accepted.  A
successful ``set`` therefore means both the command and its verification passed.

``set`` and ``show`` handle one change per process.  ``play`` and ``check`` keep
one telnet connection per UE open and handle a whole timeline, so changes are
applied in milliseconds instead of one SSH session per change.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import socket
import subprocess
import sys
import time


PARAMETERS = {
    "noise_power_dB": re.compile(r"\bnoise:\s*(-?\d+(?:\.\d+)?)"),
    "ploss": re.compile(r"\bpath loss:\s*(-?\d+(?:\.\d+)?)"),
    "riceanf": re.compile(r"\bricean fact\.:\s*(-?\d+(?:\.\d+)?)"),
    "aoa": re.compile(r"\bangle of arrival:\s*(-?\d+(?:\.\d+)?)"),
    "offset": re.compile(r"\brchannel offset:\s*(-?\d+(?:\.\d+)?)"),
    "forgetf": re.compile(r"\bforget factor;\s*(-?\d+(?:\.\d+)?)"),
}


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def container_running(name: str) -> bool:
    try:
        return run("docker", "inspect", "-f", "{{.State.Status}}", name) == "running"
    except subprocess.CalledProcessError:
        return False


def endpoint(cell: int, direction: str, ue: int | None) -> tuple[str, str, int]:
    if direction == "ul":
        raise RuntimeError(
            "UL runtime channel control is disabled: active gNB RFsim model "
            "indices follow connection state and cannot be mapped safely to a UE")
    if ue is None or ue < 1:
        raise ValueError("downlink control requires --ue >= 1")
    container = f"ric5g-ue-cell{cell}-{ue}"
    address = run(
        "docker", "inspect", "-f",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container)
    if not address:
        raise RuntimeError(f"{container} has no Docker address")
    return container, address, 0


def receive_available(sock: socket.socket, *, first_timeout: float = 1.5) -> str:
    chunks: list[bytes] = []
    sock.settimeout(first_timeout)
    while True:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
        if b">" in chunk:
            sock.settimeout(0.15)
    return b"".join(chunks).decode(errors="replace")


def command(address: str, text: str) -> str:
    with socket.create_connection((address, 9090), timeout=3.0) as sock:
        receive_available(sock, first_timeout=0.5)
        sock.sendall(text.encode() + b"\n")
        return receive_available(sock)


def show(address: str) -> str:
    return command(address, "channelmod show current")


def model_block(output: str, index: int) -> str:
    match = re.search(
        rf"(?ms)^model\s+{index}\s+.*?(?=^model\s+\d+\s+|\Z)", output)
    if not match:
        raise RuntimeError(f"channel model {index} was not present in telnet output")
    return match.group(0)


def model_identity(output: str, index: int) -> tuple[str, str]:
    first = model_block(output, index).splitlines()[0]
    match = re.match(rf"model\s+{index}\s+(\S+)\s+type\s+([^:]+):", first)
    if not match:
        raise RuntimeError(f"could not read channel model {index} identity")
    return match.group(1), match.group(2).strip()


def observed_value(output: str, index: int, parameter: str) -> float:
    match = PARAMETERS[parameter].search(model_block(output, index))
    if not match:
        raise RuntimeError(
            f"could not read {parameter} from channel model {index}")
    return float(match.group(1))


def inspect(args: argparse.Namespace) -> dict:
    container, address, index = endpoint(args.cell, args.direction, args.ue)
    if not container_running(container):
        raise RuntimeError(f"{container} is not running")
    output = show(address)
    model_name, model_type = model_identity(output, index)
    result = {
        "cell": args.cell,
        "ue": args.ue if args.direction == "dl" else None,
        "direction": args.direction,
        "container": container,
        "model_index": index,
        "model_name": model_name,
        "model_type": model_type,
        "telnet_address": f"{address}:9090",
        "reachable": True,
        "observed_epoch": time.time(),
    }
    if args.parameter:
        result["parameter"] = args.parameter
        result["observed"] = observed_value(output, index, args.parameter)
    return result


def modify(args: argparse.Namespace) -> dict:
    container, address, index = endpoint(args.cell, args.direction, args.ue)
    if not container_running(container):
        raise RuntimeError(f"{container} is not running")
    command(address, f"channelmod modify {index} {args.parameter} {args.value}")
    output = show(address)
    model_name, model_type = model_identity(output, index)
    observed = observed_value(output, index, args.parameter)
    verified = math.isclose(observed, args.value, rel_tol=1e-6, abs_tol=1e-6)
    result = {
        "cell": args.cell,
        "ue": args.ue if args.direction == "dl" else None,
        "direction": args.direction,
        "container": container,
        "model_index": index,
        "model_name": model_name,
        "model_type": model_type,
        "parameter": args.parameter,
        "requested": args.value,
        "observed": observed,
        "verified": verified,
        "applied_epoch": time.time(),
    }
    if not verified:
        raise RuntimeError(json.dumps(result, sort_keys=True))
    return result


def read_prompt(sock: socket.socket, timeout: float) -> str:
    """Read until the output ends with the telnet prompt."""
    deadline = time.monotonic() + timeout
    data = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if b">" in data:
                break
            raise TimeoutError("no telnet prompt")
        sock.settimeout(remaining)
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("telnet connection closed")
        data += chunk
        if data.rstrip().endswith(b">"):
            break
    return data.decode(errors="replace")


class Link:
    """One persistent telnet connection to the RFsim server of a UE."""

    def __init__(self, address: str, port: int = 9090, timeout: float = 3.0):
        host, separator, given = address.partition(":")
        self.address, self.port, self.timeout = host, int(given) if separator else port, timeout
        self.sock: socket.socket | None = None

    def open(self) -> None:
        self.close()
        self.sock = socket.create_connection((self.address, self.port), timeout=self.timeout)
        try:
            read_prompt(self.sock, 1.0)
        except TimeoutError:
            pass

    def send(self, text: str) -> str:
        if self.sock is None:
            self.open()
        self.sock.sendall(text.encode() + b"\n")
        return read_prompt(self.sock, self.timeout)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None


def docker_address(cell: int, ue: int) -> tuple[str, str]:
    container, address, _ = endpoint(cell, "dl", ue)
    return container, address


class Cell:
    """Verified downlink changes for the UEs of one cell over open connections."""

    def __init__(self, cell: int, resolve=docker_address, connect=Link):
        self.cell, self.resolve, self.connect = cell, resolve, connect
        self.links: dict[int, tuple[str, Link]] = {}

    def link(self, ue: int) -> tuple[str, Link]:
        if ue not in self.links:
            container, address = self.resolve(self.cell, ue)
            link = self.connect(address)
            link.open()
            self.links[ue] = (container, link)
        return self.links[ue]

    def drop(self, ue: int) -> None:
        if ue in self.links:
            self.links.pop(ue)[1].close()

    def exchange(self, ue: int, lines: list[str]) -> tuple[str, list[str]]:
        for attempt in (0, 1):
            try:
                container, link = self.link(ue)
                return container, [link.send(line) for line in lines]
            except (OSError, TimeoutError):
                self.drop(ue)
                if attempt:
                    raise
        raise RuntimeError("unreachable")

    def show(self, ue: int, parameter: str | None = None, index: int = 0) -> dict:
        container, (output,) = self.exchange(ue, ["channelmod show current"])
        model_name, model_type = model_identity(output, index)
        result = {
            "cell": self.cell, "ue": ue, "direction": "dl", "container": container,
            "model_index": index, "model_name": model_name, "model_type": model_type,
            "reachable": True, "observed_epoch": time.time(),
        }
        if parameter:
            result["parameter"] = parameter
            result["observed"] = observed_value(output, index, parameter)
        return result

    def set(self, ue: int, parameter: str, value: float, index: int = 0) -> dict:
        container, (_, output) = self.exchange(
            ue, [f"channelmod modify {index} {parameter} {value}", "channelmod show current"])
        model_name, model_type = model_identity(output, index)
        observed = observed_value(output, index, parameter)
        result = {
            "cell": self.cell, "ue": ue, "direction": "dl", "container": container,
            "model_index": index, "model_name": model_name, "model_type": model_type,
            "parameter": parameter, "requested": value, "observed": observed,
            "verified": math.isclose(observed, value, rel_tol=1e-6, abs_tol=1e-6),
            "applied_epoch": time.time(),
        }
        if not result["verified"]:
            raise RuntimeError(json.dumps(result, sort_keys=True))
        return result

    def close(self) -> None:
        for ue in list(self.links):
            self.drop(ue)


def emit(record: dict) -> None:
    print(json.dumps(record, sort_keys=True), flush=True)


def check_targets(cell: Cell, targets: list[dict]) -> dict:
    seen, results = set(), []
    for item in targets:
        identity = (item["ue"], item["parameter"])
        if identity in seen:
            continue
        seen.add(identity)
        result = cell.show(item["ue"], item["parameter"])
        result["target"] = item["target"]
        results.append(result)
    return {"checked": len(results), "targets": results}


def play(cell: Cell, events: list[dict], start_epoch: float, report=emit,
         clock=time.time, sleep=time.sleep) -> bool:
    """Apply the events at start_epoch + at_s.  A late event is still applied and
    its lag is recorded.  Stops at the first failed change.  Connections are opened
    before the first deadline so that setup time does not delay the first events."""
    for ue in sorted({event["ue"] for event in events}):
        cell.link(ue)
    for event in sorted(events, key=lambda row: (row["at_s"], row["event_index"])):
        deadline = start_epoch + event["at_s"]
        wait = deadline - clock()
        if wait > 0:
            sleep(wait)
        record = dict(event)
        record["scheduler_apply_epoch"] = clock()
        record["lag_s"] = record["scheduler_apply_epoch"] - deadline
        try:
            record.update(cell.set(event["ue"], event["parameter"], event["value"]))
            record["status"] = "verified"
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            report(record)
            return False
        report(record)
    return True


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="operation", required=True)
    show_parser = sub.add_parser("show")
    set_parser = sub.add_parser("set")
    for item in (show_parser, set_parser):
        item.add_argument("--cell", type=int, required=True)
        item.add_argument("--direction", choices=("dl", "ul"), required=True)
        item.add_argument("--ue", type=int)
    show_parser.add_argument("--parameter", choices=tuple(PARAMETERS))
    set_parser.add_argument("--parameter", choices=tuple(PARAMETERS), required=True)
    set_parser.add_argument("--value", type=float, required=True)
    for name in ("play", "check"):
        item = sub.add_parser(name, help="read a JSON request from standard input")
        item.add_argument("--cell", type=int, required=True)
        item.add_argument("--address", action="append", default=[], metavar="UE=HOST[:PORT]",
                          help="telnet endpoint of a UE instead of its Docker address (bench use)")
    return root


def main_batch(args: argparse.Namespace) -> None:
    request = json.load(sys.stdin)
    fixed = {int(key): value for key, _, value in (item.partition("=") for item in args.address)}
    cell = Cell(args.cell, resolve=(lambda c, ue: (f"ric5g-ue-cell{c}-{ue}", fixed[ue])) if fixed else docker_address)
    try:
        if args.operation == "check":
            emit(check_targets(cell, request["targets"]))
        elif not play(cell, request["events"], float(request["start_epoch"])):
            raise SystemExit(1)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    finally:
        cell.close()


def main() -> None:
    args = parser().parse_args()
    if args.operation in ("play", "check"):
        main_batch(args)
        return
    try:
        result = modify(args) if args.operation == "set" else inspect(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
