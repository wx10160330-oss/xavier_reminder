"""调度循环 + 触发逻辑。"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from astrbot.api import logger

from .models import Reminder, today_str
from .store import ReminderStore
from .injector import MessageInjector


class ReminderScheduler:
    """后台定时扫描器。"""

    def __init__(
        self,
        store: ReminderStore,
        injector: MessageInjector,
        get_template: Callable[[], str],
        get_scan_interval: Callable[[], int],
        get_no_interrupt: Callable[[], int],
        get_bot_qq_id: Callable[[], str],
        is_umo_enabled: Callable[[str], bool],
    ):
        self.store = store
        self.injector = injector
        self._get_template = get_template
        self._get_scan_interval = get_scan_interval
        self._get_no_interrupt = get_no_interrupt
        self._get_bot_qq_id = get_bot_qq_id
        self._is_umo_enabled = is_umo_enabled

        self._task: Optional[asyncio.Task] = None
        self._stop_flag = False
        self._last_activity: Dict[str, float] = {}  # umo -> ts

    # ---------- 外部通知 ----------

    def note_user_activity(self, umo: str) -> None:
        """由主插件在收到用户消息 / bot 发出消息时调用。"""
        if umo:
            self._last_activity[umo] = time.time()

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_flag = False
        self._task = asyncio.create_task(self._tick_loop())
        logger.info("[reminder] 调度器已启动")

    async def stop(self) -> None:
        self._stop_flag = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("[reminder] 调度器已停止")

    # ---------- 主循环 ----------

    async def _tick_loop(self) -> None:
        # 启动后先延迟一小段，等 bot 就绪
        await asyncio.sleep(3)
        while not self._stop_flag:
            try:
                await self._tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[reminder] tick 异常")
            interval = max(5, int(self._get_scan_interval() or 30))
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    async def _tick_once(self) -> None:
        now = time.time()
        no_interrupt_sec = max(0, int(self._get_no_interrupt() or 0))
        due: list[Reminder] = []
        for r in self.store.all():
            # 已完成的单次任务不再触发
            if r.type == "once" and r.is_completed():
                continue
            if r.next_fire_ts <= now:
                due.append(r)

        if not due:
            return

        changed = False
        for r in due:
            try:
                changed = await self._handle_due(r, no_interrupt_sec) or changed
            except Exception:
                logger.exception(f"[reminder] 处理任务失败 | id={r.id}")

        if changed:
            await self.store.save()

    async def _handle_due(
        self, r: Reminder, no_interrupt_sec: int
    ) -> bool:
        """处理一条到期任务，返回是否有变更（需要保存）。"""
        # 触发前先确认这条仍在 store 中。
        # 因为 _tick_once 先收集 due 列表再逐条 await 处理，
        # 用户可能在处理途中通过 Web / LLM 删除这条 → 引用还在 due 里，
        # 若不检查会照常触发，造成"删了还提醒"的假触发。
        if self.store.get(r.id) is None:
            logger.info(
                f"[reminder] 任务已被删除，取消触发 | id={r.id} | content={r.content}"
            )
            return False

        # 已完成的单次任务不该被触发（防御）：直接不动
        if r.type == "once" and r.is_completed():
            # 避免 due 一直命中：把时间挪远
            r.next_fire_ts = time.time() + 365 * 24 * 3600
            return True

        # 会话禁用：跳过本次触发但保留任务
        if not self._is_umo_enabled(r.umo):
            logger.info(
                f"[reminder] 会话禁用，跳过触发 | umo={r.umo} | id={r.id}"
            )
            if r.type == "daily":
                r.bump_to_next_daily()
                return True
            # once 类型：推迟 1 分钟再看
            r.next_fire_ts = time.time() + 60
            return True

        today = today_str()

        # 跳过今天
        if today in r.skip_dates:
            r.skip_dates.remove(today)
            logger.info(
                f"[reminder] 今日跳过 | umo={r.umo} | id={r.id} | content={r.content}"
            )
            if r.type == "daily":
                r.bump_to_next_daily()
            else:
                # 单次提醒被跳过 → 标记完成（保留卡片，日历上显示划掉）
                r.completed_at = time.time()
            return True

        # 不打断：最近 N 秒内该会话有活动 → 推迟
        if no_interrupt_sec > 0:
            last = self._last_activity.get(r.umo, 0.0)
            if time.time() - last < no_interrupt_sec:
                r.next_fire_ts = time.time() + 60
                logger.info(
                    f"[reminder] 会话正在活跃，推迟 1 分钟 | id={r.id}"
                )
                return True

        # 触发
        ok = await self._fire(r)
        if not ok:
            # 注入失败：推迟 3 分钟再试
            r.next_fire_ts = time.time() + 180
            return True

        # 触发成功：调整下次时间 / 标记完成
        if r.type == "daily":
            r.bump_to_next_daily()
        else:
            # 单次：标记为已完成，保留在数据里让用户手动删
            r.completed_at = time.time()
        return True

    # ---------- 触发单条 ----------

    async def _fire(self, r: Reminder) -> bool:
        # 确保 bot 就绪
        if not self.injector.has_bot():
            await self.injector.try_acquire_bot()
            if not self.injector.has_bot():
                logger.warning(
                    f"[reminder] Bot 未就绪，本次触发跳过 | id={r.id}"
                )
                return False

        template = self._get_template() or ""
        try:
            fire_dt = datetime.fromtimestamp(r.next_fire_ts)
            time_str = fire_dt.strftime("%Y-%m-%d %H:%M")
            type_label = {"once": "单次", "daily": "每日"}.get(
                r.type, r.type
            )
            prompt = template.format(
                content=r.content,
                time=time_str,
                type=type_label,
            )
        except Exception:
            logger.exception("[reminder] 模板渲染失败，使用兜底模板")
            prompt = f"[提醒] 到点了：{r.content}"

        try:
            await self.injector.inject(r.umo, prompt)
            logger.info(
                f"[reminder] ⏰ 触发成功 | umo={r.umo} | id={r.id} | content={r.content}"
            )
            return True
        except Exception:
            logger.exception(
                f"[reminder] 注入失败 | umo={r.umo} | id={r.id}"
            )
            return False
