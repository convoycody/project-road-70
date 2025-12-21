from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

EVENT_STATUSES = {"open", "acknowledged", "closed"}


def _now_ts() -> int:
    return int(time.time())


def ensure_event_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS road_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          aggregate_id INTEGER,
          segment_id TEXT,
          event_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          score REAL,
          status TEXT NOT NULL DEFAULT 'open',
          reason TEXT NOT NULL,
          analysis_payload TEXT,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL
        );
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_road_events_segment
        ON road_events(segment_id, created_at);
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_road_events_aggregate
        ON road_events(aggregate_id);
        """
    )
    con.commit()


def _f(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _event(
    event_type: str,
    severity: str,
    reason: str,
    score: Optional[float] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "event_type": event_type,
        "severity": severity,
        "score": score,
        "reason": reason,
        "analysis_payload": payload or {},
    }


def analyze_aggregate(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rule-based analyzer to flag notable road events and telemetry issues."""
    events: List[Dict[str, Any]] = []

    roughness = _f(data.get("road_roughness"))
    shocks = _f(data.get("shock_events"))
    confidence = _f(data.get("confidence"))
    quality_note = (data.get("quality_note") or "").lower()

    if roughness is not None:
        if roughness >= 0.55:
            events.append(
                _event(
                    "rough_surface",
                    "major",
                    "roughness >= 0.55",
                    score=roughness,
                    payload={"roughness": roughness},
                )
            )
        elif roughness >= 0.35:
            events.append(
                _event(
                    "rough_surface",
                    "moderate",
                    "roughness >= 0.35",
                    score=roughness,
                    payload={"roughness": roughness},
                )
            )

    if shocks is not None:
        if shocks >= 6:
            events.append(
                _event(
                    "shock_cluster",
                    "major",
                    "shock_events >= 6",
                    score=shocks,
                    payload={"shock_events": shocks},
                )
            )
        elif shocks >= 3:
            events.append(
                _event(
                    "shock_cluster",
                    "moderate",
                    "shock_events >= 3",
                    score=shocks,
                    payload={"shock_events": shocks},
                )
            )

    if confidence is not None and confidence < 0.3:
        events.append(
            _event(
                "low_confidence",
                "minor",
                "confidence < 0.30",
                score=confidence,
                payload={"confidence": confidence},
            )
        )

    if "sanity:" in quality_note or "lat_out_of_range" in quality_note or "lon_out_of_range" in quality_note:
        events.append(
            _event(
                "telemetry_issue",
                "minor",
                "quality_note indicates telemetry anomaly",
                payload={"quality_note": quality_note},
            )
        )

    return events


def insert_events(
    con: sqlite3.Connection,
    aggregate_id: int,
    segment_id: Optional[str],
    events: Iterable[Dict[str, Any]],
) -> int:
    ensure_event_tables(con)
    now = _now_ts()
    count = 0
    for ev in events:
        con.execute(
            """
            INSERT INTO road_events(
              aggregate_id, segment_id, event_type, severity, score, status,
              reason, analysis_payload, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(aggregate_id) if aggregate_id else None,
                segment_id,
                ev["event_type"],
                ev["severity"],
                ev.get("score"),
                "open",
                ev["reason"],
                json.dumps(ev.get("analysis_payload") or {}),
                now,
                now,
            ),
        )
        count += 1
    if count:
        con.commit()
    return count


def analyze_and_store(
    con: sqlite3.Connection,
    aggregate_id: int,
    data: Dict[str, Any],
    segment_id: Optional[str] = None,
) -> int:
    events = analyze_aggregate(data)
    if not events:
        return 0
    return insert_events(con, aggregate_id, segment_id, events)
