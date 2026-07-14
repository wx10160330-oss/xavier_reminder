"""持久化：reminders.json 读写与线程安全操作。"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Dict, List, Optional

from astrbot.api import logger

from .models import Reminder


class ReminderStore:
    """所有 reminder 的内存 + 文件同步存储。"""

    def __init__(self, data_file: str):
        self.data_file = data_file
        self._items: Dict[str, Reminder] = {}
        self._lock = asyncio.Lock()

    # ---------- 加载 / 保存 ----------

    def load_sync(self) -> None:
        """启动时同步加载。"""
        if not os.path.exists(self.data_file):
            self._items = {}
            return
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            items = {}
            for rid, data in (raw or {}).items():
                try:
                    r = Reminder.from_dict(data)
                    items[r.id] = r
                except Exception as e:
                    logger.warning(
                        f"[reminder] 跳过损坏的记录 {rid}: {e}"
                    )
            self._items = items
            logger.info(
                f"[reminder] 已加载 {len(self._items)} 条提醒"
            )
        except Exception:
            logger.exception("[reminder] 加载 reminders.json 失败")
            self._items = {}

    def save_sync(self) -> None:
        """同步保存到文件。"""
        try:
            data = {rid: r.to_dict() for rid, r in self._items.items()}
            tmp = self.data_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # 原子替换
            os.replace(tmp, self.data_file)
        except Exception:
            logger.exception("[reminder] 保存 reminders.json 失败")

    async def save(self) -> None:
        async with self._lock:
            self.save_sync()

    # ---------- 增删查改 ----------

    def all(self) -> List[Reminder]:
        return list(self._items.values())

    def by_umo(self, umo: str) -> List[Reminder]:
        return [r for r in self._items.values() if r.umo == umo]

    def get(self, rid: str) -> Optional[Reminder]:
        return self._items.get(rid)

    def search(
        self, query: str, umo: Optional[str] = None
    ) -> List[Reminder]:
        results = []
        for r in self._items.values():
            if umo and r.umo != umo:
                continue
            if r.matches_query(query):
                results.append(r)
        return results

    async def add(self, reminder: Reminder) -> Reminder:
        async with self._lock:
            self._items[reminder.id] = reminder
            self.save_sync()
        return reminder

    async def remove(self, rid: str) -> bool:
        async with self._lock:
            if rid in self._items:
                del self._items[rid]
                self.save_sync()
                return True
            return False

    async def remove_many(self, rids: List[str]) -> int:
        async with self._lock:
            count = 0
            for rid in rids:
                if rid in self._items:
                    del self._items[rid]
                    count += 1
            if count:
                self.save_sync()
            return count

    async def clear(self, umo: Optional[str] = None) -> int:
        async with self._lock:
            if umo is None:
                count = len(self._items)
                self._items.clear()
            else:
                to_del = [
                    rid for rid, r in self._items.items() if r.umo == umo
                ]
                for rid in to_del:
                    del self._items[rid]
                count = len(to_del)
            if count:
                self.save_sync()
            return count

    async def update(self, reminder: Reminder) -> None:
        async with self._lock:
            self._items[reminder.id] = reminder
            self.save_sync()

    def count_by_umo(self, umo: str) -> int:
        return sum(1 for r in self._items.values() if r.umo == umo)
