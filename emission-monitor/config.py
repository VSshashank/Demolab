"""Every tunable constant in the system lives here, and nowhere else.

Each physical value carries its source. When a reviewer asks "where did 2.68
come from?", the answer must be in the file, not in someone's memory.
"""

from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ML_DIR = ROOT / "ml"
MODEL_DIR = ML_DIR / "models"
STATIC_DIR = ROOT / "static"

DB_PATH = DATA_DIR / "emission_monitor.db"
RECORDED_RUN_PATH = DATA_DIR / "recorded_run.jsonl"
METRICS_PATH = ML_DIR / "metrics.json"
DATASET_PATH = ML_DIR / "dataset.csv"

SCHEMA_VERSION = "1.0"

# --------------------------------------------------------------------------
# Physics
# --------------------------------------------------------------------------
AIR_DENSITY = 1.225  # kg/m^3, ISA sea level, 15 C
GRAVITY = 9.81  # m/s^2
ROLLING_RESISTANCE = 0.008  # Crr, radial truck tyre on dry asphalt (SAE J1263 range 0.006-0.010)
DRIVELINE_EFFICIENCY = 0.85  # eta, engine crank -> wheel, typical manual/AMT heavy duty
ACCESSORY_POWER_W = 3500  # alternator + AC compressor + air brake compressor, steady draw

# Below this the vehicle counts as stationary rather than rolling: the road-load
# equation divides by v to get force from power, so it is undefined at v = 0.
CREEP_SPEED_MS = 0.5

# Coasting/overrun: modern diesel ECUs cut injection on a closed throttle, but
# never fully to zero at the pump. 30% of idle flow is the conventional figure.
COASTING_FUEL_FRACTION = 0.3

VEHICLE_SPECS = {
    # bsfc = brake specific fuel consumption at the efficient part of the map.
    # 200-220 g/kWh is the published band for modern EU VI / BS VI diesels.
    "LCV": {
        "kerb_kg": 2800,
        "rated_gvw_kg": 7500,
        "cd": 0.70,
        "frontal_area_m2": 5.0,
        "bsfc_g_per_kwh": 215,
        "idle_fuel_gps": 0.45,  # g/s; see README "A note on the idle figure"
        "displacement_l": 3.0,  # 7.5 t chassis diesel; sets the airflow the ECU sees
        "rated_power_kw": 110,  # 7.5 t chassis, BS VI 2.5 L turbodiesel class
        "tank_l": 80,
    },
    "RIGID_HGV": {
        "kerb_kg": 8000,
        "rated_gvw_kg": 16000,
        "cd": 0.80,
        "frontal_area_m2": 8.5,
        "bsfc_g_per_kwh": 205,
        "idle_fuel_gps": 0.85,  # g/s
        "displacement_l": 5.2,
        "rated_power_kw": 180,  # 16 t rigid, 5 L six-cylinder class
        "tank_l": 200,
    },
}

# --------------------------------------------------------------------------
# Fuel and emission factors
# Densities: IPCC 2006 Guidelines Vol.2 Ch.3 default liquid fuel properties.
# TTW factors: UK DEFRA/BEIS GHG conversion factors, "Fuels" table, kg CO2e per litre.
# WTW factors: DEFRA "WTT - fuels" upstream added to the TTW direct figure.
# --------------------------------------------------------------------------
FUEL_SPECS = {
    "DIESEL": {"density_kg_per_l": 0.835, "ef_ttw_kg_per_l": 2.68, "ef_wtw_kg_per_l": 3.24},
    "PETROL": {"density_kg_per_l": 0.745, "ef_ttw_kg_per_l": 2.31, "ef_wtw_kg_per_l": 2.80},
}

# --------------------------------------------------------------------------
# Sensor noise. Applied on top of the physics ground truth, never instead of it.
# Without this the ML problem is algebra, not estimation, and R^2 comes out at
# 0.999 which tells you nothing.
# --------------------------------------------------------------------------
NOISE = {
    "gps_m": 3.0,  # 1-sigma horizontal, consumer GPS with good sky view
    "speed_pct": 0.01,  # OBD speed is wheel-derived, quantised to 1 km/h
    "fuel_rate_pct": 0.03,  # PID 0x5E is itself an ECU estimate
    "load_pct": 0.02,  # PID 0x04 calculated load
}

# --------------------------------------------------------------------------
# Anomaly thresholds
# --------------------------------------------------------------------------
IDLE_SPEED_KMH = 3.0
IDLE_ALERT_SECONDS = 180
HARSH_ACCEL_MS2 = 2.5
HARSH_ACCEL_SUSTAIN_S = 2
HARSH_BRAKE_MS2 = -3.0
OVERSPEED_MARGIN_KMH = 10.0
OVERSPEED_SUSTAIN_S = 10
ROUTE_DEVIATION_M = 500
ROUTE_DEVIATION_SUSTAIN_S = 30
EMISSION_SPIKE_SIGMA = 3.0
EMISSION_SPIKE_WINDOW_S = 600
# The baseline the spike is compared against excludes the most recent samples.
# Without the lag a slowly developing fault walks its own baseline upward and
# never clears three sigma: by the time the rate has risen, the mean has risen
# with it. Standard practice for change detection, and it also makes the rule
# fire sooner because the baseline stays clean.
EMISSION_SPIKE_BASELINE_LAG_S = 30
EMISSION_SPIKE_MIN_BASELINE_S = 90
ALERT_COOLDOWN_SECONDS = 60

