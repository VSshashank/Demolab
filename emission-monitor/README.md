# FleetCarbon

Real-time logistics fleet emission monitoring. Live map, ML CO2 estimation,
anomaly detection and ISO 14083 / GLEC reporting.

---

The IoT hardware layer is not yet procured. For this build, the physical sensor
layer is replaced by a hardware-in-the-loop telemetry simulator that emits the
identical JSON schema, at the same 1 Hz rate, over the same transport that an
ESP32 + ELM327 OBD-II dongle would. Ground truth fuel consumption is generated
from the standard road-load equation (the physics used by MOVES and COPERT),
with sensor noise applied. All layers above the sensor layer, meaning
preprocessing, feature engineering, the trained Random Forest and XGBoost
models, anomaly detection, the dashboard, and the ISO 14083 reporting, are real
implementations running on live data flow. Hardware integration requires
implementing a single adapter class; see `adapters/esp32_adapter.py`.

---

`EXPLAINER.md` is a plain-language walkthrough of why the system is built this
way, written for someone who has not read the code. Start there if you want the
reasoning rather than the reference.

## Run it

```bash
./run_demo.sh
```

First run creates a virtualenv and installs pinned dependencies. Then open
<http://localhost:8000>. `./run_demo.sh --replay` replays the committed golden
run instead of generating fresh telemetry; `--fresh` wipes the database first.

Trained models and `ml/metrics.json` are committed, so nothing has to be
trained on the delivery machine.

## The three numbers

Read from live output by `python -m scripts.acceptance`, never estimated.

| | Value | How it is measured |
|---|---|---|
| Test R2 | **0.9845** (XGBoost) | `GroupShuffleSplit` on `trip_id`, 70/15/15. A random row split on the same data reports 0.9921; the 0.0076 gap is the leakage that split would have hidden. |
| Alert latency | **1.2 ms mean, 2.0 ms p95** | Anomalous packet received to alert delivered to the browser, against a 3000 ms target. Over 13 alerts. |
| Inference latency | **0.88 ms mean, 1.31 ms p95** | Per packet, timed inside `backend/inference.py`, over 500 samples. |

`/api/health` and `/api/metrics` serve all three live. Nothing in the frontend
is hardcoded.

All six scenarios raise their distinct alert. Injection to alert averages
101 s across the six, because that figure is dominated by the rules' own dwell
windows rather than by the system.

On alert latency: the 3-second target is about the pipeline, not about the
rules. `EXCESSIVE_IDLING` is defined as 180 seconds of continuous idling, so it
cannot fire sooner than that however fast the plumbing is. Injection-to-alert
times are reported separately, with each rule's dwell window broken out.

## Architecture

```
L1  Data collection   GPS + OBD-II + fuel sensing at 1 Hz        SIMULATED
L2  Preprocessing     cleaning, gap handling, feature building   live
L3  AI / ML           7 regressors + a 7-rule anomaly engine     live
L4  Cloud / comms     WebSocket ingest, SQLite, live fan-out     live
L5  Dashboard         map, KPIs, alerts, ISO 14083 export        live
```

```
emission-monitor/
├── config.py                    every constant, with its source
├── run_demo.sh                  backend + simulator together
├── simulator/
│   ├── physics.py               road load, Willans line, air path
│   ├── routes.py                Mangalore-region polylines and terrain
│   ├── vehicle.py               per-vehicle state machine
│   ├── scenarios.py             injectable faults
│   └── run_simulator.py         CLI: live, replay, headless
├── adapters/
│   ├── base.py                  TelemetryAdapter, the swap point
│   ├── simulator_adapter.py     implemented
│   └── esp32_adapter.py         documented stub, raises NotImplementedError
├── backend/
│   ├── main.py                  FastAPI, REST, WebSocket hub
│   ├── db.py                    SQLite schema, batched writes
│   ├── preprocess.py            L2, and the ONLY feature implementation
│   ├── inference.py             model loading, per-packet scoring
│   ├── anomalies.py             the rule engine
│   └── reporting.py             trip accounting, ISO 14083 builder
├── ml/
│   ├── generate_dataset.py      headless simulator to training CSV
│   ├── train_models.py          trains and evaluates seven models
│   ├── models/                  committed: xgboost_model.joblib
│   └── metrics.json             committed: real evaluation output
├── scripts/
│   ├── gate_a_physics.py        physics correctness gate
│   └── acceptance.py            end-to-end acceptance gate
└── static/                      no build step, everything vendored
```

## Telemetry schema v1.0

