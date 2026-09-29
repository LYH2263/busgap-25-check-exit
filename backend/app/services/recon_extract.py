"""间隔对账——原材料抽取层。

职责边界（只做「收集」，不做「判读」）：
  * 到站原始行：与 GET /arrivals 同一构造口径；
  * 报告事件原始行：与 POST /reports/run 同一检测口径（复用 bunch_engine）；
  * 时间轴原始行：与 GET /reports/timeline 同一构造口径。

本模块禁止 import app.services.recon_rules（判读只能依赖抽取产物，反向不成立）。
直连库时只读，不做任何进程探活；夹具数据原样装载，缺字段不在此处理，留给判读层点名。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import Arrival, Line, Trip
from app.services.bunch_engine import (
    arrival_is_cancelled,
    arrivals_to_payload,
    build_timeline_marks,
    detect_bunching,
    events_to_dicts,
)


@dataclass
class RawBundle:
    """三类原始行的容器，判读层唯一允许的输入。"""
    source: str
    line: dict
    arrivals: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    timeline: dict = field(default_factory=lambda: {"stop_name": "", "marks": []})


def _line_dict(line: Line) -> dict:
    return {"id": line.id, "code": line.code, "name": line.name,
            "planned_headway_min": line.planned_headway_min,
            "bunch_threshold": line.bunch_threshold,
            "large_threshold": line.large_threshold}


def extract_from_database(db: Session, timeline_stop: str,
                          line_id: int | None = None, line_code: str | None = None) -> RawBundle:
    """只读直连库抽取。按 id 或 code 选线；报告事件现场重算，口径与报告页完全一致。"""
    if line_id is not None:
        line = db.get(Line, line_id)
    elif line_code is not None:
        line = db.scalars(select(Line).where(Line.code == line_code)).first()
    else:
        raise ValueError("必须指定 line_id 或 line_code")
    if line is None:
        raise LookupError(f"线路不存在: line_id={line_id!r} line_code={line_code!r}")

    trips = db.scalars(select(Trip).where(Trip.line_id == line.id)).all()
    trip_ids = [t.id for t in trips]
    trip_no_map = {t.id: t.trip_no for t in trips}

    # 到站原始行：与 GET /arrivals 同形（actual_arrive 为 ISO 字符串），保留取消标记。
    rows = db.scalars(
        select(Arrival).where(Arrival.trip_id.in_(trip_ids or [-1]))
        .order_by(Arrival.actual_arrive)).all()
    arrivals: list[dict] = []
    for a in rows:
        arrivals.append({
            "id": a.id, "trip_id": a.trip_id, "trip_no": trip_no_map.get(a.trip_id),
            "line_id": line.id, "stop_name": a.stop_name, "stop_seq": a.stop_seq,
            "actual_arrive": a.actual_arrive.isoformat(),
            "cancelled": arrival_is_cancelled(a),
        })

    # 报告事件原始行：与 POST /reports/run 同路径（取消到站不参与）。
    active = [a for a in rows if not arrival_is_cancelled(a)]
    payload = arrivals_to_payload(active, trip_no_map)
    events = events_to_dicts(detect_bunching(
        payload, line.planned_headway_min, line.bunch_threshold, line.large_threshold))

    # 时间轴原始行：与 GET /reports/timeline 同形（取消到站不参与）。
    stop_rows = [a for a in active if a.stop_name == timeline_stop]
    marks = build_timeline_marks(stop_rows, trip_no_map)

    return RawBundle(source="database", line=_line_dict(line), arrivals=arrivals,
                     events=events, timeline={"stop_name": timeline_stop, "marks": marks})


def bundle_from_obj(obj: dict, source: str = "fixture") -> RawBundle:
    """把夹具 dict 原样装成 RawBundle，不做任何判读或字段兜底。"""
    line = obj.get("line") or {}
    arrivals = obj.get("arrivals") or []
    events = obj.get("events") or []
    timeline = obj.get("timeline") or {"stop_name": "", "marks": []}
    return RawBundle(source=source, line=dict(line),
                     arrivals=list(arrivals), events=list(events),
                     timeline={"stop_name": timeline.get("stop_name", ""),
                               "marks": list(timeline.get("marks", []))})


def load_fixture(path: str | Path) -> RawBundle:
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    return bundle_from_obj(obj, source=f"fixture:{Path(path).name}")
