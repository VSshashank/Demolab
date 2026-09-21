"""Injectable fault scenarios.

The point of these is that a demo cannot wait for a 3-minute idle event to
occur naturally. Each scenario perturbs one physical input to the vehicle model
for a bounded duration; none of them writes an alert directly. The alert has to
come out of the rule engine in backend/anomalies.py looking at the resulting
telemetry, or the demo is proving nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

import config

SCENARIO_TYPES = (
    "IDLE_EVENT",
    "HARSH_ACCEL",
    "HARSH_BRAKE",
    "OVERLOAD",
    "ROUTE_DEVIATION",
    "EMISSION_SPIKE",
)

# Defaults per scenario: how long it holds, the knob it turns, and how long the
# corresponding RULE needs before it will fire.
#
# That last field matters and is easy to get wrong. The acceptance target of
# "an alert within 3 seconds" is a statement about pipeline latency, not about
# the rules: EXCESSIVE_IDLING is defined as 180 seconds of continuous idling,
# so no amount of fast plumbing can raise it sooner, and a build that did would
# be reporting a different thing than the one it claims to detect. What is
# measured against 3 seconds is the time from the condition being satisfied to
# the alert reaching the browser.
SCENARIO_DEFAULTS: dict[str, dict] = {
    "IDLE_EVENT": {
        "rule_dwell_s": config.IDLE_ALERT_SECONDS,
        "seconds": 240,
        "label": "Forced stop with the engine running",
        "expect": "EXCESSIVE_IDLING after 180 s continuous below 3 km/h",
    },
    "HARSH_ACCEL": {
        "rule_dwell_s": config.HARSH_ACCEL_SUSTAIN_S,
        # Holds an arming window, not just the 4 s of force. A vehicle already
        # at its target speed cannot accelerate hard, so the scenario waits for
        # a moment when it can rather than silently doing nothing. The window
        # outlasts a loading stop (up to 300 s) because otherwise the button
        # does nothing at all when the target happens to be parked.
        "seconds": 420,
        "force_seconds": 4,
        "max_speed_ms": 30.0,   # only stand down near the top of the range
        "accel_ms2": 3.5,
        "label": "Hard launch at 3.5 m/s^2",
        "expect": "HARSH_ACCELERATION once sustained past 2 s",
    },
    "HARSH_BRAKE": {
        "rule_dwell_s": 0,
        # Same arming logic, for the opposite reason: you cannot decelerate at
        # 4 m/s^2 for three seconds from 12 km/h, and you certainly cannot do it
        # from a standstill at a loading bay.
        "seconds": 420,
        "force_seconds": 3,
        "min_speed_ms": 6.0,
        "accel_ms2": -4.0,
        "label": "Emergency braking at -4.0 m/s^2",
        "expect": "HARSH_BRAKING on the first sample past -3.0 m/s^2",
    },
    "OVERLOAD": {
        "rule_dwell_s": 0,
        "seconds": 300,
        "factor": 1.25,
        "label": "Payload pushed 25 % over rated GVW",
        "expect": "OVERLOAD immediately, and a visibly higher fuel rate",
    },
    "ROUTE_DEVIATION": {
        "rule_dwell_s": config.ROUTE_DEVIATION_SUSTAIN_S + 12,  # plus route-match voting
        "seconds": 120,
        "offset_m": 800.0,
        "label": "GPS displaced 800 m perpendicular to the planned route",
        "expect": "ROUTE_DEVIATION after 30 s beyond 500 m",
    },
    "EMISSION_SPIKE": {
        # The detector needs a clean baseline before it can call anything
        # anomalous, which on a freshly started backend takes a couple of
        # minutes of observing the vehicle. Held long enough to cover that.
        "rule_dwell_s": config.EMISSION_SPIKE_MIN_BASELINE_S + config.EMISSION_SPIKE_BASELINE_LAG_S,
        "seconds": 420,
        "driveline_efficiency": 0.60,
        "label": "Driveline efficiency collapses to 0.60 (simulated fault)",
        "expect": "EMISSION_SPIKE once the rate clears the 10-min rolling mean + 3 sigma",
    },
}


@dataclass
class ActiveScenario:
    """A scenario currently applied to one vehicle, counting itself down."""

    type: str
    remaining_s: float
    params: dict
    forced_s: float = 0.0     # seconds for which the condition was actually applied
    announced: bool = False   # has the "condition is now live" frame been sent

    def may_force(self, speed_ms: float) -> bool:
        """Is the vehicle in a state where this scenario can bite?

        Returns False while the scenario is armed but the vehicle is not moving
        in a way that makes the forced condition physically possible.
        """
        budget = self.params.get("force_seconds")
        if budget is not None and self.forced_s >= budget:
            return False
        if speed_ms < self.params.get("min_speed_ms", 0.0):
            return False
        if speed_ms > self.params.get("max_speed_ms", float("inf")):
            return False
        return True

    def tick(self, dt: float) -> bool:
        """Advance by dt. Returns False once the scenario has expired."""
        self.remaining_s -= dt
        return self.remaining_s > 0.0


class ScenarioBook:
    """Per-vehicle scenario registry.

    One active scenario per type per vehicle: re-injecting the same type simply
    restarts its clock rather than stacking, which is what someone jabbing the
    button twice during a demo actually wants.
    """

    def __init__(self) -> None:
        self._active: dict[str, dict[str, ActiveScenario]] = {}

    def inject(self, vehicle_id: str, scenario: str, **overrides) -> ActiveScenario:
        if scenario not in SCENARIO_TYPES:
            raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIO_TYPES}")
        params = dict(SCENARIO_DEFAULTS[scenario])
        params.update({k: v for k, v in overrides.items() if v is not None})
        active = ActiveScenario(type=scenario, remaining_s=float(params["seconds"]), params=params)
        self._active.setdefault(vehicle_id, {})[scenario] = active
        return active

    def tick(self, vehicle_id: str, dt: float) -> None:
        book = self._active.get(vehicle_id)
        if not book:
            return
        for name in [n for n, s in book.items() if not s.tick(dt)]:
            del book[name]

    def active_for(self, vehicle_id: str) -> dict[str, ActiveScenario]:
        return self._active.get(vehicle_id, {})

    def get(self, vehicle_id: str, scenario: str) -> ActiveScenario | None:
        return self._active.get(vehicle_id, {}).get(scenario)

    def clear(self, vehicle_id: str | None = None) -> None:
        if vehicle_id is None:
            self._active.clear()
        else:
            self._active.pop(vehicle_id, None)

    def describe(self) -> list[dict]:
        """Flat view for /api/health and the scenario panel."""
        return [
            {"vehicle_id": vid, "type": s.type, "remaining_s": round(s.remaining_s, 1),
             "label": s.params.get("label", "")}
            for vid, book in self._active.items()
            for s in book.values()
        ]


# Shared by the simulator process. The backend does not import this -- it posts
# to /api/scenario, which relays over the ingest socket, so the same code path
# works whether the packet source is the simulator or a real dongle fleet.
BOOK = ScenarioBook()
