import json
import os
import time
from typing import Dict, Optional, Tuple

import requests


class TLEFetcher:
    """Fetches and caches OneWeb TLE & OMM JSON data from CelesTrak."""

    def __init__(
        self,
        tle_dir: str = "data/tle",
        celestrak_tle_url: str = "https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=TLE",
        celestrak_json_url: str = "https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=JSON",
        refresh_interval_sec: float = 21600.0,
        timeout_sec: float = 15.0,
    ) -> None:
        self.tle_dir = tle_dir
        self.celestrak_tle_url = celestrak_tle_url
        self.celestrak_json_url = celestrak_json_url
        self.refresh_interval_sec = refresh_interval_sec
        self.timeout_sec = timeout_sec

        os.makedirs(self.tle_dir, exist_ok=True)
        self.latest_tle_path = os.path.join(self.tle_dir, "oneweb_latest.tle")
        self.latest_json_path = os.path.join(self.tle_dir, "oneweb_latest.json")

        self._last_fetch_time: float = 0.0
        self._tle_by_norad: Dict[str, Tuple[str, str, str]] = {}
        self._tle_by_name: Dict[str, Tuple[str, str, str]] = {}

        if os.path.isfile(self.latest_tle_path):
            with open(self.latest_tle_path, "r", encoding="utf-8") as f:
                self._parse_tle_text(f.read())

    def _parse_tle_text(self, text: str) -> int:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        by_norad: Dict[str, Tuple[str, str, str]] = {}
        by_name: Dict[str, Tuple[str, str, str]] = {}

        i = 0
        while i + 2 < len(lines):
            name = lines[i]
            l1 = lines[i + 1]
            l2 = lines[i + 2]
            if l1.startswith("1 ") and l2.startswith("2 "):
                norad_id = l1[2:7].strip()
                by_norad[norad_id] = (name, l1, l2)
                by_name[name.upper()] = (name, l1, l2)
                i += 3
            else:
                i += 1

        self._tle_by_norad = by_norad
        self._tle_by_name = by_name
        return len(by_norad)

    def needs_refresh(self) -> bool:
        if not self._tle_by_norad:
            return True
        return (time.time() - self._last_fetch_time) >= self.refresh_interval_sec

    def refresh(self, force: bool = False) -> Dict[str, object]:
        if not force and not self.needs_refresh():
            return {
                "refreshed": False,
                "satellite_count": len(self._tle_by_norad),
                "tle_path": self.latest_tle_path,
                "json_path": self.latest_json_path if os.path.isfile(self.latest_json_path) else None,
            }

        tle_resp = requests.get(self.celestrak_tle_url, timeout=self.timeout_sec)
        tle_resp.raise_for_status()
        tle_text = tle_resp.text
        count = self._parse_tle_text(tle_text)

        with open(self.latest_tle_path, "w", encoding="utf-8") as f:
            f.write(tle_text)

        json_path: Optional[str] = None
        try:
            json_resp = requests.get(self.celestrak_json_url, timeout=self.timeout_sec)
            json_resp.raise_for_status()
            json_data = json_resp.json()
            with open(self.latest_json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, ensure_ascii=False)
            json_path = self.latest_json_path
        except Exception:
            pass

        self._last_fetch_time = time.time()
        return {
            "refreshed": True,
            "satellite_count": count,
            "tle_path": self.latest_tle_path,
            "json_path": json_path,
        }

    def find_satellite(
        self, sat_id: Optional[str] = None, sat_name: Optional[str] = None
    ) -> Optional[Tuple[str, str, str]]:
        """Look up (name, line1, line2) by NORAD ID or satellite name."""
        if sat_id:
            clean_id = str(sat_id).strip().lstrip("0")
            for key, val in self._tle_by_norad.items():
                if key.lstrip("0") == clean_id:
                    return val
        if sat_name:
            key = sat_name.strip().upper()
            if key in self._tle_by_name:
                return self._tle_by_name[key]
            for name, val in self._tle_by_name.items():
                if key in name:
                    return val
        return None

    @property
    def satellite_count(self) -> int:
        return len(self._tle_by_norad)
