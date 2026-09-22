# How this project works, and why it was built this way

A plain-language walkthrough. No prior knowledge assumed. If you can read a
sentence about trucks and a sentence about arithmetic, you can read this.

Read `README.md` if you want the technical reference. Read `DEMO.md` if you are
about to present. Read this if you want to understand *why*.

Sections 2, 5 and 6 are the ones worth reading closely. Everything else is
description; those three are the actual engineering decisions.

---

## 1. What the system does

Imagine a logistics company with a fleet of delivery trucks around Mangalore.
They want to know, right now and for each truck:

- Where is it, and how fast is it going?
- How much fuel is it burning this second?
- How much CO2 is that?
- Is the driver doing something wasteful, like idling for ten minutes?
- At the end of the month, what goes in the sustainability report?

This system answers all five. A truck reports its condition once a second, the
server cleans that up, a machine learning model estimates the fuel burn, a set
of rules watches for bad behaviour, and a dashboard shows it all live. At the
end, it produces a report in the format international standards require.

---

## 2. The problem we were handed

The whole design rests on one awkward fact.

**The hardware does not exist yet.** The plan called for an ESP32 microcontroller
and an ELM327 OBD-II dongle plugged into each truck. Those had not been
purchased. There were no trucks, no dongles, and no data.

Everything else in the project sits on top of that missing layer. So the first
real decision was: what do you put in its place?

### The tempting shortcut, and why it fails

The obvious move is to make the numbers up:

```python
co2 = random.uniform(2, 9)   # looks fine on a chart
```

This works for about ninety seconds. It fails the moment anyone asks a
follow-up question, and it fails in four specific ways:

1. **The numbers do not relate to each other.** A truck can be shown standing
   still while burning fuel like it is climbing a hill. Anyone who knows
   vehicles will spot it immediately.
2. **The machine learning becomes meaningless.** A model trained on random
   numbers learns randomness. You cannot report an accuracy figure, because
   there is nothing real to be accurate about.
3. **You cannot answer "what if".** What if the truck were heavier? What if the
   road were steeper? Random numbers have no opinion.
4. **Nothing transfers.** When the real dongles arrive, none of the work above
   the fake layer has ever seen realistic data.

### What we did instead

We built a **hardware-in-the-loop simulator**. That phrase sounds grand; the
idea is simple:

> Write a program that behaves exactly like the missing hardware would, and
> plug it into the same socket the hardware will use.

Concretely, our simulator:

- Emits **the same JSON message** a real dongle emits, field for field.
- Emits it at **the same rate**, once per second.
- Sends it over **the same connection** the real dongle will use.
- Calculates fuel consumption from **real physics**, not a random number
  generator.
- Adds **realistic sensor error** on top, because real sensors are imperfect.

This is not a trick or a workaround. It is standard engineering practice.
Carmakers test engine control software against simulated engines for years
before it touches a real one. It is how you build the software before the
hardware is ready.

**The payoff:** every layer above the sensor is real, working code operating on
a realistic data flow. When the dongles arrive, one file changes. We can say
that precisely, and the code proves it.

---

## 3. Why the architecture looks like this

### Five layers

The system is built in five stacked layers, each one only talking to its
neighbours.

```
L5  Dashboard        what a person looks at
L4  Cloud and comms  moving and storing the data
L3  AI and ML        estimating emissions, spotting problems
L2  Preprocessing    cleaning up messy sensor data
L1  Data collection  the truck's sensors            <-- SIMULATED
```

Why split it up at all? Because each layer has one job, and a problem in one
place does not spread. If the map is broken, you know it is not the physics. If
the fuel numbers look wrong, you know it is not the database.

Only layer 1 is simulated. Layers 2 through 5 are the real thing.

### The adapter: the single most important design choice

Here is the idea that makes the hardware claim believable rather than just
optimistic.

There is a file, `adapters/base.py`, that defines a **contract**. It says: "a
telemetry source is anything that produces messages in this exact shape." That
is all it says. It does not care where the messages come from.

