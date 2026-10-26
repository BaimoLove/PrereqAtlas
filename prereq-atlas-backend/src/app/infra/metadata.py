"""把全部模块的 ORM 实体汇总到同一份 ``Base.metadata``。

Alembic 与结构对照测试只依赖这里导出的元数据，不逐个模块手工拼装。
新增实体时必须在本文件登记，否则首版迁移与结构巡检会漏表。
"""

from __future__ import annotations

from sqlalchemy import MetaData

from app.infra.db import Base
from app.modules.catalog import models as catalog_models
from app.modules.graph_admin import models as graph_admin_models
from app.modules.identity import models as identity_models
from app.modules.ingestion import models as ingestion_models
from app.modules.mastery import models as mastery_models
from app.modules.plans import models as plans_models

_MODEL_MODULES = (
    identity_models,
    catalog_models,
    graph_admin_models,
    ingestion_models,
    mastery_models,
    plans_models,
)

metadata: MetaData = Base.metadata
