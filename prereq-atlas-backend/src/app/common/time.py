"""全项目时间约定：UTC+8 应用时间、数据库值及 ISO-8601 毫秒输出。"""
from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))
MYSQL_TIME_ZONE = "+08:00"


def now_beijing() -> datetime:
    return datetime.now(BEIJING_TZ)


def as_beijing(value: datetime) -> datetime:
    """无时区数据库值按北京时间解释，有时区值转换到北京时间。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=BEIJING_TZ)
    return value.astimezone(BEIJING_TZ)


def serialize_beijing_millis(value: datetime) -> str:
    return as_beijing(value).isoformat(timespec="milliseconds")