# --------------------------------------------------------------------------
# Data quality gates (L2 preprocessing)
# --------------------------------------------------------------------------
MAX_HDOP = 5.0
MIN_SATS = 4
MAX_PLAUSIBLE_SPEED_KMH = 140.0
MAX_PLAUSIBLE_ACCEL_MS2 = 8.0
MAX_GAP_SECONDS = 5.0
ROLLING_WINDOW_SECONDS = 120  # deque length per vehicle; 30s and 60s windows read from it

# --------------------------------------------------------------------------
# Engine breathing and air/fuel ratio.
#
# This block is what makes the ML problem an estimation problem instead of an
# algebra problem, so it is worth explaining.
#
# PID 0x04 "calculated engine load" is defined by SAE J1979 as current air mass
# flow divided by peak air mass flow at the current engine speed. It is an AIR
# measurement. A diesel is unthrottled and runs lean, with the excess-air ratio
# lambda swinging from about 6 at idle to about 1.3 at full fuelling, so the
# same airflow can correspond to very different fuel flows. Recovering fuel
# from load therefore requires knowing lambda, which is not on the bus.
#
# An earlier version of this file computed load directly from engine power.
# That made fuel an exact affine function of load: one straight line through
# engine_load_pct explained 99.6 % of the clean fuel rate, every model scored
# R2 > 0.99, and the reported accuracy meant nothing. Modelling the air path
# is both the physically correct thing to do and the thing that gives the
# models something real to estimate.
# --------------------------------------------------------------------------
VOLUMETRIC_EFFICIENCY = 0.85
INTAKE_AIR_DENSITY_G_PER_L = 1.20  # ~30 C ambient at the intake
MAX_BOOST_RATIO = 2.4              # VGT turbodiesel, peak manifold/ambient pressure ratio
TURBO_LAG_TAU_S = 1.8              # first-order spool-up; why load lags fuel in transients

AFR_STOICHIOMETRIC = {"DIESEL": 14.5, "PETROL": 14.7}  # kg air per kg fuel
# Excess-air ratio against fuelling demand. Diesels run very lean at light load
# and approach the smoke limit under full fuelling.
LAMBDA_AT_IDLE = 2.9
LAMBDA_AT_FULL_LOAD = 1.28

# --------------------------------------------------------------------------
# Unobserved variation. None of this reaches the telemetry packet, which is the
# point: it is the part of real fuel consumption that an OBD dongle cannot see.
# --------------------------------------------------------------------------
# Engine-to-engine and wear variation in specific fuel consumption. Two trucks
# of the same model at the same duty differ by this much and nothing on the bus
# reveals it.
BSFC_UNIT_VARIATION = 0.08          # 1-sigma, lognormal, fixed per vehicle
BSFC_DRIFT_SIGMA = 0.045            # slow drift: fuel batch, injector fouling, air temp
BSFC_DRIFT_TAU_S = 900.0

# Accessory duty. The AC compressor and the air-brake compressor cycle on and
# off independently of the driver.
ACCESSORY_POWER_MIN_W = 1500
ACCESSORY_POWER_MAX_W = 6500
ACCESSORY_CYCLE_S = (90.0, 400.0)

# Rolling resistance varies with tyre pressure, tread and surface. Fixed per
# vehicle per trip; SAE J1263 puts truck tyres in this band.
CRR_RANGE = (0.0062, 0.0108)

# Wind. A headwind component is a first-order term in the road load at highway
# speed and is completely invisible to the vehicle.
WIND_SIGMA_MS = 3.4
WIND_TAU_S = 240.0

# Air-path deviation: EGR duty, injector timing drift, charge-air temperature.
LAMBDA_DEV_SIGMA = 0.07
LAMBDA_DEV_TAU_S = 90.0

# Pedal behaviour. PID 0x11 is a position sensor on a pedal a human foot is
# resting on, not an ECU readout of torque. Feet are not servos: drivers
# modulate constantly, hold different positions for the same output, and do not
# react to changes below a dead band.
PEDAL_MODULATION_SIGMA = 5.2   # percentage points, 1-sigma
PEDAL_MODULATION_TAU_S = 3.0
PEDAL_DEADBAND_PCT = 1.8

# Torque curve. Peak torque well below rated speed, which is what makes pedal
# position depend on the gear as well as on the power being asked for.
PEAK_TORQUE_RPM = 1600.0
TORQUE_CURVE_FALLOFF = 0.1975  # calibrated so T(rated rpm) / T(peak) = 0.84

# Engine friction rises with speed, so the Willans intercept is not constant.
# Roughly doubles from idle to rated speed on a diesel.
FRICTION_RPM_GAIN = 0.55

AMBIENT_TEMP_C = 30.0  # Mangalore coastal mean
COOLANT_OPERATING_C = 88.0
COOLANT_WARMUP_TAU_S = 180.0  # first-order warm-up, ~3 min to operating temp

# Fraction of fixes that arrive degraded enough to be rejected by the L2 quality
# gate. Real GPS does this under canopy and between buildings; if it never
# happened the quality gate would be untested code.
GPS_DEGRADED_RATE = 0.005

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
TICK_HZ = 1.0
DEFAULT_VEHICLE_COUNT = 6
DB_BATCH_ROWS = 50
DB_BATCH_SECONDS = 2.0

# Feature vector handed to the model. Order is load-bearing: inference must
# build the frame in exactly this order or XGBoost silently scores garbage.
FEATURE_COLUMNS = [
    "speed_kmh",
    "accel_ms2",
    "rpm",
    "engine_load_pct",
    "throttle_pct",
    "coolant_temp_c",
    "gvw_kg",
    "load_ratio",
    "vehicle_type_enc",
    "fuel_type_enc",
    "rolling_mean_speed_30s",
    "rolling_std_accel_30s",
    "idle_flag",
    "stop_go_ratio_60s",
]

TARGET_COLUMN = "fuel_rate_gps_true"
