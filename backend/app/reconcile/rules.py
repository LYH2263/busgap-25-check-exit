"""间隔对账 · 规则判读层。

只基于抽取层产出的 RawMaterials 做四项核对，不重新接触数据库或夹具文件：

1. PAIR_HIT       报告事件的班次对（早班/晚班）必须都能在到站表命中（同站、未取消）；
2. BUNCH_GAP      串车事件与间隔严格一致：标串车 ⇔ 实际间隔严格小于串车阈；
3. LARGE_GAP      大间隔事件与间隔严格一致：标大间隔 ⇔ 实际间隔严格大于大间隔阈；
4. TIMELINE_COUNT 时间轴点数必须等于该站「未取消」到站数。

任一项失败，整次对账即为非 0 退出 —— 只打印警告但退出 0 不允许。
结构问题（到站缺字段等）优先于语义判读，直接判 EXIT_MISSING_FIELD，
缺字段的行不参与配对，因此绝不会被误判成「造假串车」。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.reconcile.model import RawMaterials

# 退出码常量（甲/乙）：两者互不相等，且都非 0。
EXIT_OK = 0
EXIT_CONTRADICTION = 10   # 常量甲：四项判读与展示存在矛盾
EXIT_MISSING_FIELD = 20   # 常量乙：原材料缺字段（与甲不同码）

CHECK_PAIR_HIT = "PAIR_HIT"
CHECK_BUNCH_GAP = "BUNCH_GAP"
CHECK_LARGE_GAP = "LARGE_GAP"
CHECK_TIMELINE_COUNT = "TIMELINE_COUNT"


@dataclass
class CheckFailure:
    check: str
    message: str


@dataclass
class ReconcileResult:
    exit_code: int = EXIT_OK
    missing_fields: list[tuple[str, str, str]] = field(default_factory=list)
    failures: list[CheckFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == EXIT_OK


def _active_arrival_index(raw: RawMaterials) -> dict[tuple[str, str], "object"]:
    """(班次, 站点) -> 未取消到站；取消班次不参与任何间隔与命中核对。"""
    index: dict[tuple[str, str], object] = {}
    for a in raw.arrivals:
        if a.cancelled:
            continue
        index[(a.trip_no, a.stop_name)] = a
    return index


def reconcile(raw: RawMaterials) -> ReconcileResult:
    # —— 结构裁决优先：缺字段直接走常量乙，且不进入下面任何语义判读 ——
    if raw.missing_fields or raw.line is None:
        return ReconcileResult(
            exit_code=EXIT_MISSING_FIELD,
            missing_fields=list(raw.missing_fields)
            or [("line", "line=?", "line")],
        )

    result = ReconcileResult()
    line = raw.line
    index = _active_arrival_index(raw)

    def gap_of(stop: str, earlier: str, later: str):
        a = index.get((earlier, stop))
        b = index.get((later, stop))
        if a is None or b is None:
            return None
        return (b.actual_arrive - a.actual_arrive).total_seconds() / 60.0

    # —— 四项核对 ——
    for ev in raw.report_events:
        earlier_hit = (ev.earlier_trip, ev.stop_name) in index
        later_hit = (ev.later_trip, ev.stop_name) in index

        # 第 1 项：事件班次对必须在到站命中
        if not earlier_hit or not later_hit:
            miss = [name for name, hit in ((ev.earlier_trip, earlier_hit), (ev.later_trip, later_hit)) if not hit]
            result.failures.append(CheckFailure(
                CHECK_PAIR_HIT,
                f"[{ev.stop_name}] {ev.earlier_trip} → {ev.later_trip} 报告事件班次对在到站表未命中："
                f"缺少 {('、'.join(miss))} 的未取消到站",
            ))
            continue  # 命中都不成立，无法重算间隔，2/3 两项不再重复报错

        gap = gap_of(ev.stop_name, ev.earlier_trip, ev.later_trip)

        # 第 2 项：串车 ⇔ 间隔严格小于串车阈（双向，治「实串车却标正常」）
        is_bunch = gap < line.bunch_threshold
        labeled_bunch = ev.status == "bunching"
        if labeled_bunch and not is_bunch:
            result.failures.append(CheckFailure(
                CHECK_BUNCH_GAP,
                f"[{ev.stop_name}] {ev.earlier_trip} → {ev.later_trip} 标为串车，"
                f"但实际间隔 {gap:.2f} 分钟未严格小于串车阈 {line.bunch_threshold}",
            ))
        elif is_bunch and not labeled_bunch:
            result.failures.append(CheckFailure(
                CHECK_BUNCH_GAP,
                f"[{ev.stop_name}] {ev.earlier_trip} → {ev.later_trip} 实际间隔 {gap:.2f} 分钟"
                f"严格小于串车阈 {line.bunch_threshold}（实串车），报告却标注为「{ev.status}」",
            ))

        # 第 3 项：大间隔 ⇔ 间隔严格大于大间隔阈（双向）
        is_large = gap > line.large_threshold
        labeled_large = ev.status == "large_gap"
        if labeled_large and not is_large:
            result.failures.append(CheckFailure(
                CHECK_LARGE_GAP,
                f"[{ev.stop_name}] {ev.earlier_trip} → {ev.later_trip} 标为大间隔，"
                f"但实际间隔 {gap:.2f} 分钟未严格大于大间隔阈 {line.large_threshold}",
            ))
        elif is_large and not labeled_large:
            result.failures.append(CheckFailure(
                CHECK_LARGE_GAP,
                f"[{ev.stop_name}] {ev.earlier_trip} → {ev.later_trip} 实际间隔 {gap:.2f} 分钟"
                f"严格大于大间隔阈 {line.large_threshold}（实大间隔），报告却标注为「{ev.status}」",
            ))

    # 第 4 项：时间轴点数 == 该站未取消到站数
    stop = raw.timeline_stop
    if stop:
        expected = sum(1 for a in raw.arrivals if a.stop_name == stop and not a.cancelled)
        actual = len(raw.timeline_points)
        if actual != expected:
            result.failures.append(CheckFailure(
                CHECK_TIMELINE_COUNT,
                f"站点「{stop}」时间轴 {actual} 个点，到站表未取消到站 {expected} 条，数量不一致",
            ))

    if result.failures:
        result.exit_code = EXIT_CONTRADICTION
    return result