Two things can satisfy that contract:

- `simulator_adapter.py` - implemented, produces messages from our physics model
- `esp32_adapter.py` - not implemented, will produce messages from real dongles

Everything above layer 1 talks only to the contract. It has never seen a
simulator object and does not know one exists.

**Why this matters so much:** without the contract, the code above would
gradually start reaching into the simulator for convenient extras. "I'll just
grab the true fuel value from the simulator here, it's easier." Do that three
times and the hardware swap quietly becomes impossible, and nobody notices
until the dongles are already fitted to the trucks.

We enforce it mechanically. Every single message passes through
`validate_packet()`, which rejects anything that does not match the agreed
shape. If someone adds a field the real hardware cannot produce, it fails
immediately and loudly, at the boundary, rather than six months later.

`esp32_adapter.py` is worth reading even though it does nothing. It contains
the exact sequence of commands to send the dongle, what each reply means, what
to do about trucks that do not support the fuel sensor, and why the cargo
weight has to come from the dispatch system rather than the truck. It raises an
error rather than returning plausible-looking data, because a system silently
showing fake data is worse than one that stops.

---

## 4. The physics, in plain terms

This is the foundation. Every number in the system inherits from it, so if it
is wrong, everything is wrong.

### What makes a truck burn fuel

A moving truck has to fight four things:

| Force | In plain terms |
|---|---|
| **Inertia** | Speeding up takes effort. Heavier truck, more effort. |
| **Rolling resistance** | Tyres flex against the road. Constant drag. |
| **Gravity on a slope** | Going uphill means lifting the whole truck. |
| **Air resistance** | Pushing air out of the way. Gets much worse with speed. |

Add those up and you get the force the engine must produce. Multiply force by
speed and you get **power**. This is the "road-load equation", and it is the
same physics used by the US Environmental Protection Agency's MOVES model and
Europe's COPERT. We did not invent it.

One detail worth knowing: air resistance grows with the **square** of speed. Go
twice as fast and you fight four times the air. This is why the fuel economy
curve is U-shaped, which the verification script checks.

### From power to fuel

Knowing the power, how much diesel does that take?

The naive answer is "multiply by a constant". We tried that. It gave a wrong
answer in an obvious way: a lightly loaded van rolling gently downhill came out
"cruising" while burning **less fuel than the same van sitting still with the
engine running**. That is not possible.

The fix is a **Willans line**, a standard engine model:

```
fuel burned = (what the engine burns doing nothing) + (rate x work done)
```

The first part is the idle burn. An engine turning over uses fuel even when it
is not moving the truck, to overcome its own friction and to run the
alternator, the air conditioning and the brake compressor. The second part is
the extra fuel for actual work.

Because the "doing nothing" part is built in, an engine doing work can never
burn less than an engine doing none. The impossible result becomes impossible.

We also make that idle figure rise with engine speed, because engine friction
genuinely does: a diesel spinning at 2500 rpm wastes roughly twice as much
energy on internal friction as one at idle.

### From fuel to CO2

This part is simple chemistry. Burning a litre of diesel releases a fixed
amount of CO2. We use the UK government's published factor, 2.68 kg of CO2 per
litre burned in the engine, and 3.24 kg per litre when you also count the
energy used to extract, refine and deliver that diesel.

Those two numbers have names: **tank-to-wheel** (what comes out of the exhaust)
and **well-to-wheel** (the full footprint). Sustainability reporting wants the
second one.

### Proving the physics is right

Unit mistakes are the classic failure here. Watts against kilowatts, grams
against litres. Get one wrong and your answer is off by a factor of a thousand,
and you will not notice until the ML results look strange.

So before anything else was built, we wrote `scripts/gate_a_physics.py`. It
drives a simulated van at a steady 60 km/h on flat road with 3 tonnes of cargo,
and checks the answer against reality:

```
Fuel economy    11.5 L/100km     a real van does 7 to 14        PASS
CO2             307 g/km         a real van does 185 to 375     PASS
```

