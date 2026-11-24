import csv
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class TelemetryStorage:
    """Stores Intellian data in 3 SQLite tables: satellites, user_terminals, telemetry_per_second."""

    def __init__(
        self,
        data_dir: str = "data",
        sqlite_db: str = "data/intellian_telemetry.db",
        save_csv: bool = True,
        save_jsonl: bool = True,
    ) -> None:
        self.data_dir = data_dir
        self.sqlite_db = sqlite_db
        self.save_csv = save_csv
        self.save_jsonl = save_jsonl
        self._lock = threading.Lock()

        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "csv"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "jsonl"), exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "tle"), exist_ok=True)

        db_parent = os.path.dirname(self.sqlite_db)
        if db_parent:
            os.makedirs(db_parent, exist_ok=True)

        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(self.sqlite_db)
            try:
                cur = conn.cursor()
                cur.execute("PRAGMA foreign_keys = ON")

                # Table 1: Satellite master/latest TLE info
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS satellites (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sat_id TEXT UNIQUE NOT NULL,
                        sat_name TEXT,
                        tle_line1 TEXT,
                        tle_line2 TEXT,
                        updated_at_utc TEXT NOT NULL
                    )
                    """
                )

                # Table 2: User Terminal (UT) device info
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_terminals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ut_code TEXT UNIQUE NOT NULL,
                        source TEXT,
                        ut_lat REAL,
                        ut_lon REAL,
                        ut_alt_m REAL,
                        link_state TEXT,
                        updated_at_utc TEXT NOT NULL
                    )
                    """
                )

                # Table 3: Per-second time-series telemetry received
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS telemetry_per_second (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_utc TEXT NOT NULL,
                        ut_ref_id INTEGER,
                        sat_ref_id INTEGER,
                        ut_lat REAL,
                        ut_lon REAL,
                        ut_alt_m REAL,
                        sat_id TEXT,
                        sat_name TEXT,
                        sat_lat REAL,
                        sat_lon REAL,
                        sat_alt_km REAL,
                        sat_azimuth_deg REAL,
                        sat_elevation_deg REAL,
                        snr_db REAL,
                        cn0_dbhz REAL,
                        rssi_dbm REAL,
                        link_state TEXT,
                        ping_cnx_ms REAL,
                        ping_internet_ms REAL,
                        raw_json TEXT,
                        FOREIGN KEY (ut_ref_id) REFERENCES user_terminals(id),
                        FOREIGN KEY (sat_ref_id) REFERENCES satellites(id)
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def utc_now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def today_str() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _upsert_satellite(
        cur: sqlite3.Cursor,
        sat_id: Optional[str],
        sat_name: Optional[str],
        tle_line1: Optional[str],
        tle_line2: Optional[str],
        ts: str,
    ) -> Optional[int]:
        if not sat_id and not sat_name:
            return None
        key = str(sat_id or sat_name)
        row = cur.execute("SELECT id FROM satellites WHERE sat_id = ?", (key,)).fetchone()
        if row:
            cur.execute(
                """
                UPDATE satellites
                SET sat_name = COALESCE(?, sat_name),
                    tle_line1 = COALESCE(?, tle_line1),
                    tle_line2 = COALESCE(?, tle_line2),
                    updated_at_utc = ?
                WHERE id = ?
                """,
                (sat_name, tle_line1, tle_line2, ts, row[0]),
            )
            return int(row[0])

        cur.execute(
            """
            INSERT INTO satellites (sat_id, sat_name, tle_line1, tle_line2, updated_at_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            (key, sat_name, tle_line1, tle_line2, ts),
        )
        return int(cur.lastrowid)

    @staticmethod
    def _upsert_user_terminal(
        cur: sqlite3.Cursor,
        ut_code: str,
        source: str,
        ut_lat: Optional[float],
        ut_lon: Optional[float],
        ut_alt_m: Optional[float],
        link_state: Optional[str],
        ts: str,
    ) -> int:
        row = cur.execute(
            "SELECT id FROM user_terminals WHERE ut_code = ?", (ut_code,)
        ).fetchone()
        if row:
            cur.execute(
                """
                UPDATE user_terminals
                SET source = ?,
                    ut_lat = COALESCE(?, ut_lat),
                    ut_lon = COALESCE(?, ut_lon),
                    ut_alt_m = COALESCE(?, ut_alt_m),
                    link_state = COALESCE(?, link_state),
                    updated_at_utc = ?
                WHERE id = ?
                """,
                (source, ut_lat, ut_lon, ut_alt_m, link_state, ts, row[0]),
            )
            return int(row[0])

        cur.execute(
            """
            INSERT INTO user_terminals (
                ut_code, source, ut_lat, ut_lon, ut_alt_m, link_state, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ut_code, source, ut_lat, ut_lon, ut_alt_m, link_state, ts),
        )
        return int(cur.lastrowid)

    def save_telemetry(self, record: Dict[str, Any]) -> None:
        ts = record.get("timestamp_utc") or self.utc_now_iso()
        record["timestamp_utc"] = ts
        raw_json = json.dumps(record.get("raw", {}), ensure_ascii=False)
        source = record.get("source", "unknown")
        ut_code = record.get("ut_code") or source

        with self._lock:
            conn = sqlite3.connect(self.sqlite_db)
            try:
                cur = conn.cursor()
                cur.execute("PRAGMA foreign_keys = ON")

                sat_ref_id = self._upsert_satellite(
                    cur,
                    record.get("sat_id"),
                    record.get("sat_name"),
                    record.get("tle_line1"),
                    record.get("tle_line2"),
                    ts,
                )
                ut_ref_id = self._upsert_user_terminal(
                    cur,
                    ut_code,
                    source,
                    record.get("ut_lat"),
                    record.get("ut_lon"),
                    record.get("ut_alt_m"),
                    record.get("link_state"),
                    ts,
                )

                cur.execute(
                    """
                    INSERT INTO telemetry_per_second (
                        timestamp_utc, ut_ref_id, sat_ref_id,
                        ut_lat, ut_lon, ut_alt_m,
                        sat_id, sat_name, sat_lat, sat_lon, sat_alt_km,
                        sat_azimuth_deg, sat_elevation_deg,
                        snr_db, cn0_dbhz, rssi_dbm, link_state,
                        ping_cnx_ms, ping_internet_ms, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        ut_ref_id,
                        sat_ref_id,
                        record.get("ut_lat"),
                        record.get("ut_lon"),
                        record.get("ut_alt_m"),
                        record.get("sat_id"),
                        record.get("sat_name"),
                        record.get("sat_lat"),
                        record.get("sat_lon"),
                        record.get("sat_alt_km"),
                        record.get("sat_azimuth_deg"),
                        record.get("sat_elevation_deg"),
                        record.get("snr_db"),
                        record.get("cn0_dbhz"),
                        record.get("rssi_dbm"),
                        record.get("link_state"),
                        record.get("ping_cnx_ms"),
                        record.get("ping_internet_ms"),
                        raw_json,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            if self.save_csv:
                csv_path = os.path.join(
                    self.data_dir, "csv", "telemetry_{}.csv".format(self.today_str())
                )
                fields = [
                    "timestamp_utc",
                    "source",
                    "ut_lat",
                    "ut_lon",
                    "ut_alt_m",
                    "sat_id",
                    "sat_name",
                    "sat_lat",
                    "sat_lon",
                    "sat_alt_km",
                    "sat_azimuth_deg",
                    "sat_elevation_deg",
                    "snr_db",
                    "cn0_dbhz",
                    "rssi_dbm",
                    "link_state",
                    "ping_cnx_ms",
                    "ping_internet_ms",
                    "tle_line1",
                    "tle_line2",
                ]
                self._append_csv(csv_path, fields, record)

            if self.save_jsonl:
                jsonl_path = os.path.join(
                    self.data_dir, "jsonl", "telemetry_{}.jsonl".format(self.today_str())
                )
                with open(jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_ping(self, ping_result: Dict[str, Any]) -> None:
        """Append raw ping event to daily CSV (per-second values are also stored in telemetry_per_second)."""
        ts = ping_result.get("timestamp_utc") or self.utc_now_iso()
        ping_result["timestamp_utc"] = ts
        if not self.save_csv:
            return
        with self._lock:
            csv_path = os.path.join(
                self.data_dir, "csv", "ping_{}.csv".format(self.today_str())
            )
            fields = ["timestamp_utc", "target", "success", "rtt_ms", "ttl", "error"]
            self._append_csv(csv_path, fields, ping_result)

    @staticmethod
    def _append_csv(path: str, fields: list, row: Dict[str, Any]) -> None:
        file_exists = os.path.isfile(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow({k: row.get(k) for k in fields})
