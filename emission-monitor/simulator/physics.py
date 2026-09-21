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
    P_out   = max(P_wheel, 0) / eta_driveline                                 [W]
    fuel    = b + a * P_out                                                   [g/s]

The last line is a Willans line: fuel flow affine in net engine output. A
single bsfc figure is the *best* point on an engine map and overstates
efficiency badly at light load -- with a flat 215 g/kWh a lightly laden van on
a gentle descent comes out "cruising" at 0.29 g/s against a 0.45 g/s idle burn,
which is not possible. The Willans form has the idle burn as its intercept, so
an engine making positive power can never consume less than one making none,
and it degrades efficiency at part load the way a real engine does. The slope
`a` is calibrated so that at rated output the overall bsfc equals the published
figure in config.VEHICLE_SPECS, which is what that constant is for.

Fuel flow is converted to CO2 with a tank-to-wheel emission factor expressed
per litre, divided by fuel density to land back in per-gram-of-fuel terms.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import config


@dataclass
class Perturbations:
    """Everything that moves real fuel consumption and never reaches the bus.

    Held per vehicle and evolved by the vehicle state machine. The model is
    never shown any of it -- that is what stops the estimation problem from
    collapsing into arithmetic.
    """

    wind_ms: float = 0.0                   # headwind component, +ve opposes travel
    crr: float | None = None               # tyre pressure, tread, surface
    accessory_power_w: float | None = None # AC and air compressor duty
    bsfc_scale: float = 1.0                # unit-to-unit and wear
    lambda_scale: float = 1.0              # combustion/air-path deviation
    pedal_gain: float = 1.0                # how hard this driver leans on the pedal
    driveline_efficiency: float | None = None


@dataclass
class EngineState:
    """Carried between ticks. The air path has inertia; the fuel path does not."""

    maf_gps: float = 0.0        # lagged air mass flow -- the turbo cannot spool instantly
    pedal_pct: float = 0.0
    pedal_noise: float = 0.0    # driver modulation, wanders rather than hisses


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
    maf_gps: float
    lambda_excess_air: float
    regime: str  # CRUISE | IDLE | COAST


def tractive_force_n(
    mass_kg: float,
    accel_ms2: float,
    speed_ms: float,
    grade_rad: float,
    cd: float,
    frontal_area_m2: float,
    crr: float | None = None,
    wind_ms: float = 0.0,
) -> float:
    """Total force the tyres must put down, in newtons.

    Sign is meaningful: a negative result means the road is doing the work
    (downhill or braking) and the engine is on overrun.

    Aerodynamic drag is computed against airspeed, not ground speed. A 3 m/s
    headwind at 80 km/h is worth roughly 15 % more drag force, which is a real
    and completely unobservable swing in fuel consumption.
    """
    crr = config.ROLLING_RESISTANCE if crr is None else crr
    v_air = speed_ms + wind_ms  # +ve wind_ms is a headwind
    f_inertia = mass_kg * accel_ms2  # N
    f_rolling = mass_kg * config.GRAVITY * crr * math.cos(grade_rad)  # N
    f_grade = mass_kg * config.GRAVITY * math.sin(grade_rad)  # N; negative downhill
    # v*|v| rather than v**2 so a tailwind stronger than the vehicle pushes it.
    f_aero = 0.5 * config.AIR_DENSITY * cd * frontal_area_m2 * v_air * abs(v_air)  # N
    return f_inertia + f_rolling + f_grade + f_aero


def willans_slope_g_per_ws(spec: dict) -> float:
    """Marginal fuel per joule of engine output, g/(W.s).

    Solved from the two anchors we have: fuel flow is `idle_fuel_gps` at zero
    output, and overall bsfc equals `bsfc_g_per_kwh` at rated output.

        bsfc = (b + a*P_r) * 3.6e6 / P_r   ->   a = bsfc/3.6e6 - b/P_r
    """
    p_rated_w = spec["rated_power_kw"] * 1000.0
    # At rated speed the friction intercept is already elevated, so calibrate
    # against that value rather than the idle one or the slope comes out high.
    b_rated = spec["idle_fuel_gps"] * (1.0 + config.FRICTION_RPM_GAIN)
    return spec["bsfc_g_per_kwh"] / 3.6e6 - b_rated / p_rated_w


