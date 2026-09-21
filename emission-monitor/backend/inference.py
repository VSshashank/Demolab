"""L3 at serving time: load the trained model once, score every packet.

Three things this file refuses to do:

1. Silently substitute. If the model will not load, predictions are marked
   `physics_fallback` all the way to the browser and the health endpoint says
   so. A dashboard quietly showing physics estimates labelled as ML output is
   worse than one showing an error.
2. Rebuild features. It consumes what backend/preprocess.py produced, in
   config.FEATURE_COLUMNS order. Column order is load-bearing for a tree model:
   permuted inputs do not raise, they just score nonsense.
3. Guess at latency. Every prediction is timed and the distribution is exposed,
   because "inference latency" is one of the three numbers this build is
   expected to defend.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import config
from backend.preprocess import (FUEL_TYPE_CATEGORIES, VEHICLE_TYPE_CATEGORIES, Sample)
from simulator import physics

log = logging.getLogger("inference")


@dataclass
class Prediction:
    fuel_rate_gps: float      # g/s of fuel
    co2_gps: float            # g CO2/s, tank to wheel
    source: str               # "model" | "physics_fallback"
    inference_ms: float


class FuelRatePredictor:
    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = Path(model_path or (config.MODEL_DIR / "xgboost_model.joblib"))
        self.model = None
        self.model_name = "physics_fallback"
        self.feature_columns = config.FEATURE_COLUMNS
        self.load_error: str | None = None
        self.trained_at: str | None = None
        self.latencies_ms: deque = deque(maxlen=500)
        self.predictions = 0
        self.fallbacks = 0
        self._load()

    def _load(self) -> None:
        if not self.model_path.exists():
            self.load_error = f"model file not found: {self.model_path}"
            log.error("MODEL NOT LOADED: %s. Falling back to the road-load estimate; "
                      "every prediction will be marked physics_fallback.", self.load_error)
            return
        try:
            import joblib
            bundle = joblib.load(self.model_path)
            self.model = bundle["model"]
            self.model_name = bundle.get("model_name", "unknown")
            self.feature_columns = bundle.get("feature_columns", config.FEATURE_COLUMNS)
            self.trained_at = bundle.get("trained_at")

            # The encoders are a consistency check, not a second source of
            # truth: preprocess.py derives its encoding from config, and if the
            # persisted encoders disagree the model was trained against a
            # different category order and its output is meaningless.
            vt = list(bundle["vehicle_type_encoder"].classes_)
            ft = list(bundle["fuel_type_encoder"].classes_)
            if vt != VEHICLE_TYPE_CATEGORIES or ft != FUEL_TYPE_CATEGORIES:
                raise ValueError(
                    f"encoder mismatch: model was trained with vehicle_type={vt}, "
                    f"fuel_type={ft}; this build encodes "
                    f"{VEHICLE_TYPE_CATEGORIES}/{FUEL_TYPE_CATEGORIES}")
            if self.feature_columns != config.FEATURE_COLUMNS:
                raise ValueError(
                    f"feature order mismatch: model expects {self.feature_columns}, "
                    f"preprocess produces {config.FEATURE_COLUMNS}")
            log.info("loaded %s from %s (trained %s)", self.model_name,
                     self.model_path.name, self.trained_at)
        except Exception as exc:  # noqa: BLE001 - any failure must be visible
            self.model = None
            self.load_error = f"{type(exc).__name__}: {exc}"
            log.error("MODEL LOAD FAILED: %s. Falling back to the road-load estimate.",
                      self.load_error)

    # ------------------------------------------------------------------ core
    def predict(self, sample: Sample) -> Prediction:
        t0 = time.perf_counter()
        if self.model is None:
            fuel_gps = self._physics_estimate(sample)
            source = "physics_fallback"
            self.fallbacks += 1
        else:
            row = sample.feature_row()
            x = np.array([[row[c] for c in self.feature_columns]], dtype=np.float32)
            fuel_gps = float(self.model.predict(x)[0])
            source = "model"
        # A negative fuel rate is not a prediction, it is an extrapolation
        # artefact. Clamp at the coasting floor rather than at zero: a running
        # engine always burns something.
        floor = config.COASTING_FUEL_FRACTION * config.VEHICLE_SPECS[
            sample.vehicle_type]["idle_fuel_gps"]
        fuel_gps = max(floor, fuel_gps)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.latencies_ms.append(elapsed_ms)
        self.predictions += 1

        spec = config.FUEL_SPECS[sample.fuel_type]
        co2_gps = fuel_gps * (spec["ef_ttw_kg_per_l"] / spec["density_kg_per_l"])
        return Prediction(fuel_rate_gps=fuel_gps, co2_gps=co2_gps,
                          source=source, inference_ms=elapsed_ms)

    def _physics_estimate(self, sample: Sample) -> float:
        """Road-load estimate from packet fields only.

        This is a genuine fallback, not a stand-in for the simulator's truth:
        it uses the same published vehicle constants the model was trained
        against, the GPS-derived grade proxy, and nothing that is unavailable
        on real hardware. It is less accurate than the model, which is the
        whole point of training one.
        """
        spec = config.VEHICLE_SPECS[sample.vehicle_type]
        speed_ms = sample.speed_kmh / 3.6
        if speed_ms <= config.CREEP_SPEED_MS:
            return spec["idle_fuel_gps"]
        grade_rad = float(np.arctan(sample.grade_proxy))
        f_trac = physics.tractive_force_n(
            sample.gvw_kg, sample.accel_ms2, speed_ms, grade_rad,
            spec["cd"], spec["frontal_area_m2"])
        p_wheel = f_trac * speed_ms
        if p_wheel <= 0:
            return config.COASTING_FUEL_FRACTION * spec["idle_fuel_gps"]
        p_out = p_wheel / config.DRIVELINE_EFFICIENCY
        return (physics.friction_intercept_gps(spec, sample.rpm)
                + physics.willans_slope_g_per_ws(spec) * p_out)

    # ------------------------------------------------------------------ meta
    def stats(self) -> dict:
        lat = sorted(self.latencies_ms)
        def pct(p: float) -> float:
            return round(lat[min(len(lat) - 1, int(len(lat) * p))], 4) if lat else 0.0
        return {
            "model_loaded": self.model is not None,
            "model_name": self.model_name,
            "model_file": self.model_path.name,
            "trained_at": self.trained_at,
            "load_error": self.load_error,
            "predictions": self.predictions,
            "fallbacks": self.fallbacks,
            "inference_ms_mean": round(sum(lat) / len(lat), 4) if lat else 0.0,
            "inference_ms_p50": pct(0.50),
            "inference_ms_p95": pct(0.95),
            "inference_ms_max": round(max(lat), 4) if lat else 0.0,
            "sampled_over": len(lat),
        }
