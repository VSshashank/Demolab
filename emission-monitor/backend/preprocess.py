"""L2: cleaning, gap handling and streaming feature engineering.

Two things matter here.

First, this is the *only* implementation of feature engineering in the system.
ml/generate_dataset.py runs the training set through this exact code, and
backend/main.py runs live packets through it. Building features twice is how a
model that scores 0.96 offline scores 0.6 in production.

Second, everything is computed from an in-memory ring buffer per vehicle. A
rolling 30-second mean is one line of pandas and a disaster in a streaming
loop: re-reading SQLite for every packet would put a disk round trip in the
1 Hz path and cap throughput well below what six vehicles need.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import config
from simulator.routes import haversine_m

# Category encodings. Deterministic and derived from config, so the training
# set and live inference agree by construction. train_models.py persists
# sklearn LabelEncoders built from these same lists, and inference.py refuses
# to start if the persisted ones disagree -- the encoder file is then a check,
# not a second source of truth.
VEHICLE_TYPE_CATEGORIES = sorted(config.VEHICLE_SPECS)
FUEL_TYPE_CATEGORIES = sorted(config.FUEL_SPECS)
VEHICLE_TYPE_ENC = {name: i for i, name in enumerate(VEHICLE_TYPE_CATEGORIES)}
FUEL_TYPE_ENC = {name: i for i, name in enumerate(FUEL_TYPE_CATEGORIES)}

REJECT_REASONS = (
    "gps_quality", "speed_implausible", "accel_implausible", "malformed", "duplicate_ts",
)


@dataclass
class Sample:
    """One accepted packet, cleaned, with its engineered features."""

    ts: datetime
    vehicle_id: str
    trip_id: str
    device_id: str
    lat: float
    lon: float
    alt_m: float
    speed_kmh: float
    accel_ms2: float
    rpm: int
    engine_load_pct: float
    throttle_pct: float
    coolant_temp_c: float
    fuel_rate_lph: float
    payload_kg: float
    gvw_kg: float
    rated_gvw_kg: float
    vehicle_type: str
    fuel_type: str
    driver_id: str
    source: str
    dtc_count: int
    heading_deg: float
    # engineered
    load_ratio: float
    rolling_mean_speed_30s: float
    rolling_std_accel_30s: float
    idle_flag: int
    stop_go_ratio_60s: float
    grade_proxy: float
    distance_m: float  # ground distance since the previous accepted sample
    dt_s: float
    trip_break: bool  # a gap over MAX_GAP_SECONDS ended the previous segment
    forward_filled: bool

    def feature_row(self) -> dict:
        """Exactly config.FEATURE_COLUMNS, in that order."""
        return {
            "speed_kmh": self.speed_kmh,
            "accel_ms2": self.accel_ms2,
            "rpm": self.rpm,
            "engine_load_pct": self.engine_load_pct,
            "throttle_pct": self.throttle_pct,
            "coolant_temp_c": self.coolant_temp_c,
            "gvw_kg": self.gvw_kg,
            "load_ratio": self.load_ratio,
            "vehicle_type_enc": VEHICLE_TYPE_ENC[self.vehicle_type],
            "fuel_type_enc": FUEL_TYPE_ENC[self.fuel_type],
            "rolling_mean_speed_30s": self.rolling_mean_speed_30s,
            "rolling_std_accel_30s": self.rolling_std_accel_30s,
            "idle_flag": self.idle_flag,
            "stop_go_ratio_60s": self.stop_go_ratio_60s,
        }


@dataclass
class _VehicleWindow:
    """Ring buffer of recent history for one vehicle.

    maxlen is 120 samples = 2 minutes at 1 Hz, which covers the longest window
    any feature needs (60 s) with headroom for the anomaly rules.
    """

    speeds: deque = field(default_factory=lambda: deque(maxlen=config.ROLLING_WINDOW_SECONDS))
    accels: deque = field(default_factory=lambda: deque(maxlen=config.ROLLING_WINDOW_SECONDS))
    last_ts: datetime | None = None
    last_speed_kmh: float | None = None
    last_lat: float | None = None
    last_lon: float | None = None
    last_alt: float | None = None
    last_trip_id: str | None = None
    last_sample: Sample | None = None
    consecutive_rejects: int = 0


def parse_ts(raw: str) -> datetime:
    """Parse the schema's ISO-8601 Z timestamp. Kept in one place on purpose."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


