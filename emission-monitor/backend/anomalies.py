"""L3 rule engine: turn a stream of clean samples into actionable alerts.

Design decisions worth stating.

Every recommendation figure is computed from config.py at import time. The
build spec's suggested wording for the idling alert quotes "~0.5 L/hr and
~1.3 kg CO2/hr", but the configured idle rate of 0.45 g/s works out at 1.94 L/h
and 5.2 kg CO2/h. Rather than pick which of the two to hardcode, the numbers
are derived, so the text is arithmetically true whatever the constants say.

Messages name the specific condition ("Idling 4m 12s, engine still running"),
never "Anomaly detected". A driver-facing alert that does not say what happened
is noise.

Route geometry is matched, not told. Schema v1.0 carries no route field, so the
engine snaps each new trip to the nearest known route from its own GPS trace.
A real deployment gets the planned route from dispatch keyed on trip_id; either
way nothing above the adapter boundary reads a simulator internal.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

import config
from backend.inference import Prediction
from backend.preprocess import Sample
from simulator import physics, routes

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# ---------------------------------------------------------------------------
# Derived figures for the recommendation text. Computed once, from config.
# ---------------------------------------------------------------------------
_IDLE_LCV_GPS = config.VEHICLE_SPECS["LCV"]["idle_fuel_gps"]
IDLE_LPH = physics.fuel_gps_to_lph(_IDLE_LCV_GPS, "DIESEL")
IDLE_CO2_KG_PER_H = (
    _IDLE_LCV_GPS * 3600.0
    * (config.FUEL_SPECS["DIESEL"]["ef_ttw_kg_per_l"]
       / config.FUEL_SPECS["DIESEL"]["density_kg_per_l"]) / 1000.0
)


def _duration(seconds: float) -> str:
    """4m 12s, 45s, 1h 03m. Read aloud during a demo, so no decimals."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


@dataclass
class _VehicleState:
    idle_since: datetime | None = None
    harsh_accel_since: datetime | None = None
    overspeed_since: datetime | None = None
    deviation_since: datetime | None = None
    co2_window: deque = field(
        default_factory=lambda: deque(maxlen=config.EMISSION_SPIKE_WINDOW_S))
    last_fired: dict = field(default_factory=dict)  # type -> datetime
    route_key: str | None = None
    route_votes: dict = field(default_factory=dict)
    trip_id: str | None = None