What a real ESP32 + ELM327 publishes. OBD-II PIDs noted so the hardware path is
concrete. Nothing above layer 1 may read a field outside this;
`adapters/base.py::validate_packet` enforces it on every packet.

```json
{
  "schema_version": "1.0",
  "device_id": "OBD-MITE-004",
  "vehicle_id": "KA-19-AB-4521",
  "trip_id": "TRIP-20260922-004-01",
  "ts": "2026-09-22T09:14:03.000Z",
  "gps":     { "lat": 12.9141, "lon": 74.8560, "alt_m": 34.2, "hdop": 0.9, "sats": 11 },
  "obd":     { "speed_kmh": 52.4,        // PID 0x0D
               "rpm": 1840,              // PID 0x0C
               "engine_load_pct": 46.5,  // PID 0x04
               "throttle_pct": 31.2,     // PID 0x11
               "coolant_temp_c": 88,     // PID 0x05
               "maf_gps": 18.7,          // PID 0x10
               "fuel_level_pct": 61.4,   // PID 0x2F
               "fuel_rate_lph": 9.8,     // PID 0x5E  <- the ML target
               "engine_on": true,
               "dtc_count": 0 },         // PID 0x01
  "derived": { "accel_ms2": 0.42, "heading_deg": 187.5 },
  "cargo":   { "payload_kg": 3200, "gvw_kg": 7500, "rated_gvw_kg": 7500 },
  "meta":    { "vehicle_type": "LCV", "fuel_type": "DIESEL",
               "driver_id": "DRV-11", "source": "SIMULATOR" }
}
```

Every field is quantised the way SAE J1979 quantises it. Speed is a whole
number of km/h, engine load and throttle arrive in 0.392 % steps, fuel rate in
0.05 L/h steps. This matters downstream: acceleration recomputed from
consecutive whole-km/h samples carries about 0.28 m/s2 of quantisation noise at
1 Hz, and the harsh-driving thresholds have to live with it.

## The physics

```
F_trac  = m*a + m*g*Crr*cos(theta) + m*g*sin(theta) + 0.5*rho*Cd*A*v_air*|v_air|   [N]
P_wheel = F_trac * v                                                                [W]
P_out   = max(P_wheel, 0) / eta_driveline                                           [W]
fuel    = b(rpm) + a * P_out                                                        [g/s]
CO2     = fuel * (ef_ttw_kg_per_l / density_kg_per_l)                               [g/s]
```

Drag is computed against airspeed, not ground speed, because a headwind is a
first-order term at highway speed and is completely invisible to the vehicle.

The fuel line is a **Willans line**: fuel flow affine in net engine output. A
single bsfc figure is the best point on an engine map and overstates efficiency
badly at light load. With a flat 215 g/kWh a lightly laden van on a gentle
descent came out "cruising" at 0.29 g/s against a 0.45 g/s idle burn, which is
not possible. The Willans intercept is the idle burn, so an engine making
positive power can never consume less than one making none, and the slope is
calibrated so that overall bsfc at rated output equals the published figure.
The intercept rises with engine speed, because friction does.

Constants and their sources are in `config.py`, one per line with a citation.

### Why PID 0x04 is modelled through the air path

This is the decision that determines whether any ML number in this repo means
anything, so it is worth stating plainly.

The first version of `physics.py` computed engine load directly from engine
power. That made the clean fuel rate an exact affine function of a single
observed feature. One straight line through `engine_load_pct` explained **99.6 %**
of it, every model scored R2 above 0.99, and Gate B's leakage warning fired.
The ML problem was arithmetic.

SAE J1979 defines PID 0x04 as air mass flow over peak air mass flow at the
current engine speed. It is an **air** measurement. A diesel is unthrottled and
runs lean, with lambda swinging from about 2.9 at idle to 1.28 under full
fuelling, so the same airflow corresponds to very different fuel flows.
Recovering fuel from load therefore requires knowing lambda, which is not on
the bus. Modelling the air path is both the correct physics and the thing that
gives the models something real to estimate.

The same mistake was then found in `throttle_pct`, which had been derived from
the same engine power and was taking 73 % of the feature importance. PID 0x11
is a position sensor under a human foot: it commands a fraction of the torque
available at the current engine speed, so the same road power reads differently
in different gears, and the foot modulates constantly.

`scripts/gate_a_physics.py` check [8] now tests **every** feature, not the one
that failed last time, and refuses to pass if any single signal reconstructs
the target with R2 above 0.90. It currently reports 0.64.

### Unobserved variation

