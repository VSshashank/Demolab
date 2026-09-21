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
        "displacement_l": 2.5,  # needed for MAF (PID 0x10), not for the fuel model
        "tank_l": 80,
    },
    "RIGID_HGV": {
        "kerb_kg": 8000,
        "rated_gvw_kg": 16000,
        "cd": 0.80,
        "frontal_area_m2": 8.5,
        "bsfc_g_per_kwh": 205,
        "idle_fuel_gps": 0.85,  # g/s
        "displacement_l": 5.0,
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
# Engine breathing. Only feeds MAF (PID 0x10), which is reported for realism
# and is deliberately NOT a model feature. Deriving MAF from air rather than
# from fuel keeps the training target out of the telemetry packet entirely.
# --------------------------------------------------------------------------
VOLUMETRIC_EFFICIENCY = 0.85
INTAKE_AIR_DENSITY_G_PER_L = 1.20  # ~30 C ambient, naturally aspirated equivalent

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
