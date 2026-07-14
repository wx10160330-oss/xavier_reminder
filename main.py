"""
xavier_reminder - 显式定时提醒 + Web 日历面板

- LLM Tool（add / cancel / skip）让用户用自然语言设/改提醒
- 后台定时扫描，到点通过伪造消息注入 pipeline，LLM 用自己的口吻自然表达
- 内嵌 aiohttp Web 面板（Basic Auth），日历可视化管理

作者: yuuuuuouo
版本: 1.0.0
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta
from typing import Optional

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .core.injector import MessageInjector
from .core.models import (
    Reminder,
    build_daily_reminder,
    build_once_reminder,
    parse_daily_time,
    parse_once_time,
    parse_date_str,
    today_str,
)
from .core.scheduler import ReminderScheduler
from .core.store import ReminderStore
from .web.server import WebServer


PLUGIN_NAME = "xavier_reminder"


TOOL_INSTRUCTIONS = """
【提醒工具使用规范】
你有 add_reminder / cancel_reminder / skip_reminder 三个工具，用于帮用户管理定时提醒。

时间解析规则（重要）：
- 用户说的口语时间（"明天早上八点"、"两小时后"）必须由你换算成绝对时间再传给工具
- once（单次）类型：fire_time 传 "YYYY-MM-DD HH:MM"
- daily（每日循环）类型：fire_time 只传 "HH:MM"
- 当前时间以对话上下文中的系统时间戳为准
- 你必须先解析出时间再调用工具，不要把口语时间原样传入

自然表达：
- 调用工具成功后，用你自己的口吻回复用户（比如"好的宝宝我记住啦"），不要复读工具返回的技术信息
- 如果你收到 [提醒系统消息] 开头的消息，那是有提醒到点了。请结合上下文，用你自己的口吻自然地提醒用户，不要暴露"系统消息"这四个字

模糊匹配：
- cancel_reminder / skip_reminder 的 query 参数按内容关键词模糊匹配。用户说"取消每天早饭那个"你就传 "早饭"
- 如果命中多条，工具会返回所有候选，你需要问清楚用户想操作哪一条

