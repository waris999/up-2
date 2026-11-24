import argparse
import json
import os
import signal
import sys
import time
from typing import Any, Dict

from intellian_client import IntellianClient, MockIntellianClient
from ping_monitor import RealtimePinger, ping_once
from storage import TelemetryStorage
from tle_fetcher import TLEFetcher

try:
    from rich.console import Console
    from rich.table import Table
    HAS_RICH = True
except ImportError:
    HAS_RICH = False


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_status(
    telemetry: Dict[str, Any],
    tle_count: int,
    ping_snap: Dict[str, Dict[str, Any]],
) -> None:
    if HAS_RICH:
        console = Console()
        table = Table(title="Intellian OneWeb Monitor (Live)")
        table.add_column("Category", style="cyan", no_wrap=True)
        table.add_column("Metric", style="white")
        table.add_column("Value", style="green")

        table.add_row(
            "1. UT & Sat Coords",
            "User Terminal (Lat, Lon, Alt)",
            "{}, {}, {} m".format(
                telemetry.get("ut_lat"),
                telemetry.get("ut_lon"),
                telemetry.get("ut_alt_m"),
            ),
        )
        table.add_row(
            "1. UT & Sat Coords",
            "Satellite (ID / Name)",
            "{} / {}".format(telemetry.get("sat_id"), telemetry.get("sat_name")),
        )
        table.add_row(
            "1. UT & Sat Coords",
            "Satellite (Lat, Lon, Alt)",
            "{}, {}, {} km".format(
                telemetry.get("sat_lat"),
                telemetry.get("sat_lon"),
                telemetry.get("sat_alt_km"),
            ),
        )
        table.add_row(
            "1. UT & Sat Coords",
            "Pointing (Azimuth / Elevation)",
            "{} deg / {} deg".format(
                telemetry.get("sat_azimuth_deg"), telemetry.get("sat_elevation_deg")
            ),
        )
        table.add_row(
            "2. TLE",
            "Cached OneWeb Satellites",
            "{} | Line1: {}".format(
                tle_count,
                (telemetry.get("tle_line1") or "N/A")[:32],
            ),
        )
        table.add_row(
            "3. Signal Strength",
            "SNR / C/N0 / RSSI / State",
            "SNR={} dB | C/N0={} dBHz | RSSI={} dBm | {}".format(
                telemetry.get("snr_db"),
                telemetry.get("cn0_dbhz"),
                telemetry.get("rssi_dbm"),
                telemetry.get("link_state"),
            ),
        )
        for target, st in ping_snap.items():
            table.add_row(
                "4. Realtime Ping",
                target,
                "last={} ms | avg={} ms | loss={}% ({}/{})".format(
                    st.get("last_ms"),
                    st.get("avg_ms"),
                    st.get("loss_pct"),
                    st.get("received"),
                    st.get("sent"),
                ),
            )
        console.print(table)
    else:
        print(
            "[{}] UT=({}, {}) Sat={} SatPos=({}, {}) Az/El=({}/{}) SNR={}dB TLEs={} Ping={}".format(
                telemetry.get("timestamp_utc"),
                telemetry.get("ut_lat"),
                telemetry.get("ut_lon"),
                telemetry.get("sat_name") or telemetry.get("sat_id"),
                telemetry.get("sat_lat"),
                telemetry.get("sat_lon"),
                telemetry.get("sat_azimuth_deg"),
                telemetry.get("sat_elevation_deg"),
                telemetry.get("snr_db"),
                tle_count,
                {k: v.get("last_ms") for k, v in ping_snap.items()},
            )
        )


