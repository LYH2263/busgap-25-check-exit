"""间隔对账 CLI 入口。

只负责「选库或选夹具」并把抽取层、判读层串联起来：
    库：  python -m app.cli.reconcile --line-code B12 [--timeline-stop 市民中心]
    夹具：python -m app.cli.reconcile --fixture tests/fixtures/xxx.json

可重复执行：直连库全程只读（报告事件由抽取层现场重算，不写 BunchReport）；
不做任何进程/端口探活。退出码语义见 app.services.recon_rules。
"""
from __future__ import annotations

import argparse
import sys

from app.database import SessionLocal
from app.services.recon_extract import extract_from_database, load_fixture
from app.services.recon_rules import EXIT_OK, evaluate

EXIT_RUNTIME_ERROR = 4  # 选源/连接等运行问题（与常量甲 2、乙 3 均不同）

DEFAULT_TIMELINE_STOP = "市民中心"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reconcile", description="公交到站间隔对账（与报告页/时间轴/到站表同一口径）")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--fixture", help="使用夹具 JSON 文件，不连库")
    src.add_argument("--line-id", type=int, help="直连库：按线路 ID 选线")
    src.add_argument("--line-code", help="直连库：按线路编码选线（如 B12）")
    p.add_argument("--timeline-stop", default=DEFAULT_TIMELINE_STOP,
                   help=f"直连库时对账的时间轴站点（默认 {DEFAULT_TIMELINE_STOP}）")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.fixture:
            bundle = load_fixture(args.fixture)
        else:
            if args.line_id is None and args.line_code is None:
                args.line_code = "B12"
            db = SessionLocal()
            try:
                bundle = extract_from_database(db, timeline_stop=args.timeline_stop,
                                               line_id=args.line_id, line_code=args.line_code)
            finally:
                db.close()
    except (OSError, ValueError, LookupError) as exc:
        print(f"[对账中止] 数据源不可用或无法读取：{exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    verdict = evaluate(bundle)
    stream = sys.stdout if verdict.exit_code == EXIT_OK else sys.stderr
    for line in verdict.lines:
        print(line, file=stream)
    return verdict.exit_code


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
