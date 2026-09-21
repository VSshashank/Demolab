"""Stage 3: headless simulator run -> training CSV.

Features are built by backend/preprocess.py -- the same code the live backend
runs on every packet. That is not an optimisation, it is the only way the
offline R2 means anything about production behaviour.

The label is `fuel_rate_gps_true`: the CLEAN road-load output, taken from the
vehicle's held-out truth channel. The packet the features come from carries the
noised PID 0x5E reading instead, so the model has a genuine estimation problem.
Handing it the clean value in both places is the classic way to manufacture an
R2 of 0.999 that means nothing.

    python -m ml.generate_dataset --hours 200 --out ml/dataset.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from backend.preprocess import Rejection, StreamPreprocessor
from simulator.scenarios import ScenarioBook
from simulator.vehicle import Vehicle

# Diagnostic columns. Written for analysis and the scatter plot; deliberately
# NOT in config.FEATURE_COLUMNS, and train_models.py asserts none of them leaks
# into the model input.
EXTRA_COLUMNS = [
    "trip_id", "vehicle_id", "route_key", "regime", "ts",
    "co2_gps_true", "fuel_rate_lph_obs", "grade_rad", "payload_kg", "driver_factor",
]

CSV_COLUMNS = config.FEATURE_COLUMNS + [config.TARGET_COLUMN] + EXTRA_COLUMNS


def generate(
    hours: float = 200.0,
    out_path: Path | None = None,
    seed: int = 42,
    vehicles: int = 48,
    progress: bool = True,
) -> dict:
    """Run `vehicles` randomized trucks for `hours` vehicle-hours in total."""
    out_path = Path(out_path or config.DATASET_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dt = 1.0 / config.TICK_HZ
    ticks_per_vehicle = int(round(hours * 3600.0 / vehicles / dt))
    start = datetime(2026, 9, 1, 6, 0, 0, tzinfo=timezone.utc)

    fleet = [Vehicle.randomized(i, seed, start) for i in range(vehicles)]
    pre = StreamPreprocessor()
    book = ScenarioBook()

    rows = 0
    trips: set[str] = set()
    idle_rows = 0
    regimes: dict[str, int] = {}
    t0 = time.time()

    print(f"generating {hours:g} vehicle-hours: {vehicles} vehicles x "
          f"{ticks_per_vehicle} ticks -> {out_path}")

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for tick in range(ticks_per_vehicle):
            for v in fleet:
                packet = v.step(dt, book)
                result = pre.process(packet)
                if isinstance(result, Rejection):
                    continue
                truth = v.last_truth
                row = result.feature_row()
                row[config.TARGET_COLUMN] = truth.fuel_rate_gps_true
                row["trip_id"] = result.trip_id
                row["vehicle_id"] = result.vehicle_id
                row["route_key"] = v.route_key
                row["regime"] = truth.regime
                row["ts"] = result.ts.isoformat()
                row["co2_gps_true"] = truth.co2_gps_true
                row["fuel_rate_lph_obs"] = result.fuel_rate_lph
                row["grade_rad"] = truth.grade_rad
                row["payload_kg"] = result.payload_kg
                row["driver_factor"] = v.driver_factor
                writer.writerow(row)
                rows += 1
                trips.add(result.trip_id)
                idle_rows += result.idle_flag
                regimes[truth.regime] = regimes.get(truth.regime, 0) + 1
            if progress and ticks_per_vehicle >= 20 and tick % (ticks_per_vehicle // 20) == 0:
                pct = 100.0 * tick / ticks_per_vehicle
                print(f"  {pct:5.1f}%  {rows:>9,} rows  {time.time() - t0:6.1f}s",
                      file=sys.stderr, flush=True)

    elapsed = time.time() - t0
    stats = pre.stats()
    summary = {
        "rows": rows,
        "trips": len(trips),
        "vehicles": vehicles,
        "vehicle_hours": hours,
        "idle_rows": idle_rows,
        "idle_fraction": round(idle_rows / rows, 4) if rows else 0.0,
        "regimes": regimes,
        "preprocess": stats,
        "seconds": round(elapsed, 1),
        "out": str(out_path),
        "size_mb": round(out_path.stat().st_size / 1e6, 1),
    }

    print("\n" + "=" * 66)
    print("CHECKPOINT 3 - training data")
    print("=" * 66)
    print(f"  rows                 {rows:>12,}   target >= 200,000")
    print(f"  trips                {len(trips):>12,}   (grouping key for the split)")
    print(f"  idle_flag = 1        {idle_rows:>12,}   {100 * idle_rows / max(rows, 1):5.2f}% of rows")
    for name, count in sorted(regimes.items(), key=lambda kv: -kv[1]):
        print(f"  regime {name:<14}{count:>12,}   {100 * count / max(rows, 1):5.2f}%")
    print(f"  packets rejected     {stats['rejected_total']:>12,}   "
          f"accept rate {stats['accept_rate']:.4f}")
    print(f"  file                 {summary['size_mb']:>12.1f} MB  in {elapsed:.1f}s")
    print("=" * 66)
    if rows < 200_000:
        print(f"\x1b[93m  WARNING: {rows:,} rows is under the 200,000 target. "
              f"Raise --hours.\x1b[0m")
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ml.generate_dataset")
    p.add_argument("--hours", type=float, default=200.0, help="total vehicle-hours")
    p.add_argument("--out", default=str(config.DATASET_PATH))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--vehicles", type=int, default=48, help="distinct randomized configs")
    args = p.parse_args(argv)
    generate(hours=args.hours, out_path=Path(args.out), seed=args.seed, vehicles=args.vehicles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
