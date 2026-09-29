"""间隔对账：原材料抽取 → 规则判读 → 命令入口（依赖单向，详见各模块）。"""
from app.reconcile.rules import (
    EXIT_CONTRADICTION,
    EXIT_MISSING_FIELD,
    EXIT_OK,
    ReconcileResult,
    reconcile,
)

__all__ = [
    "EXIT_OK",
    "EXIT_CONTRADICTION",
    "EXIT_MISSING_FIELD",
    "ReconcileResult",
    "reconcile",
]
