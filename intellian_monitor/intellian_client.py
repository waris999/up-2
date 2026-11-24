import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _to_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _find_first_value(data: Any, candidate_keys: Tuple[str, ...]) -> Any:
    """Recursively search nested dicts/lists for the first matching key (case-insensitive)."""
    lowered = {k.lower(): k for k in candidate_keys}

    if isinstance(data, dict):
        # Direct match pass
        for key, value in data.items():
            if str(key).lower() in lowered and value not in (None, ""):
                return value
        # Recursive pass
        for value in data.values():
            found = _find_first_value(value, candidate_keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_first_value(item, candidate_keys)
            if found not in (None, ""):
                return found
    return None


class IntellianClient:
    """HTTP/REST client for Intellian OneWeb User Terminal / CNX Local User Interface."""

    COMMON_DISCOVERY_PATHS = [
        "/",
        "/api/v1/status",
        "/api/v1/ut/status",
        "/api/v1/antenna/status",
        "/api/v1/modem/status",
        "/api/v1/gnss/status",
        "/api/v1/satellite",
        "/api/v1/ephemeris",
        "/api/v1/system/info",
        "/api/status",
        "/api/telemetry",
        "/api/ut",
        "/api/antenna",
        "/api/modem",
        "/status.json",
        "/cgi-bin/status",
    ]

    def __init__(
        self,
        base_url: str = "http://192.168.100.1",
        username: str = "",
        password: str = "",
        verify_ssl: bool = False,
        timeout_sec: float = 3.0,
        endpoints: Optional[List[str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout_sec = timeout_sec
        self.endpoints = endpoints or [
            "/api/v1/status",
            "/api/v1/ut/status",
            "/api/v1/antenna/status",
            "/api/v1/modem/status",
            "/api/v1/gnss/status",
            "/api/status",
            "/status.json",
        ]
        self.session = requests.Session()
        if self.username and self.password:
            self.session.auth = (self.username, self.password)

    def _get_json_or_text(self, path: str) -> Tuple[int, Optional[Any], str]:
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        resp = self.session.get(
            url, timeout=self.timeout_sec, verify=self.verify_ssl
        )
        content_type = resp.headers.get("Content-Type", "")
        if "json" in content_type.lower():
            try:
                return resp.status_code, resp.json(), content_type
            except ValueError:
                pass
        try:
            return resp.status_code, resp.json(), content_type
        except ValueError:
            return resp.status_code, resp.text[:500], content_type

    def discover_endpoints(self, output_path: str = "data/discovery_report.json") -> Dict[str, Any]:
        """Probe common Intellian LUI/API endpoints and save a diagnostic report."""
        report: Dict[str, Any] = {
            "base_url": self.base_url,
            "probed_at_unix": time.time(),
            "results": {},
        }
        paths = list(dict.fromkeys(self.endpoints + self.COMMON_DISCOVERY_PATHS))
        for path in paths:
            try:
                status, payload, ctype = self._get_json_or_text(path)
                report["results"][path] = {
                    "status_code": status,
                    "content_type": ctype,
                    "is_json": isinstance(payload, (dict, list)),
                    "preview": payload,
                }
            except requests.RequestException as exc:
                report["results"][path] = {
                    "error": str(exc),
                }

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report

    def fetch_telemetry(self) -> Dict[str, Any]:
        """Poll configured Intellian endpoints and normalize UT/satellite/signal fields."""
        merged_raw: Dict[str, Any] = {}
        errors: List[str] = []

        for path in self.endpoints:
            try:
                status, payload, _ = self._get_json_or_text(path)
                if status == 200 and isinstance(payload, (dict, list)):
                    merged_raw[path] = payload
            except requests.RequestException as exc:
                errors.append("{}: {}".format(path, exc))

        if not merged_raw:
            return {
                "source": "intellian_live",
                "connected": False,
                "error": "; ".join(errors) if errors else "No JSON endpoints returned HTTP 200",
                "raw": {},
            }

        ut_lat = _to_float(_find_first_value(merged_raw, ("latitude", "lat", "ut_lat", "gps_lat")))
        ut_lon = _to_float(_find_first_value(merged_raw, ("longitude", "lon", "lng", "ut_lon", "gps_lon")))
        ut_alt_m = _to_float(_find_first_value(merged_raw, ("altitude", "alt", "ut_alt", "gps_alt")))

        sat_id = _find_first_value(merged_raw, ("sat_id", "satellite_id", "norad_id", "sv_id"))
        sat_name = _find_first_value(merged_raw, ("sat_name", "satellite_name", "satellite"))
        sat_lat = _to_float(_find_first_value(merged_raw, ("sat_lat", "satellite_lat", "satellite_latitude", "subpoint_lat")))
        sat_lon = _to_float(_find_first_value(merged_raw, ("sat_lon", "satellite_lon", "satellite_longitude", "subpoint_lon")))
        sat_alt_km = _to_float(_find_first_value(merged_raw, ("sat_alt", "sat_alt_km", "satellite_alt", "satellite_altitude")))
        sat_az = _to_float(_find_first_value(merged_raw, ("azimuth", "az", "sat_az", "pointing_az")))
        sat_el = _to_float(_find_first_value(merged_raw, ("elevation", "el", "sat_el", "pointing_el")))

        snr_db = _to_float(_find_first_value(merged_raw, ("snr", "snr_db", "rx_snr", "esno", "ebno")))
        cn0_dbhz = _to_float(_find_first_value(merged_raw, ("cn0", "c_n0", "cn0_dbhz")))
        rssi_dbm = _to_float(_find_first_value(merged_raw, ("rssi", "rssi_dbm", "rx_power", "signal_strength")))
        link_state = _find_first_value(merged_raw, ("link_state", "state", "status", "modem_state", "ut_state"))

        return {
            "source": "intellian_live",
            "connected": True,
            "ut_lat": ut_lat,
            "ut_lon": ut_lon,
            "ut_alt_m": ut_alt_m,
            "sat_id": str(sat_id) if sat_id is not None else None,
            "sat_name": str(sat_name) if sat_name is not None else None,
            "sat_lat": sat_lat,
            "sat_lon": sat_lon,
            "sat_alt_km": sat_alt_km,
            "sat_azimuth_deg": sat_az,
            "sat_elevation_deg": sat_el,
            "snr_db": snr_db,
            "cn0_dbhz": cn0_dbhz,
            "rssi_dbm": rssi_dbm,
            "link_state": str(link_state) if link_state is not None else None,
            "raw": merged_raw,
        }


class MockIntellianClient:
    """Simulated Intellian OneWeb UT client for offline testing without hardware."""

    def __init__(self) -> None:
        self._start = time.time()

    def fetch_telemetry(self) -> Dict[str, Any]:
        t = time.time() - self._start
        az = round((120.0 + t * 1.5) % 360.0, 2)
        el = round(45.0 + 25.0 * math.sin(t / 15.0), 2)
        snr = round(11.5 + 1.8 * math.cos(t / 8.0), 2)
        return {
            "source": "intellian_mock",
            "connected": True,
            "ut_lat": 13.7563,
            "ut_lon": 100.5018,
            "ut_alt_m": 12.5,
            "sat_id": "44057",
            "sat_name": "ONEWEB-0012",
            "sat_lat": round(14.5 + 3.0 * math.sin(t / 12.0), 6),
            "sat_lon": round(101.2 + 3.0 * math.cos(t / 12.0), 6),
            "sat_alt_km": 1200.0,
            "sat_azimuth_deg": az,
            "sat_elevation_deg": el,
            "snr_db": snr,
            "cn0_dbhz": round(snr + 58.0, 2),
            "rssi_dbm": round(-62.0 + 1.5 * math.sin(t / 10.0), 2),
            "link_state": "ONLINE_TRACKING",
            "raw": {"mock": True, "azimuth": az, "elevation": el, "snr": snr},
        }
