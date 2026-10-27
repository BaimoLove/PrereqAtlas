"""identity 的公开用例；其他模块只从此入口调用。"""
from app.modules.identity.service import (
    authenticate,
    create_admin,
    get_active_user,
    register_student,
    resolve_actor,
)

__all__ = ["authenticate", "create_admin", "get_active_user", "register_student", "resolve_actor"]