It also checks that heavier is always thirstier, that hills cost more, that
coasting downhill nearly cuts the fuel off but not entirely, and that a 16
tonne truck comes out in the right band too.

**Nothing downstream was written until this passed.**

---

## 5. The machine learning, in plain terms

### What the model is actually for

Modern vehicles can report their fuel flow directly. Many older ones cannot.
The model's job is: **given everything else the truck reports, estimate the fuel
flow.** That way the system works on the whole fleet, not just the new trucks.

The model sees fourteen things, all of which a real dongle can read: speed,
acceleration, engine speed, engine load, throttle position, coolant
temperature, weight, and a few summaries of recent behaviour.

It is deliberately **not** shown the road gradient, the wind, or any of the
physics constants. If we gave it those, it could just redo our own equation and
the accuracy figure would be meaningless.

### Seven models, one winner

We train seven different algorithms and compare them fairly:

| Model | What it is, roughly |
|---|---|
| Linear Regression | Fit a straight line. The honest baseline. |
| Ridge, Lasso | Straight lines that resist over-reacting to noise. |
| SVR | Fits a flexible curved surface. |
| MLP | A small neural network. |
| Random Forest | Hundreds of decision trees voting. |
| **XGBoost** | Trees built in sequence, each fixing the last one's mistakes. |

XGBoost wins with an R2 of **0.9845**. R2 is "what fraction of the variation
did we explain", where 1.0 is perfect. So it accounts for about 98% of the
variation in fuel consumption.

### The trap almost everyone falls into

This is the part worth understanding properly, because it is where most student
ML projects quietly go wrong.

To test a model honestly you hold back some data, train on the rest, and see
how it does on the held-back part. The standard way is to shuffle all your rows
and take a random 15% for testing.

**On this data that is cheating.** Our readings come once per second. Second 500
and second 501 are almost identical: the truck has barely moved. Shuffle
randomly and second 500 lands in training while second 501 lands in testing. The
model does not need to understand anything. It just needs to remember second
500 and repeat it.

You get a beautiful number and it means nothing.

The fix is to split by **whole journeys**. Every reading from a given trip goes
entirely into training or entirely into testing, never split across both. Now
the question is the honest one: can it score a journey it has never seen?

We compute both numbers and print both:

```
Grouped by journey    R2 = 0.9845    <- what we report
Random shuffle        R2 = 0.9921    <- what we discarded
```

The gap is the size of the mistake we avoided. We show it deliberately. It is
evidence that we knew about the trap and measured it, rather than walking into
it.

### An automatic tripwire

The training script fails the build if R2 comes out above 0.995. Above that, the
right conclusion is not "excellent model", it is "something is leaking". A
suspiciously perfect score is a bug report, not an achievement.

---

## 6. The three bugs that nearly made all of it worthless

This is the most useful section in this document, because it shows the
difference between code that runs and code that is correct.

### Bug one: the model was doing arithmetic, not learning

Everything ran. The dashboard worked. Accuracy was 99.6%. It was all worthless.

The problem was in how we calculated **engine load**, one of the fourteen inputs.
We had computed it directly from engine power. But fuel consumption is also
computed from engine power. So the two were, mathematically, the same quantity
wearing different hats.

We measured it: **a single straight line through engine load explained 99.6% of
fuel consumption.** The model was not estimating anything. It was doing one
multiplication.

The fix came from reading the actual specification. The standard that defines
these sensor readings, SAE J1979, says engine load is a measure of **air flow**,
not power. And here is the thing about a diesel: unlike a petrol engine it does not
throttle the air. It takes in a full gulp every time and varies only how much
fuel it injects. So the ratio of air to fuel changes enormously with load:
roughly 19 parts air to 1 part fuel when working hard, and over 40 to 1 when
barely loaded. The same amount of air can mean very different amounts of fuel,
and the missing piece is exactly the thing the sensor does not tell you.

