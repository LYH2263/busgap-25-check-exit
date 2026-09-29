"""间隔对账：原材料数据结构。

本模块只描述「从到站表 / 报告事件 / 时间轴原始行」收集上来的原始形态，
不做任何判读。抽取层（extract.py）与判读层（rules.py）共用这里的类型，
但判读规则本身不写在这里。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class RawArrival:
    """到站表原始行（与 GET /arrivals 同口径，cancelled=False 才计入间隔/时间轴）。"""

    trip_no: str
    stop_name: str
    actual_arrive: datetime
    cancelled: bool = False
    stop_seq: Optional[int] = None
    trip_id: Optional[int] = None


@dataclass(frozen=True)
class RawReportEvent:
    """串车报告中的原始事件行（GET /reports 或 /reports/run 的 summary_json 单项）。"""

    stop_name: str
    earlier_trip: str
    later_trip: str
    gap_min: float
    status: str


@dataclass(frozen=True)
class RawTimelinePoint:
    """时间轴原始标记点（GET /reports/timeline 的 marks 单项）。"""

    stop_name: str
    trip_no: str
    actual_arrive: datetime


@dataclass(frozen=True)
class RawLine:
    """线路阈值原材料。"""

    id: int
    code: str
    name: str
    bunch_threshold: float
    large_threshold: float


@dataclass
class RawMaterials:
    """一次对账的全部原材料，外加收集过程中发现的结构问题（缺字段等）。"""

    line: Optional[RawLine] = None
    timeline_stop: Optional[str] = None
    arrivals: list[RawArrival] = field(default_factory=list)
    report_events: list[RawReportEvent] = field(default_factory=list)
    timeline_points: list[RawTimelinePoint] = field(default_factory=list)
    # 元素形如 (来源, 班次/站点定位串, 缺失字段名)
    missing_fields: list[tuple[str, str, str]] = field(default_factory=list)
