"""Simulator CLI -- the process that stands in for the dongle fleet.

    # live, pushing into the backend
    python -m simulator.run_simulator --vehicles 6 --seed 42 --rate 1.0 \
           --sink ws://localhost:8000/ws/ingest

    # replay a recorded golden run, packet for packet
    python -m simulator.run_simulator --replay data/recorded_run.jsonl

    # headless, for building the training set
    python -m simulator.run_simulator --headless --hours 200 --out ml/dataset.csv

stdout is one aligned line per packet and is visible during the demo, so it is
column-stable and colour-coded by driving regime rather than a wall of JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from adapters.simulator_adapter import SimulatorAdapter
from simulator import physics

RESET = "\x1b[0m"
DIM = "\x1b[2m"
GREEN = "\x1b[92m"
AMBER = "\x1b[93m"
ROSE = "\x1b[91m"
CYAN = "\x1b[96m"
BOLD = "\x1b[1m"

# Cap on the golden replay file. Six vehicles at 1 Hz fills this in half an
# hour, which is plenty to replay a demo from and keeps the committed file
# around 12 MB rather than unbounded.
DEFAULT_RECORD_LIMIT = 10_800


def _co2_colour(co2_gps: float) -> str:
    """Green under 3 g/s, amber to 8, rose above. Matches the map marker bands."""
    if co2_gps < 3.0:
        return GREEN
    if co2_gps < 8.0:
        return AMBER
    return ROSE


def _print_header() -> None:
    print(f"{BOLD}{'time':<9}{'vehicle':<16}{'type':<11}{'state':<13}"
          f"{'speed':>9}{'rpm':>7}{'load':>7}{'fuel':>10}{'CO2':>11}{RESET}")
    print(DIM + "-" * 93 + RESET)


def _format_line(packet: dict, state: str, co2_gps: float) -> str:
    obd = packet["obd"]
    ts = packet["ts"][11:19]
    colour = _co2_colour(co2_gps)
    return (
        f"{DIM}{ts:<9}{RESET}"
        f"{CYAN}{packet['vehicle_id']:<16}{RESET}"
        f"{packet['meta']['vehicle_type']:<11}"
        f"{state:<13}"
        f"{obd['speed_kmh']:>7.1f} k"
        f"{obd['rpm']:>7}"
        f"{obd['engine_load_pct']:>6.1f}%"
        f"{obd['fuel_rate_lph']:>8.2f} L/h"
        f"{colour}{co2_gps:>8.2f} g/s{RESET}"
    )


class Recorder:
    """Appends emitted packets to the golden replay file, up to a cap."""

    def __init__(self, path: Path, limit: int, enabled: bool = True) -> None:
        self.path = path
        self.limit = limit
        self.enabled = enabled
        self.count = 0
        self._fh = None
        self._warned = False

    def __enter__(self) -> "Recorder":
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
        return self

    def write(self, packet: dict) -> None:
        if not self._fh:
            return
        if self.count >= self.limit:
            if not self._warned:
                print(f"{DIM}  [recorder] {self.path.name} reached {self.limit} packets; "
                      f"no longer recording. Replay file is complete.{RESET}", file=sys.stderr)
                self._warned = True
            return
        self._fh.write(json.dumps(packet, separators=(",", ":")) + "\n")
        self.count += 1
        if self.count % 200 == 0:
            self._fh.flush()

    def __exit__(self, *exc) -> None:
        if self._fh:
            self._fh.flush()
            self._fh.close()


async def _sink_sender(sink_url: str, outbox: asyncio.Queue, adapter: SimulatorAdapter) -> None:
    """Ship packets to the backend, and take scenario injections back.

    The ingest socket is bidirectional on purpose: POST /api/scenario lands on
    the backend, which relays a control frame down this same socket. That way
    the scenario path is identical whether packets come from here or from a
    real broker -- the backend never reaches into the simulator's process.
    """
    import websockets

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(sink_url, max_queue=256, ping_interval=20) as ws:
                print(f"{GREEN}  [sink] connected to {sink_url}{RESET}", file=sys.stderr)
                backoff = 1.0

                async def receive_control() -> None:
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("kind") != "scenario":
                            continue
                        ok = await adapter.inject_scenario(
                            msg["vehicle_id"], msg["scenario"],
                            **{k: v for k, v in msg.items()
                               if k not in ("kind", "vehicle_id", "scenario")},
                        )
                        flag = f"{AMBER}INJECTED{RESET}" if ok else f"{ROSE}REJECTED{RESET}"
                        print(f"  [scenario] {flag} {msg['scenario']} -> {msg['vehicle_id']}",
                              file=sys.stderr)

                receiver = asyncio.create_task(receive_control())
                try:
                    while True:
                        item = await outbox.get()
                        await ws.send(json.dumps(item, separators=(",", ":")))
                finally:
                    receiver.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport failure retries
            print(f"{ROSE}  [sink] {type(exc).__name__}: {exc} -- retrying in {backoff:.0f}s{RESET}",
                  file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2)


async def run_live(args: argparse.Namespace) -> int:
    adapter = SimulatorAdapter(vehicles=args.vehicles, seed=args.seed, rate_hz=args.rate)
    outbox: asyncio.Queue = asyncio.Queue(maxsize=512)
    sender = asyncio.create_task(_sink_sender(args.sink, outbox, adapter)) if args.sink else None

    print(f"{BOLD}HIL telemetry simulator{RESET}  "
          f"{args.vehicles} vehicles | {args.rate:g} Hz | seed {args.seed} | "
          f"sink {args.sink or 'stdout only'}")
    for v in adapter.fleet:
        print(f"{DIM}  {v.device_id}  {v.vehicle_id}  {v.vehicle_type:<10} "
              f"{v.route_key:<16} driver {v.driver_id} (factor {v.driver_factor}){RESET}")
    print()
    _print_header()

    state_by_id = {v.vehicle_id: v for v in adapter.fleet}
    recorder = Recorder(Path(args.record), args.record_limit, enabled=not args.no_record)
    emitted = 0
    try:
        with recorder:
            async for packet in adapter.stream():
                vehicle = state_by_id[packet["vehicle_id"]]
                # The printed CO2 is derived from the packet's own PID 0x5E
                # reading, not from the clean physics -- what is on screen is
                # what a real dongle would have reported.
                litres_per_s = packet["obd"]["fuel_rate_lph"] / 3600.0
                grams_per_s = litres_per_s * config.FUEL_SPECS[
                    packet["meta"]["fuel_type"]]["density_kg_per_l"] * 1000.0
                co2_gps = grams_per_s * (
                    config.FUEL_SPECS[packet["meta"]["fuel_type"]]["ef_ttw_kg_per_l"]
                    / config.FUEL_SPECS[packet["meta"]["fuel_type"]]["density_kg_per_l"])
                print(_format_line(packet, vehicle.state, co2_gps))
                recorder.write(packet)
                for name in vehicle.newly_forced:
                    print(f"  [scenario] {AMBER}LIVE{RESET} {name} on {vehicle.vehicle_id}",
                          file=sys.stderr)
                    if args.sink:
                        try:
                            outbox.put_nowait({"kind": "scenario_live", "scenario": name,
                                               "vehicle_id": vehicle.vehicle_id})
                        except asyncio.QueueFull:
                            pass
                if args.sink:
                    try:
                        outbox.put_nowait(packet)
                    except asyncio.QueueFull:
                        # Backend is behind. Dropping the oldest is the right
                        # trade at 1 Hz: a live dashboard wants current data,
                        # not a backlog.
                        outbox.get_nowait()
                        outbox.put_nowait(packet)
                emitted += 1
                if args.max_packets and emitted >= args.max_packets:
                    return 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if sender:
            sender.cancel()
        print(f"\n{DIM}  emitted {emitted} packets; recorded {recorder.count} "
              f"to {recorder.path}{RESET}", file=sys.stderr)
    return 0


async def run_replay(args: argparse.Namespace) -> int:
    """Replay a recorded run at the original rate.

    Byte-identical packets go out, so a replay reproduces a known-good run
    exactly: same GPS, same PID readings, same trip IDs.
    """
    path = Path(args.replay)
    if not path.exists():
        print(f"{ROSE}replay file not found: {path}{RESET}", file=sys.stderr)
        return 1

    packets = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not packets:
        print(f"{ROSE}replay file is empty: {path}{RESET}", file=sys.stderr)
        return 1

    print(f"{BOLD}REPLAY{RESET}  {len(packets)} packets from {path}  "
          f"({packets[0]['ts']} -> {packets[-1]['ts']})")
    print()
    _print_header()

    outbox: asyncio.Queue = asyncio.Queue(maxsize=512)
    sender = None
    if args.sink:
        adapter = SimulatorAdapter(vehicles=1, seed=args.seed)  # scenario sink only
        sender = asyncio.create_task(_sink_sender(args.sink, outbox, adapter))

    # Packets are interleaved across vehicles; one tick is one group sharing a
    # timestamp, so pace per distinct timestamp rather than per packet.
    per_tick = max(1, len({p["vehicle_id"] for p in packets[:64]}))
    try:
        for i, packet in enumerate(packets):
            litres_per_s = packet["obd"]["fuel_rate_lph"] / 3600.0
            fuel = config.FUEL_SPECS[packet["meta"]["fuel_type"]]
            co2_gps = litres_per_s * fuel["ef_ttw_kg_per_l"] * 1000.0
            print(_format_line(packet, "REPLAY", co2_gps))
            if args.sink:
                await outbox.put(packet)
            if (i + 1) % per_tick == 0:
                await asyncio.sleep(1.0 / args.rate)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if sender:
            await asyncio.sleep(0.5)  # let the tail drain
            sender.cancel()
    return 0


def run_headless(args: argparse.Namespace) -> int:
    """Delegate to the dataset builder -- one implementation, two entry points."""
    from ml.generate_dataset import generate

    out = Path(args.out or config.DATASET_PATH)
    generate(hours=args.hours, out_path=out, seed=args.seed, vehicles=args.vehicles)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m simulator.run_simulator",
        description="Hardware-in-the-loop telemetry simulator (stands in for the ESP32 fleet).",
    )
    p.add_argument("--vehicles", type=int, default=config.DEFAULT_VEHICLE_COUNT)
    p.add_argument("--seed", type=int, default=42, help="deterministic run seed")
    p.add_argument("--rate", type=float, default=config.TICK_HZ, help="ticks per second")
    p.add_argument("--sink", default=None, help="ws:// URL to push packets to")
    p.add_argument("--replay", default=None, help="replay a recorded .jsonl run")
    p.add_argument("--headless", action="store_true", help="generate a training CSV instead")
    p.add_argument("--hours", type=float, default=200.0, help="vehicle-hours, headless mode")
    p.add_argument("--out", default=None, help="output CSV, headless mode")
    p.add_argument("--record", default=str(config.RECORDED_RUN_PATH))
    p.add_argument("--record-limit", type=int, default=DEFAULT_RECORD_LIMIT)
    p.add_argument("--no-record", action="store_true", help="do not append to the golden run")
    p.add_argument("--max-packets", type=int, default=0, help="stop after N packets (0 = forever)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.headless:
        return run_headless(args)
    if args.replay:
        return asyncio.run(run_replay(args))
    return asyncio.run(run_live(args))


if __name__ == "__main__":
    sys.exit(main())
