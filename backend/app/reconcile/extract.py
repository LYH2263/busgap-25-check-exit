"""间隔对账 · 原材料抽取层。

职责仅限「收集」：到站表原始行、报告事件原始行、时间轴原始行，以及线路阈值。
本模块禁止导入 app.reconcile.rules —— 抽取不做任何判读，判读只能基于抽取出的
RawMaterials 进行（依赖方向由 tests/test_reconcile.py 的 AST 检查固化）。

数据来源二选一，由命令入口决定：
* 夹具：一份 JSON，形状模拟 GET /arrivals、GET /reports、GET /reports/timeline 的返回；
* 数据库：经 SQLAlchemy 读取线上同一张表，口径与 app/api/reports.py 完全一致
  （时间轴只收未取消到站，间隔检测同样排除 cancelled）。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.reconcile.model import (
    RawArrival,
    RawLine,
    RawMaterials,
    RawReportEvent,
    RawTimelinePoint,
)

ARRIVAL_REQUIRED = ("trip_no", "stop_name", "actual_arrive")
EVENT_REQUIRED = ("stop_name", "earlier_trip", "later_trip", "gap_min", "status")
MARK_REQUIRED = ("trip_no", "actual_arrive")


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _missing(obj: dict, fields: tuple[str, ...]) -> list[str]:
    return [f for f in fields if obj.get(f) is None or (isinstance(obj.get(f), str) and not obj.get(f).strip())]


def extract_from_fixture(path: str) -> RawMaterials:
    """从夹具 JSON 收集原材料。

    夹具形状：
    {
      "line": {"code": "B12", "bunch_threshold": 3.0, "large_threshold": 15.0, ...},
      "arrivals": [{"trip_no", "stop_name", "actual_arrive", "cancelled"?}],
      "report_events": [{"stop_name", "earlier_trip", "later_trip", "gap_min", "status"}],
      "timeline": {"stop_name": "市民中心", "marks": [{"trip_no", "actual_arrive"}]}
    }
    缺字段不抛异常、不揣测，原样登记进 RawMaterials.missing_fields，交由判读层裁决。
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    raw = RawMaterials()
    line_in = data.get("line") or {}
    line_missing = _missing(line_in, ("code", "bunch_threshold", "large_threshold"))
    if line_missing:
        for f in line_missing:
            raw.missing_fields.append(("line", f"line={line_in.get('code') or '?'}", f))
    else:
        raw.line = RawLine(
            id=int(line_in.get("id") or 0),
            code=str(line_in["code"]),
            name=str(line_in.get("name") or line_in["code"]),
            bunch_threshold=float(line_in["bunch_threshold"]),
            large_threshold=float(line_in["large_threshold"]),
        )

    for row in data.get("arrivals", []):
        loc = f"班次 {row.get('trip_no') or '?'} / 站点 {row.get('stop_name') or '?'}"
        lack = _missing(row, ARRIVAL_REQUIRED)
        if lack:
            # 缺 actual_arrive 的行绝不参与配对：缺时间的到站凑不出任何间隔，
            # 不能被当成「造假串车」的素材。
            for f in lack:
                raw.missing_fields.append(("到站表", loc, f))
            continue
        dt = _parse_dt(row["actual_arrive"])
        if dt is None:
            raw.missing_fields.append(("到站表", loc, "actual_arrive"))
            continue
        raw.arrivals.append(
            RawArrival(
                trip_no=str(row["trip_no"]),
                stop_name=str(row["stop_name"]),
                actual_arrive=dt,
                cancelled=bool(row.get("cancelled", False)),
                stop_seq=row.get("stop_seq"),
            )
        )

    for ev in data.get("report_events", []):
        loc = f"班次 {ev.get('earlier_trip') or '?'} → {ev.get('later_trip') or '?'} / 站点 {ev.get('stop_name') or '?'}"
        lack = _missing(ev, EVENT_REQUIRED)
        if lack:
            for f in lack:
                raw.missing_fields.append(("报告事件", loc, f))
            continue
        raw.report_events.append(
            RawReportEvent(
                stop_name=str(ev["stop_name"]),
                earlier_trip=str(ev["earlier_trip"]),
                later_trip=str(ev["later_trip"]),
                gap_min=float(ev["gap_min"]),
                status=str(ev["status"]),
            )
        )

    timeline = data.get("timeline") or {}
    raw.timeline_stop = str(timeline.get("stop_name") or "") or None
    for mark in timeline.get("marks", []):
        loc = f"班次 {mark.get('trip_no') or '?'} / 时间轴"
        lack = _missing(mark, MARK_REQUIRED)
        if lack:
            for f in lack:
                raw.missing_fields.append(("时间轴", loc, f))
            continue
        dt = _parse_dt(mark["actual_arrive"])
        if dt is None:
            raw.missing_fields.append(("时间轴", loc, "actual_arrive"))
            continue
        raw.timeline_points.append(
            RawTimelinePoint(
                stop_name=raw.timeline_stop or "",
                trip_no=str(mark["trip_no"]),
                actual_arrive=dt,
            )
        )
    return raw


