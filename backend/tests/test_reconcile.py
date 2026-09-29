"""间隔对账测试：种子退出 0、夹具退甲/乙、四项严格口径、分层依赖。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.cli import reconcile as cli
from app.services.recon_extract import bundle_from_obj, extract_from_database
from app.services.recon_rules import (
    EXIT_MALFORMED,
    EXIT_OK,
    EXIT_RECON_MISMATCH,
    evaluate,
)

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_seed_passes_and_prints_code_and_stop(db, capsys):
    """种子数据：退出 0，输出含 B12 与市民中心。"""
    bundle = extract_from_database(db, timeline_stop="市民中心", line_code="B12")
    verdict = evaluate(bundle)
    assert verdict.exit_code == EXIT_OK
    text = "\n".join(verdict.lines)
    assert "B12" in text
    assert "市民中心" in text


def test_fixture_good_copy_passes():
    """与种子同口径的一致数据（首事件标回 bunching）退出 0。"""
    good = json.loads((FIXTURES / "bunching_marked_normal.json").read_text(encoding="utf-8"))
    good["events"][0]["status"] = "bunching"
    assert evaluate(bundle_from_obj(good)).exit_code == EXIT_OK


def test_seed_db_via_cli_run(db, monkeypatch, capsys):
    """CLI 直连库路径：默认 B12 + 市民中心，退出 0 且打印 B12、市民中心。"""
    monkeypatch.setattr(cli, "SessionLocal", lambda: db)
    code = cli.run([])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "B12" in out
    assert "市民中心" in out


def test_fixture_actual_bunching_marked_normal_is_constant_a(capsys):
    """夹具一：实串车却标正常 -> 非 0，退出码为常量甲。"""
    code = cli.run(["--fixture", str(FIXTURES / "bunching_marked_normal.json")])
    err = capsys.readouterr().err
    assert code == EXIT_RECON_MISMATCH
    assert code != 0
    assert "实串车却标正常" in err
    assert "第2项" in err


def test_fixture_missing_field_is_constant_b_and_names_it(capsys):
    """夹具二：到站缺字段 -> 常量乙（≠甲），并点名缺项 actual_arrive。"""
    code = cli.run(["--fixture", str(FIXTURES / "arrival_missing_field.json")])
    err = capsys.readouterr().err
    assert code == EXIT_MALFORMED
    assert code != EXIT_RECON_MISMATCH
    assert "actual_arrive" in err


def test_missing_field_not_misjudged_as_fabricated_bunching():
    """缺字段夹具绝不能被判成「造假串车码（甲）」——字段校验先于四项。"""
    obj = json.loads((FIXTURES / "arrival_missing_field.json").read_text(encoding="utf-8"))
    verdict = evaluate(bundle_from_obj(obj))
    assert verdict.exit_code == EXIT_MALFORMED
    assert not any("实串车" in line for line in verdict.lines)


def test_seed_events_match_api_caliber(db):
    """抽取的事件/时间轴必须与报告 API 同一口径（数值直接对齐）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    def override_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides = {}
    from app.database import get_db
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    report = client.post("/api/reports/run", params={"line_id": 1}).json()
    timeline = client.get("/api/reports/timeline", params={"line_id": 1, "stop_name": "市民中心"}).json()

    bundle = extract_from_database(db, timeline_stop="市民中心", line_id=1)
    assert bundle.events == report["events"]
    assert bundle.timeline["marks"] == timeline["marks"]
    assert evaluate(bundle).exit_code == EXIT_OK
    app.dependency_overrides.clear()


def test_seed_expected_gaps_strict(db):
    """种子中 2.0 严格判串车、16.0 严格判大间隔、3.0 与 15.0 边界本身不越阈。"""
    bundle = extract_from_database(db, timeline_stop="市民中心", line_id=1)
    statuses = {(e["earlier_trip"], e["later_trip"]): e["status"] for e in bundle.events
                if e["stop_name"] == "市民中心"}
    assert statuses[("T01", "T02")] == "bunching"   # gap 2.0 < 3.0
    assert statuses[("T02", "T03")] == "large_gap"  # gap 16.0 > 15.0
    assert statuses[("T03", "T04")] == "normal"     # gap 8.0

    from app.services.bunch_engine import classify_gap
    assert classify_gap(3.0, 8.0, 3.0, 15.0)[0] == "normal"   # 等于串车阈不算串车
    assert classify_gap(15.0, 8.0, 3.0, 15.0)[0] == "normal"  # 等于大间隔阈不算大间隔


def test_rule4_timeline_point_count_excludes_cancelled(db):
    """第 4 项：时间轴点数 == 未取消到站数；把市民中心一条到站标取消后必须报甲。"""
    bundle = extract_from_database(db, timeline_stop="市民中心", line_id=1)
    assert len(bundle.timeline["marks"]) == 4

    for a in bundle.arrivals:
        if a["stop_name"] == "市民中心" and a["trip_no"] == "T01":
            a["cancelled"] = True
            break
    # 时间轴未过滤该取消点（仍是 4 点），未取消到站只剩 3
    verdict = evaluate(bundle)
    assert verdict.exit_code == EXIT_RECON_MISMATCH
    assert any("第4项" in line for line in verdict.lines)

    # 时间轴也剔除该点后，第 4 项恢复一致
    bundle.timeline["marks"] = [m for m in bundle.timeline["marks"] if m["trip_no"] != "T01"]
    bundle.events = [e for e in bundle.events if e["earlier_trip"] != "T01"]
    assert evaluate(bundle).exit_code == EXIT_OK


def test_rule1_event_pair_must_hit_arrivals():
    """第 1 项：事件班次对在到站表缺失 -> 甲。"""
    obj = json.loads((FIXTURES / "bunching_marked_normal.json").read_text(encoding="utf-8"))
    obj["events"][0]["earlier_trip"] = "T99"
    verdict = evaluate(bundle_from_obj(obj))
    assert verdict.exit_code == EXIT_RECON_MISMATCH
    assert any("第1项" in line and "T99" in line for line in verdict.lines)


def test_layer_boundary_extract_does_not_import_rules():
    """抽取层禁止依赖判读模块（AST 级断言：不得 import recon_rules）。"""
    import ast

    src_path = BACKEND_ROOT / "app" / "services" / "recon_extract.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.endswith("recon_rules") or name.endswith(".recon_rules") for name in imported), \
        f"抽取层违规依赖判读层: {imported}"


def test_cli_main_raises_systemexit_with_constant(monkeypatch):
    """main() 必须用 SystemExit(退出码) 结束，且夹具给出常量甲。"""
    monkeypatch.setattr(sys, "argv",
                        ["reconcile", "--fixture", str(FIXTURES / "bunching_marked_normal.json")])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == EXIT_RECON_MISMATCH
