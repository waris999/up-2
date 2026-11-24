import platform
import re
import subprocess
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional


_RTT_REGEX = re.compile(r"(?:time|temps)[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)
_TTL_REGEX = re.compile(r"ttl[=:]\s*(\d+)", re.IGNORECASE)


def ping_once(target: str, timeout_sec: float = 1.0) -> Dict[str, Any]:
    """Execute a single ICMP ping cross-platform (Linux / Windows) and parse RTT."""
    is_windows = platform.system().lower().startswith("win")
    if is_windows:
        timeout_ms = max(100, int(timeout_sec * 1000))
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), target]
    else:
        timeout_s = max(1, int(round(timeout_sec)))
        cmd = ["ping", "-c", "1", "-W", str(timeout_s), target]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec + 2.0,
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        rtt_match = _RTT_REGEX.search(output)
        ttl_match = _TTL_REGEX.search(output)

        if proc.returncode == 0 and rtt_match:
            return {
                "target": target,
                "success": True,
                "rtt_ms": float(rtt_match.group(1)),
                "ttl": int(ttl_match.group(1)) if ttl_match else None,
                "error": None,
            }
        if proc.returncode == 0 and "time<1ms" in output.lower():
            return {
                "target": target,
                "success": True,
                "rtt_ms": 0.5,
                "ttl": int(ttl_match.group(1)) if ttl_match else None,
                "error": None,
            }
        return {
            "target": target,
            "success": False,
            "rtt_ms": None,
            "ttl": None,
            "error": "Timeout or unreachable (code {})".format(proc.returncode),
        }
    except Exception as exc:
        return {
            "target": target,
            "success": False,
            "rtt_ms": None,
            "ttl": None,
            "error": str(exc),
        }


class RealtimePinger:
    """Runs background real-time ping loops for one or more targets."""

    def __init__(
        self,
        targets: List[str],
        interval_sec: float = 1.0,
        timeout_sec: float = 1.0,
        on_result: Optional[Callable[[Dict[str, Any]], None]] = None,
        window_size: int = 60,
    ) -> None:
        self.targets = targets
        self.interval_sec = interval_sec
        self.timeout_sec = timeout_sec
        self.on_result = on_result
        self._stop_event = threading.Event()
        self._threads: List[threading.Thread] = []
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict[str, Any]] = {}

        for target in targets:
            self._stats[target] = {
                "sent": 0,
                "received": 0,
                "last_ms": None,
                "last_ok": None,
                "last_error": None,
                "history": deque(maxlen=window_size),
            }

    def start(self) -> None:
        self._stop_event.clear()
        for target in self.targets:
            t = threading.Thread(
                target=self._loop, args=(target,), daemon=True, name="ping-{}".format(target)
            )
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads.clear()

    def _loop(self, target: str) -> None:
        while not self._stop_event.is_set():
            started = time.time()
            res = ping_once(target, timeout_sec=self.timeout_sec)

            with self._lock:
                st = self._stats[target]
                st["sent"] += 1
                st["last_ok"] = res["success"]
                st["last_error"] = res["error"]
                if res["success"] and res["rtt_ms"] is not None:
                    st["received"] += 1
                    st["last_ms"] = res["rtt_ms"]
                    hist: Deque[float] = st["history"]
                    hist.append(res["rtt_ms"])
                else:
                    st["last_ms"] = None

            if self.on_result:
                try:
                    self.on_result(res)
                except Exception:
                    pass

            elapsed = time.time() - started
            sleep_for = max(0.05, self.interval_sec - elapsed)
            self._stop_event.wait(sleep_for)

    def get_snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            snap: Dict[str, Dict[str, Any]] = {}
            for target, st in self._stats.items():
                sent = st["sent"]
                recv = st["received"]
                hist = list(st["history"])
                loss_pct = ((sent - recv) / sent * 100.0) if sent > 0 else 0.0
                snap[target] = {
                    "sent": sent,
                    "received": recv,
                    "loss_pct": round(loss_pct, 1),
                    "last_ms": st["last_ms"],
                    "avg_ms": round(sum(hist) / len(hist), 2) if hist else None,
                    "min_ms": round(min(hist), 2) if hist else None,
                    "max_ms": round(max(hist), 2) if hist else None,
                    "last_ok": st["last_ok"],
                    "last_error": st["last_error"],
                }
            return snap
