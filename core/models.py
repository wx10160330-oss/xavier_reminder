"""Reminder 数据类与相关工具函数。"""

from __future__ import annotations

import random
import string
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


REMINDER_TYPES = ("once", "daily")
SOURCE_TYPES = ("llm", "web", "cmd", "unknown")


def _now_ts() -> float:
    return time.time()


def _gen_id() -> str:
    """生成形如 r_20260714_100000_abc123 的唯一 ID。"""
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "".join(
        random.choices(string.ascii_lowercase + string.digits, k=6)
    )
    return f"r_{now}_{suffix}"


@dataclass
class Reminder:
    """单条提醒任务。"""

    umo: str
    type: str  # "once" or "daily"
    content: str
    next_fire_ts: float
    hour: Optional[int] = None      # daily 专用
    minute: Optional[int] = None    # daily 专用
    skip_dates: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=_now_ts)
    created_by: str = "unknown"
    note: Optional[str] = None
    completed_at: Optional[float] = None  # 单次提醒触发成功后置位
    id: str = field(default_factory=_gen_id)

    # ---------- 状态 ----------
    def is_completed(self) -> bool:
        return self.completed_at is not None

    # ---------- 序列化 ----------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Reminder":
        # 兼容旧数据、缺字段
        return cls(
            id=data.get("id") or _gen_id(),
            umo=data.get("umo", ""),
            type=data.get("type", "once"),
            content=data.get("content", ""),
            next_fire_ts=float(data.get("next_fire_ts", 0.0)),
            hour=data.get("hour"),
            minute=data.get("minute"),
            skip_dates=list(data.get("skip_dates") or []),
            created_at=float(data.get("created_at") or _now_ts()),
            created_by=data.get("created_by") or "unknown",
            note=data.get("note"),
            completed_at=(
                float(data["completed_at"])
                if data.get("completed_at") is not None else None
            ),
        )

    # ---------- 时间辅助 ----------
    def next_fire_dt(self) -> datetime:
        return datetime.fromtimestamp(self.next_fire_ts)

    def bump_to_next_daily(self) -> None:
        """将 daily 类型的 next_fire_ts 推进到"下一个"同一时间。

        逻辑：以 max(当前 next_fire, now) 为基点，找到严格晚于它、
        且 (hour:minute) 命中的最近时间点。这样：
        - 刚触发过（next_fire 是今天）→ 明天同一时间
        - 时间已过 next_fire 但已经错过很久 → 从今天开始找下一个
        """
        if self.type != "daily" or self.hour is None or self.minute is None:
            return
        now = datetime.now()
        base_ts = max(self.next_fire_ts, now.timestamp())
        base = datetime.fromtimestamp(base_ts)
        target = base.replace(
            hour=self.hour,
            minute=self.minute,
            second=0,
            microsecond=0,
        )
        # target 必须严格晚于 base
        if target <= base:
            target += timedelta(days=1)
        self.next_fire_ts = target.timestamp()

    def add_skip_date(self, date_str: str) -> None:
        if date_str not in self.skip_dates:
            self.skip_dates.append(date_str)

    def add_skip_dates(self, date_list: List[str]) -> None:
        for d in date_list:
            self.add_skip_date(d)

    def matches_query(self, query: str) -> bool:
        """content 模糊匹配（大小写不敏感）。"""
        if not query:
            return False
        q = query.strip().lower()
        return q in (self.content or "").lower()


# ---------- 时间字符串解析 ----------

def parse_daily_time(s: str) -> tuple[int, int]:
    """解析 'HH:MM'。"""
    s = s.strip()
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError(f"daily 时间格式应为 HH:MM，实际: {s}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时间超出范围: {s}")
    return h, m


def parse_once_time(s: str) -> datetime:
    """解析 'YYYY-MM-DD HH:MM'。"""
    s = s.strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError as e:
        raise ValueError(
            f"once 时间格式应为 YYYY-MM-DD HH:MM，实际: {s}"
        ) from e


def parse_date_str(s: str) -> str:
    """将 'YYYY-MM-DD' 校验后返回相同格式。"""
    s = s.strip()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"日期格式应为 YYYY-MM-DD，实际: {s}") from e
    return dt.strftime("%Y-%m-%d")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def build_daily_reminder(
    umo: str,
    content: str,
    hour: int,
    minute: int,
    created_by: str = "unknown",
) -> Reminder:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return Reminder(
        umo=umo,
        type="daily",
        content=content,
        hour=hour,
        minute=minute,
        next_fire_ts=target.timestamp(),
        created_by=created_by,
    )


def build_once_reminder(
    umo: str,
    content: str,
    fire_dt: datetime,
    created_by: str = "unknown",
) -> Reminder:
    return Reminder(
        umo=umo,
        type="once",
        content=content,
        next_fire_ts=fire_dt.timestamp(),
        created_by=created_by,
    )