def extract_from_db(session: Any, *, line_id: Optional[int] = None,
                    line_code: Optional[str] = None, stop_name: str = "市民中心") -> RawMaterials:
    """从运营库收集原材料（SQLAlchemy 延迟导入，夹具链路保持纯标准库可用）。

    时间轴口径与 app/api/reports.py 的 /reports/timeline 相同：只取该站
    cancelled=False 的到站并按实际到站排序；报告事件取该线最新一份
    BunchReport.summary_json（报告页展示的就是它）。
    """
    from sqlalchemy import select

    from app.models.models import Arrival, BunchReport, Line, Trip

    raw = RawMaterials(timeline_stop=stop_name)

    q = select(Line)
    if line_id is not None:
        line = session.get(Line, line_id)
    elif line_code:
        line = session.scalars(q.where(Line.code == line_code)).first()
    else:
        line = session.scalars(q.order_by(Line.id)).first()
    if line is None:
        raw.missing_fields.append(("line", f"line_id={line_id} code={line_code}", "line"))
        return raw

    raw.line = RawLine(
        id=line.id, code=line.code, name=line.name,
        bunch_threshold=line.bunch_threshold, large_threshold=line.large_threshold,
    )

    trips = session.scalars(select(Trip).where(Trip.line_id == line.id)).all()
    trip_no_map = {t.id: t.trip_no for t in trips}

    arr_rows = session.scalars(
        select(Arrival).where(Arrival.trip_id.in_(list(trip_no_map) or [-1]))
    ).all()
    for a in arr_rows:
        raw.arrivals.append(
            RawArrival(
                trip_no=trip_no_map.get(a.trip_id, f"#{a.trip_id}"),
                stop_name=a.stop_name,
                actual_arrive=a.actual_arrive,
                cancelled=getattr(a, "cancelled", False) or False,
                stop_seq=a.stop_seq,
                trip_id=a.trip_id,
            )
        )

    report = session.scalars(
        select(BunchReport).where(BunchReport.line_id == line.id).order_by(BunchReport.id.desc())
    ).first()
    if report is not None:
        for ev in json.loads(report.summary_json or "[]"):
            lack = _missing(ev, EVENT_REQUIRED)
            loc = f"报告#{report.id} 班次 {ev.get('earlier_trip') or '?'} → {ev.get('later_trip') or '?'}"
            if lack:
                for f in lack:
                    raw.missing_fields.append(("报告事件", loc, f))
                continue
            raw.report_events.append(
                RawReportEvent(
                    stop_name=str(ev["stop_name"]),
                    earlier_trip=str(ev["earlier_trip"]),
                    later_trip=str(ev["later_trip"]),
                    gap_min=float(ev["gap_min"]),
                    status=str(ev["status"]),
                )
            )

    # 与 /reports/timeline 同一查询：该站未取消到站（两边过滤条件必须保持一致）。
    marks = sorted(
        (a for a in raw.arrivals if a.stop_name == stop_name and not a.cancelled),
        key=lambda a: a.actual_arrive,
    )
    raw.timeline_points = [
        RawTimelinePoint(stop_name=stop_name, trip_no=a.trip_no, actual_arrive=a.actual_arrive)
        for a in marks
    ]
    return raw
