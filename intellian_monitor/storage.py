import csv
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class TelemetryStorage:
    """Stores Intellian UT telemetry, TLE metadata, and realtime ping results locally."""

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
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS telemetry_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_utc TEXT NOT NULL,
                        source TEXT NOT NULL,
                        ut_lat REAL,
                        ut_lon REAL,
                        ut_alt_m REAL,
                        sat_id TEXT,
                        sat_name TEXT,
                        sat_azimuth_deg REAL,
                        sat_elevation_deg REAL,
                        snr_db REAL,
                        cn0_dbhz REAL,
                        rssi_dbm REAL,
                        link_state TEXT,
                        tle_line1 TEXT,
                        tle_line2 TEXT,
                        raw_json TEXT
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ping_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_utc TEXT NOT NULL,
                        target TEXT NOT NULL,
                        success INTEGER NOT NULL,
                        rtt_ms REAL,
                        ttl INTEGER,
                        error TEXT
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tle_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_utc TEXT NOT NULL,
                        satellite_count INTEGER NOT NULL,
                        tle_path TEXT,
                        json_path TEXT
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

    def save_telemetry(self, record: Dict[str, Any]) -> None:
        ts = record.get("timestamp_utc") or self.utc_now_iso()
        record["timestamp_utc"] = ts
        raw_json = json.dumps(record.get("raw", {}), ensure_ascii=False)

        with self._lock:
            conn = sqlite3.connect(self.sqlite_db)
            try:
                conn.execute(
                    """
                    INSERT INTO telemetry_logs (
                        timestamp_utc, source, ut_lat, ut_lon, ut_alt_m,
                        sat_id, sat_name, sat_azimuth_deg, sat_elevation_deg,
                        snr_db, cn0_dbhz, rssi_dbm, link_state,
                        tle_line1, tle_line2, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        record.get("source", "unknown"),
                        record.get("ut_lat"),
                        record.get("ut_lon"),
                        record.get("ut_alt_m"),
                        record.get("sat_id"),
                        record.get("sat_name"),
                        record.get("sat_azimuth_deg"),
                        record.get("sat_elevation_deg"),
                        record.get("snr_db"),
                        record.get("cn0_dbhz"),
                        record.get("rssi_dbm"),
                        record.get("link_state"),
                        record.get("tle_line1"),
                        record.get("tle_line2"),
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
                    "sat_azimuth_deg",
                    "sat_elevation_deg",
                    "snr_db",
                    "cn0_dbhz",
                    "rssi_dbm",
                    "link_state",
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
        ts = ping_result.get("timestamp_utc") or self.utc_now_iso()
        ping_result["timestamp_utc"] = ts

        with self._lock:
            conn = sqlite3.connect(self.sqlite_db)
            try:
                conn.execute(
                    """
                    INSERT INTO ping_logs (
                        timestamp_utc, target, success, rtt_ms, ttl, error
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        ping_result.get("target"),
                        1 if ping_result.get("success") else 0,
                        ping_result.get("rtt_ms"),
                        ping_result.get("ttl"),
                        ping_result.get("error"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            if self.save_csv:
                csv_path = os.path.join(
                    self.data_dir, "csv", "ping_{}.csv".format(self.today_str())
                )
                fields = ["timestamp_utc", "target", "success", "rtt_ms", "ttl", "error"]
                self._append_csv(csv_path, fields, ping_result)

    def save_tle_snapshot(
        self, satellite_count: int, tle_path: Optional[str], json_path: Optional[str]
    ) -> None:
        ts = self.utc_now_iso()
        with self._lock:
            conn = sqlite3.connect(self.sqlite_db)
            try:
                conn.execute(
                    """
                    INSERT INTO tle_snapshots (
                        timestamp_utc, satellite_count, tle_path, json_path
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (ts, satellite_count, tle_path, json_path),
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _append_csv(path: str, fields: list, row: Dict[str, Any]) -> None:
        file_exists = os.path.isfile(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow({k: row.get(k) for k in fields})
