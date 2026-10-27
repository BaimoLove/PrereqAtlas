"""聚合 /api/v1 下的模块路由。"""
from fastapi import APIRouter

from app.modules.identity.router import router as identity_router

api_router = APIRouter()
api_router.include_router(identity_router)
