"""聚合 /api/v1 下的模块路由；各业务模块就绪后在此挂载，不在此写业务逻辑。"""

from fastapi import APIRouter

api_router = APIRouter()
