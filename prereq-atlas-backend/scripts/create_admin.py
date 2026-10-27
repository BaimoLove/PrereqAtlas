"""受控本机管理员初始化；口令仅从 getpass 或显式标准输入读取。"""
from __future__ import annotations

import argparse
import ctypes
import getpass
import sys

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.common.contracts import PASSWORD_MAX, PASSWORD_MIN, USERNAME_MAX, USERNAME_MIN
from app.common.http_contracts import RegisterRequest
from app.infra.db import session_scope
from app.infra.errors import AppError
from app.modules.identity.public import create_admin

# 只回显字段名与规则；pydantic 的原始错误里带 input，可能含明文口令，禁止直接打印。
_FIELD_RULES = {
    "username": f"用户名需为 {USERNAME_MIN}～{USERNAME_MAX} 个 ASCII 字母、数字或下划线",
    "password": f"密码需为 {PASSWORD_MIN}～{PASSWORD_MAX} 个字符",
}


def _describe_validation_error(exc: ValidationError) -> str:
    """把校验失败翻译成可操作的提示，但不回显任何输入值。"""
    fields: list[str] = []
    for error in exc.errors():
        name = str(error["loc"][0]) if error["loc"] else "输入"
        if name not in fields:
            fields.append(name)
    return "；".join(_FIELD_RULES.get(name, f"{name} 不合法") for name in fields)


def _has_console_stdin() -> bool:
    """判断标准输入是否连着可隐藏输入的交互控制台。

    Windows 上 ``sys.stdin.isatty()`` 对 NUL 设备也返回真，会让 getpass 卡在
    等待真实控制台按键上；这里改用控制台模式判断，判断不可用时退回 ``isatty``。
    """
    if sys.platform != "win32":
        return sys.stdin.isatty()
    try:
        import msvcrt

        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        mode = ctypes.c_uint()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except (OSError, ValueError, AttributeError):
        return False


def _read_password(use_stdin: bool) -> str | None:
    """读取口令；无法安全读取或两次不一致时打印原因并返回 None。"""
    if use_stdin:
        return sys.stdin.readline().removesuffix("\n").removesuffix("\r")
    if not _has_console_stdin():
        print(
            "标准输入不是交互控制台，无法隐藏口令输入。"
            "请在本机 PowerShell 或 CMD 中运行，或改用 --password-stdin 从标准输入传入。",
            file=sys.stderr,
        )
        return None
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        print("Passwords do not match.", file=sys.stderr)
        return None
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创建管理员；重复用户名不会覆盖账号")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-stdin", action="store_true", help="从安全管道读取一行口令")
    args = parser.parse_args(argv)
    try:
        password = _read_password(args.password_stdin)
        if password is None:
            return 2
        if not password:
            print("Password is empty; no admin was created.", file=sys.stderr)
            return 2
        credentials = RegisterRequest(username=args.username, password=password)
        with session_scope() as session:
            user = create_admin(session, credentials)
    except ValidationError as exc:
        print(f"Invalid username or password. {_describe_validation_error(exc)}", file=sys.stderr)
        return 2
    except AppError as exc:
        print(f"Admin creation rejected: {exc.code.value}.", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print("Database unavailable; admin was not created.", file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("Admin creation cancelled.", file=sys.stderr)
        return 2
    print(f"Created ADMIN id={user.id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
