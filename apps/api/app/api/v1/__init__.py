"""v1 REST 接口（spec §7）。前缀统一 ``/api/v1``。"""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.app.api.v1 import (
    instruments,
    jobs,
    predictions,
    research_pool,
    stocks,
    system,
    universes,
    watchlist,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(universes.router)
api_router.include_router(instruments.router)
api_router.include_router(watchlist.router)
api_router.include_router(research_pool.router)
api_router.include_router(jobs.router)
api_router.include_router(stocks.router)
api_router.include_router(predictions.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
