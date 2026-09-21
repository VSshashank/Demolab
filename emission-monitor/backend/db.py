"""SQLite persistence. Plain sqlite3, no ORM.

Writes are batched. Committing per packet puts an fsync in the 1 Hz path; at
six to eight vehicles that is six to eight disk syncs a second for data nobody
reads until the trip closes. Batching at 50 rows or 2 seconds, whichever comes
first, keeps the worst-case loss to two seconds of telemetry, which is a fair
trade for a monitoring system.

`co2_gps_truth` is the CO2 implied by the vehicle's own PID 0x5E fuel-rate
reading; `co2_gps_pred` is what the model estimated from the other PIDs. Both
exist on real hardware, so the comparison the dashboard draws survives the
hardware swap.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry (
  id INTEGER PRIMARY KEY, ts TEXT, vehicle_id TEXT, trip_id TEXT,
  lat REAL, lon REAL, speed_kmh REAL, accel_ms2 REAL, rpm INTEGER,
  engine_load_pct REAL, payload_kg REAL, gvw_kg REAL,
  fuel_rate_lph REAL, co2_gps_pred REAL, co2_gps_truth REAL,
  vehicle_type TEXT, fuel_type TEXT, driver_id TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY, ts TEXT, vehicle_id TEXT, trip_id TEXT,
  type TEXT, severity TEXT, message TEXT, recommendation TEXT,
  value REAL, threshold REAL, acknowledged INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trips (
  trip_id TEXT PRIMARY KEY, vehicle_id TEXT, route TEXT, driver_id TEXT,
  start_ts TEXT, end_ts TEXT, distance_km REAL, fuel_l REAL,
  co2_ttw_kg REAL, co2_wtw_kg REAL, payload_t REAL, tkm REAL,
  intensity_g_per_tkm REAL, idle_seconds REAL, harsh_events INTEGER,
  behaviour_score REAL, vehicle_type TEXT, fuel_type TEXT
);
CREATE INDEX IF NOT EXISTS idx_tel_vehicle_ts ON telemetry(vehicle_id, ts);
CREATE INDEX IF NOT EXISTS idx_tel_trip ON telemetry(trip_id);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
"""

TELEMETRY_COLUMNS = (
    "ts", "vehicle_id", "trip_id", "lat", "lon", "speed_kmh", "accel_ms2", "rpm",
    "engine_load_pct", "payload_kg", "gvw_kg", "fuel_rate_lph", "co2_gps_pred",
    "co2_gps_truth", "vehicle_type", "fuel_type", "driver_id", "source",
)
_INSERT_TELEMETRY = (
    f"INSERT INTO telemetry ({','.join(TELEMETRY_COLUMNS)}) "
    f"VALUES ({','.join('?' * len(TELEMETRY_COLUMNS))})"
)


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL lets the API read while the ingest task writes. Without it the
        # dashboard's own polling intermittently blocks the writer.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

        self._pending: list[tuple] = []
        self._last_flush = time.time()
        self.rows_written = 0

    # ------------------------------------------------------------ telemetry
    def queue_telemetry(self, row: dict) -> None:
        with self._lock:
            self._pending.append(tuple(row.get(c) for c in TELEMETRY_COLUMNS))
            due = (len(self._pending) >= config.DB_BATCH_ROWS
                   or time.time() - self._last_flush >= config.DB_BATCH_SECONDS)
        if due:
            self.flush()

    def flush(self) -> int:
        with self._lock:
            if not self._pending:
                self._last_flush = time.time()
                return 0
            batch, self._pending = self._pending, []
            self.conn.executemany(_INSERT_TELEMETRY, batch)
            self.conn.commit()
            self._last_flush = time.time()
            self.rows_written += len(batch)
            return len(batch)

    def vehicle_history(self, vehicle_id: str, minutes: int = 30, limit: int = 3000) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM telemetry WHERE vehicle_id = ? "
            "ORDER BY ts DESC LIMIT ?", (vehicle_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        return list(reversed(rows))

    def trip_telemetry(self, trip_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM telemetry WHERE trip_id = ? ORDER BY ts ASC", (trip_id,))
        return [dict(r) for r in cur.fetchall()]

    def telemetry_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0])

    # --------------------------------------------------------------- alerts
    def insert_alert(self, alert: dict) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO alerts (ts, vehicle_id, trip_id, type, severity, message, "
                "recommendation, value, threshold, acknowledged) "
                "VALUES (?,?,?,?,?,?,?,?,?,0)",
                (alert["ts"], alert["vehicle_id"], alert.get("trip_id"), alert["type"],
                 alert["severity"], alert["message"], alert["recommendation"],
                 alert.get("value"), alert.get("threshold")))
            self.conn.commit()
            return int(cur.lastrowid)

    def recent_alerts(self, limit: int = 50, vehicle_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM alerts"
        params: list[Any] = []
        if vehicle_id:
            sql += " WHERE vehicle_id = ?"
            params.append(vehicle_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def trip_alerts(self, trip_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM alerts WHERE trip_id = ? ORDER BY id DESC", (trip_id,)).fetchall()]

    def ack_alert(self, alert_id: int) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def active_alert_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE acknowledged = 0").fetchone()[0])

    # ---------------------------------------------------------------- trips
    def upsert_trip(self, trip: dict) -> None:
        cols = ("trip_id", "vehicle_id", "route", "driver_id", "start_ts", "end_ts",
                "distance_km", "fuel_l", "co2_ttw_kg", "co2_wtw_kg", "payload_t", "tkm",
                "intensity_g_per_tkm", "idle_seconds", "harsh_events", "behaviour_score",
                "vehicle_type", "fuel_type")
        with self._lock:
            self.conn.execute(
                f"INSERT INTO trips ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                f"ON CONFLICT(trip_id) DO UPDATE SET "
                + ", ".join(f"{c}=excluded.{c}" for c in cols if c != "trip_id"),
                tuple(trip.get(c) for c in cols))
            self.conn.commit()

    def list_trips(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM trips ORDER BY start_ts DESC LIMIT ?", (limit,)).fetchall()]

    def get_trip(self, trip_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
        return dict(row) if row else None

    def trips_between(self, start: str | None, end: str | None) -> list[dict]:
        sql, params = "SELECT * FROM trips", []
        clauses = []
        if start:
            clauses.append("start_ts >= ?"); params.append(start)
        if end:
            clauses.append("start_ts <= ?"); params.append(end)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY start_ts ASC", params).fetchall()]

    # ------------------------------------------------------------ lifecycle
    def close(self) -> None:
        self.flush()
        self.conn.close()

    def reset(self) -> None:
        """Drop every row. Used by run_demo.sh --fresh so a demo starts clean."""
        with self._lock:
            self._pending.clear()
            for table in ("telemetry", "alerts", "trips"):
                self.conn.execute(f"DELETE FROM {table}")
            self.conn.commit()
        self.rows_written = 0
