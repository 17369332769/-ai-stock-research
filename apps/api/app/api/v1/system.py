"""系统状态路由。

spec §7 没有定义该端点，但 §13.1 把 `/settings/data-sources` 列为必须页面，
§8 要求「界面展示具体失败源和最后成功时间」，§9.3.1 要求 PSI 漂移导致的模型不可用
要能看见 —— 这些信息没有任何现有端点承载。故补此只读端点。

**只读**：不触发采集、不写库。
"""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.app.api.v1.deps import RequestIdDep, SessionDep
from apps.api.app.schemas.common import ItemResponse
from apps.api.app.schemas.system import SystemStatusDTO
from apps.api.app.services import system_status as system_status_service

router = APIRouter(tags=["system"])


@router.get(
    "/system/status",
    response_model=ItemResponse[SystemStatusDTO],
    summary="数据源、模型与 Agent 连接状态",
)
async def get_system_status(
    session: SessionDep, request_id: RequestIdDep
) -> ItemResponse[SystemStatusDTO]:
    status = await system_status_service.get_system_status(session)
    return ItemResponse[SystemStatusDTO](data=status, request_id=request_id)
