import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Arrival, BunchReport, Line, Trip
from app.services.bunch_engine import (
    arrival_is_cancelled,
    arrivals_to_payload,
    build_timeline_marks,
    detect_bunching,
    events_to_dicts,
)
router = APIRouter(prefix="/reports", tags=["reports"])


def load_line_trips(db: Session, line_id: int):
    trips = db.scalars(select(Trip).where(Trip.line_id == line_id)).all()
    return trips, {t.id: t.trip_no for t in trips}


@router.get("")
def list_reports(db: Session = Depends(get_db)):
    rows = db.scalars(select(BunchReport).order_by(BunchReport.id.desc())).all()
    return [{"id": r.id, "line_id": r.line_id, "stop_name": r.stop_name,
             "created_at": r.created_at.isoformat(), "events": json.loads(r.summary_json)} for r in rows]

@router.post("/run")
def run_detection(line_id: int, stop_name: str | None = None, db: Session = Depends(get_db)):
    line = db.get(Line, line_id)
    if not line: raise HTTPException(404, "线路不存在")
    _, trip_no_map = load_line_trips(db, line_id)
    q = select(Arrival).where(Arrival.trip_id.in_(list(trip_no_map.keys())))
    arrivals = [a for a in db.scalars(q).all()
                if (stop_name is None or a.stop_name == stop_name) and not arrival_is_cancelled(a)]
    payload = arrivals_to_payload(arrivals, trip_no_map)
    events = detect_bunching(payload, line.planned_headway_min, line.bunch_threshold, line.large_threshold)
    data = events_to_dicts(events)
    report = BunchReport(line_id=line_id, stop_name=stop_name or "*", created_at=datetime.utcnow(),
                         summary_json=json.dumps(data, ensure_ascii=False))
    db.add(report); db.commit(); db.refresh(report)
    return {"id": report.id, "events": data}

@router.get("/suggestions")
def suggestions(line_id: int, db: Session = Depends(get_db)):
    result = run_detection(line_id=line_id, stop_name=None, db=db)
    return {"line_id": line_id, "suggestions": [e for e in result["events"] if e["status"] != "normal"]}

@router.get("/timeline")
def timeline(line_id: int, stop_name: str = "市民中心", db: Session = Depends(get_db)):
    _, trip_no_map = load_line_trips(db, line_id)
    arrivals = [a for a in db.scalars(
        select(Arrival).where(Arrival.trip_id.in_(list(trip_no_map.keys())),
                              Arrival.stop_name == stop_name)).all() if not arrival_is_cancelled(a)]
    marks = build_timeline_marks(arrivals, trip_no_map)
    return {"stop_name": stop_name, "marks": marks}