IDLE_RPM = 750.0
RATED_RPM = 2500.0


def friction_intercept_gps(spec: dict, rpm: float) -> float:
    """Willans intercept at a given engine speed, g/s.

    Pumping and rubbing losses scale with engine speed; a diesel's zero-output
    fuel demand roughly doubles between idle and rated speed. Holding this
    constant is what made the old model invertible from a single signal.
    """
    frac = (max(rpm, IDLE_RPM) - IDLE_RPM) / (RATED_RPM - IDLE_RPM)
    return spec["idle_fuel_gps"] * (1.0 + config.FRICTION_RPM_GAIN * frac)


def max_airflow_gps(spec: dict, rpm: float) -> float:
    """Peak air mass flow at this engine speed, g/s -- the denominator of PID 0x04.

    Four-stroke: one intake charge every two revolutions. Peak means at full
    boost, which is why a naturally-aspirated idle reads around 1/MAX_BOOST_RATIO
    rather than near zero.
    """
    litres_per_min = (rpm / 2.0) * spec["displacement_l"] * config.VOLUMETRIC_EFFICIENCY
    litres_per_min *= config.MAX_BOOST_RATIO
    return litres_per_min / 60.0 * config.INTAKE_AIR_DENSITY_G_PER_L


def max_torque_nm(spec: dict, rpm: float) -> float:
    """Peak available torque at this engine speed, N.m.

    A diesel makes peak torque well below rated speed and falls away either
    side. This is why the same road power needs a different pedal position in a
    different gear, and it is the reason PID 0x11 is not a power readout.
    """
    omega_rated = 2.0 * math.pi * RATED_RPM / 60.0
    t_rated = spec["rated_power_kw"] * 1000.0 / omega_rated
    t_peak = t_rated / (1.0 - config.TORQUE_CURVE_FALLOFF
                        * ((RATED_RPM - config.PEAK_TORQUE_RPM) / 1000.0) ** 2)
    shape = 1.0 - config.TORQUE_CURVE_FALLOFF * ((rpm - config.PEAK_TORQUE_RPM) / 1000.0) ** 2
    return t_peak * max(0.35, shape)


def excess_air_ratio(torque_frac: float) -> float:
    """Lambda against fuelling demand.

    A diesel is unthrottled: airflow is set by engine speed and boost, while
    fuel is metered independently, so lambda falls from very lean at light load
    toward the smoke limit under full fuelling. This is the term that stops air
    mass from determining fuel mass.
    """
    t = max(0.0, min(1.0, torque_frac))
    return config.LAMBDA_AT_IDLE + (config.LAMBDA_AT_FULL_LOAD - config.LAMBDA_AT_IDLE) * t


def _rated_power_w(spec: dict) -> float:
    """Rated crank power, used both to cap acceleration and to normalise load %.

    PID 0x04 reports load as a percentage of peak available torque at the
    current RPM. We do not model a torque curve, so load is expressed against
    the flat power rating, which is the right order and is at least a quantity
    the vehicle actually has.
    """
    return spec["rated_power_kw"] * 1000.0


