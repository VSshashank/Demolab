"""GATE A -- physics sanity. Run this before building anything downstream.

Checkpoint 1 of the build spec. If the constant-speed fuel economy is wrong,
every number in the dataset, the models, the dashboard and the ISO 14083 report
is wrong by the same factor and nobody will notice until the ML metrics look
absurd.

Usage:  python -m scripts.gate_a_physics
Exit code 0 = pass, 1 = fail.
"""

from __future__ import annotations

import math
import sys

import config
from simulator import physics

PASS = "\x1b[92mPASS\x1b[0m"
FAIL = "\x1b[91mFAIL\x1b[0m"
NOTE = "\x1b[93mNOTE\x1b[0m"


def _drive_constant(speed_kmh: float, grade_pct: float, payload_kg: float,
                    vehicle_type: str = "LCV", fuel_type: str = "DIESEL"):
    """Steady-state cruise: zero acceleration, fixed grade, noise off."""
    return physics.step(
        vehicle_type=vehicle_type,
        fuel_type=fuel_type,
        payload_kg=payload_kg,
        speed_ms=speed_kmh / 3.6,
        accel_ms2=0.0,
        grade_rad=math.atan(grade_pct / 100.0),
        apply_noise=False,
    )


def main() -> int:
    failures = 0
    print("=" * 74)
    print("GATE A - road-load physics sanity")
    print("=" * 74)

    # ---------------------------------------------------------------- primary
    # LCV, 60 km/h, flat, 3 t payload, 100 km.
    r = _drive_constant(60.0, 0.0, 3000.0)
    l100 = physics.l_per_100km(r.fuel_gps_true, 60.0, "DIESEL")
    co2_per_km = r.co2_gps_true * 3600.0 / 60.0  # g CO2/s -> g/km at 60 km/h
    seconds_for_100km = 100.0 / 60.0 * 3600.0
    litres_100km = physics.g_to_litres(r.fuel_gps_true * seconds_for_100km, "DIESEL")

    print("\n[1] LCV @ 60 km/h, flat road, 3000 kg payload, 100 km")
    print(f"    mass                 {config.VEHICLE_SPECS['LCV']['kerb_kg'] + 3000:>10.0f} kg")
    print(f"    tractive force       {r.f_trac_n:>10.1f} N")
    print(f"    wheel power          {r.p_wheel_w / 1000.0:>10.2f} kW")
    print(f"    engine power         {r.p_engine_w / 1000.0:>10.2f} kW")
    print(f"    fuel rate            {r.fuel_gps_true:>10.4f} g/s")
    print(f"    fuel rate            {physics.fuel_gps_to_lph(r.fuel_gps_true, 'DIESEL'):>10.3f} L/h")
    print(f"    fuel over 100 km     {litres_100km:>10.3f} L")
    ok = 7.0 <= l100 <= 14.0
    failures += 0 if ok else 1
    print(f"    ECONOMY              {l100:>10.3f} L/100km   expect 7-14      [{PASS if ok else FAIL}]")
    ok = 185.0 <= co2_per_km <= 375.0
    failures += 0 if ok else 1
    print(f"    CO2 INTENSITY        {co2_per_km:>10.2f} g/km       expect 185-375   [{PASS if ok else FAIL}]")

    # -------------------------------------------------------------- secondary
    print("\n[2] Idle burn, engine on, stationary")
    for vt in ("LCV", "RIGID_HGV"):
        idle = physics.step(vehicle_type=vt, fuel_type="DIESEL", payload_kg=3000.0,
                            speed_ms=0.0, accel_ms2=0.0, grade_rad=0.0, apply_noise=False)
        lph = physics.fuel_gps_to_lph(idle.fuel_gps_true, "DIESEL")
        co2_kg_hr = idle.co2_gps_true * 3600.0 / 1000.0
        spec_gps = config.VEHICLE_SPECS[vt]["idle_fuel_gps"]
        # Not an exact match any more: the Willans intercept rises with engine
        # speed, and idle rpm sits a few rpm above 750 once the accessories are
        # loaded. Within 1.5 % of the configured constant is the correct test.
        ok = abs(idle.fuel_gps_true - spec_gps) / spec_gps < 0.015 and idle.regime == "IDLE"
        failures += 0 if ok else 1
        print(f"    {vt:<10} {idle.fuel_gps_true:.3f} g/s = {lph:.2f} L/h = "
              f"{co2_kg_hr:.2f} kg CO2/h  @ {idle.rpm} rpm, lambda {idle.lambda_excess_air:.2f}"
              f"   [{PASS if ok else FAIL}]")

    print(f"    {NOTE} the build spec's prose says 0.45 g/s '-> ~0.5 L/hr'. That arithmetic")
    print("         does not hold: 0.45 g/s over an hour is 1620 g, and 1620 g of diesel")
    print("         at 0.835 kg/L is 1.94 L/h. The 0.45 g/s constant is kept because it is")
    print("         the physically defensible one for a 7.5 t chassis; every L/h and")
    print("         kg CO2/h figure shown to a user is computed from it, never typed in.")

    # 4% grade
    flat = _drive_constant(60.0, 0.0, 3000.0)
    up = _drive_constant(60.0, 4.0, 3000.0)
    ratio = up.fuel_gps_true / flat.fuel_gps_true
    print("\n[3] Grade sensitivity, LCV @ 60 km/h, 3000 kg payload")
    for pct in (0.0, 1.0, 2.0, 4.0):
        g = _drive_constant(60.0, pct, 3000.0)
        print(f"    {pct:>4.1f} % grade      {g.fuel_gps_true:>8.4f} g/s   "
              f"{physics.l_per_100km(g.fuel_gps_true, 60.0, 'DIESEL'):>7.2f} L/100km   "
              f"x{g.fuel_gps_true / flat.fuel_gps_true:.2f}")
    # A 4% grade on a 5.8 t vehicle adds m*g*sin(theta) ~ 2274 N against a total
    # flat road load of ~1051 N, so the load more than triples. Anything near
    # the spec's suggested +40-60% would mean the grade term is being dropped.
    ok = 2.2 <= ratio <= 3.6
    failures += 0 if ok else 1
    print(f"    4% vs flat           x{ratio:>9.2f}            expect x2.2-3.6  [{PASS if ok else FAIL}]")
    print(f"    {NOTE} the spec's secondary note expects '+40-60%' at 4%. That is not what")
    print("         the road-load equation gives for this mass: +40-60% corresponds to")
    print("         roughly a 1% grade (shown above). The grade term is correct as written;")
    print("         the prose expectation is not. Primary gate [1] is the load-bearing one.")

    # Coasting
    coast = physics.step(vehicle_type="LCV", fuel_type="DIESEL", payload_kg=3000.0,
                         speed_ms=60.0 / 3.6, accel_ms2=0.0,
                         grade_rad=math.atan(-6.0 / 100.0), apply_noise=False)
    idle_gps = config.VEHICLE_SPECS["LCV"]["idle_fuel_gps"]
    print("\n[4] Coasting, LCV @ 60 km/h down a 6% grade")
    print(f"    wheel power          {coast.p_wheel_w / 1000.0:>10.2f} kW   (negative = road drives)")
    print(f"    regime               {coast.regime:>10}")
    print(f"    fuel rate            {coast.fuel_gps_true:>10.4f} g/s")
    ok = coast.regime == "COAST" and 0.0 < coast.fuel_gps_true < idle_gps
    failures += 0 if ok else 1
    print(f"    fuel cut-off         above zero, below idle ({idle_gps:.2f} g/s)   [{PASS if ok else FAIL}]")

    # Payload sensitivity - heavier must never be cheaper.
    print("\n[5] Payload monotonicity, LCV @ 60 km/h flat")
    prev = None
    mono = True
    for payload in (0, 1000, 2000, 3000, 4000, 4700):
        g = _drive_constant(60.0, 0.0, float(payload))
        econ = physics.l_per_100km(g.fuel_gps_true, 60.0, "DIESEL")
        print(f"    {payload:>5} kg payload   {g.fuel_gps_true:>8.4f} g/s   {econ:>7.2f} L/100km")
        if prev is not None and g.fuel_gps_true < prev:
            mono = False
        prev = g.fuel_gps_true
    failures += 0 if mono else 1
    print(f"    monotonic increase                              [{PASS if mono else FAIL}]")

    # Speed sweep - aero is quadratic, so economy must be U-shaped.
    print("\n[6] Speed sweep, LCV flat, 3000 kg payload")
    econs = []
    for v in (20, 30, 40, 50, 60, 70, 80, 90, 100):
        g = _drive_constant(float(v), 0.0, 3000.0)
        e = physics.l_per_100km(g.fuel_gps_true, float(v), "DIESEL")
        econs.append(e)
        print(f"    {v:>3} km/h          {g.fuel_gps_true:>8.4f} g/s   {e:>7.2f} L/100km")
    best = econs.index(min(econs))
    ok = 0 < best < len(econs) - 1
    failures += 0 if ok else 1
    print(f"    U-shaped curve, optimum in the interior          [{PASS if ok else FAIL}]")

    # HGV cross-check.
    print("\n[7] RIGID_HGV @ 70 km/h, flat, 8000 kg payload")
    h = _drive_constant(70.0, 0.0, 8000.0, vehicle_type="RIGID_HGV")
    h_econ = physics.l_per_100km(h.fuel_gps_true, 70.0, "DIESEL")
    h_co2 = h.co2_gps_true * 3600.0 / 70.0
    print(f"    fuel rate            {h.fuel_gps_true:>10.4f} g/s")
    print(f"    ECONOMY              {h_econ:>10.2f} L/100km   expect 18-38")
    print(f"    CO2 INTENSITY        {h_co2:>10.2f} g/km       expect 480-1000")
    ok = 18.0 <= h_econ <= 38.0
    failures += 0 if ok else 1
    print(f"    16 t rigid in the published band                 [{PASS if ok else FAIL}]")

    # ------------------------------------------------------------ leak check
    # A permanent guard, and it is generic on purpose.
    #
    # Two separate versions of this simulator handed the models a clean readout
    # of the quantity they were supposed to estimate. First engine_load_pct was
    # computed straight from engine power (one straight line explained 99.6 % of
    # the clean fuel rate). Then throttle_pct was, and it took 73 % of the
    # feature importance. Checking only the feature that failed last time would
    # have missed the second one, so every feature is checked.
    #
    # A high score here does not mean the model is good. It means the dataset is
    # an algebra exercise and any accuracy figure from it is worthless.
    print("\n[8] Single-feature leakage - can any one signal reconstruct the target?")
    import random as _random
    from backend.preprocess import StreamPreprocessor, Rejection
    from simulator.vehicle import Vehicle
    from simulator.scenarios import ScenarioBook

    fleet = [Vehicle.randomized(i, 99) for i in range(12)]
    pre, book = StreamPreprocessor(), ScenarioBook()
    rows, targets = [], []
    for _ in range(900):
        for v in fleet:
            res = pre.process(v.step(1.0, book))
            if isinstance(res, Rejection):
                continue
            rows.append(res.feature_row())
            targets.append(v.last_truth.fuel_rate_gps_true)

    def r2_of(xs, ys):
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        sxx = sum((a - mx) ** 2 for a in xs)
        syy = sum((b - my) ** 2 for b in ys)
        return (sxy ** 2 / (sxx * syy)) if sxx > 0 and syy > 0 else 0.0

    scores = sorted(
        ((f, r2_of([r[f] for r in rows], targets)) for f in config.FEATURE_COLUMNS),
        key=lambda kv: -kv[1])
    for feature, r2 in scores[:6]:
        flag = ROSE if r2 >= 0.90 else (AMBER if r2 >= 0.75 else "")
        print(f"    {feature:<26}{flag}{r2:6.3f}{RESET if flag else ''}")
    worst_feature, worst_r2 = scores[0]
    print(f"    checked {len(scores)} features over {len(rows):,} samples")
    ok = worst_r2 < 0.90
    failures += 0 if ok else 1
    print(f"    strongest single feature is {worst_feature} at R2 {worst_r2:.3f}, "
          f"expect < 0.90   [{PASS if ok else FAIL}]")

    print("\n" + "=" * 74)
    if failures:
        print(f"GATE A: {FAIL} - {failures} check(s) failed. Fix before writing anything downstream.")
    else:
        print(f"GATE A: {PASS} - physics is sane. Cleared to build the simulator.")
    print("=" * 74)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
