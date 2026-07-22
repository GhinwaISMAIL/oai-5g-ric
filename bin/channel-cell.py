#!/usr/bin/env python3
"""Read and modify live RFsim channel parameters on one POWDER cell node.

Run this helper as root on the cell node.  The gNB uses host networking, so its
telnet endpoint is 127.0.0.1:9090 and owns the cell-wide uplink model (index 0).
Each UE container has its own telnet endpoint and the UE's downlink model index
matches its one-based UE index inside the cell.

Only parameters whose value can be read back unambiguously are accepted.  A
successful ``set`` therefore means both the command and its verification passed.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import socket
import subprocess
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
        return f"ric5g-gnb-cell{cell}", "127.0.0.1", 0
    if ue is None or ue < 1:
        raise ValueError("downlink control requires --ue >= 1")
    container = f"ric5g-ue-cell{cell}-{ue}"
    address = run(
        "docker", "inspect", "-f",
        "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", container)
    if not address:
        raise RuntimeError(f"{container} has no Docker address")
    return container, address, ue


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
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        result = modify(args) if args.operation == "set" else inspect(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