def max_accel_ms2(
    *,
    vehicle_type: str,
    mass_kg: float,
    speed_ms: float,
    grade_rad: float,
    driveline_efficiency: float | None = None,
) -> float:
    """Largest acceleration the engine can actually deliver, in m/s^2.

    Without this the state machine will happily ask a 2.5 L van for 1.3 m/s^2 at
    79 km/h, which works out at 276 kW and produces a 73 L/h fuel rate that
    poisons the training set. Power, not the driver, is what limits a loaded
    truck on the move.

    May return a negative value: a heavy vehicle on a steep grade at speed
    cannot hold its speed, and slowing down is the correct answer.
    """
    spec = config.VEHICLE_SPECS[vehicle_type]
    eta = config.DRIVELINE_EFFICIENCY if driveline_efficiency is None else driveline_efficiency
    p_wheel_avail = max(0.0, _rated_power_w(spec) - config.ACCESSORY_POWER_W) * eta  # W

    if speed_ms <= config.CREEP_SPEED_MS:
        # F = P/v is unbounded at rest; a standing start is traction- and
        # clutch-limited, not power-limited.
        return 1.6

    f_avail = p_wheel_avail / speed_ms  # N
    f_resist = (
        mass_kg * config.GRAVITY * config.ROLLING_RESISTANCE * math.cos(grade_rad)
        + mass_kg * config.GRAVITY * math.sin(grade_rad)
        + 0.5 * config.AIR_DENSITY * spec["cd"] * spec["frontal_area_m2"] * speed_ms**2
    )  # N
    return (f_avail - f_resist) / mass_kg


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
    pert: Perturbations | None = None,
    engine_state: EngineState | None = None,
    dt: float = 1.0,
    rng: random.Random | None = None,
    apply_noise: bool = True,
) -> PhysicsResult:
    """Evaluate one tick of the road-load and engine model.

    `pert` carries the unobservable variation; `engine_state` carries the air
    path's inertia between ticks and is mutated in place.
    """
    spec = config.VEHICLE_SPECS[vehicle_type]
    fuel = config.FUEL_SPECS[fuel_type]
    pert = pert or Perturbations()
    engine_state = engine_state if engine_state is not None else EngineState()
    eta = config.DRIVELINE_EFFICIENCY if pert.driveline_efficiency is None else pert.driveline_efficiency
    p_acc = config.ACCESSORY_POWER_W if pert.accessory_power_w is None else pert.accessory_power_w
    rng = rng or random.Random()

    mass_kg = spec["kerb_kg"] + payload_kg  # kg

    f_trac = tractive_force_n(
        mass_kg, accel_ms2, speed_ms, grade_rad, spec["cd"], spec["frontal_area_m2"],
        crr=pert.crr, wind_ms=pert.wind_ms,
    )  # N
    p_wheel = f_trac * speed_ms  # W

    # Engine speed first: it depends on road speed and on the power being asked
    # for, neither of which depends on fuel, so there is no circularity.
    p_out_estimate = max(p_wheel, 0.0) / eta
    rated_w = _rated_power_w(spec)
    rpm = _rpm_for(speed_ms, min(1.0, (p_out_estimate + p_acc) / rated_w), engine_on)

    if not engine_on:
        p_out = p_engine = 0.0
        fuel_gps_true = 0.0
        regime = "IDLE"
    elif speed_ms <= config.CREEP_SPEED_MS:
        p_out = 0.0
        p_engine = p_acc
        fuel_gps_true = friction_intercept_gps(spec, rpm)
        regime = "IDLE"
    elif p_wheel <= 0.0:
        # Overrun: the ECU cuts injection back hard but never to zero.
        p_out = 0.0
        p_engine = p_acc
        fuel_gps_true = config.COASTING_FUEL_FRACTION * friction_intercept_gps(spec, rpm)
        regime = "COAST"
    else:
        p_out = p_wheel / eta  # W of net output at the crank
        p_engine = p_out + p_acc  # W
        fuel_gps_true = friction_intercept_gps(spec, rpm) + willans_slope_g_per_ws(spec) * p_out
        regime = "CRUISE"

    # Engine wear and unit-to-unit variation. Invisible on the bus, which is
    # exactly why it belongs here and not in the packet.
    fuel_gps_true *= pert.bsfc_scale

    co2_per_g_fuel = fuel["ef_ttw_kg_per_l"] / fuel["density_kg_per_l"]
    co2_gps_true = fuel_gps_true * co2_per_g_fuel  # g CO2/s

    # ---------------------------------------------------------------- air path
    torque_frac = min(1.0, p_engine / rated_w) if engine_on else 0.0
    lam = max(1.15, excess_air_ratio(torque_frac) * pert.lambda_scale)
    afr = config.AFR_STOICHIOMETRIC[fuel_type]
    maf_demand = fuel_gps_true * afr * lam if engine_on else 0.0  # g/s

    # Turbo spool is a first-order lag, so airflow trails fuelling through every
    # transient. This is why PID 0x04 and the true fuel rate disagree most in
    # exactly the stop-go driving the fleet spends its time in.
    alpha = 1.0 - math.exp(-dt / config.TURBO_LAG_TAU_S)
    engine_state.maf_gps += (maf_demand - engine_state.maf_gps) * alpha
    maf = max(0.0, engine_state.maf_gps)

    maf_ceiling = max_airflow_gps(spec, max(rpm, IDLE_RPM))
    engine_load_true = min(100.0, 100.0 * maf / maf_ceiling) if maf_ceiling > 0 else 0.0

    # PID 0x11 is a position sensor on a pedal under a human foot.
    #
    # The pedal commands a fraction of the torque AVAILABLE AT THIS ENGINE
    # SPEED, not a fraction of rated power, so the same road power reads
    # differently in different gears. On top of that the foot modulates
    # constantly and ignores changes below a dead band.
    #
    # An earlier version set pedal position straight from engine power. That
    # handed the model a clean readout of the quantity it was supposed to be
    # estimating, and throttle_pct took 73 % of the feature importance.
    if engine_on and rpm > 0:
        omega = 2.0 * math.pi * rpm / 60.0
        torque_demand = p_engine / omega if omega > 0 else 0.0
        pedal_frac = torque_demand / max(1.0, max_torque_nm(spec, rpm))
    else:
        pedal_frac = 0.0
    pedal_target = min(100.0, max(0.0, 100.0 * pedal_frac * pert.pedal_gain))
    if regime == "COAST":
        pedal_target = 0.0

    if apply_noise:
        # Driver modulation: a slowly wandering offset, not per-sample hiss.
        decay = dt / config.PEDAL_MODULATION_TAU_S
        engine_state.pedal_noise = (
            engine_state.pedal_noise * (1.0 - decay)
            + config.PEDAL_MODULATION_SIGMA * math.sqrt(2.0 * decay) * rng.gauss(0.0, 1.0))
        pedal_target += engine_state.pedal_noise

    # Dead band: the foot does not move for a change it cannot feel.
    if abs(pedal_target - engine_state.pedal_pct) > config.PEDAL_DEADBAND_PCT:
        engine_state.pedal_pct += (pedal_target - engine_state.pedal_pct) * min(1.0, dt / 0.7)
    throttle = min(100.0, max(0.0, engine_state.pedal_pct))

    # ------------------------------------------------------- sensor behaviour
    if apply_noise:
        fuel_gps_obs = max(0.0, fuel_gps_true * (1.0 + rng.gauss(0.0, config.NOISE["fuel_rate_pct"])))
        engine_load_obs = min(100.0, max(
            0.0, engine_load_true * (1.0 + rng.gauss(0.0, config.NOISE["load_pct"]))))
    else:
        fuel_gps_obs = fuel_gps_true
        engine_load_obs = engine_load_true

    # SAE J1979 quantisation. PID 0x04 and 0x11 are a single byte scaled to
    # 100/255, so they arrive in 0.392 % steps however precise the ECU was.
    engine_load_obs = quantize_pid_percent(engine_load_obs)
    throttle = quantize_pid_percent(throttle)

    co2_gps_obs = fuel_gps_obs * co2_per_g_fuel

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
        rpm=rpm,
        throttle_pct=throttle,
        maf_gps=maf,
        lambda_excess_air=lam,
        regime=regime,
    )


