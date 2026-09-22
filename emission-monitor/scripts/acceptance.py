"""Acceptance gate. Runs against a live backend and simulator.

    ./run_demo.sh            # in one terminal
    python -m scripts.acceptance

Covers checkpoints 5 through 7 of the build spec: end to end flow, all six
scenarios producing a distinct alert, and an ISO 14083 export that is
arithmetically self-consistent. Prints the three numbers the build is expected
to defend, all of them read from real output.

On the 3-second target: it is measured from the moment a rule's own condition
is satisfied to the alert arriving, not from the button press. EXCESSIVE_IDLING
is defined as 180 seconds of continuous idling, so the earliest it can possibly
fire is 180 seconds after injection. Anything faster would mean the rule was
weakened to hit a number. Both figures are reported.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import json
import sys
import time
import urllib.error
import urllib.request

import config
from backend import reporting
from simulator.scenarios import SCENARIO_DEFAULTS, SCENARIO_TYPES

PASS, FAIL, WARN = "\x1b[92mPASS\x1b[0m", "\x1b[91mFAIL\x1b[0m", "\x1b[93mWARN\x1b[0m"
BOLD, DIM, RESET = "\x1b[1m", "\x1b[2m", "\x1b[0m"

PIPELINE_BUDGET_S = 3.0

EXPECTED_ALERT = {
    "IDLE_EVENT": "EXCESSIVE_IDLING", "HARSH_ACCEL": "HARSH_ACCELERATION",
    "HARSH_BRAKE": "HARSH_BRAKING", "OVERLOAD": "OVERLOAD",
    "ROUTE_DEVIATION": "ROUTE_DEVIATION", "EMISSION_SPIKE": "EMISSION_SPIKE",
}


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.load(r)

    def get_text(self, path: str) -> str:
        with urllib.request.urlopen(self.base + path, timeout=20) as r:
            return r.read().decode()

    def post(self, path: str, body: dict):
        req = urllib.request.Request(
            self.base + path, method="POST", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.acceptance")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--skip-slow", action="store_true",
                    help="skip scenarios whose rule dwell exceeds 60 s")
    args = ap.parse_args(argv)
    c = Client(args.base)
    failures = 0

    print("=" * 78)
    print("ACCEPTANCE GATE")
    print("=" * 78)

    # ------------------------------------------------- checkpoint 5: pipeline
    print(f"\n{BOLD}[5] End to end{RESET}")
    try:
        h = c.get("/api/health")
    except (urllib.error.URLError, OSError) as exc:
        print(f"  {FAIL} backend unreachable at {args.base}: {exc}")
        print("       start it with ./run_demo.sh")
        return 1

    inf = h["inference"]
    checks = [
        ("model loaded", inf["model_loaded"], inf.get("model_name") or inf.get("load_error")),
        ("telemetry flowing", h["packets_per_second"] > 0, f"{h['packets_per_second']} pkt/s"),
        ("six vehicles", h["vehicles_seen"] >= 6, f"{h['vehicles_seen']} seen"),
        ("rows persisting", h["db_rows"] > 0, f"{h['db_rows']} rows"),
        ("no silent fallback", inf["fallbacks"] == 0, f"{inf['fallbacks']} physics fallbacks"),
        ("quality gate active", h["preprocess"]["accept_rate"] > 0.9,
         f"accept rate {h['preprocess']['accept_rate']}"),
    ]
    for name, ok, detail in checks:
        failures += 0 if ok else 1
        print(f"  {PASS if ok else FAIL}  {name:<24} {DIM}{detail}{RESET}")

    # --------------------------------------------- checkpoint 6: all scenarios
    print(f"\n{BOLD}[6] Scenario injection{RESET}")
    fleet = c.get("/api/vehicles")["vehicles"]
    if len(fleet) < 2:
        print(f"  {FAIL} need at least two vehicles to test in parallel")
        return 1

    # EMISSION_SPIKE compares a vehicle against its own recent history, so it
    # cannot fire until that history exists. Waiting for it is the honest thing
    # to do; failing a statistical detector for having no data yet would be a
    # bug in the test, not in the system.
    warmup_needed = config.EMISSION_SPIKE_MIN_BASELINE_S + config.EMISSION_SPIKE_BASELINE_LAG_S
    if h["uptime_s"] < warmup_needed and not args.skip_slow:
        wait = warmup_needed - h["uptime_s"]
        print(f"  {DIM}waiting {wait:.0f} s for the emission-spike baseline to populate "
              f"({warmup_needed} s of history required){RESET}")
        time.sleep(wait)

    order = sorted(SCENARIO_TYPES, key=lambda s: -SCENARIO_DEFAULTS[s]["rule_dwell_s"])
    if args.skip_slow:
        order = [s for s in order if SCENARIO_DEFAULTS[s]["rule_dwell_s"] <= 60]
        print(f"  {DIM}--skip-slow: testing {len(order)} of {len(SCENARIO_TYPES)} scenarios{RESET}")

    # Long-dwell scenarios go first, on separate vehicles, so their windows run
    # concurrently instead of serially. Four minutes becomes three.
    # Scenario-aware assignment. HARSH_BRAKE on a truck sitting at a loading bay
    # is a no-op that will correctly never fire, and reporting that as a system
    # failure would be a bug in the test. Fastest vehicles get the kinematic
    # scenarios; the rest are assigned round robin.
    needs_motion = {"HARSH_BRAKE", "HARSH_ACCEL"}
    assignment, taken = {}, set()

    def claim(vehicle_list):
        pick = next((v["vehicle_id"] for v in vehicle_list if v["vehicle_id"] not in taken), None)
        if pick is None:
            pick = vehicle_list[len(taken) % len(vehicle_list)]["vehicle_id"]
        taken.add(pick)
        return pick

    # The spike detector's baseline is built from MOVING samples only, so give
    # it the most highway-like vehicle. Pointing it at a stop-go city round
    # means waiting for the idle fraction as well as for the window.
    if "EMISSION_SPIKE" in order:
        highway = sorted(fleet, key=lambda v: v.get("stop_go_ratio_60s", 1.0))
        assignment["EMISSION_SPIKE"] = claim(highway)
    fastest = sorted(fleet, key=lambda v: -v.get("speed_kmh", 0.0))
    for scenario in sorted(order, key=lambda s: s not in needs_motion):
        if scenario not in assignment:
            assignment[scenario] = claim(fastest)

    seen_before = {a["id"] for a in c.get("/api/alerts?limit=200")["alerts"]}
    injected = {}
    for scenario in order:
        vehicle = assignment[scenario]
        try:
            c.post("/api/scenario", {"vehicle_id": vehicle, "scenario": scenario})
            injected[scenario] = (vehicle, time.time())
            dwell = SCENARIO_DEFAULTS[scenario]["rule_dwell_s"]
            snap = next((v for v in fleet if v["vehicle_id"] == vehicle), {})
            extra = (f", stop-go {snap.get('stop_go_ratio_60s', 0):.0%}"
                     if scenario == "EMISSION_SPIKE" else
                     f", at {snap.get('speed_kmh', 0):.0f} km/h" if scenario in needs_motion else "")
            print(f"  {DIM}injected {scenario:<17} -> {vehicle}   rule dwell {dwell:>3} s{extra}{RESET}")
        except urllib.error.HTTPError as exc:
            print(f"  {FAIL}  {scenario:<17} rejected: {exc.read().decode()[:80]}")
            failures += 1
        time.sleep(0.4)

    # Room for a rule's dwell plus a vehicle that has to get moving first.
    budget = max(SCENARIO_DEFAULTS[s]["rule_dwell_s"] for s in order) + 90
    print(f"  {DIM}waiting up to {budget:.0f} s for the dwell windows to elapse{RESET}")

    found: dict[str, dict] = {}
    deadline = time.time() + budget
    while time.time() < deadline and len(found) < len(injected):
        for a in c.get("/api/alerts?limit=200")["alerts"]:
            if a["id"] in seen_before:
                continue
            for scenario, (vehicle, t0) in injected.items():
                if scenario in found:
                    continue
                if a["vehicle_id"] == vehicle and a["type"] == EXPECTED_ALERT[scenario]:
                    found[scenario] = {**a, "elapsed_s": time.time() - t0}
        time.sleep(1.0)

    health = c.get("/api/health")
    print()
    print(f"  {'scenario':<18}{'alert raised':<22}{'severity':<9}{'injection to alert':>20}")
    print("  " + "-" * 71)
    for scenario in order:
        hit = found.get(scenario)
        if not hit:
            print(f"  {scenario:<18}{FAIL} no alert raised within {budget:.0f} s")
            failures += 1
            continue
        dwell = SCENARIO_DEFAULTS[scenario]["rule_dwell_s"]
        note = f"  ({dwell:.0f}s of it is the rule's own dwell)" if dwell else ""
        print(f"  {PASS}  {scenario:<15}{hit['type']:<22}{hit['severity']:<9}"
              f"{hit['elapsed_s']:>13.1f}s{note}")

    # The 3-second target is about the pipeline, not about the rules. A rule
    # defined as 180 seconds of continuous idling cannot fire sooner than that,
    # and no amount of fast plumbing changes it; what the system is responsible
    # for is the gap between the anomalous packet arriving and the alert being
    # on screen.
    pl = health["pipeline_latency"]
    if pl["samples"]:
        ok = pl["p95_ms"] <= PIPELINE_BUDGET_S * 1000
        failures += 0 if ok else 1
        print(f"\n  {PASS if ok else FAIL}  pipeline latency p95 {pl['p95_ms']:.1f} ms "
              f"(mean {pl['mean_ms']:.1f}, max {pl['max_ms']:.1f}) against a "
              f"{PIPELINE_BUDGET_S * 1000:.0f} ms target, over {pl['samples']} alerts")
        print(f"       {DIM}{pl['measures']}{RESET}")
    else:
        print(f"\n  {WARN}  no pipeline latency samples recorded")

    distinct = {f["type"] for f in found.values()}
    ok = len(distinct) == len(found)
    failures += 0 if ok else 1
    print(f"\n  {PASS if ok else FAIL}  each scenario produced a distinct alert type "
          f"({len(distinct)} types from {len(found)} injections)")

    # ------------------------------------------ checkpoint 7: report arithmetic
    print(f"\n{BOLD}[7] ISO 14083 export{RESET}")
    try:
        body = c.get_text("/api/report/iso14083?format=csv")
    except urllib.error.HTTPError as exc:
        print(f"  {FAIL} export failed: {exc}")
        return 1 if failures else 0

    rows = list(csv.DictReader(io.StringIO(body)))
    print(f"  {len(rows)} row(s), {len(body)} bytes")
    bad = 0
    for row in rows:
        typed = {k: (float(v) if k in (
            "distance_km", "cargo_mass_t", "transport_activity_tkm", "energy_consumed_l",
            "co2e_ttw_kg", "co2e_wtw_kg", "ghg_intensity_g_per_tkm", "idle_seconds",
            "behaviour_score") and v not in ("", None) else v) for k, v in row.items()}
        typed["harsh_events"] = int(row["harsh_events"] or 0)
        ok, detail = reporting.verify_row(typed)
        if not ok:
            bad += 1
            print(f"  {FAIL} {row['transport_operation_id']}: intensity inconsistent, {detail}")
    failures += bad
    print(f"  {PASS if bad == 0 else FAIL}  intensity column reconciles against kg and t.km "
          f"in every row ({len(rows) - bad}/{len(rows)})")

    sample = next((r for r in rows if r["transport_operation_id"] != "TOTAL"
                   and float(r["transport_activity_tkm"] or 0) > 0), None)
    if sample:
        wtw, tkm = float(sample["co2e_wtw_kg"]), float(sample["transport_activity_tkm"])
        print(f"  {DIM}worked example: {sample['transport_operation_id']}  "
              f"{wtw:.4f} kg x 1000 / {tkm:.3f} t.km = {wtw * 1000 / tkm:.3f}, "
              f"row says {sample['ghg_intensity_g_per_tkm']}{RESET}")

    # -------------------------------------------- checkpoint 8: shipped page
    #
    # This exists because the dashboard once shipped with every asset 404ing.
    # index.html referenced its stylesheet, scripts, fonts and icon sprite
    # relatively, but the page is served from "/" while the files are mounted
    # at "/static". The API was entirely healthy; the page rendered as unstyled
    # Times New Roman with no map and no charts. Nothing in an API-level test
    # could see it, so the check walks the served HTML itself.
    print(f"\n{BOLD}[8] Shipped page{RESET}")
    html = c.get_text("/")
    refs = sorted(set(re.findall(r'(?:src|href)="(/static/[^"#]+)', html)))
    broken = []
    for ref in refs:
        try:
            with urllib.request.urlopen(args.base + ref, timeout=10) as r:
                if r.status != 200:
                    broken.append((ref, r.status))
        except urllib.error.HTTPError as exc:
            broken.append((ref, exc.code))
    failures += len(broken)
    for ref, code in broken:
        print(f"  {FAIL} {code} {ref}")
    print(f"  {PASS if not broken else FAIL}  every referenced asset resolves "
          f"({len(refs) - len(broken)}/{len(refs)})")

    # The offline requirement: no script or stylesheet may point off-box.
    remote = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    failures += len(remote)
    print(f"  {PASS if not remote else FAIL}  no remote script or stylesheet in the shipped page"
          + (f" (found {remote})" if remote else ""))

    # Fonts and the tile fallback are referenced from CSS, not HTML.
    css = c.get_text("/static/styles.css")
    css_refs = sorted({u for u in re.findall(r'url\("([^"]+)"\)', css)
                       if not u.startswith("data:")})
    css_broken = []
    for ref in css_refs:
        try:
            with urllib.request.urlopen(f"{args.base}/static/{ref}", timeout=10) as r:
                if r.status != 200:
                    css_broken.append(ref)
        except urllib.error.HTTPError:
            css_broken.append(ref)
    failures += len(css_broken)
    print(f"  {PASS if not css_broken else FAIL}  every stylesheet url resolves "
          f"({len(css_refs) - len(css_broken)}/{len(css_refs)}): "
          f"{', '.join(r.rsplit('/', 1)[-1] for r in css_refs)}")

    has_fallback = ".tile-fallback" in css
    failures += 0 if has_fallback else 1
    print(f"  {PASS if has_fallback else FAIL}  grey graticule fallback present for tile failure")

    # ------------------------------------------------------- headline numbers
    h = c.get("/api/health")
    inf, al = h["inference"], h["alert_latency"]
    m = json.load(open("ml/metrics.json"))
    best = next(x for x in m["models"] if x["name"] == m["best_model"])

    print(f"\n{BOLD}THE THREE NUMBERS{RESET}  (all read from live output, none estimated)")
    print(f"  1. Test R2            {best['r2']:.4f}  {m['best_model']}, {m['split_strategy']}")
    print(f"                        {DIM}a random row split would have reported "
          f"{m['split_comparison']['random_row_split_r2']:.4f}{RESET}")
    pl = h["pipeline_latency"]
    if pl.get("mean_ms") is not None:
        print(f"  2. Alert latency      {pl['mean_ms']:.2f} ms mean, {pl['p95_ms']:.2f} ms p95, "
              f"{pl['max_ms']:.2f} ms max over {pl['samples']} alerts")
        print(f"                        {DIM}{pl['measures']}, against a 3000 ms target{RESET}")
        if al.get("mean_ms") is not None:
            print(f"                        {DIM}injection to alert including rule dwell: "
                  f"{al['mean_ms'] / 1000:.1f} s mean over {al['samples']} injections{RESET}")
    else:
        print(f"  2. Alert latency      {WARN} no injections recorded")
    print(f"  3. Inference latency  {inf['inference_ms_mean']:.3f} ms mean, "
          f"{inf['inference_ms_p95']:.3f} ms p95 per packet over {inf['sampled_over']} samples")

    print("\n" + "=" * 78)
    if failures:
        print(f"ACCEPTANCE: {FAIL}  {failures} check(s) failed")
    else:
        print(f"ACCEPTANCE: {PASS}  every check passed")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
