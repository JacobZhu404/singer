from __future__ import annotations
from fastapi import APIRouter, HTTPException
from dancer.core.engine import Engine
from dancer.models.signal import ScreenResult
from typing import Optional

router = APIRouter()
_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


@router.get("/api/v1/screen")
async def screen(limit: int = 20, codes: Optional[str] = None) -> ScreenResult:
    """执行选股筛选"""
    engine = get_engine()
    code_list = codes.split(",") if codes else None
    return engine.screen(codes=code_list, limit=limit)


@router.get("/api/v1/strategies")
async def list_strategies():
    """获取策略列表"""
    from dancer.strategies.registry import StrategyRegistry
    strategies = StrategyRegistry.list_all()
    return {name: {"name": s.name, "description": s.description, "weight": s.weight}
            for name, s in strategies.items()}


@router.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}