# --------------------------------------------------------------------------
# SAE J1979 quantisation. Every OBD PID is a byte or a word with a fixed
# scaling, so the bus cannot carry more precision than these steps regardless
# of what the ECU computed internally.
# --------------------------------------------------------------------------

def quantize_pid_percent(value: float) -> float:
    """PID 0x04, 0x11, 0x2F: A * 100 / 255."""
    return round(min(255, max(0, round(value * 255.0 / 100.0))) * 100.0 / 255.0, 3)


def quantize_speed_kmh(value: float) -> int:
    """PID 0x0D: a single byte of whole km/h.

    Worth noticing: acceleration recomputed from consecutive whole-km/h samples
    carries +/-0.28 m/s^2 of quantisation noise at 1 Hz. That is real, it is on
    every OBD deployment, and the harsh-driving thresholds have to live with it.
    """
    return int(min(255, max(0, round(value))))


def quantize_rpm(value: float) -> int:
    """PID 0x0C: (256A + B) / 4."""
    return int(round(value * 4.0) / 4.0)


def quantize_fuel_rate_lph(value: float) -> float:
    """PID 0x5E: (256A + B) / 20, i.e. 0.05 L/h steps."""
    return round(round(value * 20.0) / 20.0, 2)


def quantize_maf_gps(value: float) -> float:
    """PID 0x10: (256A + B) / 100."""
    return round(round(value * 100.0) / 100.0, 2)


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