@dataclass
class Rejection:
    reason: str
    detail: str
    vehicle_id: str


class StreamPreprocessor:
    """Stateful per-vehicle cleaner and feature builder.

    One instance serves the whole fleet. `process()` returns a Sample, or a
    Rejection explaining why the packet was dropped -- never silently None, so
    the health endpoint can report a real rejection breakdown instead of an
    unexplained packet deficit.
    """

    def __init__(self) -> None:
        self._windows: dict[str, _VehicleWindow] = {}
        self.accepted = 0
        self.rejected: dict[str, int] = {r: 0 for r in REJECT_REASONS}
        self.forward_filled = 0
        self.trip_breaks = 0

    def window(self, vehicle_id: str) -> _VehicleWindow:
        return self._windows.setdefault(vehicle_id, _VehicleWindow())

    def reset(self) -> None:
        self._windows.clear()
        self.accepted = 0
        self.rejected = {r: 0 for r in REJECT_REASONS}
        self.forward_filled = 0
        self.trip_breaks = 0

    def _reject(self, reason: str, detail: str, vehicle_id: str) -> Rejection:
        self.rejected[reason] += 1
        return Rejection(reason=reason, detail=detail, vehicle_id=vehicle_id)

    def process(self, packet: dict) -> Sample | Rejection:
        try:
            vehicle_id = packet["vehicle_id"]
            gps, obd, meta = packet["gps"], packet["obd"], packet["meta"]
            cargo, derived = packet["cargo"], packet["derived"]
            ts = parse_ts(packet["ts"])
        except (KeyError, ValueError, TypeError) as exc:
            return self._reject("malformed", f"{type(exc).__name__}: {exc}",
                                str(packet.get("vehicle_id", "?")))

        win = self.window(vehicle_id)

        # -- quality gate --------------------------------------------------
        # A fix with a poor dilution of precision or too few satellites is not
        # slightly wrong, it is wrong by hundreds of metres, and it will drag a
        # route-deviation alert and the distance integral with it.
        if gps["hdop"] > config.MAX_HDOP or gps["sats"] < config.MIN_SATS:
            win.consecutive_rejects += 1
            return self._reject("gps_quality",
                                f"hdop={gps['hdop']} sats={gps['sats']}", vehicle_id)

        speed_kmh = float(obd["speed_kmh"])
        if speed_kmh > config.MAX_PLAUSIBLE_SPEED_KMH or speed_kmh < 0:
            win.consecutive_rejects += 1
            return self._reject("speed_implausible", f"{speed_kmh} km/h", vehicle_id)

        # -- timing and gaps -----------------------------------------------
        dt_s = 1.0 / config.TICK_HZ
        trip_break = False
        forward_filled = False
        if win.last_ts is not None:
            dt_s = (ts - win.last_ts).total_seconds()
            if dt_s <= 0:
                return self._reject("duplicate_ts", f"{ts.isoformat()}", vehicle_id)
            if dt_s > config.MAX_GAP_SECONDS:
                # Too long to interpolate across. End the segment and restart
                # the rolling windows: a 30-second mean spanning a two-minute
                # tunnel is a fabricated number.
                trip_break = True
                self.trip_breaks += 1
                win.speeds.clear()
                win.accels.clear()
                win.last_speed_kmh = None
                win.last_lat = win.last_lon = win.last_alt = None
            elif dt_s > 1.5 / config.TICK_HZ and win.last_sample is not None:
                # A single dropped sample. Carrying the previous reading
                # forward is the standard fill and is flagged, so the report's
                # data-quality declaration can count it.
                forward_filled = True
                self.forward_filled += 1

        if packet["trip_id"] != win.last_trip_id and win.last_trip_id is not None:
            trip_break = True
            win.speeds.clear()
            win.accels.clear()
            win.last_speed_kmh = None
            win.last_lat = win.last_lon = win.last_alt = None

        # -- recompute acceleration ----------------------------------------
        # The incoming derived.accel_ms2 is not trusted: on real hardware it is
        # whatever the dongle firmware chose to compute, and it is the single
        # most important input to two of the anomaly rules.
        if win.last_speed_kmh is None or trip_break:
            accel_ms2 = 0.0
        else:
            accel_ms2 = ((speed_kmh - win.last_speed_kmh) / 3.6) / max(dt_s, 1e-6)

        if abs(accel_ms2) > config.MAX_PLAUSIBLE_ACCEL_MS2:
            win.consecutive_rejects += 1
            return self._reject("accel_implausible", f"{accel_ms2:.2f} m/s2", vehicle_id)

        # -- ground distance and grade proxy -------------------------------
        distance_m = 0.0
        grade_proxy = 0.0
        if win.last_lat is not None and not trip_break:
            distance_m = haversine_m(win.last_lat, win.last_lon, gps["lat"], gps["lon"])
            if distance_m > 1.0 and win.last_alt is not None:
                # Rise over run. Below a metre of travel the GPS noise floor
                # dominates and this becomes a random number generator.
                grade_proxy = (gps["alt_m"] - win.last_alt) / distance_m
                grade_proxy = max(-0.15, min(0.15, grade_proxy))

        # -- rolling features ----------------------------------------------
        win.speeds.append(speed_kmh)
        win.accels.append(accel_ms2)

        recent_30 = list(win.speeds)[-30:]
        recent_accel_30 = list(win.accels)[-30:]
        recent_60 = list(win.speeds)[-60:]

        rolling_mean_speed = sum(recent_30) / len(recent_30)
        if len(recent_accel_30) > 1:
            mean_a = sum(recent_accel_30) / len(recent_accel_30)
            var = sum((a - mean_a) ** 2 for a in recent_accel_30) / (len(recent_accel_30) - 1)
            rolling_std_accel = math.sqrt(var)
        else:
            rolling_std_accel = 0.0

        idle_flag = 1 if speed_kmh < config.IDLE_SPEED_KMH and obd["engine_on"] else 0
        # Fraction of the last minute spent stationary. Separates a city
        # delivery round from a highway run more cleanly than mean speed does.
        stop_go_ratio = sum(1 for s in recent_60 if s < config.IDLE_SPEED_KMH) / len(recent_60)

        rated = float(cargo["rated_gvw_kg"]) or 1.0
        sample = Sample(
            ts=ts,
            vehicle_id=vehicle_id,
            trip_id=packet["trip_id"],
            device_id=packet["device_id"],
            lat=float(gps["lat"]),
            lon=float(gps["lon"]),
            alt_m=float(gps["alt_m"]),
            speed_kmh=speed_kmh,
            accel_ms2=accel_ms2,
            rpm=int(obd["rpm"]),
            engine_load_pct=float(obd["engine_load_pct"]),
            throttle_pct=float(obd["throttle_pct"]),
            coolant_temp_c=float(obd["coolant_temp_c"]),
            fuel_rate_lph=float(obd["fuel_rate_lph"]),
            payload_kg=float(cargo["payload_kg"]),
            gvw_kg=float(cargo["gvw_kg"]),
            rated_gvw_kg=rated,
            vehicle_type=meta["vehicle_type"],
            fuel_type=meta["fuel_type"],
            driver_id=meta["driver_id"],
            source=meta["source"],
            dtc_count=int(obd["dtc_count"]),
            heading_deg=float(derived["heading_deg"]),
            load_ratio=float(cargo["gvw_kg"]) / rated,
            rolling_mean_speed_30s=rolling_mean_speed,
            rolling_std_accel_30s=rolling_std_accel,
            idle_flag=idle_flag,
            stop_go_ratio_60s=stop_go_ratio,
            grade_proxy=grade_proxy,
            distance_m=distance_m,
            dt_s=dt_s,
            trip_break=trip_break,
            forward_filled=forward_filled,
        )

        win.last_ts = ts
        win.last_speed_kmh = speed_kmh
        win.last_lat, win.last_lon, win.last_alt = sample.lat, sample.lon, sample.alt_m
        win.last_trip_id = sample.trip_id
        win.last_sample = sample
        win.consecutive_rejects = 0
        self.accepted += 1
        return sample

    def stats(self) -> dict:
        total = self.accepted + sum(self.rejected.values())
        return {
            "accepted": self.accepted,
            "rejected_total": sum(self.rejected.values()),
            "rejected_by_reason": dict(self.rejected),
            "accept_rate": round(self.accepted / total, 4) if total else 0.0,
            "forward_filled": self.forward_filled,
            "trip_breaks": self.trip_breaks,
        }
