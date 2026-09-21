"""Road-load model: the ground truth generator for the whole system.

This is the file that decides whether every number downstream is real or
nonsense. The dataset, the trained models, the dashboard KPIs and the ISO 14083
report all inherit whatever this produces, so units are annotated on every line
and the module is verified by scripts/gate_a_physics.py before anything else is
built on top of it.

The formulation is the standard road-load equation used by the US EPA MOVES
model and the European COPERT methodology:

    F_trac  = m*a + m*g*Crr*cos(theta) + m*g*sin(theta) + 0.5*rho*Cd*A*v^2   [N]
    P_wheel = F_trac * v                                                      [W]
    P_eng   = max(P_wheel, 0) / eta_driveline + P_accessory                   [W]
    fuel    = bsfc * (P_eng / 1000) / 3600                                    [g/s]

Fuel flow is converted to CO2 with a tank-to-wheel emission factor expressed
per litre, divided by fuel density to land back in per-gram-of-fuel terms.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class PhysicsResult:
    """One second of vehicle physics.

    `*_true` fields are the clean model output and are the ML training labels.
    `*_obs` fields carry sensor noise and are what goes into the telemetry
    packet. Confusing the two is how leakage gets into the dataset, so they are
    named differently and never assigned to each other.
    """

    f_trac_n: float
    p_wheel_w: float
    p_engine_w: float
    fuel_gps_true: float
    fuel_gps_obs: float
    co2_gps_true: float
    co2_gps_obs: float
    engine_load_pct_true: float
    engine_load_pct_obs: float
    rpm: int
    throttle_pct: float
    regime: str  # CRUISE | IDLE | COAST


def tractive_force_n(
    mass_kg: float,
    accel_ms2: float,
    speed_ms: float,
    grade_rad: float,
    cd: float,
    frontal_area_m2: float,
) -> float:
    """Total force the tyres must put down, in newtons.

    Sign is meaningful: a negative result means the road is doing the work
    (downhill or braking) and the engine is on overrun.
    """
    f_inertia = mass_kg * accel_ms2  # N
    f_rolling = mass_kg * config.GRAVITY * config.ROLLING_RESISTANCE * math.cos(grade_rad)  # N
    f_grade = mass_kg * config.GRAVITY * math.sin(grade_rad)  # N; negative downhill
    f_aero = 0.5 * config.AIR_DENSITY * cd * frontal_area_m2 * speed_ms**2  # N; always opposes
    return f_inertia + f_rolling + f_grade + f_aero


def _rated_power_w(spec: dict) -> float:
    """Crude but stable power rating used only to normalise engine load %.

    PID 0x04 reports load as a percentage of peak available torque at the
    current RPM. We do not model a torque curve, so load is expressed against a
    fixed rating of 28 W per kg of rated GVW -- roughly 210 kW for a 7.5 t LCV
    chassis, which is the right order for the class.
    """
    return spec["rated_gvw_kg"] * 28.0


def _rpm_for(speed_ms: float, engine_load_frac: float, engine_on: bool) -> int:
    """Plausible engine speed.

    Not a gearbox model. A real dongle reads PID 0x0C directly; here we need a
    value that correlates with road speed and load the way a real one would,
    because the ML model is given rpm as a feature and must not be handed a
    constant.
    """
    if not engine_on:
        return 0
    idle_rpm = 750.0
    if speed_ms <= config.CREEP_SPEED_MS:
        return int(idle_rpm + 40.0 * engine_load_frac)
    # Assume the driveline keeps the engine in a band: rpm climbs with speed but
    # gear changes reset it, which the modulo term approximates.
    in_gear = 1100.0 + (speed_ms * 3.6) % 22.0 * 52.0
    return int(min(2900.0, in_gear + 550.0 * engine_load_frac))


def step(
    *,
    vehicle_type: str,
    fuel_type: str,
    payload_kg: float,
    speed_ms: float,
    accel_ms2: float,
    grade_rad: float,
    engine_on: bool = True,
    driveline_efficiency: float | None = None,
    accessory_power_w: float | None = None,
    rng: random.Random | None = None,
    apply_noise: bool = True,
) -> PhysicsResult:
    """Evaluate one 1 Hz tick of the road-load model.

    `driveline_efficiency` is a parameter rather than a constant read so the
    EMISSION_SPIKE scenario can degrade it to simulate a mechanical fault
    without any other code path knowing that happened.
    """
    spec = config.VEHICLE_SPECS[vehicle_type]
    fuel = config.FUEL_SPECS[fuel_type]
    eta = config.DRIVELINE_EFFICIENCY if driveline_efficiency is None else driveline_efficiency
    p_acc = config.ACCESSORY_POWER_W if accessory_power_w is None else accessory_power_w
    rng = rng or random.Random()

    mass_kg = spec["kerb_kg"] + payload_kg  # kg

    f_trac = tractive_force_n(
        mass_kg, accel_ms2, speed_ms, grade_rad, spec["cd"], spec["frontal_area_m2"]
    )  # N
    p_wheel = f_trac * speed_ms  # W

    if not engine_on:
        p_engine = 0.0
        fuel_gps_true = 0.0
        regime = "IDLE"
    elif speed_ms <= config.CREEP_SPEED_MS:
        # Stationary with the engine running. Road load is irrelevant; the
        # engine is burning fuel to spin itself and the accessories.
        p_engine = p_acc
        fuel_gps_true = spec["idle_fuel_gps"]  # g/s
        regime = "IDLE"
    elif p_wheel <= 0.0:
        # Overrun. Injection is cut back hard but not to zero.
        p_engine = p_acc
        fuel_gps_true = config.COASTING_FUEL_FRACTION * spec["idle_fuel_gps"]  # g/s
        regime = "COAST"
    else:
        p_engine = p_wheel / eta + p_acc  # W
        # bsfc [g/kWh] * P [kW] = g/h; divide by 3600 for g/s.
        fuel_gps_true = spec["bsfc_g_per_kwh"] * (p_engine / 1000.0) / 3600.0  # g/s
        # The road-load path can dip under the idle burn at a walking pace on a
        # downgrade. A running engine never consumes less than it does at idle.
        fuel_gps_true = max(fuel_gps_true, config.COASTING_FUEL_FRACTION * spec["idle_fuel_gps"])
        regime = "CRUISE"

    # g CO2 per g fuel = (kg CO2 per litre) / (kg fuel per litre).
    co2_per_g_fuel = fuel["ef_ttw_kg_per_l"] / fuel["density_kg_per_l"]
    co2_gps_true = fuel_gps_true * co2_per_g_fuel  # g CO2/s

    rated_w = _rated_power_w(spec)
    load_frac_true = min(1.0, max(0.0, p_engine / rated_w))
    engine_load_true = load_frac_true * 100.0

    if apply_noise:
        fuel_gps_obs = max(0.0, fuel_gps_true * (1.0 + rng.gauss(0.0, config.NOISE["fuel_rate_pct"])))
        engine_load_obs = min(
            100.0,
            max(0.0, engine_load_true * (1.0 + rng.gauss(0.0, config.NOISE["load_pct"]))),
        )
    else:
        fuel_gps_obs = fuel_gps_true
        engine_load_obs = engine_load_true

    co2_gps_obs = fuel_gps_obs * co2_per_g_fuel

    # Throttle tracks load but saturates earlier and sits at a floor while the
    # engine idles, which is what PID 0x11 actually reports.
    throttle = 12.0 + 0.88 * engine_load_obs if regime != "COAST" else 8.0

    return PhysicsResult(
        f_trac_n=f_trac,
        p_wheel_w=p_wheel,
        p_engine_w=p_engine,
        fuel_gps_true=fuel_gps_true,
        fuel_gps_obs=fuel_gps_obs,
        co2_gps_true=co2_gps_true,
        co2_gps_obs=co2_gps_obs,
        engine_load_pct_true=engine_load_true,
        engine_load_pct_obs=engine_load_obs,
        rpm=_rpm_for(speed_ms, load_frac_true, engine_on),
        throttle_pct=min(100.0, throttle),
        regime=regime,
    )


# --------------------------------------------------------------------------
# Unit helpers. Every conversion in the codebase goes through one of these so
# there is exactly one place for a factor-of-1000 error to hide.
# --------------------------------------------------------------------------

def g_to_litres(grams: float, fuel_type: str) -> float:
    """Grams of fuel -> litres."""
    return grams / (config.FUEL_SPECS[fuel_type]["density_kg_per_l"] * 1000.0)


def litres_to_co2_kg(litres: float, fuel_type: str, basis: str = "ttw") -> float:
    """Litres of fuel -> kg CO2e, tank-to-wheel or well-to-wheel."""
    key = "ef_ttw_kg_per_l" if basis == "ttw" else "ef_wtw_kg_per_l"
    return litres * config.FUEL_SPECS[fuel_type][key]


def fuel_gps_to_lph(fuel_gps: float, fuel_type: str) -> float:
    """Fuel mass flow [g/s] -> volumetric flow [L/h], which is what PID 0x5E reports."""
    return g_to_litres(fuel_gps, fuel_type) * 3600.0


def l_per_100km(fuel_gps: float, speed_kmh: float, fuel_type: str) -> float:
    """Instantaneous fuel economy. Undefined at rest, so returns inf."""
    if speed_kmh <= 0.0:
        return float("inf")
    return fuel_gps_to_lph(fuel_gps, fuel_type) / speed_kmh * 100.0
