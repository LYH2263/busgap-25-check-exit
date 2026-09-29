"""间隔对账 · 命令入口。

入口不做判读、不做抽取细节，只负责：选择数据来源（库或夹具）→ 抽取 → 判读 →
打印结论并以判读层给出的常量作为退出码。可重复执行，全程只读、不做进程探活。

用法：
    # 对运营库（默认线路 B12、时间轴站点「市民中心」）
    python -m app.reconcile.cli
    python -m app.reconcile.cli --db --line-id 1 --stop 市民中心

    # 对夹具
    python -m app.reconcile.cli --fixture tests/fixtures/seed_b12.json
"""
from __future__ import annotations

import argparse
import sys

from app.reconcile.extract import extract_from_db, extract_from_fixture
from app.reconcile.rules import (
    EXIT_CONTRADICTION,
    EXIT_MISSING_FIELD,
    EXIT_OK,
    reconcile,
)


def _format(result, raw) -> None:
    line = raw.line
    if result.exit_code == EXIT_OK:
        print(
            f"对账通过：线路 {line.code}（{line.name}），时间轴站点「{raw.timeline_stop}」，"
            f"到站 {len(raw.arrivals)} 条（其中未取消 "
            f"{sum(1 for a in raw.arrivals if not a.cancelled)} 条），"
            f"报告事件 {len(raw.report_events)} 个，时间轴点 {len(raw.timeline_points)} 个；"
            f"四项核对（班次命中/串车间隔/大间隔/时间轴点数）全部一致。"
        )
        return

    if result.exit_code == EXIT_MISSING_FIELD:
        print("对账失败：原材料缺字段，无法按同一口径核对（退出码常量乙）：", file=sys.stderr)
        for source, loc, field_name in result.missing_fields:
            print(f"  [缺字段] 来源={source} {loc}：缺项 {field_name}", file=sys.stderr)
        return

    if result.exit_code == EXIT_CONTRADICTION:
        print("对账失败：报告/时间轴/到站表与四项规则存在矛盾（退出码常量甲）：", file=sys.stderr)
        for f in result.failures:
            print(f"  [{f.check}] {f.message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="间隔对账 CLI（到站表 / 报告事件 / 时间轴 同口径核对）")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--fixture", help="使用夹具 JSON 作为数据来源")
    src.add_argument("--db", action="store_true", help="使用运营数据库（默认）")
    parser.add_argument("--line-id", type=int, default=None)
    parser.add_argument("--line-code", default=None, help="线路代码，默认 B12")
    parser.add_argument("--stop", default="市民中心", help="时间轴对账站点，默认「市民中心」")
    args = parser.parse_args(argv)

    if args.fixture:
        raw = extract_from_fixture(args.fixture)
    else:
        # 数据库依赖（SQLAlchemy/驱动）仅在选库时加载，夹具链路保持纯标准库。
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            raw = extract_from_db(
                db,
                line_id=args.line_id,
                line_code=args.line_code or ("B12" if args.line_id is None else None),
                stop_name=args.stop,
            )
        finally:
            db.close()

    result = reconcile(raw)
    _format(result, raw)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
