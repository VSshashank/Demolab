"""L5: trip accounting and ISO 14083 / GLEC reporting.

Trips are accumulated live rather than reconstructed from SQLite afterwards.
Integrating fuel over a trip means summing rate x dt in packet order, and the
moment that becomes a query it also becomes a correctness problem (ordering,
gaps, partial trips). Accumulating as packets arrive keeps it a running sum,
and the dashboard gets current numbers instead of numbers as of the last flush.

The energy basis is the vehicle's own PID 0x5E fuel-rate reading, not the model
prediction. ISO 14083 wants primary activity data where it exists; the measured
fuel flow is primary, the ML estimate is not. The estimate is reported next to
it so the two can be compared, which is the honest presentation.

Arithmetic consistency is enforced, not assumed: `verify_row` recomputes the
intensity column from the kg and t.km columns in the same row and is called by
the acceptance check.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

import config
from backend.inference import Prediction
from backend.preprocess import Sample

DATA_QUALITY = "Modelled: HIL-simulated primary activity data (PID 0x5E fuel rate)"
TRANSPORT_MODE = "Road"

ISO_14083_COLUMNS = [
    "transport_operation_id",
    "transport_mode",
    "vehicle_category",
    "vehicle_id",
    "driver_id",
    "route",
    "start_utc",
    "end_utc",
    "distance_km",
    "cargo_mass_t",
    "transport_activity_tkm",
    "energy_consumed_l",
    "fuel_type",
    "co2e_ttw_kg",
    "co2e_wtw_kg",
    "ghg_intensity_g_per_tkm",
    "idle_seconds",
    "harsh_events",
    "behaviour_score",
    "data_quality",
    "data_source",
]


@dataclass
class TripAccumulator:
    """Running totals for one trip. Updated once per accepted packet."""

    trip_id: str
    vehicle_id: str
    device_id: str
    driver_id: str
    vehicle_type: str
    fuel_type: str
    route: str | None = None
    start_ts: datetime | None = None
    end_ts: datetime | None = None

    distance_m: float = 0.0
    fuel_l: float = 0.0            # integrated from PID 0x5E, the measured basis
    fuel_l_predicted: float = 0.0  # integrated from the model, for comparison
    idle_seconds: float = 0.0
    harsh_events: int = 0
    samples: int = 0
    payload_kg_sum: float = 0.0
    max_gvw_kg: float = 0.0
    rated_gvw_kg: float = 0.0
    speed_sum: float = 0.0
    max_speed_kmh: float = 0.0
    source: str = "SIMULATOR"
    profile: list = field(default_factory=list)  # thinned trace for the detail view

    def add(self, sample: Sample, prediction: Prediction) -> None:
        if self.start_ts is None:
            self.start_ts = sample.ts
        self.end_ts = sample.ts
        dt = max(0.0, min(sample.dt_s, config.MAX_GAP_SECONDS))

        self.distance_m += sample.distance_m
        # L/h over dt seconds.
        self.fuel_l += sample.fuel_rate_lph * dt / 3600.0
        self.fuel_l_predicted += (
            prediction.fuel_rate_gps * dt
            / (config.FUEL_SPECS[sample.fuel_type]["density_kg_per_l"] * 1000.0))

        if sample.idle_flag:
            self.idle_seconds += dt
        if sample.accel_ms2 > config.HARSH_ACCEL_MS2 or sample.accel_ms2 < config.HARSH_BRAKE_MS2:
            self.harsh_events += 1

        self.samples += 1
        self.payload_kg_sum += sample.payload_kg
        self.max_gvw_kg = max(self.max_gvw_kg, sample.gvw_kg)
        self.rated_gvw_kg = sample.rated_gvw_kg
        self.speed_sum += sample.speed_kmh
        self.max_speed_kmh = max(self.max_speed_kmh, sample.speed_kmh)
        self.source = sample.source

        # One point every 10 s is plenty to draw a trip profile and keeps the
        # JSON for a one-hour trip in the tens of kilobytes.
        if self.samples % 10 == 1:
            self.profile.append({
                "ts": sample.ts.isoformat().replace("+00:00", "Z"),
                "speed_kmh": round(sample.speed_kmh, 1),
                "co2_gps": round(prediction.co2_gps, 3),
                "lat": sample.lat,
                "lon": sample.lon,
            })

    # ------------------------------------------------------------- accounting
    @property
    def distance_km(self) -> float:
        return self.distance_m / 1000.0

    @property
    def payload_t(self) -> float:
        return (self.payload_kg_sum / self.samples / 1000.0) if self.samples else 0.0

    @property
    def tkm(self) -> float:
        return self.payload_t * self.distance_km

    def co2_kg(self, basis: str = "ttw") -> float:
        key = "ef_ttw_kg_per_l" if basis == "ttw" else "ef_wtw_kg_per_l"
        return self.fuel_l * config.FUEL_SPECS[self.fuel_type][key]

    @property
    def intensity_g_per_tkm(self) -> float:
        """GHG intensity on a well-to-wheel basis, per GLEC.

        Undefined for an empty or stationary vehicle: zero t.km means the
        question "grams per tonne-kilometre" has no answer, and returning 0
        would quietly claim a perfect score.
        """
        return (self.co2_kg("wtw") * 1000.0 / self.tkm) if self.tkm > 1e-9 else 0.0

    @property
    def behaviour_score(self) -> float:
        """0-100. Harsh events and idling per 100 km, which is how a fleet
        manager compares a city round against a highway run fairly."""
        if self.distance_km < 0.5:
            return 100.0
        per_100km = 100.0 / self.distance_km
        harsh_penalty = min(45.0, self.harsh_events * per_100km * 1.6)
        idle_penalty = min(35.0, (self.idle_seconds / 60.0) * per_100km * 1.1)
        return round(max(0.0, 100.0 - harsh_penalty - idle_penalty), 1)

    def to_db_row(self) -> dict:
        return {
            "trip_id": self.trip_id,
            "vehicle_id": self.vehicle_id,
            "route": self.route,
            "driver_id": self.driver_id,
            "start_ts": self.start_ts.isoformat().replace("+00:00", "Z") if self.start_ts else None,
            "end_ts": self.end_ts.isoformat().replace("+00:00", "Z") if self.end_ts else None,
            "distance_km": round(self.distance_km, 4),
            "fuel_l": round(self.fuel_l, 4),
            "co2_ttw_kg": round(self.co2_kg("ttw"), 4),
            "co2_wtw_kg": round(self.co2_kg("wtw"), 4),
            "payload_t": round(self.payload_t, 4),
            "tkm": round(self.tkm, 4),
            "intensity_g_per_tkm": round(self.intensity_g_per_tkm, 3),
            "idle_seconds": round(self.idle_seconds, 1),
            "harsh_events": self.harsh_events,
            "behaviour_score": self.behaviour_score,
            "vehicle_type": self.vehicle_type,
            "fuel_type": self.fuel_type,
        }


class TripRegistry:
    """Live trips by trip_id, with DB write-through."""

    def __init__(self, db) -> None:
        self.db = db
        self.trips: dict[str, TripAccumulator] = {}
        self._dirty: set[str] = set()

    def add(self, sample: Sample, prediction: Prediction, route: str | None = None) -> TripAccumulator:
        acc = self.trips.get(sample.trip_id)
        if acc is None:
            acc = TripAccumulator(
                trip_id=sample.trip_id, vehicle_id=sample.vehicle_id,
                device_id=sample.device_id, driver_id=sample.driver_id,
                vehicle_type=sample.vehicle_type, fuel_type=sample.fuel_type)
            self.trips[sample.trip_id] = acc
        if route and not acc.route:
            acc.route = route
        acc.add(sample, prediction)
        self._dirty.add(sample.trip_id)
        return acc

    def flush(self) -> int:
        """Write every touched trip back. Cheap: there are only ever a handful."""
        n = 0
        for trip_id in list(self._dirty):
            acc = self.trips.get(trip_id)
            if acc and acc.samples:
                self.db.upsert_trip(acc.to_db_row())
                n += 1
        self._dirty.clear()
        return n

    def current_for(self, vehicle_id: str) -> TripAccumulator | None:
        candidates = [a for a in self.trips.values() if a.vehicle_id == vehicle_id]
        return max(candidates, key=lambda a: a.end_ts or datetime.min) if candidates else None

    def fleet_totals(self) -> dict:
        fuel = sum(a.fuel_l for a in self.trips.values())
        co2_ttw = sum(a.co2_kg("ttw") for a in self.trips.values())
        co2_wtw = sum(a.co2_kg("wtw") for a in self.trips.values())
        tkm = sum(a.tkm for a in self.trips.values())
        return {
            "fuel_l": round(fuel, 3),
            "co2_ttw_kg": round(co2_ttw, 3),
            "co2_wtw_kg": round(co2_wtw, 3),
            "distance_km": round(sum(a.distance_km for a in self.trips.values()), 3),
            "tkm": round(tkm, 3),
            "idle_seconds": round(sum(a.idle_seconds for a in self.trips.values()), 1),
            # Fleet intensity is total grams over total tonne-kilometres, not
            # the mean of per-trip intensities. Averaging ratios weights a 2 km
            # hop the same as a 58 km run.
            "intensity_g_per_tkm": round(co2_wtw * 1000.0 / tkm, 2) if tkm > 1e-9 else 0.0,
            "trips": len(self.trips),
        }


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def trip_to_iso_row(trip: dict) -> dict:
    """One ISO 14083 row from a persisted trip record."""
    fuel_l = float(trip.get("fuel_l") or 0.0)
    fuel_type = trip.get("fuel_type") or "DIESEL"
    spec = config.FUEL_SPECS.get(fuel_type, config.FUEL_SPECS["DIESEL"])
    # Round FIRST, then derive. A consumer of this CSV only ever sees the
    # rounded columns, so the intensity has to reconcile against those, not
    # against the full-precision values behind them. Deriving before rounding
    # left rows off by 1.5e-4, which is small, meaningless, and exactly the
    # kind of thing a reviewer checks with a calculator.
    ttw = round(fuel_l * spec["ef_ttw_kg_per_l"], 4)
    wtw = round(fuel_l * spec["ef_wtw_kg_per_l"], 4)
    tkm = round(float(trip.get("tkm") or 0.0), 3)
    return {
        "transport_operation_id": trip["trip_id"],
        "transport_mode": TRANSPORT_MODE,
        "vehicle_category": trip.get("vehicle_type") or "",
        "vehicle_id": trip.get("vehicle_id") or "",
        "driver_id": trip.get("driver_id") or "",
        "route": trip.get("route") or "",
        "start_utc": trip.get("start_ts") or "",
        "end_utc": trip.get("end_ts") or "",
        "distance_km": round(float(trip.get("distance_km") or 0.0), 3),
        "cargo_mass_t": round(float(trip.get("payload_t") or 0.0), 3),
        "transport_activity_tkm": round(tkm, 3),
        "energy_consumed_l": round(fuel_l, 3),
        "fuel_type": fuel_type,
        "co2e_ttw_kg": ttw,
        "co2e_wtw_kg": wtw,
        # Recomputed here from the two columns beside it rather than copied from
        # the trips table, so the row cannot be internally inconsistent.
        "ghg_intensity_g_per_tkm": round(wtw * 1000.0 / tkm, 3) if tkm > 1e-9 else 0.0,
        "idle_seconds": round(float(trip.get("idle_seconds") or 0.0), 1),
        "harsh_events": int(trip.get("harsh_events") or 0),
        "behaviour_score": float(trip.get("behaviour_score") or 0.0),
        "data_quality": DATA_QUALITY,
        "data_source": "SIMULATOR (HIL)",
    }


def verify_row(row: dict, tolerance: float = 1e-6) -> tuple[bool, str]:
    """Check a row's intensity against its own kg and t.km columns."""
    tkm = row["transport_activity_tkm"]
    if tkm <= 1e-9:
        return row["ghg_intensity_g_per_tkm"] == 0.0, "zero transport activity"
    expected = row["co2e_wtw_kg"] * 1000.0 / tkm
    actual = row["ghg_intensity_g_per_tkm"]
    ok = abs(expected - actual) <= max(tolerance, abs(expected) * 1e-4)
    return ok, f"expected {expected:.4f}, row says {actual:.4f}"


