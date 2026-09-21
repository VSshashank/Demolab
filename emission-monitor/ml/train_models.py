"""Stage 4: train and evaluate seven regressors on the fuel-rate target.

The split is the part that matters. These are 1 Hz time series: second n and
second n+1 differ by a hair, so a random row split puts near-duplicates of
every test row in the training set and reports an R2 that is a measurement of
the sampling rate rather than of the model. GroupShuffleSplit on trip_id keeps
whole trips together, which is the honest question -- can it score a journey it
has never seen.

Both numbers are computed and both are printed, because the comparison is the
argument for the decision.

    python -m ml.train_models --dataset ml/dataset.csv
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Lasso, LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.svm import SVR
from xgboost import XGBRegressor

import config
from backend.preprocess import FUEL_TYPE_CATEGORIES, VEHICLE_TYPE_CATEGORIES

RESET, GREEN, AMBER, ROSE, BOLD, DIM = (
    "\x1b[0m", "\x1b[92m", "\x1b[93m", "\x1b[91m", "\x1b[1m", "\x1b[2m")

SVR_SUBSAMPLE = 20_000      # full SVR on 500k rows does not finish in this lifetime
SCATTER_SAMPLES = 2_000     # points behind the predicted-vs-actual plot
RANDOM_STATE = 42


def _split_grouped(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """70/15/15 by trip. No trip appears in more than one split."""
    groups = df["trip_id"].values
    idx = np.arange(len(df))

    gss = GroupShuffleSplit(n_splits=1, test_size=0.30, random_state=RANDOM_STATE)
    train_idx, hold_idx = next(gss.split(idx, groups=groups))

    hold_groups = groups[hold_idx]
    gss2 = GroupShuffleSplit(n_splits=1, test_size=0.50, random_state=RANDOM_STATE)
    val_rel, test_rel = next(gss2.split(hold_idx, groups=hold_groups))
    return train_idx, hold_idx[val_rel], hold_idx[test_rel]


def _metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    mse = float(mean_squared_error(y_true, y_pred))
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mse)),
        "mse": mse,
        "r2": float(r2_score(y_true, y_pred)),
    }


def _build_models() -> list[tuple[str, object, bool]]:
    """(name, estimator, needs_scaling). Trees get raw features; only the
    distance- and gradient-based learners are put in a scaling Pipeline."""
    return [
        ("Linear Regression", LinearRegression(), True),
        ("Ridge", Ridge(alpha=1.0, random_state=RANDOM_STATE), True),
        ("Lasso", Lasso(alpha=0.001, random_state=RANDOM_STATE, max_iter=5000), True),
        ("SVR (RBF)", SVR(kernel="rbf", C=10.0, epsilon=0.05, gamma="scale"), True),
        ("MLP (64,32)", MLPRegressor(
            hidden_layer_sizes=(64, 32), early_stopping=True, n_iter_no_change=8,
            max_iter=300, random_state=RANDOM_STATE, learning_rate_init=1e-3), True),
        ("Random Forest", RandomForestRegressor(
            n_estimators=200, max_depth=18, n_jobs=-1, random_state=RANDOM_STATE,
            min_samples_leaf=2), False),
        ("XGBoost", XGBRegressor(
            n_estimators=400, max_depth=8, learning_rate=0.08, subsample=0.9,
            colsample_bytree=0.9, n_jobs=-1, random_state=RANDOM_STATE,
            tree_method="hist"), False),
    ]


def _ridge_alpha_search(X, y, Xv, yv) -> float:
    """Small alpha grid, picked on validation. Printed so the choice is visible."""
    best_alpha, best_r2 = 1.0, -np.inf
    for alpha in (0.01, 0.1, 1.0, 10.0, 100.0):
        m = Pipeline([("scale", StandardScaler()), ("est", Ridge(alpha=alpha))]).fit(X, y)
        r2 = r2_score(yv, m.predict(Xv))
        if r2 > best_r2:
            best_alpha, best_r2 = alpha, r2
    return best_alpha


def train(dataset_path: Path, out_dir: Path, metrics_path: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"{BOLD}loading{RESET} {dataset_path}")
    usecols = config.FEATURE_COLUMNS + [config.TARGET_COLUMN, "trip_id", "regime", "route_key"]
    df = pd.read_csv(dataset_path, usecols=usecols)
    print(f"  {len(df):,} rows, {df['trip_id'].nunique():,} trips")

    # A feature list that quietly picked up a diagnostic column would be a
    # silent leak, so assert the exact set rather than trusting the CSV header.
    leaked = set(config.FEATURE_COLUMNS) & {"co2_gps_true", "fuel_rate_lph_obs",
                                            "grade_rad", config.TARGET_COLUMN}
    if leaked:
        raise SystemExit(f"FEATURE_COLUMNS contains target-derived columns: {leaked}")

    nans = int(df[config.FEATURE_COLUMNS + [config.TARGET_COLUMN]].isna().sum().sum())
    if nans:
        raise SystemExit(f"dataset contains {nans} NaNs; fix generate_dataset.py")

    X = df[config.FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    y = df[config.TARGET_COLUMN].to_numpy(dtype=np.float32)

    train_idx, val_idx, test_idx = _split_grouped(df)
    trips = df["trip_id"].values
    overlap = (set(trips[train_idx]) & set(trips[test_idx])) | (set(trips[val_idx]) & set(trips[test_idx]))
    if overlap:
        raise SystemExit(f"grouped split leaked {len(overlap)} trips across folds")

    Xtr, ytr = X[train_idx], y[train_idx]
    Xva, yva = X[val_idx], y[val_idx]
    Xte, yte = X[test_idx], y[test_idx]

    print(f"\n{BOLD}split{RESET}  GroupShuffleSplit on trip_id")
    print(f"  train {len(train_idx):>9,} rows / {len(set(trips[train_idx])):>4} trips")
    print(f"  val   {len(val_idx):>9,} rows / {len(set(trips[val_idx])):>4} trips")
    print(f"  test  {len(test_idx):>9,} rows / {len(set(trips[test_idx])):>4} trips")
    print(f"  {GREEN}no trip appears in more than one fold{RESET}")

    alpha = _ridge_alpha_search(Xtr[:50_000], ytr[:50_000], Xva[:20_000], yva[:20_000])
    print(f"\n{DIM}  ridge alpha grid -> {alpha}{RESET}")

    results, fitted = [], {}
    print(f"\n{BOLD}{'model':<20}{'MAE':>9}{'RMSE':>9}{'MSE':>10}{'R2':>9}"
          f"{'train s':>10}{'inf ms/row':>12}{RESET}")
    print(DIM + "-" * 79 + RESET)

    for name, est, needs_scaling in _build_models():
        if name == "Ridge":
            est = Ridge(alpha=alpha, random_state=RANDOM_STATE)
        model = Pipeline([("scale", StandardScaler()), ("est", est)]) if needs_scaling else est

        fit_X, fit_y = Xtr, ytr
        note = ""
        if name.startswith("SVR") and len(Xtr) > SVR_SUBSAMPLE:
            rs = np.random.RandomState(RANDOM_STATE)
            pick = rs.choice(len(Xtr), SVR_SUBSAMPLE, replace=False)
            fit_X, fit_y = Xtr[pick], ytr[pick]
            note = f" (fit on {SVR_SUBSAMPLE:,})"

        t0 = time.time()
        model.fit(fit_X, fit_y)
        train_s = time.time() - t0

        bench = Xte[:5000]
        t0 = time.time()
        model.predict(bench)
        inference_ms_per_row = (time.time() - t0) * 1000.0 / len(bench)

        m = _metrics(yte, model.predict(Xte))
        m_val = _metrics(yva, model.predict(Xva))
        results.append({
            "name": name, **m,
            "val_r2": m_val["r2"],
            "train_seconds": round(train_s, 2),
            "inference_ms_per_row": round(inference_ms_per_row, 5),
            "note": note.strip(),
        })
        fitted[name] = model

        colour = GREEN if m["r2"] >= 0.90 else (AMBER if m["r2"] >= 0.75 else ROSE)
        print(f"{name:<20}{m['mae']:>9.4f}{m['rmse']:>9.4f}{m['mse']:>10.4f}"
              f"{colour}{m['r2']:>9.4f}{RESET}{train_s:>10.1f}{inference_ms_per_row:>12.4f}{note}")

    best = max(results, key=lambda r: r["r2"])
    best_model = fitted[best["name"]]
    print(f"\n{BOLD}best: {best['name']}  test R2 = {best['r2']:.4f}{RESET}")

    # ---------------------------------------------------------------- GATE B
    print(f"\n{BOLD}GATE B - leakage check{RESET}")
    rand_tr, rand_te = train_test_split(np.arange(len(df)), test_size=0.15,
                                        random_state=RANDOM_STATE)
    naive = XGBRegressor(n_estimators=200, max_depth=8, learning_rate=0.08,
                         n_jobs=-1, random_state=RANDOM_STATE, tree_method="hist")
    naive.fit(X[rand_tr], y[rand_tr])
    naive_r2 = float(r2_score(y[rand_te], naive.predict(X[rand_te])))
    inflation = naive_r2 - best["r2"]
    print(f"  grouped split (trip_id)   test R2 = {best['r2']:.4f}   <- reported")
    print(f"  random row split          test R2 = {naive_r2:.4f}   <- discarded")
    print(f"  inflation from leakage              {inflation:+.4f}")

    verdict = "PASS"
    if best["r2"] > 0.995:
        print(f"{ROSE}  WARNING: test R2 > 0.995. That is not a good model, it is a leak, "
              f"or sensor noise is not being applied. Do not report this number.{RESET}")
        verdict = "FAIL_LEAKING"
    elif best["r2"] < 0.85:
        print(f"{AMBER}  WARNING: test R2 < 0.85. Features are too weak or there is a bug.{RESET}")
        verdict = "WARN_WEAK"
    elif 0.93 <= best["r2"] <= 0.98:
        print(f"{GREEN}  R2 is in the expected 0.93-0.98 band for a correctly split "
              f"noised target.{RESET}")
    else:
        print(f"{AMBER}  R2 outside the nominal 0.93-0.98 band but not pathological.{RESET}")

    # ------------------------------------------------------------- artifacts
    vt_enc = LabelEncoder().fit(VEHICLE_TYPE_CATEGORIES)
    ft_enc = LabelEncoder().fit(FUEL_TYPE_CATEGORIES)

    model_file = out_dir / "xgboost_model.joblib"
    joblib.dump({
        "model": best_model,
        "model_name": best["name"],
        "feature_columns": config.FEATURE_COLUMNS,
        "target": config.TARGET_COLUMN,
        "vehicle_type_encoder": vt_enc,
        "fuel_type_encoder": ft_enc,
        "sklearn_version": sklearn.__version__,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }, model_file)
    print(f"\n  saved {model_file}  ({model_file.stat().st_size / 1e6:.1f} MB)")
    # The Random Forest is deliberately NOT persisted. At 200 trees, depth 18,
    # over 535k rows it serialises to about 1 GB, which is past GitHub's file
    # limit and would have to be fetched by anyone cloning this. Nothing loads
    # it: the backend scores with the winning model, and the RF's evaluation
    # numbers are in metrics.json, which is what the Models tab reads. Rerun
    # training if you need the fitted object itself.

    # Feature importance from the best tree model; linear models expose
    # coefficients that are not comparable, so fall back to the RF.
    src = best_model if hasattr(best_model, "feature_importances_") else fitted["Random Forest"]
    importance = sorted(
        ({"feature": f, "importance": float(v)}
         for f, v in zip(config.FEATURE_COLUMNS, src.feature_importances_)),
        key=lambda d: -d["importance"])

    rs = np.random.RandomState(RANDOM_STATE)
    pick = rs.choice(len(Xte), min(SCATTER_SAMPLES, len(Xte)), replace=False)
    y_pred_sample = best_model.predict(Xte[pick])
    (out_dir / "predicted_vs_actual.json").write_text(json.dumps({
        "model": best["name"],
        "n": len(pick),
        "pairs": [[round(float(a), 4), round(float(b), 4)]
                  for a, b in zip(yte[pick], y_pred_sample)],
    }), encoding="utf-8")

    metrics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "rows": int(len(df)),
            "trips": int(df["trip_id"].nunique()),
            "train": int(len(train_idx)),
            "val": int(len(val_idx)),
            "test": int(len(test_idx)),
            "train_trips": len(set(trips[train_idx])),
            "val_trips": len(set(trips[val_idx])),
            "test_trips": len(set(trips[test_idx])),
            "idle_fraction": float((df["idle_flag"] == 1).mean()),
        },
        "split_strategy": "GroupShuffleSplit on trip_id (prevents temporal leakage)",
        "split_comparison": {
            "grouped_r2": round(best["r2"], 6),
            "random_row_split_r2": round(naive_r2, 6),
            "inflation": round(inflation, 6),
            "note": ("A random row split on 1 Hz data puts second n in train and "
                     "second n+1 in test. The gap between these two numbers is "
                     "the size of that mistake."),
        },
        "gate_b": {"verdict": verdict, "expected_band": [0.93, 0.98]},
        "target": config.TARGET_COLUMN,
        "target_units": "g/s of fuel",
        "features": config.FEATURE_COLUMNS,
        "withheld_features": ["road grade", "wind", "vehicle mass constants",
                              "driveline efficiency", "bsfc"],
        "models": [{k: (round(v, 6) if isinstance(v, float) else v)
                    for k, v in r.items()} for r in results],
        "best_model": best["name"],
        "feature_importance": importance,
        "environment": {
            "python": platform.python_version(),
            "sklearn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
    }
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(f"  saved {metrics_path}")
    print(f"  saved {out_dir / 'predicted_vs_actual.json'}")
    return metrics


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ml.train_models")
    p.add_argument("--dataset", default=str(config.DATASET_PATH))
    p.add_argument("--out-dir", default=str(config.MODEL_DIR))
    p.add_argument("--metrics", default=str(config.METRICS_PATH))
    args = p.parse_args(argv)
    ds = Path(args.dataset)
    if not ds.exists():
        raise SystemExit(f"dataset not found: {ds}\nRun: python -m ml.generate_dataset --hours 200")
    train(ds, Path(args.out_dir), Path(args.metrics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
