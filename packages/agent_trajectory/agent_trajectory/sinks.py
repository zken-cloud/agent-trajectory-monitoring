"""Where trajectory records go.

Three sinks behind one interface so the same agent code runs on a laptop, on
GKE, and at a customer without edits. The lab uses jsonl -> bigquery -> pubsub
in that order across Labs 2.1-2.2.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path
from typing import Any

from .schema import to_row


class Sink:
    def emit(self, record: Any) -> None: ...
    def flush(self) -> None: ...


class JsonlSink(Sink):
    """Local file sink. No GCP required - this is what makes Lab 2.1 debuggable
    and what the catch-up scripts replay."""

    def __init__(self, directory: str | Path = "./telemetry_out"):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, record: Any) -> None:
        path = self.dir / f"{record.TABLE}.jsonl"
        with self._lock, path.open("a") as fh:
            fh.write(json.dumps(to_row(record), default=str) + "\n")

    def flush(self) -> None:
        pass


class BigQuerySink(Sink):
    """Batched async writes. Never blocks the agent loop - a security pipeline
    that adds latency to tool calls gets switched off."""

    def __init__(self, project: str, dataset: str, batch_size: int = 50,
                 flush_seconds: float = 5.0):
        from google.cloud import bigquery
        self._client = bigquery.Client(project=project)
        self._dataset = dataset
        self._project = project
        self._batch_size = batch_size
        self._q: queue.Queue = queue.Queue(maxsize=10_000)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, args=(flush_seconds,),
                                        daemon=True)
        self._thread.start()

    def emit(self, record: Any) -> None:
        try:
            self._q.put_nowait((record.TABLE, to_row(record)))
        except queue.Full:
            # Drop rather than block the agent. Loudly, so it shows up as a gap.
            print("[agent_trajectory] telemetry queue full, dropping record")

    def _worker(self, flush_seconds: float) -> None:
        pending: dict[str, list[dict]] = {}
        count = 0
        while not self._stop.is_set():
            try:
                table, row = self._q.get(timeout=flush_seconds)
                pending.setdefault(table, []).append(row)
                count += 1
            except queue.Empty:
                pass
            if count >= self._batch_size or (pending and self._q.empty()):
                self._drain(pending)
                pending, count = {}, 0
        self._drain(pending)

    def _drain(self, pending: dict[str, list[dict]]) -> None:
        for table, rows in pending.items():
            if not rows:
                continue
            ref = f"{self._project}.{self._dataset}.{table}"
            errors = self._client.insert_rows_json(ref, rows)
            if errors:
                print(f"[agent_trajectory] BQ insert errors on {table}: {errors[:2]}")

    def flush(self) -> None:
        self._stop.set()
        self._thread.join(timeout=15)


class PubSubSink(Sink):
    """Streaming hop for sub-minute detection latency. Deployed by the
    telemetry-pipeline/ Terraform module, not hand-built in the lab."""

    def __init__(self, project: str, topic: str):
        from google.cloud import pubsub_v1
        self._publisher = pubsub_v1.PublisherClient()
        self._topic = self._publisher.topic_path(project, topic)
        self._futures: list = []

    def emit(self, record: Any) -> None:
        payload = json.dumps({"table": record.TABLE, "row": to_row(record)},
                             default=str).encode()
        self._futures.append(
            self._publisher.publish(self._topic, payload, table=record.TABLE)
        )
        if len(self._futures) > 500:
            self.flush()

    def flush(self) -> None:
        for f in self._futures:
            try:
                f.result(timeout=30)
            except Exception as exc:
                print(f"[agent_trajectory] pubsub publish failed: {exc}")
        self._futures.clear()


class MultiSink(Sink):
    def __init__(self, *sinks: Sink):
        self._sinks = [s for s in sinks if s is not None]

    def emit(self, record: Any) -> None:
        for s in self._sinks:
            s.emit(record)

    def flush(self) -> None:
        for s in self._sinks:
            s.flush()


def from_env() -> Sink:
    """Sink selection by env var so the same image runs in every lab.

    TRAJECTORY_SINK = jsonl | bigquery | pubsub | bigquery+pubsub
    """
    kind = os.getenv("TRAJECTORY_SINK", "jsonl").lower()
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    dataset = os.getenv("TRAJECTORY_DATASET", "trajectory")
    topic = os.getenv("TRAJECTORY_TOPIC", "agent-telemetry")
    parts: list[Sink] = []
    if "jsonl" in kind:
        parts.append(JsonlSink(os.getenv("TRAJECTORY_OUT_DIR", "./telemetry_out")))
    if "bigquery" in kind:
        parts.append(BigQuerySink(project, dataset))
    if "pubsub" in kind:
        parts.append(PubSubSink(project, topic))
    return MultiSink(*parts) if len(parts) != 1 else parts[0]
