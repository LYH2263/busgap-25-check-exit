"""Bus bunching: planned headway vs actual arrival gaps."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime


def arrival_is_cancelled(arrival: object) -> bool:
    """未取消到站口径：当前模型没有取消字段，所有到站均视为未取消。

    ORM 行与夹具 dict 通用；将来模型增加 cancelled 列后此处自动生效，
    报告检测、时间轴与对账 CLI 随之统一过滤。
    """
    if isinstance(arrival, dict):
        return bool(arrival.get("cancelled", False))
    return bool(getattr(arrival, "cancelled", False))

@dataclass
class GapEvent:
    stop_name: str
    earlier_trip: str
    later_trip: str
    gap_min: float
    planned_headway_min: float
    status: str
    suggestion: str

def classify_gap(gap_min: float, planned_headway_min: float, bunch_threshold: float, large_threshold: float) -> tuple[str, str]:
    if gap_min < bunch_threshold:
        return ("bunching", f"间隔 {gap_min:.1f} 分钟低于串车阈值 {bunch_threshold}，建议后车缓行或抽稀。")
    if gap_min > large_threshold:
        return ("large_gap", f"间隔 {gap_min:.1f} 分钟超过大间隔阈值 {large_threshold}，建议前车减速或加发。")
    return ("normal", f"间隔接近计划 {planned_headway_min:.1f} 分钟，保持即可。")

def detect_bunching(arrivals: list[dict], planned_headway_min: float, bunch_threshold: float, large_threshold: float) -> list[GapEvent]:
    by_stop: dict[str, list[dict]] = {}
    for a in arrivals:
        by_stop.setdefault(a["stop_name"], []).append(a)
    events: list[GapEvent] = []
    for stop, items in by_stop.items():
        items = sorted(items, key=lambda x: x["actual_arrive"])
        for i in range(1, len(items)):
            prev, cur = items[i - 1], items[i]
            gap_min = (cur["actual_arrive"] - prev["actual_arrive"]).total_seconds() / 60.0
            status, suggestion = classify_gap(gap_min, planned_headway_min, bunch_threshold, large_threshold)
            events.append(GapEvent(stop, prev["trip_no"], cur["trip_no"], round(gap_min, 2), planned_headway_min, status, suggestion))
    return events

def events_to_dicts(events: list[GapEvent]) -> list[dict]:
    return [asdict(e) for e in events]


def arrivals_to_payload(arrivals: list, trip_no_map: dict[int, str]) -> list[dict]:
    """ORM 到站行 -> detect_bunching 入参（与 /reports/run 同一构造口径）。"""
    return [{"stop_name": a.stop_name, "trip_no": trip_no_map[a.trip_id],
             "actual_arrive": a.actual_arrive} for a in arrivals]


def build_timeline_marks(arrivals: list, trip_no_map: dict[int, str]) -> list[dict]:
    """某站到站行 -> 时间轴标记（与 /reports/timeline 同一构造口径）。

    取消的到站由调用方在传入前过滤；本函数只负责排序与相对位置。
    """
    arrivals = sorted(arrivals, key=lambda a: a.actual_arrive)
    if not arrivals:
        return []
    t0 = arrivals[0].actual_arrive
    span = max((arrivals[-1].actual_arrive - t0).total_seconds(), 1)
    return [{"trip_no": trip_no_map[a.trip_id], "actual_arrive": a.actual_arrive.isoformat(),
             "pct": round((a.actual_arrive - t0).total_seconds() / span * 100, 2)} for a in arrivals]
