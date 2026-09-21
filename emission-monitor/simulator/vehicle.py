"""Per-vehicle state machine. One instance = one truck = one OBD dongle.

Drives a position along a route polyline at 1 Hz, decides a target speed from
the segment limit and the driver's aggressiveness, ramps toward it, and hands
the resulting kinematics to the road-load model. The output is a telemetry
packet in the schema of README "Telemetry schema" -- byte for byte what an
ESP32 + ELM327 would publish.

The clean physics values never enter the packet. They are exposed separately
through `last_truth` so ml/generate_dataset.py can label the training set, and
so nothing above the adapter boundary can accidentally read them.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import config
from simulator import physics, routes
from simulator.physics import EngineState, Perturbations
from simulator.scenarios import ScenarioBook

STATES = ("STOPPED", "IDLE", "ACCELERATING", "CRUISING", "DECELERATING")

# Traffic interruption rate, mean seconds between unplanned stops. The city loop
# is an order of magnitude denser, which is what makes its idle fraction and
# stop-go ratio a genuinely different regime for the model to learn.
_STOP_INTERVAL_S = {
    "CITY_LOOP": 95.0,
    "PORT_BANTWAL": 320.0,
    "MITE_MOODBIDRI": 420.0,
    "NH66_MNG_UDUPI": 700.0,
}

_FLEET = [
    # (device, plate, type, fuel, route, driver)   KA-19 is the Mangalore RTO series.
    ("OBD-MITE-001", "KA-19-AB-4521", "LCV", "DIESEL", "NH66_MNG_UDUPI", "DRV-11"),
    ("OBD-MITE-002", "KA-19-AC-7738", "RIGID_HGV", "DIESEL", "PORT_BANTWAL", "DRV-04"),
    ("OBD-MITE-003", "KA-19-AD-1190", "LCV", "DIESEL", "CITY_LOOP", "DRV-27"),
    ("OBD-MITE-004", "KA-19-AB-8802", "LCV", "PETROL", "MITE_MOODBIDRI", "DRV-11"),
    ("OBD-MITE-005", "KA-19-AE-3365", "RIGID_HGV", "DIESEL", "NH66_MNG_UDUPI", "DRV-19"),
    ("OBD-MITE-006", "KA-19-AF-5014", "LCV", "DIESEL", "CITY_LOOP", "DRV-08"),
    ("OBD-MITE-007", "KA-19-AG-2276", "RIGID_HGV", "DIESEL", "MITE_MOODBIDRI", "DRV-04"),
    ("OBD-MITE-008", "KA-19-AH-9931", "LCV", "DIESEL", "PORT_BANTWAL", "DRV-27"),
]


@dataclass
class TruthSample:
    """Clean physics, held out of the packet. The ML label lives here."""

    fuel_rate_gps_true: float
    co2_gps_true: float
    engine_load_pct_true: float
    grade_rad: float
    regime: str
    p_engine_w: float


@dataclass
class Vehicle:
    device_id: str
    vehicle_id: str
    vehicle_type: str
    fuel_type: str
    route_key: str
    driver_id: str
    seed: int

    # kinematic state
    cum_m: float = 0.0
    speed_ms: float = 0.0
    accel_ms2: float = 0.0
    direction: int = 1  # +1 along the polyline, -1 back
    state: str = "IDLE"

    payload_kg: float = 0.0
    driver_factor: float = 1.0
    engine_on: bool = True
    coolant_c: float = config.AMBIENT_TEMP_C
    fuel_level_l: float = 0.0
    dtc_count: int = 0

    trip_seq: int = 1
    trip_started: datetime | None = None
    trip_distance_m: float = 0.0
    trip_fuel_g: float = 0.0

    stop_remaining_s: float = 0.0
    next_stop_in_s: float = 0.0
    cruise_bias: float = 1.0

    # Hidden state. None of this is observable from the OBD bus, and none of it
    # reaches the telemetry packet -- it is the part of real fuel consumption a
    # dongle cannot see, and it is what the models have to estimate around.
    crr: float = config.ROLLING_RESISTANCE
    bsfc_unit: float = 1.0
    bsfc_drift: float = 0.0
    lambda_dev: float = 0.0
    wind_ms: float = 0.0
    pedal_gain: float = 1.0
    accessory_on: bool = False
    accessory_switch_in_s: float = 0.0
    engine_state: EngineState = field(default_factory=EngineState)

    # Scenarios that started actually biting on this tick. run_simulator sends
    # these back to the backend so alert latency is measured from the condition
    # going live, not from the button press: an armed HARSH_BRAKE that waits
    # eight seconds for the vehicle to get up to speed has not been slow, it has
    # been waiting, and conflating the two would overstate pipeline latency.
    newly_forced: list = field(default_factory=list)

    rng: random.Random = field(default_factory=random.Random)
    last_truth: TruthSample | None = None
    sim_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ---------------------------------------------------------------- setup
    @classmethod
    def build(cls, idx: int, seed: int, start_time: datetime | None = None) -> "Vehicle":
        device, plate, vtype, fuel, route_key, driver = _FLEET[idx % len(_FLEET)]
        rng = random.Random(seed * 1000 + idx)
        spec = config.VEHICLE_SPECS[vtype]
        v = cls(
            device_id=device,
            vehicle_id=plate,
            vehicle_type=vtype,
            fuel_type=fuel,
            route_key=route_key,
            driver_id=driver,
            seed=seed * 1000 + idx,
            rng=rng,
        )
        route = routes.get_route(route_key)
        # Stagger the fleet along its routes so the map is not six markers
        # stacked on one pixel at t=0.
        v.cum_m = rng.uniform(0.0, route.length_m * 0.85)
        v.direction = rng.choice((1, -1))
        v.driver_factor = round(rng.uniform(0.6, 1.4), 3)
        v.cruise_bias = rng.uniform(0.96, 1.04)
        v.payload_kg = v._draw_payload()
        v.fuel_level_l = spec["tank_l"] * rng.uniform(0.45, 0.95)
        v.coolant_c = config.AMBIENT_TEMP_C + rng.uniform(0.0, 45.0)
        v.next_stop_in_s = rng.expovariate(1.0 / _STOP_INTERVAL_S[route_key])
        v.sim_time = start_time or datetime.now(timezone.utc)
        v.trip_started = v.sim_time
        v.trip_seq = rng.randint(1, 4)
        v._init_hidden_state()
        return v

    @classmethod
    def randomized(cls, idx: int, seed: int, start_time: datetime | None = None) -> "Vehicle":
        """A vehicle with freely sampled configuration, for dataset generation.

        The demo fleet is eight fixed trucks, which is the right thing for a
        live map and the wrong thing for a training set: the model would see
        eight payloads and four driver factors. This samples route, class, fuel,
        payload and driver independently so the feature space is actually
        covered.
        """
        rng = random.Random(seed * 7919 + idx)
        vtype = rng.choice(list(config.VEHICLE_SPECS))
        fuel = "PETROL" if rng.random() < 0.15 else "DIESEL"  # LCV petrol is the minority case
        route_key = rng.choice(routes.ROUTE_KEYS)
        spec = config.VEHICLE_SPECS[vtype]
        v = cls(
            device_id=f"OBD-GEN-{idx:04d}",
            vehicle_id=f"GEN-{idx:04d}",
            vehicle_type=vtype,
            fuel_type=fuel,
            route_key=route_key,
            driver_id=f"DRV-{rng.randint(1, 40):02d}",
            seed=seed * 7919 + idx,
            rng=rng,
        )
        route = routes.get_route(route_key)
        v.cum_m = rng.uniform(0.0, route.length_m)
        v.direction = rng.choice((1, -1))
        v.driver_factor = round(rng.uniform(0.6, 1.4), 3)
        v.cruise_bias = rng.uniform(0.94, 1.06)
        # Uniform payload here rather than the beta the demo fleet uses: the
        # model must see empty and overloaded, not just the commercially typical
        # band, or it extrapolates badly the moment an OVERLOAD is injected.
        v.payload_kg = round((spec["rated_gvw_kg"] - spec["kerb_kg"]) * rng.uniform(0.0, 1.15), -1)
        v.fuel_level_l = spec["tank_l"] * rng.uniform(0.2, 1.0)
        v.coolant_c = config.AMBIENT_TEMP_C + rng.uniform(0.0, 58.0)
        v.next_stop_in_s = rng.expovariate(1.0 / _STOP_INTERVAL_S[route_key])
        v.sim_time = start_time or datetime.now(timezone.utc)
        v.trip_started = v.sim_time
        v.trip_seq = rng.randint(1, 20)
        v._init_hidden_state()
        return v

    def _init_hidden_state(self) -> None:
        """Draw the per-vehicle unobservables. Fixed for the vehicle's life."""
        lo, hi = config.CRR_RANGE
        self.crr = self.rng.uniform(lo, hi)
        self.bsfc_unit = math.exp(self.rng.gauss(0.0, config.BSFC_UNIT_VARIATION))
        self.pedal_gain = self.rng.uniform(0.82, 1.24)
        self.wind_ms = self.rng.gauss(0.0, config.WIND_SIGMA_MS)
        self.accessory_on = self.rng.random() < 0.45
        self.accessory_switch_in_s = self.rng.uniform(*config.ACCESSORY_CYCLE_S)

    def _advance_hidden_state(self, dt: float) -> Perturbations:
        """Evolve the unobservables one tick and package them for the physics.

        Wind and the two efficiency drifts are Ornstein-Uhlenbeck: they wander
        but stay bounded, which is how these quantities actually behave. A plain
        random walk would let the headwind reach 40 m/s by the afternoon.
        """
        def ou(value: float, sigma: float, tau: float) -> float:
            decay = dt / tau
            return value * (1.0 - decay) + sigma * math.sqrt(2.0 * decay) * self.rng.gauss(0.0, 1.0)

        self.wind_ms = ou(self.wind_ms, config.WIND_SIGMA_MS, config.WIND_TAU_S)
        self.bsfc_drift = ou(self.bsfc_drift, config.BSFC_DRIFT_SIGMA, config.BSFC_DRIFT_TAU_S)
        self.lambda_dev = ou(self.lambda_dev, config.LAMBDA_DEV_SIGMA, config.LAMBDA_DEV_TAU_S)

        # The AC and air-brake compressors cycle on their own schedule.
        self.accessory_switch_in_s -= dt
        if self.accessory_switch_in_s <= 0.0:
            self.accessory_on = not self.accessory_on
            self.accessory_switch_in_s = self.rng.uniform(*config.ACCESSORY_CYCLE_S)
        p_acc = config.ACCESSORY_POWER_MAX_W if self.accessory_on else config.ACCESSORY_POWER_MIN_W

        return Perturbations(
            wind_ms=self.wind_ms,
            crr=self.crr,
            accessory_power_w=p_acc,
            bsfc_scale=self.bsfc_unit * (1.0 + self.bsfc_drift),
            lambda_scale=1.0 + self.lambda_dev,
            pedal_gain=self.pedal_gain,
        )

    def _draw_payload(self) -> float:
        """Payload as a fraction of usable capacity. Skewed toward well-loaded
        but not full, which is what a distribution fleet actually runs at."""
        spec = config.VEHICLE_SPECS[self.vehicle_type]
        capacity = spec["rated_gvw_kg"] - spec["kerb_kg"]
        return round(capacity * min(1.0, max(0.05, self.rng.betavariate(3.2, 2.0))), -1)

    # ------------------------------------------------------------- geometry
    @property
    def route(self) -> routes.Route:
        return routes.get_route(self.route_key)

    @property
    def gvw_kg(self) -> float:
        return config.VEHICLE_SPECS[self.vehicle_type]["kerb_kg"] + self.payload_kg

    @property
    def rated_gvw_kg(self) -> float:
        return config.VEHICLE_SPECS[self.vehicle_type]["rated_gvw_kg"]

    @property
    def trip_id(self) -> str:
        stamp = (self.trip_started or self.sim_time).strftime("%Y%m%d")
        return f"TRIP-{stamp}-{self.device_id[-3:]}-{self.trip_seq:02d}"

    def _segment_limit_kmh(self) -> float:
        return self.route.point_at(self.cum_m).speed_limit_kmh

    def _heading(self) -> float:
        r = self.route
        p = r.point_at(self.cum_m)
        ahead = r.point_at(min(r.length_m, max(0.0, self.cum_m + self.direction * 60.0)))
        if abs(ahead.cum_m - p.cum_m) < 1.0:
            return 0.0
        return routes.bearing_deg(p.lat, p.lon, ahead.lat, ahead.lon)

    # ----------------------------------------------------------------- trip
    def _start_new_trip(self) -> None:
        self.trip_seq += 1
        self.trip_started = self.sim_time
        self.trip_distance_m = 0.0
        self.trip_fuel_g = 0.0

    def _endpoint_stop(self) -> None:
        """Loading or unloading at a route end: engine on, payload changes."""
        self.stop_remaining_s = self.rng.uniform(120.0, 300.0)
        self.payload_kg = self._draw_payload()
        self.state = "IDLE"

    # ----------------------------------------------------------------- tick
    def step(self, dt: float, book: ScenarioBook | None = None) -> dict:
        """Advance one tick and return a telemetry packet."""
        book = book or ScenarioBook()
        book.tick(self.vehicle_id, dt)
        active = book.active_for(self.vehicle_id)
        self.newly_forced = []

        def announce(name: str) -> None:
            sc = active.get(name)
            if sc is not None and not sc.announced:
                sc.announced = True
                self.newly_forced.append(name)

        route = self.route
        spec = config.VEHICLE_SPECS[self.vehicle_type]

        # -- scenario knobs ------------------------------------------------
        forced_idle = "IDLE_EVENT" in active
        if forced_idle and self.speed_ms * 3.6 < config.IDLE_SPEED_KMH:
            # Not at injection: the rule counts continuous seconds below the
            # idle speed, and the vehicle has to decelerate first.
            announce("IDLE_EVENT")

        forced_accel = None
        for name in ("HARSH_ACCEL", "HARSH_BRAKE"):
            sc = active.get(name)
            if sc is None:
                continue
            if sc.may_force(self.speed_ms):
                forced_accel = float(sc.params["accel_ms2"])
                sc.forced_s += dt
                announce(name)
            break

        payload = self.payload_kg
        if "OVERLOAD" in active:
            announce("OVERLOAD")
            factor = float(active["OVERLOAD"].params["factor"])
            # Push GVW to `factor` x rated, expressed as the payload needed.
            payload = self.rated_gvw_kg * factor - spec["kerb_kg"]

        pert = self._advance_hidden_state(dt)
        eta = config.DRIVELINE_EFFICIENCY
        if "EMISSION_SPIKE" in active:
            announce("EMISSION_SPIKE")
            eta = float(active["EMISSION_SPIKE"].params["driveline_efficiency"])
            self.dtc_count = 1
        elif self.dtc_count and "EMISSION_SPIKE" not in active:
            self.dtc_count = 0
        pert.driveline_efficiency = eta

        # -- decide the target speed --------------------------------------
        if forced_idle:
            self.stop_remaining_s = max(self.stop_remaining_s, dt)

        if self.stop_remaining_s > 0.0:
            self.stop_remaining_s -= dt
            target_ms = 0.0
        else:
            self.next_stop_in_s -= dt
            if self.next_stop_in_s <= 0.0:
                # Poisson arrivals: exponential gaps between traffic stops.
                mean = _STOP_INTERVAL_S[self.route_key]
                self.next_stop_in_s = self.rng.expovariate(1.0 / mean)
                lo, hi = (12.0, 70.0) if self.route_key == "CITY_LOOP" else (18.0, 55.0)
                self.stop_remaining_s = self.rng.uniform(lo, hi)
                target_ms = 0.0
            else:
                limit_kmh = self._segment_limit_kmh()
                target_ms = limit_kmh * self.driver_factor * self.cruise_bias / 3.6

        # -- ramp toward it ------------------------------------------------
        if forced_accel is not None:
            self.accel_ms2 = forced_accel
        else:
            gap = target_ms - self.speed_ms
            # Two ceilings, and the engine usually wins above 40 km/h: a comfort
            # limit the driver imposes, and the power the engine can actually
            # put down at this speed, mass and grade.
            a_comfort = 1.10 * self.driver_factor
            a_power = physics.max_accel_ms2(
                vehicle_type=self.vehicle_type,
                mass_kg=spec["kerb_kg"] + payload,
                speed_ms=self.speed_ms,
                grade_rad=route.point_at(self.cum_m).grade_rad * self.direction,
                driveline_efficiency=eta,
            )
            a_max = min(a_comfort, a_power)
            d_max = 1.70 * self.driver_factor
            if gap > 0.05:
                self.accel_ms2 = min(a_max, gap / max(dt, 1e-6), gap * 0.55)
            elif gap < -0.05:
                self.accel_ms2 = max(-d_max, gap / max(dt, 1e-6), gap * 0.75)
            else:
                self.accel_ms2 = 0.0

        prev_speed = self.speed_ms
        self.speed_ms = max(0.0, self.speed_ms + self.accel_ms2 * dt)
        # Recompute from the clamp so accel and speed can never disagree --
        # a vehicle at 0 m/s must not report -4 m/s^2 for the whole stop.
        self.accel_ms2 = (self.speed_ms - prev_speed) / dt

        # -- advance along the polyline ------------------------------------
        travelled = self.speed_ms * dt
        self.cum_m += self.direction * travelled
        self.trip_distance_m += travelled

        if self.cum_m >= route.length_m:
            if self.route_key == "CITY_LOOP":
                self.cum_m -= route.length_m  # the loop closes on itself
                self._endpoint_stop()
                self._start_new_trip()
            else:
                self.cum_m = route.length_m
                self.direction = -1
                self._endpoint_stop()
                self._start_new_trip()
        elif self.cum_m <= 0.0:
            self.cum_m = 0.0
            self.direction = 1
            self._endpoint_stop()
            self._start_new_trip()

        # -- state machine label -------------------------------------------
        if not self.engine_on:
            self.state = "STOPPED"
        elif self.speed_ms <= config.CREEP_SPEED_MS:
            self.state = "IDLE"
        elif self.accel_ms2 > 0.15:
            self.state = "ACCELERATING"
        elif self.accel_ms2 < -0.15:
            self.state = "DECELERATING"
        else:
            self.state = "CRUISING"

        # -- physics -------------------------------------------------------
        point = route.point_at(self.cum_m)
        # Going the other way turns every climb into a descent.
        grade_rad = point.grade_rad * self.direction
        result = physics.step(
            vehicle_type=self.vehicle_type,
            fuel_type=self.fuel_type,
            payload_kg=payload,
            speed_ms=self.speed_ms,
            accel_ms2=self.accel_ms2,
            grade_rad=grade_rad,
            engine_on=self.engine_on,
            pert=pert,
            engine_state=self.engine_state,
            dt=dt,
            rng=self.rng,
            apply_noise=True,
        )
        self.last_truth = TruthSample(
            fuel_rate_gps_true=result.fuel_gps_true,
            co2_gps_true=result.co2_gps_true,
            engine_load_pct_true=result.engine_load_pct_true,
            grade_rad=grade_rad,
            regime=result.regime,
            p_engine_w=result.p_engine_w,
        )

        # -- consumables ---------------------------------------------------
        burned_l = physics.g_to_litres(result.fuel_gps_true * dt, self.fuel_type)
        self.trip_fuel_g += result.fuel_gps_true * dt
        self.fuel_level_l -= burned_l
        if self.fuel_level_l <= spec["tank_l"] * 0.08:
            self.fuel_level_l = spec["tank_l"] * self.rng.uniform(0.85, 1.0)  # refuelled
        # First-order warm-up toward operating temperature.
        drive = config.COOLANT_OPERATING_C if self.engine_on else config.AMBIENT_TEMP_C
        self.coolant_c += (drive - self.coolant_c) * (dt / config.COOLANT_WARMUP_TAU_S)

        # -- GPS -----------------------------------------------------------
        lat, lon = point.lat, point.lon
        if "ROUTE_DEVIATION" in active:
            announce("ROUTE_DEVIATION")
            offset = float(active["ROUTE_DEVIATION"].params["offset_m"])
            lat, lon = routes.offset_point(lat, lon, (self._heading() + 90.0) % 360.0, offset)
        # Sensor noise: a metre-scale wander, converted from metres to degrees.
        jitter_m = config.NOISE["gps_m"]
        lat += self.rng.gauss(0.0, jitter_m) / 111_320.0
        lon += self.rng.gauss(0.0, jitter_m) / (111_320.0 * math.cos(math.radians(lat)))

        if self.rng.random() < config.GPS_DEGRADED_RATE:
            hdop, sats = round(self.rng.uniform(5.5, 12.0), 1), self.rng.randint(0, 3)
        else:
            hdop, sats = round(self.rng.uniform(0.6, 1.4), 1), self.rng.randint(8, 12)

        speed_kmh = self.speed_ms * 3.6
        speed_obs = max(0.0, speed_kmh * (1.0 + self.rng.gauss(0.0, config.NOISE["speed_pct"])))

        self.sim_time += timedelta(seconds=dt)

        return {
            "schema_version": config.SCHEMA_VERSION,
            "device_id": self.device_id,
            "vehicle_id": self.vehicle_id,
            "trip_id": self.trip_id,
            "ts": self.sim_time.strftime("%Y-%m-%dT%H:%M:%S.") + f"{self.sim_time.microsecond // 1000:03d}Z",
            "gps": {
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "alt_m": round(point.alt_m, 1),
                "hdop": hdop,
                "sats": sats,
            },
            "obd": {
                # Every field below is quantised the way SAE J1979 quantises it.
                # The bus cannot carry more precision than this, so neither can
                # anything downstream.
                "speed_kmh": physics.quantize_speed_kmh(speed_obs),        # PID 0x0D
                "rpm": physics.quantize_rpm(result.rpm),                   # PID 0x0C
                "engine_load_pct": result.engine_load_pct_obs,             # PID 0x04
                "throttle_pct": result.throttle_pct,                       # PID 0x11
                "coolant_temp_c": round(self.coolant_c),                   # PID 0x05
                "maf_gps": physics.quantize_maf_gps(result.maf_gps),       # PID 0x10
                "fuel_level_pct": physics.quantize_pid_percent(
                    100.0 * self.fuel_level_l / spec["tank_l"]),            # PID 0x2F
                "fuel_rate_lph": physics.quantize_fuel_rate_lph(
                    physics.fuel_gps_to_lph(result.fuel_gps_obs, self.fuel_type)),  # PID 0x5E
                "engine_on": self.engine_on,
                "dtc_count": self.dtc_count,                              # PID 0x01
            },
            "derived": {
                "accel_ms2": round(self.accel_ms2, 2),
                "heading_deg": round(self._heading(), 1),
            },
            "cargo": {
                "payload_kg": round(payload, 1),
                "gvw_kg": round(spec["kerb_kg"] + payload, 1),
                "rated_gvw_kg": spec["rated_gvw_kg"],
            },
            "meta": {
                "vehicle_type": self.vehicle_type,
                "fuel_type": self.fuel_type,
                "driver_id": self.driver_id,
                "source": "SIMULATOR",
            },
        }


def build_fleet(count: int, seed: int, start_time: datetime | None = None) -> list[Vehicle]:
    return [Vehicle.build(i, seed, start_time) for i in range(count)]