跳过：
- 用户说"明天不用提醒"→ skip_type="once"（默认跳下一次）
- 用户说"这周都别提醒"→ skip_type="count", count=剩余天数
- 用户说"下周一之前都别提醒"→ skip_type="until", until="YYYY-MM-DD"
"""


@register(
    PLUGIN_NAME,
    "yuuuuuouo",
    "显式定时提醒 + Web 日历面板",
    "1.0.0",
)
class XavierReminderPlugin(Star):

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.name = PLUGIN_NAME

        # ---------- 数据目录 ----------
        try:
            data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        except Exception:
            data_dir = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(data_dir, exist_ok=True)
        self.data_dir = data_dir
        self.data_file = os.path.join(data_dir, "reminders.json")

        # ---------- 组件 ----------
        self.store = ReminderStore(self.data_file)
        self.injector = MessageInjector(context)

        # 从配置读取 bot_qq_id
        cfg_qq = str(self.config.get("bot_qq_id") or "").strip()
        if cfg_qq:
            self.injector.set_bot_qq_id(cfg_qq)

        self.scheduler = ReminderScheduler(
            store=self.store,
            injector=self.injector,
            get_template=lambda: self._cfg_str(
                "trigger_prompt_template", DEFAULT_TEMPLATE
            ),
            get_scan_interval=lambda: self._cfg_int(
                "scan_interval_seconds", 30
            ),
            get_no_interrupt=lambda: self._cfg_int(
                "no_interrupt_seconds", 60
            ),
            get_bot_qq_id=lambda: self.injector.get_bot_qq_id(),
            is_umo_enabled=self._is_umo_enabled_by_str,
        )

        # ---------- Web ----------
        static_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "web", "static",
        )
        self.web = WebServer(
            store=self.store,
            host=self._cfg_str("web_host", "0.0.0.0"),
            port=self._cfg_int("web_port", 8899),
            username=self._cfg_str("web_username", "xavier"),
            password=self._cfg_str("web_password", ""),
            base_path=self._cfg_str("web_base_path", ""),
            static_dir=static_dir,
        )

        logger.info(
            f"[reminder] 插件已加载 | data_dir={self.data_dir}"
        )

    # ==================== 配置辅助 ====================

    def _cfg_str(self, key: str, default: str) -> str:
        v = self.config.get(key, default)
        return str(v) if v is not None else default

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (ValueError, TypeError):
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        v = self.config.get(key, default)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    # ==================== 生命周期 ====================

    async def initialize(self):
        self.store.load_sync()
        self.scheduler.start()

        # 延迟主动搜索 bot
        async def _delayed_search():
            await asyncio.sleep(3)
            if not self.injector.has_bot():
                await self.injector.try_acquire_bot()

        asyncio.create_task(_delayed_search())

        # 启动 Web
        if self._cfg_bool("enable_web", True):
            asyncio.create_task(self._start_web_delayed())
        else:
            logger.info("[reminder] Web 面板已在配置中禁用")

    async def _start_web_delayed(self):
        # 给主进程一点启动时间
        await asyncio.sleep(1)
        await self.web.start()

    async def terminate(self):
        logger.info("[reminder] 插件卸载中...")
        try:
            await self.scheduler.stop()
        except Exception:
            logger.exception("[reminder] 调度器停止失败")
        try:
            await self.web.stop()
        except Exception:
            logger.exception("[reminder] Web 停止失败")

    # ==================== 会话隔离 ====================

    def _is_enabled(self, event) -> bool:
        try:
            from astrbot.core.plugin.session_plugin_manager import (
                SessionPluginManager,
            )
            return SessionPluginManager.is_plugin_enabled_for_session(
                plugin_name=self.name,
                session_id=event.get_session_id(),
            )
        except Exception:
            return True

    def _is_umo_enabled_by_str(self, umo: str) -> bool:
        """给 scheduler 用的：没有 event 对象时，用 umo 反查。"""
        try:
            from astrbot.core.plugin.session_plugin_manager import (
                SessionPluginManager,
            )
            # umo 后半段一般就是 session_id
            parts = umo.rsplit(":", 2)
            sid = parts[-1] if parts else umo
            return SessionPluginManager.is_plugin_enabled_for_session(
                plugin_name=self.name,
                session_id=sid,
            )
        except Exception:
            return True

    # ==================== Hooks ====================

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        """给 LLM 注入工具使用说明 + 当前时间。"""
        if not self._is_enabled(event):
            return
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
            extra = (
                f"\n【当前系统时间】{now_str}\n"
                + TOOL_INSTRUCTIONS.strip()
            )
            if hasattr(req, "system_prompt") and req.system_prompt is not None:
                req.system_prompt = (req.system_prompt or "") + "\n" + extra
            else:
                # 兜底：尝试挂在字段上
                setattr(
                    req, "system_prompt",
                    (getattr(req, "system_prompt", "") or "") + "\n" + extra,
                )
        except Exception:
            logger.exception("[reminder] 注入 system prompt 失败")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """通知 scheduler 会话有活动，便于不打断插话。"""
        try:
            # 捕获 bot 引用 & QQ 号
            self.injector.try_capture_bot_from_event(event)
            umo = event.unified_msg_origin
            if umo:
                self.scheduler.note_user_activity(umo)
        except Exception:
            pass

    # ==================== LLM Tools ====================

    @filter.llm_tool(name="add_reminder")
    async def tool_add(
        self,
        event: AstrMessageEvent,
        reminder_type: str,
        content: str,
        fire_time: str,
    ):
        """添加一条定时提醒。

        Args:
            reminder_type(string): 类型，必须是 "once"（单次）或 "daily"（每日循环）
            content(string): 提醒的内容，简短一句话，例如"吃早饭"、"拿快递"
            fire_time(string): 触发时间。once 类型格式 "YYYY-MM-DD HH:MM"，daily 类型格式 "HH:MM"

        Returns:
            成功返回 {"ok": true, "id": "...", "message": "..."}
            失败返回 {"ok": false, "msg": "..."}
        """
        if not self._is_enabled(event):
            return {"ok": False, "msg": "本会话已禁用 reminder 插件"}

        umo = event.unified_msg_origin
        rtype = (reminder_type or "").strip().lower()
        content = (content or "").strip()
        fire_time = (fire_time or "").strip()

        if not content:
            return {"ok": False, "msg": "content 不能为空"}
        if rtype not in ("once", "daily"):
            return {"ok": False, "msg": "type 必须是 once 或 daily"}

        # 数量限制
        max_per = self._cfg_int("max_reminders_per_session", 50)
        if self.store.count_by_umo(umo) >= max_per:
            return {
                "ok": False,
                "msg": f"本会话提醒数量已达上限 {max_per}，请先删除一些"
            }

        try:
            if rtype == "daily":
                h, m = parse_daily_time(fire_time)
                r = build_daily_reminder(
                    umo=umo, content=content, hour=h, minute=m,
                    created_by="llm",
                )
            else:
                dt = parse_once_time(fire_time)
                if dt.timestamp() <= time.time():
                    return {"ok": False, "msg": "单次提醒的时间必须在未来"}
                r = build_once_reminder(
                    umo=umo, content=content, fire_dt=dt,
                    created_by="llm",
                )
        except ValueError as e:
            return {"ok": False, "msg": str(e)}

        await self.store.add(r)
        next_fire = datetime.fromtimestamp(r.next_fire_ts).strftime("%Y-%m-%d %H:%M")
        logger.info(
            f"[reminder] LLM 新增 | id={r.id} | type={r.type} | content={content} | next={next_fire}"
        )
        return {
            "ok": True,
            "id": r.id,
            "message": f"已添加 {rtype} 提醒：{content}，下次触发 {next_fire}",
        }

    @filter.llm_tool(name="cancel_reminder")
    async def tool_cancel(
        self,
        event: AstrMessageEvent,
        query: str,
    ):
        """按内容关键词取消提醒。命中多条时会返回全部候选让你二次确认。

        Args:
            query(string): 内容关键词，模糊匹配

        Returns:
            {"ok": true, "cancelled": [...], "message": "..."} 或
            {"ok": false, "candidates": [...], "msg": "命中多条，请确认"}
        """
        if not self._is_enabled(event):
            return {"ok": False, "msg": "本会话已禁用 reminder 插件"}
        umo = event.unified_msg_origin
        q = (query or "").strip()
        if not q:
            return {"ok": False, "msg": "query 不能为空"}

        matched = self.store.search(q, umo=umo)
        if not matched:
            return {"ok": False, "msg": f"没有匹配到含'{q}'的提醒"}
        if len(matched) > 1:
            return {
                "ok": False,
                "candidates": [
                    {
                        "id": r.id,
                        "content": r.content,
                        "type": r.type,
                        "next_fire": datetime.fromtimestamp(
                            r.next_fire_ts
                        ).strftime("%Y-%m-%d %H:%M"),
                    }
                    for r in matched
                ],
                "msg": f"命中 {len(matched)} 条，请问用户想取消哪一条？",
            }
        r = matched[0]
        await self.store.remove(r.id)
        logger.info(f"[reminder] LLM 取消 | id={r.id} | content={r.content}")
        return {
            "ok": True,
            "cancelled": [{"id": r.id, "content": r.content}],
            "message": f"已取消提醒：{r.content}",
        }

    @filter.llm_tool(name="skip_reminder")
    async def tool_skip(
        self,
        event: AstrMessageEvent,
        query: str,
        skip_type: str = "once",
        count: int = 1,
        until: str = "",
    ):
        """按内容关键词让提醒临时跳过若干次或到某一天。

        Args:
            query(string): 内容关键词，模糊匹配
            skip_type(string): 跳过策略，可选 "once"（默认，跳下一次）/ "count"（跳 N 次）/ "until"（跳到某天为止）
            count(number): skip_type=count 时必填，要跳过多少次（天）
            until(string): skip_type=until 时必填，格式 YYYY-MM-DD，跳到这一天为止（含）

        Returns:
            {"ok": true, "message": "..."} 或 {"ok": false, ...}
        """
        if not self._is_enabled(event):
            return {"ok": False, "msg": "本会话已禁用 reminder 插件"}
        umo = event.unified_msg_origin
        q = (query or "").strip()
        if not q:
            return {"ok": False, "msg": "query 不能为空"}

        matched = self.store.search(q, umo=umo)
        if not matched:
            return {"ok": False, "msg": f"没有匹配到含'{q}'的提醒"}
        if len(matched) > 1:
            return {
                "ok": False,
                "candidates": [
                    {
                        "id": r.id,
                        "content": r.content,
                        "type": r.type,
                    }
                    for r in matched
                ],
                "msg": f"命中 {len(matched)} 条，请问用户想跳过哪一条？",
            }
        r = matched[0]
        st = (skip_type or "once").strip().lower()

        try:
            dates_to_skip: list[str] = []
            if st == "once":
                # 跳过下一次的日期
                next_dt = datetime.fromtimestamp(r.next_fire_ts)
                dates_to_skip = [next_dt.strftime("%Y-%m-%d")]
            elif st == "count":
                n = max(1, int(count))
                if r.type != "daily":
                    return {
                        "ok": False,
                        "msg": "只有 daily 类型才支持 count 跳过多次",
                    }
                start_dt = datetime.fromtimestamp(r.next_fire_ts)
                for i in range(n):
                    d = start_dt + timedelta(days=i)
                    dates_to_skip.append(d.strftime("%Y-%m-%d"))
            elif st == "until":
                until_str = parse_date_str(until)
                until_dt = datetime.strptime(until_str, "%Y-%m-%d")
                start_dt = datetime.fromtimestamp(r.next_fire_ts)
                d = start_dt
                while d.date() <= until_dt.date():
                    dates_to_skip.append(d.strftime("%Y-%m-%d"))
                    d += timedelta(days=1)
                if not dates_to_skip:
                    return {"ok": False, "msg": "until 已过或无天数需要跳过"}
            else:
                return {
                    "ok": False,
                    "msg": "skip_type 必须是 once / count / until",
                }
        except ValueError as e:
            return {"ok": False, "msg": str(e)}
        except Exception:
            logger.exception("[reminder] skip 计算失败")
            return {"ok": False, "msg": "内部错误"}

        r.add_skip_dates(dates_to_skip)
        await self.store.update(r)

        first, last = dates_to_skip[0], dates_to_skip[-1]
        if first == last:
            msg = f"已让「{r.content}」在 {first} 跳过一次"
        else:
            msg = f"已让「{r.content}」从 {first} 到 {last} 都跳过"
        logger.info(
            f"[reminder] LLM 跳过 | id={r.id} | dates={dates_to_skip}"
        )
        return {"ok": True, "message": msg, "skipped_dates": dates_to_skip}

    # ==================== 指令 ====================

    @filter.command("reminders")
    async def cmd_list(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        items = self.store.by_umo(umo)
        if not items:
            yield event.plain_result("当前会话还没有提醒。")
            return
        lines = ["📌 当前会话的提醒："]
        sorted_items = sorted(items, key=lambda r: r.next_fire_ts)
        for r in sorted_items:
            fire_str = datetime.fromtimestamp(r.next_fire_ts).strftime(
                "%m-%d %H:%M"
            )
            if r.type == "daily":
                head = f"• [每日 {r.hour:02d}:{r.minute:02d}]"
                extra = f"下次: {fire_str}"
            else:
                head = "• [单次]"
                extra = fire_str
            skip_note = ""
            if r.skip_dates:
                skip_note = f"（已跳 {len(r.skip_dates)} 天）"
            lines.append(f"{head} {r.content}  {extra}{skip_note}")
        yield event.plain_result("\n".join(lines))

    @filter.command("reminder_clear")
    async def cmd_clear(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        n = self.store.count_by_umo(umo)
        if n == 0:
            yield event.plain_result("当前会话没有提醒。")
            return
        yield event.plain_result(
            f"确认清空当前会话的 {n} 条提醒？回复 /reminder_clear_yes 确认。"
        )

    @filter.command("reminder_clear_yes")
    async def cmd_clear_yes(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        n = await self.store.clear(umo=umo)
        yield event.plain_result(f"已清空 {n} 条提醒。")

    @filter.command("reminder_web")
    async def cmd_web(self, event: AstrMessageEvent):
        if not self._cfg_bool("enable_web", True):
            yield event.plain_result("Web 面板未启用（配置里 enable_web=false）")
            return
        host = self._cfg_str("web_host", "0.0.0.0")
        port = self._cfg_int("web_port", 8899)
        base = self._cfg_str("web_base_path", "")
        user = self._cfg_str("web_username", "xavier")
        pwd_set = bool(self._cfg_str("web_password", ""))
        show_host = "你的服务器IP" if host == "0.0.0.0" else host
        msg = (
            f"🌐 Web 面板地址\n"
            f"http://{show_host}:{port}{base}/\n"
            f"用户名: {user}\n"
            f"密码: {'（已设置）' if pwd_set else '⚠️ 未设置，Web 未启动'}"
        )
        yield event.plain_result(msg)


DEFAULT_TEMPLATE = (
    "[提醒系统消息]\n"
    "到点了。你之前答应过要提醒她这件事：\n"
    "「{content}」\n"
    "请结合你们的对话上下文和你当前的状态，"
    "用你自己的口吻自然地提醒她，"
    "不要生硬复读上面的内容，也不要暴露「系统消息」这几个字。"
)
