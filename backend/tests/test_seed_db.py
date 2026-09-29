"""种子数据走真实数据库链路的对账测试（sqlite 内存库 + app.api.reports 真实口径）。

在容器内随 pytest 运行；验证：
* seed 后对运营库跑 CLI 退出 0，输出含 B12、市民中心；
* 报告事件、时间轴（/reports/run、/reports/timeline 同一函数产出）与四项规则不矛盾；
* 取消班次后重跑检测，时间轴与对账仍同口径。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

import app.database as db_module
from app.api.reports import run_detection, timeline
from app.database import Base
from app.models.models import Arrival, Trip
from app.reconcile import cli
from app.reconcile.extract import extract_from_db
from app.reconcile.rules import EXIT_OK, reconcile
from app.services.seed import seed_if_empty


@pytest.fixture
def sqlite_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    db = session_local()
    seed_if_empty(db)
    # CLI --db 经 app.database.SessionLocal 取会话，指向同一内存库
    monkeypatch.setattr(db_module, "SessionLocal", session_local)
    yield db
    db.close()


def test_seed_db_cli_passes_and_mentions_b12_and_stop(sqlite_db, capsys):
    # 报告页挂载时就是先 POST /reports/run，这里直接调同一函数
    run = run_detection(line_id=1, stop_name=None, db=sqlite_db)
    statuses = {(e["stop_name"], e["earlier_trip"], e["later_trip"]): e["status"] for e in run["events"]}
    assert statuses[("市民中心", "T01", "T02")] == "bunching"
    assert statuses[("市民中心", "T02", "T03")] == "large_gap"

    tl = timeline(line_id=1, stop_name="市民中心", db=sqlite_db)
    assert [m["trip_no"] for m in tl["marks"]] == ["T01", "T02", "T03", "T04"]

    code = cli.main([])  # 默认 --db，线路 B12 / 站点市民中心
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "B12" in out and "市民中心" in out


def test_seed_db_extract_and_rules_agree_with_apis(sqlite_db):
    run_detection(line_id=1, stop_name=None, db=sqlite_db)
    raw = extract_from_db(sqlite_db, line_code="B12", stop_name="市民中心")
    result = reconcile(raw)
    assert result.exit_code == EXIT_OK, result.failures

    tl = timeline(line_id=1, stop_name="市民中心", db=sqlite_db)
    active = [a for a in raw.arrivals if a.stop_name == "市民中心" and not a.cancelled]
    assert len(tl["marks"]) == len(active) == len(raw.timeline_points)


def test_cancelled_arrival_excluded_everywhere(sqlite_db, capsys):
    # 取消 T02 在市民中心的到站：检测/时间轴/对账三处必须同时排除
    t02_id = sqlite_db.scalar(select(Trip.id).where(Trip.trip_no == "T02"))
    sqlite_db.execute(
        update(Arrival)
        .where(Arrival.stop_name == "市民中心", Arrival.trip_id == t02_id)
        .values(cancelled=True)
    )
    sqlite_db.commit()
    run_detection(line_id=1, stop_name=None, db=sqlite_db)

    tl = timeline(line_id=1, stop_name="市民中心", db=sqlite_db)
    assert [m["trip_no"] for m in tl["marks"]] == ["T01", "T03", "T04"]

    code = cli.main([])
    assert code == EXIT_OK, capsys.readouterr().err