def enrich_with_tle(telemetry: Dict[str, Any], tle_fetcher: TLEFetcher) -> None:
    """Attach matching TLE lines and compute satellite lat/lon/alt if missing."""
    tle_match = tle_fetcher.find_satellite(
        sat_id=telemetry.get("sat_id"), sat_name=telemetry.get("sat_name")
    )
    if not tle_match:
        return

    telemetry["tle_line1"] = tle_match[1]
    telemetry["tle_line2"] = tle_match[2]
    if telemetry.get("sat_lat") is None or telemetry.get("sat_lon") is None:
        subpoint = tle_fetcher.compute_sat_subpoint(tle_match[1], tle_match[2])
        if subpoint is not None:
            telemetry["sat_lat"], telemetry["sat_lon"], telemetry["sat_alt_km"] = subpoint


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Intellian OneWeb UT Telemetry, TLE, Signal & Realtime Ping Logger"
    )
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
        help="Path to config.json",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Probe Intellian CNX/UT endpoints and save data/discovery_report.json",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use simulated Intellian telemetry (for testing without hardware)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect one cycle of telemetry, TLE, and ping, then exit",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    int_cfg = cfg["intellian"]
    tle_cfg = cfg["tle"]
    ping_cfg = cfg["ping"]
    sto_cfg = cfg["storage"]

    storage = TelemetryStorage(
        data_dir=sto_cfg["data_dir"],
        sqlite_db=sto_cfg["sqlite_db"],
        save_csv=sto_cfg.get("save_csv", True),
        save_jsonl=sto_cfg.get("save_jsonl", True),
    )

    if args.discover:
        client = IntellianClient(
            base_url=int_cfg["base_url"],
            username=int_cfg.get("username", ""),
            password=int_cfg.get("password", ""),
            verify_ssl=int_cfg.get("verify_ssl", False),
            timeout_sec=int_cfg.get("timeout_sec", 3.0),
            endpoints=int_cfg.get("endpoints"),
        )
        report_path = os.path.join(sto_cfg["data_dir"], "discovery_report.json")
        print("Probing Intellian CNX at {} ...".format(int_cfg["base_url"]))
        report = client.discover_endpoints(output_path=report_path)
        ok_paths = [
            p for p, info in report["results"].items() if info.get("status_code") == 200
        ]
        print("Discovery complete. Saved to:", report_path)
        print("HTTP 200 paths:", ok_paths)
        return 0

    client = (
        MockIntellianClient()
        if args.mock
        else IntellianClient(
            base_url=int_cfg["base_url"],
            username=int_cfg.get("username", ""),
            password=int_cfg.get("password", ""),
            verify_ssl=int_cfg.get("verify_ssl", False),
            timeout_sec=int_cfg.get("timeout_sec", 3.0),
            endpoints=int_cfg.get("endpoints"),
        )
    )

    tle_fetcher = TLEFetcher(
        tle_dir=os.path.join(sto_cfg["data_dir"], "tle"),
        celestrak_tle_url=tle_cfg["celestrak_tle_url"],
        celestrak_json_url=tle_cfg["celestrak_json_url"],
        refresh_interval_sec=tle_cfg.get("refresh_interval_sec", 21600),
        timeout_sec=tle_cfg.get("timeout_sec", 15.0),
    )

    if tle_cfg.get("enabled", True):
        try:
            tle_fetcher.refresh(force=False)
        except Exception as exc:
            print("Warning: TLE refresh failed ({}), using cached TLEs: {}".format(
                exc, tle_fetcher.satellite_count
            ))

    if args.once:
        telemetry = client.fetch_telemetry()
        enrich_with_tle(telemetry, tle_fetcher)

        ping_snap: Dict[str, Dict[str, Any]] = {}
        if ping_cfg.get("enabled", True):
            targets = ping_cfg.get("targets", [])
            for target in targets:
                res = ping_once(target, timeout_sec=ping_cfg.get("timeout_sec", 1.0))
                storage.save_ping(res)
                ping_snap[target] = {
                    "sent": 1,
                    "received": 1 if res["success"] else 0,
                    "loss_pct": 0.0 if res["success"] else 100.0,
                    "last_ms": res["rtt_ms"],
                    "avg_ms": res["rtt_ms"],
                }
            if len(targets) >= 1:
                telemetry["ping_cnx_ms"] = ping_snap[targets[0]].get("last_ms")
            if len(targets) >= 2:
                telemetry["ping_internet_ms"] = ping_snap[targets[1]].get("last_ms")

        storage.save_telemetry(telemetry)
        print_status(telemetry, tle_fetcher.satellite_count, ping_snap)
        return 0

    pinger = RealtimePinger(
        targets=ping_cfg.get("targets", ["192.168.100.1", "8.8.8.8"]),
        interval_sec=ping_cfg.get("interval_sec", 1.0),
        timeout_sec=ping_cfg.get("timeout_sec", 1.0),
        on_result=storage.save_ping,
    )

    stop_requested = False

    def _handle_sig(signum: int, frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    if ping_cfg.get("enabled", True):
        pinger.start()

    poll_interval = float(int_cfg.get("poll_interval_sec", 1.0))
    targets = ping_cfg.get("targets", ["192.168.100.1", "8.8.8.8"])
    print("Starting Intellian Monitor... Press Ctrl+C to stop.")

    try:
        while not stop_requested:
            loop_start = time.time()
            if tle_cfg.get("enabled", True) and tle_fetcher.needs_refresh():
                try:
                    tle_fetcher.refresh(force=False)
                except Exception:
                    pass

            telemetry = client.fetch_telemetry()
            enrich_with_tle(telemetry, tle_fetcher)

            ping_snap = pinger.get_snapshot() if ping_cfg.get("enabled", True) else {}
            if len(targets) >= 1 and targets[0] in ping_snap:
                telemetry["ping_cnx_ms"] = ping_snap[targets[0]].get("last_ms")
            if len(targets) >= 2 and targets[1] in ping_snap:
                telemetry["ping_internet_ms"] = ping_snap[targets[1]].get("last_ms")

            storage.save_telemetry(telemetry)
            print_status(telemetry, tle_fetcher.satellite_count, ping_snap)

            elapsed = time.time() - loop_start
            time.sleep(max(0.05, poll_interval - elapsed))
    finally:
        pinger.stop()
        print("Stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