Modelling the air path is both the correct physics **and** the thing that turns
this back into a real estimation problem. One straight line now explains 37%.

### Bug two: the same mistake, in a different place

Having fixed engine load, we checked which inputs the model was leaning on.
**Throttle position was carrying 73% of the weight.** Suspicious.

Same mistake. We had calculated throttle position from engine power too.

Real throttle position is a sensor under a human foot. It commands a fraction
of the torque available *at the current engine speed*, so the same road power
reads differently in different gears. And feet are not machines: drivers
constantly adjust, and they do not react to changes too small to feel.

Once modelled that way, no single input dominates.

### The lesson, and the guard

We had checked the thing that failed last time and missed the same bug
somewhere else. So the check is now **generic**: `gate_a_physics.py` tests
*every one* of the fourteen inputs and refuses to pass if any single one can
reconstruct fuel consumption on its own.

The strongest is now 0.64, comfortably below the 0.90 limit. If anyone ever
reintroduces this class of bug, the build stops.

### Bug three: found only by opening the page

Late on, we rendered the dashboard in a real browser for the first time.

**Every stylesheet, script and font returned "not found."** The page was
unstyled text with no map and no charts.

Every automated test had passed. Every API endpoint reported healthy. The bug
was that the page asked for its files using addresses relative to the wrong
folder, and nothing except an actual browser could see it.

The gate now fetches the real page, reads out every file it asks for, and
checks each one arrives.

**The general lesson in all three:** a system that runs is not the same as a
system that is correct. Each of these was found by deliberately trying to
disprove something we believed, not by running the code and seeing output.

---

## 7. Cleaning the data (layer 2)

Real sensors lie sometimes. GPS loses signal under trees. Readings go missing.
Layer 2 deals with it before anything else sees the data:

- **Throw out bad GPS fixes.** If the satellite geometry is poor or too few
  satellites are visible, the position could be hundreds of metres out. That
  would wrongly trigger the off-route alarm and corrupt the distance total.
- **Throw out impossible readings.** A delivery truck is not doing 200 km/h.
- **Handle gaps.** One missing reading gets filled from the previous one. A long
  gap ends the journey segment, because averaging across a two-minute tunnel
  invents data.
- **Recalculate acceleration** from the change in speed rather than trusting
  what the device sends, because on real hardware that field is whatever the
  device's firmware decided to compute.
- **Summarise recent behaviour:** average speed over 30 seconds, how jerky the
  driving has been, what fraction of the last minute was spent stationary.

One implementation detail that matters: those summaries are kept in a small
rolling buffer in memory, roughly a notepad holding the last two minutes per
truck. The alternative, asking the database every second, would put a disk read
in the middle of the live data path and slow everything down.

**One subtle but important point:** this exact same code runs both when building
the training data and when scoring live traffic. If you build those features
twice, in two places, they drift apart, and a model that scored 0.96 in testing
scores 0.6 in production. We deliberately built this file before the
data-generation script, out of the planned order, to make that impossible.

---

## 8. Spotting bad behaviour (layer 3)

Seven rules run against every reading:

| Alert | Fires when |
|---|---|
| Excessive idling | Stationary with the engine running for 3 minutes |
| Harsh acceleration | Accelerating hard for more than 2 seconds |
| Harsh braking | Braking hard |
| Over-speeding | Over the limit by 10 km/h for 10 seconds |
| Overload | Heavier than the truck is rated for |
| Route deviation | More than 500 m off the planned route for 30 seconds |
| Emission spike | Emitting far more than this truck's own normal |

Three things about how these are written.

**Every message says what actually happened.** Not "anomaly detected", but
"Idling 4m 12s with the engine running. About 0.13 L burned going nowhere." A
driver-facing alert that does not say what happened is noise.

**Every number in the advice is calculated, never typed in.** The original brief
suggested telling drivers that idling wastes "about 0.5 litres per hour". We
checked: with the engine settings in the configuration, it is actually 1.94
litres per hour. Rather than pick which of the two to hard-code, we calculate
it, so the text is arithmetically true whatever the settings say.