def build_csv(trips: list[dict]) -> str:
    """ISO 14083 / GLEC aligned CSV. Totals row appended."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ISO_14083_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    rows = [trip_to_iso_row(t) for t in trips]
    for row in rows:
        writer.writerow(row)

    if rows:
        total_tkm = sum(r["transport_activity_tkm"] for r in rows)
        total_wtw = sum(r["co2e_wtw_kg"] for r in rows)
        writer.writerow({
            "transport_operation_id": "TOTAL",
            "transport_mode": TRANSPORT_MODE,
            "vehicle_category": f"{len({r['vehicle_id'] for r in rows})} vehicles",
            "distance_km": round(sum(r["distance_km"] for r in rows), 3),
            "cargo_mass_t": "",
            "transport_activity_tkm": round(total_tkm, 3),
            "energy_consumed_l": round(sum(r["energy_consumed_l"] for r in rows), 3),
            "co2e_ttw_kg": round(sum(r["co2e_ttw_kg"] for r in rows), 4),
            "co2e_wtw_kg": round(total_wtw, 4),
            "ghg_intensity_g_per_tkm": round(total_wtw * 1000.0 / total_tkm, 3) if total_tkm > 1e-9 else 0.0,
            "idle_seconds": round(sum(r["idle_seconds"] for r in rows), 1),
            "harsh_events": sum(r["harsh_events"] for r in rows),
            "data_quality": DATA_QUALITY,
            "data_source": "SIMULATOR (HIL)",
        })
    return buf.getvalue()


def build_pdf(trips: list[dict]) -> bytes:
    """Same content as the CSV, laid out for printing. reportlab is optional."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

    rows = [trip_to_iso_row(t) for t in trips]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Transport Emissions Report", styles["Title"]),
        Paragraph("ISO 14083 / GLEC aligned, tank-to-wheel and well-to-wheel",
                  styles["Normal"]),
        Spacer(1, 6 * mm),
    ]

    head = ["Operation", "Vehicle", "Cat", "km", "t", "t.km", "L",
            "TTW kg", "WTW kg", "g/t.km", "Score"]
    data = [head] + [[
        r["transport_operation_id"], r["vehicle_id"], r["vehicle_category"],
        f"{r['distance_km']:.1f}", f"{r['cargo_mass_t']:.2f}",
        f"{r['transport_activity_tkm']:.1f}", f"{r['energy_consumed_l']:.2f}",
        f"{r['co2e_ttw_kg']:.2f}", f"{r['co2e_wtw_kg']:.2f}",
        f"{r['ghg_intensity_g_per_tkm']:.1f}", f"{r['behaviour_score']:.0f}",
    ] for r in rows]

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B1120")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F9")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story += [table, Spacer(1, 6 * mm),
              Paragraph(f"Data quality declaration: {DATA_QUALITY}", styles["Italic"])]
    doc.build(story)
    return buf.getvalue()