None of this reaches the telemetry packet, which is the point. It is the part
of real fuel consumption that an OBD dongle cannot see, and it is what the
models have to estimate around.

| | Modelled as |
|---|---|
| Headwind | Ornstein-Uhlenbeck, 3.4 m/s sigma, 240 s correlation |
| Rolling resistance | per vehicle, 0.0062 to 0.0108 (SAE J1263 band) |
| Accessory load | AC and air-brake compressors cycling, 1.5 to 6.5 kW |
| Engine wear and unit variation | lognormal, 8 % sigma, fixed per vehicle |
| Efficiency drift | fuel batch, injector fouling; 4.5 % sigma, 900 s |
| Air-path deviation | EGR duty, charge-air temperature; 7 % sigma |
| Turbo lag | first-order, 1.8 s, so airflow trails fuelling in transients |

Road grade is withheld from the feature set entirely.

## ML methodology

Seven models, trained and compared: Linear Regression, Ridge, Lasso, SVR (RBF,
on a 20k subsample), MLP (64, 32), Random Forest (200 trees, depth 18) and
XGBoost (400 rounds, depth 8). Trees get raw features; only the distance- and
gradient-based learners are put in a scaling pipeline.

**The split is the part that matters.** These are 1 Hz time series: second *n*
and second *n+1* differ by a hair, so a random row split puts near-duplicates
of every test row into training and reports an R2 that measures the sampling
rate rather than the model. `GroupShuffleSplit` on `trip_id` keeps whole trips
together, which is the honest question: can it score a journey it has never
seen. Both numbers are computed and both are printed, because the comparison is
the argument for the decision.

Features are built by `backend/preprocess.py`, the same code the live backend
runs on every packet. That is not an optimisation. Building features twice is
how a model that scores 0.96 offline scores 0.6 in production, and it is why
`preprocess.py` is written before `generate_dataset.py` rather than after it as
the original plan had it.

Training target is `fuel_rate_gps_true`, the clean road-load output held out of
the packet. The packet carries the noised PID 0x5E reading instead.

Only what a real dongle observes is given to the model: `speed_kmh`,
`accel_ms2`, `rpm`, `engine_load_pct`, `throttle_pct`, `coolant_temp_c`,
`gvw_kg`, `load_ratio`, `vehicle_type_enc`, `fuel_type_enc`,
`rolling_mean_speed_30s`, `rolling_std_accel_30s`, `idle_flag`,
`stop_go_ratio_60s`.

## Anomaly rules

| Type | Condition | Severity |
|---|---|---|
| `EXCESSIVE_IDLING` | under 3 km/h, engine on, 180 s continuous | HIGH |
| `HARSH_ACCELERATION` | over 2.5 m/s2 for 2 s | MEDIUM |
| `HARSH_BRAKING` | under -3.0 m/s2 | MEDIUM |
| `OVER_SPEEDING` | over the segment limit + 10 km/h for 10 s | MEDIUM |
| `OVERLOAD` | GVW over rated GVW | HIGH |
| `ROUTE_DEVIATION` | over 500 m off the polyline for 30 s | LOW |
| `EMISSION_SPIKE` | predicted CO2 over baseline mean + 3 sigma | HIGH |

De-duplicated with a 60 s per-vehicle per-type cooldown.

The spike detector compares against a baseline that **excludes the most recent
30 samples**. Without the lag a slowly developing fault walks its own baseline
upward and never clears three sigma: by the time the rate has risen, the mean
has risen with it. With the lag it fires in about 44 s instead of 143 s.

Every figure in an alert recommendation is computed from `config.py`, not typed
in. This matters: the build spec's suggested idling text quotes "~0.5 L/hr and
~1.3 kg CO2/hr", but the configured idle rate of 0.45 g/s works out at
**1.94 L/h and 5.2 kg CO2/h**. Rather than pick which of the two to hardcode,
the numbers are derived, so the text is arithmetically true whatever the
constants say.

## API

| | |
|---|---|
| `GET /` | the dashboard |
| `WS /ws/ingest` | telemetry in, scenario control frames back out |
| `WS /ws/live` | enriched stream to the dashboard |
| `GET /api/vehicles` | current state of every vehicle |
| `GET /api/vehicle/{id}/history?minutes=30` | recorded trace |
| `GET /api/routes` | planned route polylines |
| `GET /api/alerts?limit=50` | newest first |
| `POST /api/alerts/{id}/ack` | acknowledge |
| `GET /api/trips`, `GET /api/trip/{trip_id}` | trip records and detail |
| `GET /api/metrics` | `ml/metrics.json`, served verbatim |
| `GET /api/models/scatter` | predicted against actual pairs |
| `GET /api/report/iso14083?trip_id=&format=csv\|pdf` | the export |
| `POST /api/scenario` | inject a fault |
| `GET /api/scenarios` | catalogue, with each rule's dwell window |
| `GET /api/health` | uptime, packets/s, model state, both latencies |

