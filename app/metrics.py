"""In-process counters exposed at GET /metrics (JSON). Enough for a demo;
in production these become Prometheus/OTel instruments with the same names."""

from __future__ import annotations

import statistics
import threading
from collections import Counter, defaultdict, deque


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.counters: Counter = Counter()
        self.latency_ms: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self.tokens = Counter()
        self.cost_usd = 0.0

    def inc(self, name: str, n: int = 1, **labels) -> None:
        key = name + ("{" + ",".join(f"{k}={v}" for k, v in sorted(labels.items())) + "}" if labels else "")
        with self._lock:
            self.counters[key] += n

    def observe_run(self, kind: str, ms: float, prompt: int, completion: int, cost: float) -> None:
        with self._lock:
            self.latency_ms[kind].append(ms)
            self.tokens["prompt"] += prompt
            self.tokens["completion"] += completion
            self.cost_usd += cost

    def snapshot(self) -> dict:
        with self._lock:
            lat = {}
            for k, v in self.latency_ms.items():
                xs = sorted(v)
                if xs:
                    lat[k] = {"n": len(xs), "p50": round(statistics.median(xs), 1),
                              "p95": round(xs[min(len(xs) - 1, int(0.95 * len(xs)))], 1)}
            return {"counters": dict(self.counters), "latency_ms": lat, "tokens": dict(self.tokens),
                    "cost_usd_estimate": round(self.cost_usd, 6)}


metrics = Metrics()