**Alerts do not repeat.** A single 4-minute idle would otherwise generate 240
identical rows. Each type is silenced for 60 seconds per truck after firing.

### The emission spike rule is different, and interesting

The other six are fixed thresholds. This one is statistical: it compares a truck
against **its own recent behaviour**. A loaded 16 tonne truck climbing a hill is
not an anomaly. The same emission rate from a small empty van is.

This one took three attempts:

1. **First version** compared against a window that included the spike itself. A
   slowly developing fault dragged its own baseline up with it and never looked
   abnormal. Fixed by comparing against the period *before* the recent past.
2. **Second version** fired on innocent trucks, because the baseline included
   lots of idling. Every truck pulling away from a junction looked alarming.
   Fixed by only comparing moving against moving.
3. **Third version** was then too strict and missed real faults in city
   traffic, because a red light in the middle of a fault reset the timer. Fixed
   so only a genuine return to normal clears it.

Worth noting as an example of how a statistical detector is genuinely harder to
get right than a threshold, and how each fix exposed the next problem.

---

## 9. The report (layer 5)

At the end, this has to produce something an auditor accepts. The relevant
standard is **ISO 14083**, with the industry's GLEC framework.

The headline number is **grams of CO2 per tonne-kilometre**: how much carbon to
move one tonne of goods one kilometre. This is the number that lets you compare
a small van against an articulated lorry fairly, and most fleet dashboards do
not show it.

Three decisions here:

**Fuel is measured, not predicted.** The report uses the truck's own fuel sensor
reading, not our model's estimate. The standard wants measured data where it
exists. The model's estimate is shown next to it for comparison, but the
official number is the measured one. Claiming a prediction as a measurement
would be dishonest.

**The arithmetic has to survive a calculator.** Somebody will check that the
intensity column equals the CO2 column divided by the tonne-kilometre column.
Early on it was off in the fourth decimal place, because we calculated the
intensity from full-precision values and then printed rounded ones. Small,
meaningless, and exactly what a reviewer notices. We now calculate from the
printed values, and the acceptance test checks every row.

**Sometimes the answer is "not defined".** A truck that is stationary or empty
has done zero tonne-kilometres. Dividing by zero is not a small number, it is
no answer at all. Showing 0 would look like a perfect score. We say "not
defined".

---

## 10. The dashboard (layer 5)

One page, four tabs, no build step. You open a file and it works.

- **Live Monitor** - map with a dot per truck coloured by emission rate,
  six headline figures, a live chart, and the alert feed.
- **Models** - the seven-model comparison table, read straight from the
  training output. Nothing on this tab is typed in.
- **Trips and Reports** - every journey with its emissions and efficiency, and
  the export button.
- **Architecture** - the five layers, showing which is simulated.

Three deliberate decisions:

**The amber "SIMULATOR" badge never goes away.** Anyone looking at this screen
learns the sensor layer is simulated without having to ask. Putting that in an
appendix would read as hiding it. Putting it in the header reads as rigour.

**It works with the internet off.** Venue wifi fails. Every library, font and
icon is stored inside the project rather than fetched from the web. The map
tiles are the one exception, and when they fail the map draws a grey grid so
the routes and trucks stay readable instead of the page looking broken.

**A dot shows whether the connection is alive.** If the link drops, a live
dashboard and a fleet of parked trucks look identical. The dot is the
difference, and the page reconnects on its own.

---

## 11. What each file does

**Settings**
- `config.py` - every number the system uses, each with a note on where it came
  from. One place to look, one place to change.

**The simulator (the stand-in for hardware)**
- `simulator/physics.py` - the force, power and fuel calculations.
- `simulator/routes.py` - four real routes around Mangalore, with their hills.
- `simulator/vehicle.py` - one truck: where it is, how fast, whether it is
  stopped at a junction or loading.
- `simulator/scenarios.py` - the faults you can trigger on demand for a demo.
- `simulator/run_simulator.py` - starts it all and prints a line per reading.

