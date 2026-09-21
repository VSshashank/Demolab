"""Real-hardware adapter: ESP32 + ELM327 OBD-II dongle over MQTT.

STATUS: NOT IMPLEMENTED. The hardware is not procured. This file is the
specification of the work, not a placeholder -- it exists so that "swapping to
real hardware is one class" is a claim anyone can check by reading it.

Nothing else in this repository changes when this class is finished. The
backend consumes `TelemetryAdapter.stream()`; run_demo.sh picks the adapter.

WHAT THE HARDWARE LOOKS LIKE
----------------------------
Per vehicle:
  ESP32-WROOM-32  -- MCU, wifi/LTE uplink, MQTT client
  ELM327 (v1.5+)  -- OBD-II interpreter on the vehicle's 16-pin DLC, UART to
                     the ESP32 at 38400 baud. Genuine STN1110-based units only;
                     the cheap clones drop PIDs under sustained polling.
  NEO-6M / M8N    -- GPS, NMEA at 1 Hz over a second UART
  Buck converter  -- 12/24 V vehicle supply down to 5 V, with a hold-up cap so
                     a crank-induced brownout does not reboot the MCU mid-trip.

POLL SEQUENCE (one full pass per second)
----------------------------------------
The ELM327 is a serial device answering one PID at a time. Ten PIDs at ~25 ms
round trip is ~250 ms, which fits inside a 1 Hz budget with room for the GPS
read and the MQTT publish.

    AT Z            reset                            (once, at boot)
    AT E0           echo off -- halves the bytes on the wire
    AT L0           linefeeds off
    AT SP 0         auto-detect protocol             (once, at boot)

    01 0D  ->  speed_kmh        A                    km/h
    01 0C  ->  rpm              (256A + B) / 4       rpm
    01 04  ->  engine_load_pct  A * 100 / 255        %
    01 11  ->  throttle_pct     A * 100 / 255        %
    01 05  ->  coolant_temp_c   A - 40               degC
    01 10  ->  maf_gps          (256A + B) / 100     g/s
    01 2F  ->  fuel_level_pct   A * 100 / 255        %
    01 5E  ->  fuel_rate_lph    (256A + B) / 20      L/h
    01 01  ->  dtc_count        A & 0x7F             count
    01 1F  ->  engine_on        runtime > 0          bool

PID 0x5E (engine fuel rate) is the important one: it is the field this whole
system estimates with ML. Not every vehicle supports it. Probe with mode 01
PID 0x40/0x60 at boot; where it is absent, fall back to deriving fuel flow from
MAF and an assumed AFR, and set `meta.fuel_rate_source = "MAF_DERIVED"` so the
data quality declaration in the ISO 14083 report stays honest.

TRANSPORT
---------
    Broker topic (publish):   fleet/{vehicle_id}/telemetry
    Backend subscribes to:    fleet/+/telemetry
    QoS 1, retain false. QoS 2 is not worth the round trips at 1 Hz; a
    duplicate packet is harmless because the backend keys on (vehicle_id, ts).
    Payload: the same JSON as schema v1.0, `meta.source` set to "ESP32".

    Offline buffering: LTE drops constantly on the NH-66 stretch past Mulki.
    Spool unsent packets to SPIFFS and replay on reconnect, oldest first, with
    the original `ts` preserved. The backend's preprocessing already ends a
    trip segment on a gap over 5 s, so late arrivals must not be re-stamped.

WHAT HAS TO BE WRITTEN HERE
---------------------------
1. `start()`  -- connect to the broker, subscribe, begin the receive loop.
2. `stream()` -- yield packets off an asyncio.Queue the MQTT callback feeds.
3. Per-vehicle staleness: if a device goes quiet for more than 30 s, emit
   nothing for it rather than the last value. A frozen marker that looks live
   is worse than a marker that visibly stops.
4. `cargo.payload_kg` has no OBD PID. It comes from the dispatch system, not
   the vehicle. Join it in here from the TMS, keyed on trip_id.
5. Clock: trust the GPS time, not the ESP32 RTC, which drifts badly.

WHAT DOES NOT HAVE TO CHANGE
----------------------------
Preprocessing, feature engineering, the trained models, the anomaly rules, the
dashboard, and the ISO 14083 report. They consume schema v1.0 and have never
seen a simulator object. The models are trained on physics-derived labels, and
the real PID 0x5E reading is the same quantity in the same units -- so they
apply directly, though they should be re-validated against a few hundred hours
of real dongle data before anyone quotes the R2 on a slide.
"""

from __future__ import annotations

from typing import AsyncIterator

from adapters.base import TelemetryAdapter

MQTT_SUBSCRIBE_TOPIC = "fleet/+/telemetry"
MQTT_PUBLISH_TEMPLATE = "fleet/{vehicle_id}/telemetry"
EXPECTED_RATE_HZ = 1.0
DEVICE_STALE_AFTER_S = 30.0
ELM327_BAUD = 38_400

#: Mode-01 PIDs polled once per second, in issue order. See module docstring
#: for the decode of each.
PID_POLL_SEQUENCE = (
    "010D", "010C", "0104", "0111", "0105",
    "0110", "012F", "015E", "0101", "011F",
)


class ESP32Adapter(TelemetryAdapter):
    """Pending hardware procurement -- Phase 2.

    Deliberately raises rather than returning empty or synthetic data. If this
    ever silently yields nothing, a dashboard shows an idle fleet and everyone
    assumes the trucks are parked.
    """

    source_name = "ESP32"

    def __init__(self, broker_host: str = "localhost", broker_port: int = 1883,
                 topic: str = MQTT_SUBSCRIBE_TOPIC) -> None:
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.topic = topic

    async def start(self) -> None:
        raise NotImplementedError(
            "Pending hardware procurement - Phase 2. "
            "Needs: an MQTT broker, ESP32 firmware publishing to "
            f"{MQTT_PUBLISH_TEMPLATE}, and a payload-kg join from the dispatch "
            "system. See this module's docstring for the full PID sequence."
        )

    def stream(self) -> AsyncIterator[dict]:
        raise NotImplementedError(
            "Pending hardware procurement - Phase 2. "
            "Implement as an async generator draining the MQTT receive queue; "
            "validate each payload with adapters.base.validate_packet before "
            "yielding it."
        )
