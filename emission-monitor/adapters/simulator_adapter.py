"""Hardware-in-the-loop adapter: packets from the physics simulator.

This is the implemented adapter. It owns a fleet of `Vehicle` objects, steps
them at the configured rate, and yields the resulting schema v1.0 packets.

It deliberately exposes nothing else. The clean physics values the vehicles
compute are reachable through `truth_for()`, which only ml/generate_dataset.py
calls -- the backend never does, because on real hardware there is no truth to
read.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import config
from adapters.base import TelemetryAdapter, validate_packet
from simulator.scenarios import ScenarioBook
from simulator.vehicle import Vehicle, build_fleet


class SimulatorAdapter(TelemetryAdapter):
    source_name = "SIMULATOR"

    def __init__(
        self,
        vehicles: int = config.DEFAULT_VEHICLE_COUNT,
        seed: int = 42,
        rate_hz: float = config.TICK_HZ,
        start_time: datetime | None = None,
        realtime: bool = True,
    ) -> None:
        self.rate_hz = rate_hz
        self.dt = 1.0 / rate_hz
        self.realtime = realtime
        self.book = ScenarioBook()
        self.fleet: list[Vehicle] = build_fleet(vehicles, seed, start_time)
        self._by_id = {v.vehicle_id: v for v in self.fleet}

    async def stream(self):
        """Emit one packet per vehicle per tick, pacing to wall clock.

        Sleeps on the deadline rather than on `dt` so a slow consumer causes a
        skipped beat instead of accumulating drift -- over a ten-minute demo a
        naive `sleep(dt)` visibly falls behind.
        """
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while True:
            for packet in self.tick():
                yield packet
            next_tick += self.dt
            if self.realtime:
                await asyncio.sleep(max(0.0, next_tick - loop.time()))
            else:
                await asyncio.sleep(0)

    def tick(self) -> list[dict]:
        """Advance every vehicle one step. Synchronous, for headless dataset runs."""
        return [validate_packet(v.step(self.dt, self.book)) for v in self.fleet]

    def truth_for(self, vehicle_id: str):
        """Clean physics for the last tick. Training labels only -- NOT for the backend.

        There is no equivalent on real hardware, which is precisely why this is
        not part of the TelemetryAdapter interface.
        """
        return self._by_id[vehicle_id].last_truth

    async def inject_scenario(self, vehicle_id: str, scenario: str, **params) -> bool:
        if vehicle_id not in self._by_id:
            return False
        self.book.inject(vehicle_id, scenario, **params)
        return True

    def vehicle_ids(self) -> list[str]:
        return [v.vehicle_id for v in self.fleet]