**The boundary**
- `adapters/base.py` - the contract. The single swap point.
- `adapters/simulator_adapter.py` - the simulator honouring that contract.
- `adapters/esp32_adapter.py` - the real hardware version, documented, not built.

**The server**
- `backend/preprocess.py` - cleaning and summarising. The only place features
  are built.
- `backend/inference.py` - loads the model, scores each reading, times itself.
- `backend/anomalies.py` - the seven rules.
- `backend/reporting.py` - journey totals and the ISO 14083 export.
- `backend/db.py` - storage, writing in batches rather than one at a time.
- `backend/main.py` - the web server and the live connections.

**Machine learning**
- `ml/generate_dataset.py` - runs the simulator fast to build training data.
- `ml/train_models.py` - trains the seven, compares them, saves the winner.
- `ml/metrics.json` - the results. What the Models tab displays.

**The page**
- `static/index.html`, `app.js`, `styles.css` - the dashboard.
- `static/vendor/` - the libraries and fonts, stored locally so it works offline.

**Checks**
- `scripts/gate_a_physics.py` - is the physics right?
- `scripts/acceptance.py` - does the whole thing work end to end?

---

## 12. The numbers, and how to prove them

Three figures will get asked about. All three are measured by running the
system, never estimated.

| | Value | What it means |
|---|---|---|
| **Accuracy** | R2 = 0.9845 | Explains 98.45% of fuel variation, on journeys it has never seen |
| **Alert speed** | 1.2 ms average | From the reading arriving to the alert on screen. The target was 3000 ms |
| **Prediction speed** | 0.88 ms | Per reading. One truck needs one of these per second |

To prove any of them, run `python -m scripts.acceptance` against a running
system. It prints all three from live output.

**A note on alert speed**, because it invites a fair question. The 3-second
target is about the *system*, not the *rules*. The idling alert is defined as
three minutes of continuous idling, so it can never fire sooner than three
minutes, and a version that did would be detecting something different. What we
measure against 3 seconds is the gap between the evidence arriving and the
alert appearing. Both figures are reported separately so neither is hidden.

---

## 13. What this is not

Stated plainly, because being asked and having an answer beats being caught out.

- **The sensor layer is simulated.** Said in the header, the README's opening
  paragraph, and on the Architecture tab.
- **The models have never seen a real truck.** They are trained on physics-
  derived data. The physics is the standard one and the sensor readings are the
  right shape, so they should transfer, but they must be re-validated against a
  few hundred hours of real dongle data before anyone quotes the accuracy
  figure about real vehicles.
- **The routes are hand-drawn.** They follow the real corridors and are the
  right length, but they are not surveyed road geometry.
- **There is no login.** Anyone who can reach the page can see everything. Fine
  for a demo, not for deployment.
- **One server, one file database.** Six trucks is comfortable. Six hundred
  would need rethinking layer 4.
- **The accuracy figure is slightly above the range originally expected.** The
  brief anticipated 0.93 to 0.98 and we get 0.9845. We looked hard at whether
  that was another leak. It is not: no single input reconstructs the answer, and
  removing the strongest one costs only 0.017. Engine load, throttle and engine
  speed genuinely are strong physical predictors of fuel use once the air path
  is modelled properly.

---

## 14. If you remember one thing

The interesting work here was not writing the code. It was the three occasions
where the code ran perfectly and was wrong anyway:

- A 99.6% accurate model that was doing a single multiplication.
- A fault detector that raised the alarm on healthy trucks and stayed silent on
  broken ones.
- A dashboard that reported itself completely healthy while showing the user
  nothing but unstyled text.

None of those were found by running the program and looking at the output. All
three came from trying to disprove something we had assumed, and building a
permanent check once we knew what to look for.

That is why `gate_a_physics.py` and `acceptance.py` exist, and why they are
written to fail loudly. They are not tests added at the end to tick a box. They
are the reason the numbers in this project can be trusted.
