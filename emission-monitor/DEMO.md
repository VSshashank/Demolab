# Demo script

One page. Follow it in order. Total run time about 8 minutes.

## Before anyone walks in

```bash
./run_demo.sh
```

Open <http://localhost:8000>. **Let it run for three minutes before you
present.** Two things need that time: trips have to accumulate before the Trips
tab has anything in it, and the emission-spike detector needs about two minutes
of per-vehicle history before it can call anything anomalous.

Leave the simulator terminal visible on a second screen if you have one. It
prints one colour-coded line per packet and makes the point that this is a real
data flow better than any slide.

---

## 1. Open on the header (20 seconds)

Point at the amber pill: **DATA SOURCE: SIMULATOR (HIL)**. Say the sentence
before anyone asks:

> The dongles are not procured yet. The sensor layer is a hardware-in-the-loop
> simulator emitting the identical JSON schema at the same 1 Hz over the same
> transport. Everything above it is real and running on that live flow.

Then point at the three live numbers next to it: ingest rate, inference
latency, and the connection dot. All three are measured, not typed.

## 2. Live Monitor (90 seconds)

- Six vehicles moving on real Mangalore corridors: NH-66 to Udupi, the port run
  to Bantwal, a city delivery loop, and the MITE Moodbidri run.
- Marker colour is the live CO2 rate. Click one: speed, CO2 g/s, cumulative kg,
  payload, trip intensity, behaviour score.
- Faint blue lines are the planned routes. Brighter traces are where each
  vehicle has actually been.
- KPI strip across the top. **Avg intensity in g CO2e per tonne-kilometre is
  the number a sustainability auditor cares about**, and it is the one most
  fleet dashboards do not show.

## 3. Inject the slow scenarios first (30 seconds)

Open **Scenario injection**, bottom right. Fire these two now so their windows
run while you talk about something else:

1. Pick a vehicle, hit **IDLE EVENT**. The rule needs 180 seconds of continuous
   idling, so it will land near the end of the demo.
2. Pick a different vehicle, hit **EMISSION SPIKE**. About 45 seconds.

If someone asks why idling takes three minutes: because the rule is *defined*
as three minutes of idling. Making it fire faster would mean detecting a
different thing.

## 4. Models tab (2 minutes)

This is the strongest tab. Everything on it is read from `ml/metrics.json`.

- Seven models, MAE / RMSE / MSE / R2 / train time / inference cost. XGBoost
  wins at **R2 0.9845**.
- Read the caption under the table out loud. It says the same data under a
  random row split scores 0.9921, and that the difference is leakage a naive
  split would have hidden. **This is the single most credible thing in the
  build**: it shows you knew about a trap and measured it rather than walking
  into it.
- Predicted against actual, with the y=x line.
- Feature importance. If asked why throttle dominates: it is a strong physical
  proxy for engine output, and the ablation shows no single feature recovers
  the target alone (best is 0.64).

## 5. Back to Live Monitor for the fast scenarios (60 seconds)

Fire these three and watch the alert feed. Each lands in about a second.

3. **HARSH BRAKE** (pick a vehicle that is actually moving; the dropdown shows
   each vehicle's speed)
4. **OVERLOAD**
5. **ROUTE DEVIATION** (about 30 seconds; watch the marker jump off its route)

Point out that alerts carry a **specific** message and a **recommendation with
real numbers**, for example "Idling 4m 12s with the engine running. About
0.13 L burned going nowhere." Not "anomaly detected".

By now the emission spike and probably the idle alert have landed too.

## 6. Trips and Reports (90 seconds)

- Table of transport operations: distance, litres, TTW and WTW kg, tonne-
  kilometres, **g CO2e per t.km**, behaviour score.
- Click a row. Speed and CO2 profile for that trip, its ISO 14083 line, and the
  alerts raised during it.
- Hit **Export ISO 14083 CSV**. Open it. Pick any row and check with a
  calculator: WTW kg times 1000, divided by t.km, equals the intensity column.
  It reconciles exactly, and the acceptance gate checks every row.

## 7. Architecture (40 seconds)

Five layers, each badged SIMULATED or LIVE. One badge is amber.

Close on the swap: `adapters/base.py` defines the interface, nothing above
layer 1 may read a field outside schema v1.0, and `validate_packet` enforces
that on every packet. `adapters/esp32_adapter.py` has the full ELM327 poll
sequence, the MQTT topic layout and the offline buffering strategy, and raises
`NotImplementedError` rather than returning anything plausible.

---

## Questions you will get

**"How do you know the ML is not just memorising?"**
Grouped split on trip_id, and the random-split number is printed next to it.
Gate B fails the build above R2 0.995.

**"Is the data made up?"**
Ground truth is the road-load equation, the physics behind MOVES and COPERT,
with a Willans line for the engine. `random.uniform` appears nowhere in any
path that produces a fuel or emission number. Run
`python -m scripts.gate_a_physics` in front of them if they push: it drives a
simulated LCV at 60 km/h and checks the answer lands in 7 to 14 L/100km.

**"What happens when the wifi drops?"**
Hard-reload with wifi off. The page renders fully styled; the map falls back to
a grey grid with routes and markers still on it.

**"How long to integrate real hardware?"**
One class. The stub lists exactly what has to be written, including the two
things that are not on the OBD bus: payload mass comes from dispatch, and the
clock should come from GPS rather than the ESP32 RTC.

**"Why is idling 1.94 L/h when the brief said 0.5?"**
Because 0.45 g/s over an hour is 1620 g, and 1620 g of diesel at 0.835 kg/L is
1.94 L. The brief's two numbers contradict each other. Every figure shown is
computed from the constant so the text is always true.

## If something breaks

- **Dashboard frozen, dot red.** The backend died. Restart `./run_demo.sh`. The
  browser reconnects on its own with backoff.
- **Every prediction says physics_fallback.** `ml/models/xgboost_model.joblib`
  did not load. `/api/health` carries the exact error.
- **No trips.** It has not been running long enough. Give it two minutes.
- **A scenario button says "armed".** The target vehicle cannot perform that
  manoeuvre yet, usually because it is parked at a loading bay. Pick one with a
  non-zero speed in the dropdown.
