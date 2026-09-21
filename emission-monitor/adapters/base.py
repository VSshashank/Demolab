"""The adapter boundary -- the single seam between the sensor layer and
everything above it.

This is the file that makes the hardware claim in the README checkable rather
than aspirational. Layer 2 upward consumes `TelemetryAdapter.stream()` and is
forbidden from reading any field outside schema v1.0. If something above this
line reaches for a simulator internal -- clean physics, route geometry, a
Vehicle object -- the hardware swap breaks and nobody finds out until the
dongles are already on the trucks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

# Every field a consumer above L1 is permitted to rely on. Validated by
# `validate_packet` so a schema drift fails at the boundary, loudly, rather
# than as a KeyError three layers up at demo time.
SCHEMA_V1_SHAPE: dict[str, tuple[str, ...]] = {
    "gps": ("lat", "lon", "alt_m", "hdop", "sats"),
    "obd": (
        "speed_kmh", "rpm", "engine_load_pct", "throttle_pct", "coolant_temp_c",
        "maf_gps", "fuel_level_pct", "fuel_rate_lph", "engine_on", "dtc_count",
    ),
    "derived": ("accel_ms2", "heading_deg"),
    "cargo": ("payload_kg", "gvw_kg", "rated_gvw_kg"),
    "meta": ("vehicle_type", "fuel_type", "driver_id", "source"),
}
SCHEMA_V1_TOP = ("schema_version", "device_id", "vehicle_id", "trip_id", "ts")


class SchemaViolation(ValueError):
    """Raised when a packet does not conform to schema v1.0."""


def validate_packet(packet: dict) -> dict:
    """Assert a packet conforms to schema v1.0. Returns it unchanged.

    Cheap enough to run on every packet at 6-8 Hz, and worth it: a silent
    schema mismatch between a new adapter and the backend is exactly the class
    of bug that only shows up in front of an audience.
    """
    missing = [k for k in SCHEMA_V1_TOP if k not in packet]
    if missing:
        raise SchemaViolation(f"missing top-level field(s): {missing}")
    if packet["schema_version"] != "1.0":
        raise SchemaViolation(f"unsupported schema_version {packet['schema_version']!r}")
    for block, fields in SCHEMA_V1_SHAPE.items():
        if block not in packet:
            raise SchemaViolation(f"missing block {block!r}")
        absent = [f for f in fields if f not in packet[block]]
        if absent:
            raise SchemaViolation(f"block {block!r} missing field(s): {absent}")
    return packet


class TelemetryAdapter(ABC):
    """Source of schema v1.0 telemetry packets.

    Implementations are the only code allowed to know where packets come from.
    """

    #: Value that appears in `meta.source` on every packet this adapter emits.
    source_name: str = "UNKNOWN"

    @abstractmethod
    def stream(self) -> AsyncIterator[dict]:
        """Yield telemetry packets conforming to schema v1.0.

        Must be an async iterator. Should run until cancelled. Must not raise
        on a single malformed reading -- drop it and keep the stream alive,
        because a fleet monitor that dies on one bad packet is worthless.
        """

    async def start(self) -> None:
        """Open whatever the adapter needs. Default: nothing."""

    async def stop(self) -> None:
        """Release whatever `start` opened. Default: nothing."""

    async def inject_scenario(self, vehicle_id: str, scenario: str, **params) -> bool:
        """Apply a fault scenario, if this source can simulate one.

        Real hardware cannot, which is the honest answer and the default.
        """
        return False
