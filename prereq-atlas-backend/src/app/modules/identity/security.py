"""账号口令封装：只生成 Argon2 散列。"""
from pwdlib import PasswordHash

_passwords = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _passwords.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _passwords.verify(password, password_hash)
