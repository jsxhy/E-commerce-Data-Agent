"""路由层通用小工具：时间格式化、金额换算、分页参数归一、模糊匹配。

只放「纯函数」，不依赖数据库会话，便于各 router 复用与单测。
"""
from __future__ import annotations

from datetime import datetime

# 契约 §0：分页 payload 的 size 上限 100
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

# 契约 §0：时间输出 "YYYY-MM-DD HH:MM:SS"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
DATE_FMT = "%Y-%m-%d"


def fmt_dt(value: datetime | None) -> str:
    """把 datetime 格式化成契约要求的本地时间字符串（秒级）。"""
    if value is None:
        return ""
    return value.strftime(DATETIME_FMT)


def fmt_date(value: datetime | None) -> str:
    """把 datetime 格式化成日期字符串 YYYY-MM-DD。"""
    if value is None:
        return ""
    return value.strftime(DATE_FMT)


def yuan(cents: int | float | None) -> float:
    """分 → 元，保留两位小数（契约 §0：金额出口一律是元）。"""
    return round((cents or 0) / 100, 2)


def ratio(num: float, den: float, digits: int = 1) -> float:
    """百分比/比例，den 为 0 时返回 0.0，避免除零。"""
    if not den:
        return 0.0
    return round(num / den * 100, digits)


def norm_page(page: int | None, size: int | None,
              default_size: int = DEFAULT_PAGE_SIZE) -> tuple[int, int]:
    """归一化分页参数：page>=1，1<=size<=100。"""
    p = page if page and page > 0 else 1
    s = size if size and size > 0 else default_size
    if s > MAX_PAGE_SIZE:
        s = MAX_PAGE_SIZE
    return p, s


def like_pattern(q: str | None) -> str | None:
    """把 q 转成 LIKE 模式；空串返回 None（表示不加条件）。"""
    if not q:
        return None
    text = q.strip()
    if not text:
        return None
    return f"%{text}%"
