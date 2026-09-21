"""L4: transport, fan-out and the HTTP surface.

The ingest socket is bidirectional. Packets come up it; scenario injections go
back down it. That matters for the hardware story: POST /api/scenario does not
reach into the simulator's process, it publishes a control frame on the same
link a real broker would carry, so the path is unchanged when the dongles
arrive (at which point the simulator refuses the frame, which is correct, since
you cannot inject a fault into a real truck from a web page).

Every number this module reports is measured. Packets per second is counted
over a rolling window, alert latency is timestamped from injection to
broadcast, and inference latency comes from the predictor's own timing. None of
the three is estimated, because all three get asked about.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

import config
from adapters.base import SchemaViolation, validate_packet
from backend import reporting
from backend.anomalies import AnomalyEngine
from backend.db import Database
from backend.inference import FuelRatePredictor
from backend.preprocess import Rejection, StreamPreprocessor
from simulator import routes as route_lib
from simulator.scenarios import SCENARIO_DEFAULTS, SCENARIO_TYPES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)-12s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backend")


class Hub:
    """Fan-out to dashboard sockets.

    A slow or dead browser tab must never stall ingest, so a failed send drops
    that subscriber rather than propagating the exception upstream.
    """

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def register(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)
        log.info("dashboard connected (%d live)", len(self.clients))

    def drop(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        if not self.clients:
            return
        text = json.dumps(payload, separators=(",", ":"), default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001 - a dropped tab is not an error here
                dead.append(ws)
        for ws in dead:
            self.drop(ws)


class State:
    """Everything the app owns. One instance, created in the lifespan."""

    def __init__(self) -> None:
        self.db = Database()
        self.pre = StreamPreprocessor()
        self.predictor = FuelRatePredictor()
        self.anomalies = AnomalyEngine()
        self.trips = reporting.TripRegistry(self.db)
        self.hub = Hub()

        self.started_at = time.time()
        self.packet_times: deque = deque(maxlen=600)
        self.vehicles: dict[str, dict] = {}
        self.co2_cumulative_g: dict[str, float] = {}
        self.ingest_sockets: set[WebSocket] = set()

        # Injection time per (vehicle, scenario) so alert latency is measured
        # end to end rather than guessed at.
        self.pending_injections: dict[tuple, float] = {}
        # When the simulator reported the forced condition actually biting. For
        # an armed scenario this is later than the injection, and it is the
        # honest origin for a pipeline-latency measurement.
        self.condition_live_at: dict[tuple, float] = {}
        # Packet in to alert on the dashboard. What the system controls.
        self.pipeline_latencies_ms: deque = deque(maxlen=200)
        # Injection to alert. Includes each rule's own dwell window by design.
        self.alert_latencies_ms: deque = deque(maxlen=100)
        self.last_alert_latency: dict | None = None
        # One record per closed injection, so the acceptance gate and the demo
        # can show the whole table rather than only the most recent number.
        self.latency_records: deque = deque(maxlen=60)

    def packets_per_second(self) -> float:
        now = time.time()
        recent = [t for t in self.packet_times if now - t <= 10.0]
        return round(len(recent) / 10.0, 2)

    def close(self) -> None:
        self.trips.flush()
        self.db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.ctx = State()
    flusher = asyncio.create_task(_periodic_flush(app.state.ctx))
    log.info("backend up; model_loaded=%s", app.state.ctx.predictor.model is not None)
    try:
        yield
    finally:
        flusher.cancel()
        app.state.ctx.close()


async def _periodic_flush(ctx: State) -> None:
    """Time-based half of the write batching. Without it a quiet fleet would
    leave the last few rows unwritten indefinitely."""
    while True:
        await asyncio.sleep(config.DB_BATCH_SECONDS)
        try:
            ctx.db.flush()
            ctx.trips.flush()
        except Exception:  # noqa: BLE001
            log.exception("flush failed")


app = FastAPI(title="FleetCarbon", version="1.0", lifespan=lifespan)


def ctx(request: Request) -> State:
    return request.app.state.ctx


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------
@app.websocket("/ws/ingest")
async def ws_ingest(ws: WebSocket) -> None:
    await ws.accept()
    state: State = ws.app.state.ctx
    state.ingest_sockets.add(ws)
    log.info("telemetry source connected")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Control frames from the source share the socket with telemetry.
            if message.get("kind") == "scenario_live":
                key = (message.get("vehicle_id"), message.get("scenario"))
                if key in state.pending_injections:
                    state.condition_live_at[key] = time.time()
                    log.info("scenario condition live: %s on %s", key[1], key[0])
                continue
            try:
                packet = validate_packet(message)
            except SchemaViolation as exc:
                log.warning("rejected packet at the adapter boundary: %s", exc)
                continue
            await handle_packet(state, packet)
    except WebSocketDisconnect:
        log.info("telemetry source disconnected")
    finally:
        state.ingest_sockets.discard(ws)


async def handle_packet(state: State, packet: dict) -> None:
    """L2 -> L3 -> L4 for one packet."""
    t_recv = time.time()
    state.packet_times.append(t_recv)

    result = state.pre.process(packet)
    if isinstance(result, Rejection):
        # Rejections are counted, not broadcast. The health endpoint carries
        # the breakdown so a quality problem is visible without spamming the UI.
        return
    sample = result

    prediction = state.predictor.predict(sample)
    alerts = state.anomalies.evaluate(sample, prediction)

    route_key = state.anomalies.state(sample.vehicle_id).route_key
    trip = state.trips.add(sample, prediction, route=route_key)

    cumulative_g = state.co2_cumulative_g.get(sample.vehicle_id, 0.0)
    cumulative_g += prediction.co2_gps * min(sample.dt_s, config.MAX_GAP_SECONDS)
    state.co2_cumulative_g[sample.vehicle_id] = cumulative_g

    # CO2 implied by the vehicle's own fuel-rate sensor, for side-by-side
    # comparison with the model. Both exist on real hardware.
    fuel_spec = config.FUEL_SPECS[sample.fuel_type]
    measured_gps = (sample.fuel_rate_lph / 3600.0) * fuel_spec["ef_ttw_kg_per_l"] * 1000.0

    state.db.queue_telemetry({
        "ts": sample.ts.isoformat().replace("+00:00", "Z"),
        "vehicle_id": sample.vehicle_id, "trip_id": sample.trip_id,
        "lat": sample.lat, "lon": sample.lon, "speed_kmh": sample.speed_kmh,
        "accel_ms2": sample.accel_ms2, "rpm": sample.rpm,
        "engine_load_pct": sample.engine_load_pct, "payload_kg": sample.payload_kg,
        "gvw_kg": sample.gvw_kg, "fuel_rate_lph": sample.fuel_rate_lph,
        "co2_gps_pred": prediction.co2_gps, "co2_gps_truth": measured_gps,
        "vehicle_type": sample.vehicle_type, "fuel_type": sample.fuel_type,
        "driver_id": sample.driver_id, "source": sample.source,
    })

    stored_alerts = []
    for alert in alerts:
        alert["id"] = state.db.insert_alert(alert)
        stored_alerts.append(alert)
        try:
            _record_alert_latency(state, alert)
        except Exception:  # noqa: BLE001
            # Latency bookkeeping is instrumentation. A bug in it must never
            # take down telemetry ingest: an exception here used to propagate
            # out of the websocket handler and drop the source's connection,
            # which then rejected the next scenario injection. Measuring the
            # system is not allowed to break the system.
            log.exception("alert latency bookkeeping failed; ingest continues")

    snapshot = {
        "vehicle_id": sample.vehicle_id,
        "device_id": sample.device_id,
        "trip_id": sample.trip_id,
        "ts": sample.ts.isoformat().replace("+00:00", "Z"),
        "lat": sample.lat, "lon": sample.lon, "alt_m": sample.alt_m,
        "heading_deg": sample.heading_deg,
        "speed_kmh": sample.speed_kmh, "accel_ms2": round(sample.accel_ms2, 3),
        "rpm": sample.rpm, "engine_load_pct": sample.engine_load_pct,
        "throttle_pct": sample.throttle_pct, "coolant_temp_c": sample.coolant_temp_c,
        "fuel_rate_lph": sample.fuel_rate_lph,
        "payload_kg": sample.payload_kg, "gvw_kg": sample.gvw_kg,
        "rated_gvw_kg": sample.rated_gvw_kg, "load_ratio": round(sample.load_ratio, 4),
        "vehicle_type": sample.vehicle_type, "fuel_type": sample.fuel_type,
        "driver_id": sample.driver_id, "source": sample.source,
        "dtc_count": sample.dtc_count,
        "idle_flag": sample.idle_flag,
        "stop_go_ratio_60s": round(sample.stop_go_ratio_60s, 3),
        "route_key": route_key,
        "co2_gps_pred": round(prediction.co2_gps, 4),
        "co2_gps_measured": round(measured_gps, 4),
        "fuel_rate_gps_pred": round(prediction.fuel_rate_gps, 4),
        "prediction_source": prediction.source,
        "co2_cumulative_kg": round(cumulative_g / 1000.0, 5),
        "inference_ms": round(prediction.inference_ms, 4),
        "trip_distance_km": round(trip.distance_km, 3),
        "trip_fuel_l": round(trip.fuel_l, 3),
        "trip_intensity_g_per_tkm": round(trip.intensity_g_per_tkm, 2),
        "trip_idle_seconds": round(trip.idle_seconds, 0),
        "behaviour_score": trip.behaviour_score,
    }
    state.vehicles[sample.vehicle_id] = snapshot

    await state.hub.broadcast({"kind": "telemetry", **snapshot, "new_alerts": stored_alerts})

    # Pipeline latency: the packet carrying the anomaly arrives, and the alert
    # reaches the browser. Preprocess, inference, rule evaluation, persistence
    # and fan-out, and nothing else.
    #
    # This is deliberately not "time since the button was pressed". Injecting
    # IDLE_EVENT into a truck at 70 km/h cannot raise EXCESSIVE_IDLING for at
    # least 180 seconds, because the rule is defined as 180 seconds of
    # continuous idling and the truck has to stop first. Charging the rule's
    # own definition to the transport layer would make the number meaningless
    # in both directions: flattering for fast rules, damning for slow ones.
    if stored_alerts:
        state.pipeline_latencies_ms.append((time.time() - t_recv) * 1000.0)


def _record_alert_latency(state: State, alert: dict) -> None:
    """Close the loop on an injected scenario, if this alert answers one."""
    expected = {
        "IDLE_EVENT": "EXCESSIVE_IDLING", "HARSH_ACCEL": "HARSH_ACCELERATION",
        "HARSH_BRAKE": "HARSH_BRAKING", "OVERLOAD": "OVERLOAD",
        "ROUTE_DEVIATION": "ROUTE_DEVIATION", "EMISSION_SPIKE": "EMISSION_SPIKE",
    }
    for (vehicle_id, scenario), injected_at in list(state.pending_injections.items()):
        if vehicle_id != alert["vehicle_id"] or expected.get(scenario) != alert["type"]:
            continue
        now = time.time()
        key = (vehicle_id, scenario)
        live_at = state.condition_live_at.pop(key, None)
        dwell_s = SCENARIO_DEFAULTS[scenario]["rule_dwell_s"]
        total_ms = (now - injected_at) * 1000.0
        # Time from the forced condition actually biting to the alert. Still
        # contains the rule's dwell window, which is reported alongside it so
        # the two can be told apart.
        since_live_ms = (now - live_at) * 1000.0 if live_at else None
        state.alert_latencies_ms.append(total_ms)
        state.last_alert_latency = {
            "vehicle_id": vehicle_id, "scenario": scenario, "alert_type": alert["type"],
            "total_ms": round(total_ms, 1),
            "since_condition_live_ms": round(since_live_ms, 1) if since_live_ms else None,
            "rule_dwell_s": dwell_s,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        state.latency_records.append(state.last_alert_latency)
        del state.pending_injections[key]
        log.info("%s -> %s: %.0f ms from injection (rule dwell %ss, %s since the "
                 "condition went live)", scenario, alert["type"], total_ms, dwell_s,
                 f"{since_live_ms:.0f} ms" if since_live_ms else "not reported")
        break


# ---------------------------------------------------------------------------
# Live fan-out
# ---------------------------------------------------------------------------
@app.websocket("/ws/live")
async def ws_live(ws: WebSocket) -> None:
    state: State = ws.app.state.ctx
    await state.hub.register(ws)
    try:
        # Seed the tab with current state so a late joiner is not staring at an
        # empty map until the next tick.
        await ws.send_text(json.dumps({
            "kind": "snapshot",
            "vehicles": list(state.vehicles.values()),
            "alerts": state.db.recent_alerts(limit=40),
            "routes": _routes_payload(),
            "fleet": state.trips.fleet_totals(),
        }, default=str))
        while True:
            await ws.receive_text()  # client keepalives; nothing to act on
    except WebSocketDisconnect:
        pass
    finally:
        state.hub.drop(ws)
        log.info("dashboard disconnected (%d live)", len(state.hub.clients))


def _routes_payload() -> list[dict]:
    return [{
        "key": r.key, "name": r.name, "description": r.description,
        "length_km": round(r.length_km, 2), "speed_limit_kmh": r.default_limit_kmh,
        "polyline": r.polyline(),
    } for r in route_lib.ROUTES.values()]


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------
@app.get("/api/vehicles")
def api_vehicles(request: Request):
    state = ctx(request)
    return {"vehicles": list(state.vehicles.values()), "fleet": state.trips.fleet_totals()}


@app.get("/api/vehicle/{vehicle_id}/history")
def api_vehicle_history(vehicle_id: str, request: Request, minutes: int = Query(30, ge=1, le=240)):
    rows = ctx(request).db.vehicle_history(vehicle_id, minutes=minutes)
    if not rows:
        raise HTTPException(404, f"no telemetry recorded for {vehicle_id}")
    return {"vehicle_id": vehicle_id, "minutes": minutes, "points": rows}


@app.get("/api/routes")
def api_routes():
    return {"routes": _routes_payload()}


@app.get("/api/alerts")
def api_alerts(request: Request, limit: int = Query(50, ge=1, le=500),
               vehicle_id: str | None = None):
    return {"alerts": ctx(request).db.recent_alerts(limit=limit, vehicle_id=vehicle_id)}


@app.post("/api/alerts/{alert_id}/ack")
def api_ack(alert_id: int, request: Request):
    if not ctx(request).db.ack_alert(alert_id):
        raise HTTPException(404, f"no alert {alert_id}")
    return {"ok": True, "id": alert_id}


@app.get("/api/trips")
def api_trips(request: Request, limit: int = Query(100, ge=1, le=500)):
    state = ctx(request)
    state.trips.flush()
    return {"trips": state.db.list_trips(limit=limit)}


@app.get("/api/trip/{trip_id}")
def api_trip(trip_id: str, request: Request):
    state = ctx(request)
    state.trips.flush()
    trip = state.db.get_trip(trip_id)
    if trip is None:
        raise HTTPException(404, f"no trip {trip_id}")
    live = state.trips.trips.get(trip_id)
    return {
        "trip": trip,
        "iso14083": reporting.trip_to_iso_row(trip),
        "profile": live.profile if live else [],
        "alerts": state.db.trip_alerts(trip_id),
    }


@app.get("/api/metrics")
def api_metrics():
    """Served verbatim from ml/metrics.json.

    Deliberately a passthrough: the Models tab must show what training actually
    produced. Any transformation here is a place for a number to drift away
    from the file it claims to come from.
    """
    path = Path(config.METRICS_PATH)
    if not path.exists():
        raise HTTPException(503, "ml/metrics.json not found. Run: python -m ml.train_models")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/models/scatter")
def api_scatter():
    path = config.MODEL_DIR / "predicted_vs_actual.json"
    if not path.exists():
        raise HTTPException(503, "predicted_vs_actual.json not found. Run training first.")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/report/iso14083")
def api_report(request: Request, trip_id: str | None = None, fmt: str = Query("csv", alias="format"),
               start: str | None = None, end: str | None = None):
    state = ctx(request)
    state.trips.flush()

    if trip_id:
        trip = state.db.get_trip(trip_id)
        if trip is None:
            raise HTTPException(404, f"no trip {trip_id}")
        trips = [trip]
        stem = trip_id
    else:
        trips = state.db.trips_between(start, end)
        stem = f"fleet_{datetime.now(timezone.utc):%Y%m%d}"
    if not trips:
        raise HTTPException(404, "no trips in range")

    if fmt == "pdf":
        try:
            body = reporting.build_pdf(trips)
        except ImportError:
            raise HTTPException(501, "reportlab is not installed; use format=csv")
        return Response(body, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="iso14083_{stem}.pdf"'})

    return PlainTextResponse(reporting.build_csv(trips), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="iso14083_{stem}.csv"'})


@app.post("/api/scenario")
async def api_scenario(request: Request):
    state = ctx(request)
    body = await request.json()
    vehicle_id, scenario = body.get("vehicle_id"), body.get("scenario")
    if scenario not in SCENARIO_TYPES:
        raise HTTPException(400, f"unknown scenario {scenario!r}; expected {list(SCENARIO_TYPES)}")
    if not vehicle_id:
        raise HTTPException(400, "vehicle_id is required")
    if not state.ingest_sockets:
        raise HTTPException(503, "no telemetry source connected; start the simulator")

    frame = json.dumps({"kind": "scenario", "vehicle_id": vehicle_id, "scenario": scenario,
                        **{k: v for k, v in body.items()
                           if k not in ("vehicle_id", "scenario")}})
    state.pending_injections[(vehicle_id, scenario)] = time.time()
    sent = 0
    for ws in list(state.ingest_sockets):
        try:
            await ws.send_text(frame)
            sent += 1
        except Exception:  # noqa: BLE001
            state.ingest_sockets.discard(ws)
    if not sent:
        state.pending_injections.pop((vehicle_id, scenario), None)
        raise HTTPException(503, "telemetry source went away before the frame was sent")
    defaults = SCENARIO_DEFAULTS[scenario]
    # A kinematic scenario cannot bite on a parked vehicle. Say so up front
    # rather than letting the operator watch a button that looks ignored.
    snapshot = state.vehicles.get(vehicle_id, {})
    speed_ms = float(snapshot.get("speed_kmh", 0.0)) / 3.6
    armed = (speed_ms < defaults.get("min_speed_ms", 0.0)
             or speed_ms > defaults.get("max_speed_ms", float("inf")))
    return {"ok": True, "vehicle_id": vehicle_id, "scenario": scenario,
            "expect": defaults["expect"],
            "duration_s": defaults["seconds"],
            "rule_dwell_s": defaults["rule_dwell_s"],
            "armed_waiting": armed,
            "note": ("Armed. This vehicle is not moving in a way that makes the "
                     "condition possible yet, so it will apply as soon as it is."
                     if armed else "Condition applied immediately.")}


@app.get("/api/scenarios")
def api_scenario_catalogue():
    return {"scenarios": [{"type": t, **SCENARIO_DEFAULTS[t]} for t in SCENARIO_TYPES]}


@app.get("/api/health")
def api_health(request: Request):
    state = ctx(request)
    lat = sorted(state.alert_latencies_ms)
    pipe = sorted(state.pipeline_latencies_ms)

    def q(values, p):
        return round(values[min(len(values) - 1, int(len(values) * p))], 2) if values else None

    return {
        "status": "ok",
        "uptime_s": round(time.time() - state.started_at, 1),
        "packets_per_second": state.packets_per_second(),
        "telemetry_sources": len(state.ingest_sockets),
        "dashboard_clients": len(state.hub.clients),
        "db_rows": state.db.telemetry_count(),
        "active_alerts": state.db.active_alert_count(),
        "vehicles_seen": len(state.vehicles),
        "inference": state.predictor.stats(),
        "preprocess": state.pre.stats(),
        "anomalies": state.anomalies.stats(),
        # The number measured against the 3 s target.
        "pipeline_latency": {
            "measures": "anomalous packet received to alert delivered to the browser",
            "samples": len(pipe),
            "mean_ms": round(sum(pipe) / len(pipe), 2) if pipe else None,
            "p50_ms": q(pipe, 0.50),
            "p95_ms": q(pipe, 0.95),
            "max_ms": round(max(pipe), 2) if pipe else None,
            "target_ms": 3000,
        },
        # Injection to alert. Longer by design: a rule defined as 180 seconds of
        # continuous idling cannot fire sooner than that, whatever the plumbing.
        "alert_latency": {
            "measures": "scenario injected to alert delivered; includes each rule's dwell window",
            "samples": len(lat),
            "mean_ms": round(sum(lat) / len(lat), 1) if lat else None,
            "p95_ms": q(lat, 0.95),
            "max_ms": round(max(lat), 1) if lat else None,
            "last": state.last_alert_latency,
            "records": list(state.latency_records),
        },
        "fleet": state.trips.fleet_totals(),
    }


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")