## ISO 14083 reporting

Energy is integrated from the vehicle's own PID 0x5E fuel-rate reading, not
from the model prediction. ISO 14083 wants primary activity data where it
exists; the measured fuel flow is primary, an ML estimate is not. The estimate
is reported next to it so the two can be compared.

The intensity column is derived from the rounded kg and t.km values that appear
in the same row, so the CSV reconciles against itself. Deriving before rounding
left rows off by 1.5e-4, which is small, meaningless, and exactly the kind of
thing a reviewer checks with a calculator. `reporting.verify_row` recomputes it
and the acceptance gate calls it on every row.

Intensity is reported as "not defined" rather than 0 when transport activity is
zero. A stationary or empty vehicle has no answer to "grams per tonne-
kilometre", and 0 would claim a perfect score.

## Offline

Venue wifi fails. Leaflet, Chart.js, Geist, JetBrains Mono and a 28-symbol
Phosphor subset are vendored into `static/vendor/` with their licences. No
script or stylesheet in `index.html` points at a CDN. Map tiles are the one
network resource, and the dashboard degrades to a grey graticule when they
cannot be fetched, keeping routes and vehicle markers readable.

The stylesheet is hand-authored rather than Tailwind. Tailwind's no-build
option is the Play CDN, which is a ~400 KB in-browser JIT compiler: it cannot
be vendored as static CSS, it flashes unstyled content on load, and it is a
script tag pointing at a CDN, which the offline requirement forbids.

## Gates

```bash
python -m scripts.gate_a_physics        # physics, before anything downstream
python -m ml.train_models               # prints Gate B, the leakage check
python -m scripts.acceptance            # end to end, against a running stack
python -m scripts.acceptance --skip-slow # same, minus the long dwell windows
```

Gate A checks constant-speed fuel economy against 7 to 14 L/100km, idle burn,
grade sensitivity, coasting cut-off, payload monotonicity, the U-shaped
economy curve, an HGV cross-check, and single-feature leakage.

Gate B fails the build if test R2 exceeds 0.995, which is treated as evidence
of leakage rather than of a good model.

The acceptance gate covers the live pipeline, all six scenarios, ISO 14083
arithmetic on every row, and the shipped page itself: it walks every `src` and
`href` in the served HTML plus every `url()` in the stylesheet and asserts each
resolves. That last check exists because the dashboard once shipped with every
asset 404ing while the API was entirely healthy (see below).

Last full run: **every check passed**, six of six scenarios, 8/8 report rows
reconciling.

## Known deviations from the build spec

Recorded rather than quietly accommodated.

1. **Idle figures.** The spec's prose says 0.45 g/s is "~0.5 L/hr". It is
   1.94 L/h. The constant is kept because it is defensible for a 7.5 t chassis;
   all derived figures are computed.
2. **Grade sensitivity.** The spec expects a 4 % grade to raise fuel use 40 to
   60 %. The road-load equation gives about 155 % for this mass, because the
   grade term alone is 2274 N against a flat road load of 1051 N. 40 to 60 %
   corresponds to roughly a 1 % grade.
3. **Tailwind.** Replaced with hand-authored CSS, for the reasons above.
4. **Build order.** `preprocess.py` is built before `generate_dataset.py` to
   avoid train/serve skew.
5. **Random Forest artifact.** Not committed. It serialises to about 1 GB at
   the configured depth, past GitHub's file limit, and nothing loads it; its
   metrics are in `metrics.json`.
6. **Relative asset paths.** `index.html` originally referenced its assets
   relatively while being served from `/` with the files mounted at `/static`.
   Every stylesheet, script, font and icon returned 404 and the page rendered
   as unstyled Times New Roman with no map and no charts, while every API
   endpoint reported healthy. Found only by opening the page in a real
   browser, which is now part of the verification rather than an afterthought.
7. **Test R2 of 0.9845** sits just above the spec's nominal 0.93 to 0.98 band
   and well below Gate B's 0.995 failure threshold. Engine load, throttle and
   rpm are genuinely strong physical predictors of fuel rate once the air path
   is modelled correctly. The ablation is in the commit history: dropping
   `engine_load_pct` costs about 0.017, and no single feature exceeds 0.64
   alone.
