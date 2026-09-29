from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.router import api_router
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.services.seed import seed_if_empty


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # 轻量加列（仅加法）：旧开发库没有 arrivals.cancelled 时补上，保持与报告/时间轴同口径
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("arrivals")}
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text(
                "ALTER TABLE arrivals ADD COLUMN IF NOT EXISTS cancelled BOOLEAN NOT NULL DEFAULT false"
            ))
        elif "cancelled" not in cols:
            conn.execute(text("ALTER TABLE arrivals ADD COLUMN cancelled BOOLEAN NOT NULL DEFAULT 0"))
    if settings.seed_on_empty:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    yield


app = FastAPI(title="BusGap", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")
