"""间隔对账 CLI 的夹具级测试（纯标准库 + app.reconcile，不依赖数据库）。"""
from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path

from app.reconcile import cli
from app.reconcile.extract import extract_from_fixture
from app.reconcile.rules import (
    EXIT_CONTRADICTION,
    EXIT_MISSING_FIELD,
    EXIT_OK,
    reconcile,
)
from app.services.bunch_engine import detect_bunching

BACKEND = Path(__file__).resolve().parents[1]
FIX = BACKEND / "tests" / "fixtures"


def run_fixture(name: str) -> int:
    return cli.main(["--fixture", str(FIX / name)])


def test_exit_constants_are_distinct_nonzero():
    assert EXIT_OK == 0
    assert EXIT_CONTRADICTION != 0
    assert EXIT_MISSING_FIELD != 0
    assert EXIT_CONTRADICTION != EXIT_MISSING_FIELD  # 常量甲 ≠ 常量乙


def test_seed_fixture_passes(capsys):
    assert run_fixture("seed_b12.json") == EXIT_OK
    out = capsys.readouterr().out
    assert "B12" in out and "市民中心" in out


def test_real_bunching_labeled_normal_is_contradiction(capsys):
    # 「实串车却标正常」：非 0，退出码为模块常量甲
    assert run_fixture("bunching_labeled_normal.json") == EXIT_CONTRADICTION
    err = capsys.readouterr().err
    assert "BUNCH_GAP" in err
    assert "实串车" in err


def test_missing_field_uses_second_constant_and_names_the_gap(capsys):
    # 「到站缺字段」：常量乙（不等于甲），并点名缺项
    assert run_fixture("arrival_missing_field.json") == EXIT_MISSING_FIELD
    err = capsys.readouterr().err
    assert "actual_arrive" in err
    assert "T02" in err


def test_missing_field_must_not_be_misread_as_fabricated_bunching():
    # 缺字段行不参与配对：禁止把缺字段夹具误判成造假串车（不得落常量甲/串车项）
    raw = extract_from_fixture(str(FIX / "arrival_missing_field.json"))
    result = reconcile(raw)
    assert result.exit_code == EXIT_MISSING_FIELD
    assert result.failures == []  # 结构问题优先，不产生任何四项语义失败
    assert result.missing_fields and all(
        field_name == "actual_arrive" for _, _, field_name in result.missing_fields
    )


def test_timeline_point_count_uses_uncancelled_arrivals(capsys):
    assert run_fixture("timeline_includes_cancelled.json") == EXIT_CONTRADICTION
    err = capsys.readouterr().err
    assert "TIMELINE_COUNT" in err
    assert "4" in err and "3" in err


def test_repeatable_runs_are_identical():
    # 可重复执行：连跑两次结论一致
    def twice(name):
        r1 = reconcile(extract_from_fixture(str(FIX / name)))
        r2 = reconcile(extract_from_fixture(str(FIX / name)))
        return r1, r2

    for name, code in [
        ("seed_b12.json", EXIT_OK),
        ("bunching_labeled_normal.json", EXIT_CONTRADICTION),
        ("arrival_missing_field.json", EXIT_MISSING_FIELD),
        ("timeline_includes_cancelled.json", EXIT_CONTRADICTION),
    ]:
        r1, r2 = twice(name)
        assert r1.exit_code == r2.exit_code == code


def test_seed_fixture_matches_bunch_engine_caliber():
    # 种子镜像夹具的事件必须与 bunch_engine（报告页同一引擎）逐项一致
    data = json.loads((FIX / "seed_b12.json").read_text(encoding="utf-8"))
    payload = [
        {"stop_name": a["stop_name"], "trip_no": a["trip_no"],
         "actual_arrive": datetime.fromisoformat(a["actual_arrive"])}
        for a in data["arrivals"] if not a.get("cancelled")
    ]
    events = detect_bunching(payload, 8.0, 3.0, 15.0)
    engine_view = {
        (e.stop_name, e.earlier_trip, e.later_trip): (round(e.gap_min, 2), e.status)
        for e in events
    }
    for ev in data["report_events"]:
        key = (ev["stop_name"], ev["earlier_trip"], ev["later_trip"])
        assert key in engine_view
        assert engine_view[key] == (ev["gap_min"], ev["status"])


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_layer_dependency_direction():
    here = BACKEND / "app" / "reconcile"
    extract_imports = _imports_of(here / "extract.py")
    rules_imports = _imports_of(here / "rules.py")
    # 抽取禁止依赖判读模块
    assert not any("rules" in name for name in extract_imports)
    # 判读只基于原材料：不回头依赖抽取，也不碰数据库/夹具读取
    assert not any("extract" in name or "sqlalchemy" in name for name in rules_imports)
    # 入口只做串联：不写四项判读逻辑（不含阈值比较运算的直接重算）
    cli_src = (here / "cli.py").read_text(encoding="utf-8")
    assert "bunch_threshold" not in cli_src and "large_threshold" not in cli_src