class AnomalyEngine:
    """Stateful per-vehicle rule evaluation. One instance serves the fleet."""

    def __init__(self) -> None:
        self._state: dict[str, _VehicleState] = {}
        self.fired: dict[str, int] = {}

    def state(self, vehicle_id: str) -> _VehicleState:
        return self._state.setdefault(vehicle_id, _VehicleState())

    def reset(self) -> None:
        self._state.clear()
        self.fired.clear()

    # ---------------------------------------------------------------- routes
    def _match_route(self, st: _VehicleState, sample: Sample) -> routes.Route | None:
        """Snap a trip to a known route by majority vote over its first fixes.

        Voting rather than trusting a single fix: one bad GPS sample at a
        junction would otherwise lock the whole trip to the wrong polyline and
        spray false deviation alerts for the next hour.
        """
        if st.trip_id != sample.trip_id:
            st.trip_id = sample.trip_id
            st.route_key = None
            st.route_votes = {}
            st.deviation_since = None

        if st.route_key is not None:
            return routes.get_route(st.route_key)

        best_key, best_d = None, float("inf")
        for key, route in routes.ROUTES.items():
            d = route.distance_to_m(sample.lat, sample.lon)
            if d < best_d:
                best_key, best_d = key, d
        if best_key is not None and best_d < 1500.0:
            st.route_votes[best_key] = st.route_votes.get(best_key, 0) + 1
            if sum(st.route_votes.values()) >= 12:
                st.route_key = max(st.route_votes, key=st.route_votes.get)
                return routes.get_route(st.route_key)
        return None

    # ----------------------------------------------------------------- rules
    def evaluate(self, sample: Sample, prediction: Prediction) -> list[dict]:
        st = self.state(sample.vehicle_id)
        route = self._match_route(st, sample)
        ts = sample.ts
        out: list[dict] = []

        def emit(kind: str, severity: str, message: str, recommendation: str,
                 value: float, threshold: float) -> None:
            # 60 s per vehicle per type. Without it a single 4-minute idle
            # produces 240 identical rows and the alert feed is unreadable.
            last = st.last_fired.get(kind)
            if last is not None and (ts - last).total_seconds() < config.ALERT_COOLDOWN_SECONDS:
                return
            st.last_fired[kind] = ts
            self.fired[kind] = self.fired.get(kind, 0) + 1
            out.append({
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "vehicle_id": sample.vehicle_id,
                "trip_id": sample.trip_id,
                "driver_id": sample.driver_id,
                "type": kind,
                "severity": severity,
                "message": message,
                "recommendation": recommendation,
                "value": round(float(value), 3),
                "threshold": round(float(threshold), 3),
                "acknowledged": 0,
            })

        # --- EXCESSIVE_IDLING ------------------------------------------------
        if sample.speed_kmh < config.IDLE_SPEED_KMH:
            st.idle_since = st.idle_since or ts
            idle_s = (ts - st.idle_since).total_seconds()
            if idle_s >= config.IDLE_ALERT_SECONDS:
                wasted_l = IDLE_LPH * idle_s / 3600.0
                emit("EXCESSIVE_IDLING", "HIGH",
                     f"Idling {_duration(idle_s)} with the engine running. "
                     f"About {wasted_l:.2f} L burned going nowhere.",
                     f"Switch the engine off. Idling costs roughly {IDLE_LPH:.1f} L/h "
                     f"and {IDLE_CO2_KG_PER_H:.1f} kg CO2/h on this class of vehicle.",
                     idle_s, config.IDLE_ALERT_SECONDS)
        else:
            st.idle_since = None

        # --- HARSH_ACCELERATION ---------------------------------------------
        if sample.accel_ms2 > config.HARSH_ACCEL_MS2:
            st.harsh_accel_since = st.harsh_accel_since or ts
            held = (ts - st.harsh_accel_since).total_seconds()
            if held >= config.HARSH_ACCEL_SUSTAIN_S:
                emit("HARSH_ACCELERATION", "MEDIUM",
                     f"Hard acceleration at {sample.accel_ms2:.1f} m/s2 held for "
                     f"{_duration(held)} at {sample.speed_kmh:.0f} km/h.",
                     "Feed the throttle in progressively. Hard launches raise fuel "
                     "use by roughly 15 % over the same journey.",
                     sample.accel_ms2, config.HARSH_ACCEL_MS2)
        else:
            st.harsh_accel_since = None

        # --- HARSH_BRAKING ---------------------------------------------------
        # No sustain window: by the time a -3 m/s2 event has lasted two seconds
        # the incident is already over.
        if sample.accel_ms2 < config.HARSH_BRAKE_MS2:
            emit("HARSH_BRAKING", "MEDIUM",
                 f"Heavy braking at {sample.accel_ms2:.1f} m/s2 from "
                 f"{sample.speed_kmh:.0f} km/h.",
                 "Increase following distance. Brake-and-accelerate cycles throw away "
                 "fuel already burned to build the speed.",
                 sample.accel_ms2, config.HARSH_BRAKE_MS2)

        # --- OVER_SPEEDING ---------------------------------------------------
        if route is not None:
            nearest = min(route.points[::10],
                          key=lambda p: routes.haversine_m(sample.lat, sample.lon, p.lat, p.lon))
            limit = nearest.speed_limit_kmh
            if sample.speed_kmh > limit + config.OVERSPEED_MARGIN_KMH:
                st.overspeed_since = st.overspeed_since or ts
                held = (ts - st.overspeed_since).total_seconds()
                if held >= config.OVERSPEED_SUSTAIN_S:
                    emit("OVER_SPEEDING", "MEDIUM",
                         f"{sample.speed_kmh:.0f} km/h in a {limit:.0f} zone, held for "
                         f"{_duration(held)} on {route.name}.",
                         "Ease back to the posted limit. Drag rises with the square of "
                         "speed, so the last 10 km/h is the most expensive.",
                         sample.speed_kmh, limit + config.OVERSPEED_MARGIN_KMH)
            else:
                st.overspeed_since = None

            # --- ROUTE_DEVIATION ---------------------------------------------
            off_m = route.distance_to_m(sample.lat, sample.lon)
            if off_m > config.ROUTE_DEVIATION_M:
                st.deviation_since = st.deviation_since or ts
                held = (ts - st.deviation_since).total_seconds()
                if held >= config.ROUTE_DEVIATION_SUSTAIN_S:
                    emit("ROUTE_DEVIATION", "LOW",
                         f"{off_m:.0f} m off {route.name} for {_duration(held)}.",
                         "Confirm the diversion with the driver. Unplanned detours "
                         "break the distance basis of the ISO 14083 report.",
                         off_m, config.ROUTE_DEVIATION_M)
            else:
                st.deviation_since = None

        # --- OVERLOAD --------------------------------------------------------
        if sample.gvw_kg > sample.rated_gvw_kg:
            over_kg = sample.gvw_kg - sample.rated_gvw_kg
            pct = 100.0 * over_kg / sample.rated_gvw_kg
            emit("OVERLOAD", "HIGH",
                 f"Gross weight {sample.gvw_kg:,.0f} kg against a {sample.rated_gvw_kg:,.0f} kg "
                 f"rating, {pct:.0f} % over by {over_kg:,.0f} kg.",
                 "Redistribute across the fleet. Beyond the rating this is a braking and "
                 "insurance exposure before it is an emissions one.",
                 sample.gvw_kg, sample.rated_gvw_kg)

        # --- EMISSION_SPIKE --------------------------------------------------
        # Compared against the vehicle's own recent behaviour, not a fleet
        # constant: a loaded 16 t rigid on a climb is not an anomaly, and the
        # same rate from a light van is.
        window = st.co2_window
        # Baseline excludes the last EMISSION_SPIKE_BASELINE_LAG_S samples, so a
        # fault that has already been running for half a minute is still
        # compared against how this vehicle behaved before it started.
        baseline = list(window)[:-config.EMISSION_SPIKE_BASELINE_LAG_S] if \
            len(window) > config.EMISSION_SPIKE_BASELINE_LAG_S else []
        if len(baseline) >= config.EMISSION_SPIKE_MIN_BASELINE_S:
            mean = sum(baseline) / len(baseline)
            var = sum((v - mean) ** 2 for v in baseline) / (len(baseline) - 1)
            sigma = var ** 0.5
            threshold = mean + config.EMISSION_SPIKE_SIGMA * sigma
            if sigma > 1e-6 and prediction.co2_gps > threshold and sample.speed_kmh > config.IDLE_SPEED_KMH:
                emit("EMISSION_SPIKE", "HIGH",
                     f"CO2 rate {prediction.co2_gps:.1f} g/s against this vehicle's own "
                     f"{mean:.1f} g/s baseline, {(prediction.co2_gps - mean) / sigma:.1f} sigma out."
                     + (f" {sample.dtc_count} diagnostic code(s) present."
                        if sample.dtc_count else ""),
                     "Book an engine diagnostic. A sustained step change in emission rate "
                     "at normal load usually means the driveline, not the driver.",
                     prediction.co2_gps, threshold)
        window.append(prediction.co2_gps)

        out.sort(key=lambda a: SEVERITY_ORDER[a["severity"]])
        return out

    def stats(self) -> dict:
        return {
            "fired_by_type": dict(self.fired),
            "total_fired": sum(self.fired.values()),
            "vehicles_tracked": len(self._state),
            "routes_matched": {v: s.route_key for v, s in self._state.items()},
        }
