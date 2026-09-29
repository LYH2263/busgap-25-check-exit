"""间隔对账——规则判读层。

只基于抽取层产物 RawBundle 做判读，不重新查库、不做进程探活。
退出码常量（互不相等）：
    EXIT_OK               0  四项全部通过
    EXIT_RECON_MISMATCH   甲 四项判读出现矛盾（如「实串车却标正常」）
    EXIT_MALFORMED        乙 原材料缺字段/字段不可解析（点名缺项，绝不并入甲）
缺字段先于四项判读，因此缺字段夹具永远不会被误判成「造假串车码（甲）」。

四项（与报告页、到站表、时间轴同一口径，严格不等号与 bunch_engine.classify_gap 一致）：
    1. 每个报告事件的班次对（earlier_trip, later_trip）都能在到站记录命中同站到站；
    2. 串车事件间隔严格小于串车阈（gap < bunch_threshold），标 normal 的事件同样
       不得严格越过串车阈——「实串车却标正常」在此项失败；
    3. 大间隔事件间隔严格大于大间隔阈（gap > large_threshold），标 normal 的事件
       同样不得严格越过大间隔阈；
    4. 时间轴点数等于该时间轴站点的未取消到站数。
任一项失败即整次对账返回甲（非 0），不得只打警告仍退出 0。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

EXIT_OK = 0
EXIT_RECON_MISMATCH = 2  # 常量甲：四项矛盾
EXIT_MALFORMED = 3       # 常量乙：原材料缺字段（不等于甲）

_STATUS_BUNCHING = "bunching"
_STATUS_LARGE = "large_gap"
_STATUS_NORMAL = "normal"
_VALID_STATUSES = {_STATUS_BUNCHING, _STATUS_LARGE, _STATUS_NORMAL}

_LINE_FIELDS = ("code", "name", "planned_headway_min", "bunch_threshold", "large_threshold")
_ARRIVAL_FIELDS = ("stop_name", "trip_no", "actual_arrive")
_EVENT_FIELDS = ("stop_name", "earlier_trip", "later_trip", "gap_min", "status")


@dataclass
class Verdict:
    exit_code: int
    lines: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _missing(obj: dict, fields: tuple[str, ...]) -> list[str]:
    return [k for k in fields if k not in obj or obj[k] is None or obj[k] == ""]


def validate_bundle(bundle: Any) -> Verdict | None:
    """字段完备性校验。通过返回 None；否则返回乙并逐项点名。"""
    problems: list[str] = []

    line = getattr(bundle, "line", None)
    if not isinstance(line, dict):
        problems.append("line 缺失或不是对象")
    else:
        for k in _missing(line, _LINE_FIELDS):
            problems.append(f"line 缺字段: {k}")
        for k in ("planned_headway_min", "bunch_threshold", "large_threshold"):
            v = line.get(k)
            if v is not None and not _is_number(v):
                problems.append(f"line.{k} 不是数值: {v!r}")

    arrivals = getattr(bundle, "arrivals", None)
    if not isinstance(arrivals, list):
        problems.append("arrivals 缺失或不是数组")
    else:
        for i, a in enumerate(arrivals):
            if not isinstance(a, dict):
                problems.append(f"arrivals[{i}] 不是对象")
                continue
            for k in _missing(a, _ARRIVAL_FIELDS):
                problems.append(f"arrivals[{i}]（站点={a.get('stop_name')!r} 班次={a.get('trip_no')!r}）缺字段: {k}")
            raw = a.get("actual_arrive")
            if raw not in (None, ""):
                _parse_dt(raw, f"arrivals[{i}].actual_arrive", problems)

    events = getattr(bundle, "events", None)
    if not isinstance(events, list):
        problems.append("events 缺失或不是数组")
    else:
        for i, e in enumerate(events):
            if not isinstance(e, dict):
                problems.append(f"events[{i}] 不是对象")
                continue
            for k in _missing(e, _EVENT_FIELDS):
                problems.append(f"events[{i}] 缺字段: {k}")
            if e.get("gap_min") is not None and not _is_number(e.get("gap_min")):
                problems.append(f"events[{i}].gap_min 不是数值: {e.get('gap_min')!r}")
            status = e.get("status")
            if status is not None and status not in _VALID_STATUSES:
                problems.append(f"events[{i}].status 非法: {status!r}")

    timeline = getattr(bundle, "timeline", None)
    if not isinstance(timeline, dict):
        problems.append("timeline 缺失或不是对象")
    elif not isinstance(timeline.get("marks"), list):
        problems.append("timeline.marks 缺失或不是数组")

    if problems:
        lines = ["[对账失败] 原材料字段不完整，未进入四项判读："]
        lines += [f"  - {p}" for p in problems]
        return Verdict(EXIT_MALFORMED, lines)
    return None


def _parse_dt(raw: Any, where: str, problems: list[str]) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        problems.append(f"{where} 不是可解析的时间: {raw!r}")
        return None


def evaluate(bundle: Any) -> Verdict:
    """判读入口：先字段（乙），后四项（甲）。"""
    malformed = validate_bundle(bundle)
    if malformed is not None:
        return malformed

    # 时间字段在 validate 阶段已确认可解析。
    arrivals = bundle.arrivals
    events = bundle.events
    line = bundle.line
    bunch_thr = float(line["bunch_threshold"])
    large_thr = float(line["large_threshold"])
    timeline_stop = bundle.timeline.get("stop_name", "")
    marks = bundle.timeline.get("marks", [])

    failures: list[str] = []

    # 未取消到站（与报告检测/时间轴同口径）。
    active = [a for a in arrivals if not a.get("cancelled", False)]
    active_at: dict[str, list[dict]] = {}
    for a in active:
        active_at.setdefault(a["stop_name"], []).append(a)

    # 第 1 项：事件班次对必须能在到站命中同站未取消到站。
    for i, e in enumerate(events):
        present = {a["trip_no"] for a in active_at.get(e["stop_name"], [])}
        for side in ("earlier_trip", "later_trip"):
            if e[side] not in present:
                failures.append(
                    f"第1项 班次命中失败：events[{i}] {e['stop_name']} {e['earlier_trip']}→{e['later_trip']} "
                    f"的 {side}={e[side]} 在到站表无未取消到站")

    # 第 2/3 项：严格不等号，标 normal 也不得越过任一阈（堵住「实串车却标正常」）。
    for i, e in enumerate(events):
        gap = float(e["gap_min"])
        status = e["status"]
        pair = f"events[{i}] {e['stop_name']} {e['earlier_trip']}→{e['later_trip']} gap={gap}"
        if status == _STATUS_BUNCHING:
            if not gap < bunch_thr:
                failures.append(f"第2项 {pair} 标记串车，但间隔未严格小于串车阈 {bunch_thr}")
        elif status == _STATUS_LARGE:
            if not gap > large_thr:
                failures.append(f"第3项 {pair} 标记大间隔，但间隔未严格大于大间隔阈 {large_thr}")
        else:  # normal
            if gap < bunch_thr:
                failures.append(
                    f"第2项 {pair} 实串车却标正常：间隔 {gap} 严格小于串车阈 {bunch_thr}")
            if gap > large_thr:
                failures.append(
                    f"第3项 {pair} 实大间隔却标正常：间隔 {gap} 严格大于大间隔阈 {large_thr}")

    # 第 4 项：时间轴点数 == 该站未取消到站数。
    expected_points = len(active_at.get(timeline_stop, []))
    actual_points = len(marks)
    if actual_points != expected_points:
        failures.append(
            f"第4项 时间轴「{timeline_stop}」点数 {actual_points} != 该站未取消到站数 {expected_points}")

    head = (f"[对账] 数据源={bundle.source} 线路={line['code']}（{line['name']}） "
            f"到站原始行={len(arrivals)}（未取消 {len(active)}） 报告事件={len(events)} "
            f"时间轴「{timeline_stop}」点数={actual_points} "
            f"阈值 串车<{bunch_thr} 大间隔>{large_thr}")
    if failures:
        return Verdict(EXIT_RECON_MISMATCH,
                       [head, f"[对账失败] 四项判读 {len(failures)} 处矛盾："]
                       + [f"  - {p}" for p in failures])
    return Verdict(EXIT_OK, [head, "[对账通过] 四项全部一致：班次对可命中、串车/大间隔严格过阈、时间轴点数相符。"])
