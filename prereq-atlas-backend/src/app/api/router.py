"""聚合 /api/v1 下的模块路由。"""
from fastapi import APIRouter

from app.modules.catalog.router import router as catalog_router
from app.modules.graph_admin.router import router as graph_admin_router
from app.modules.identity.router import router as identity_router
from app.modules.ingestion.router import router as ingestion_router

api_router = APIRouter()
api_router.include_router(identity_router)
api_router.include_router(ingestion_router)
api_router.include_router(graph_admin_router)

api_router.include_router(catalog_router)
