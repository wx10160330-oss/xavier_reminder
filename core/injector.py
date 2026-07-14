"""伪造消息注入 pipeline，抄自 wakeup 的成熟实现。"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from astrbot.api import logger


class MessageInjector:
    """负责向 aiocqhttp 平台注入伪造消息，让 LLM 生成自然回复。"""

    def __init__(self, context):
        self.context = context
        self._cqhttp_bot = None
        self._bot_qq_id: str = ""

    # ---------- Getter / Setter ----------

    def set_bot_qq_id(self, qq_id: str) -> None:
        if qq_id and qq_id != "0" and qq_id != "None":
            self._bot_qq_id = str(qq_id)

    def get_bot_qq_id(self) -> str:
        return self._bot_qq_id

    def has_bot(self) -> bool:
        return self._cqhttp_bot is not None and bool(self._bot_qq_id)

    def try_capture_bot_from_event(self, event) -> None:
        """从消息事件中捕获 bot 实例（备用手段）。"""
        if self._cqhttp_bot is None and hasattr(event, "bot"):
            bot = getattr(event, "bot", None)
            if bot and hasattr(bot, "send_private_msg"):
                self._cqhttp_bot = bot
                logger.info("[reminder] ✅ 通过消息事件捕获 CQHttp 实例")
        if not self._bot_qq_id:
            try:
                detected = str(event.get_self_id())
                if detected and detected != "None":
                    self._bot_qq_id = detected
                    logger.info(
                        f"[reminder] ✅ 自动检测到机器人 QQ: {self._bot_qq_id}"
                    )
            except Exception:
                pass

    async def try_acquire_bot(self) -> bool:
        """从 AstrBot 内部主动搜索 CQHttp 实例。"""
        if self._cqhttp_bot is None:
            try:
                ctx = self.context
                for mgr_name in [
                    "platform_manager",
                    "_platform_manager",
                    "platform_mgr",
                    "_platform_mgr",
                ]:
                    mgr = getattr(ctx, mgr_name, None)
                    if mgr is None:
                        continue
                    for list_name in [
                        "platforms",
                        "platform_insts",
                        "_platforms",
                        "adapters",
                    ]:
                        plist = getattr(mgr, list_name, None)
                        if not plist or not hasattr(plist, "__iter__"):
                            continue
                        for p in plist:
                            bot = getattr(p, "bot", None)
                            if bot and hasattr(bot, "send_private_msg"):
                                self._cqhttp_bot = bot
                                logger.info(
                                    "[reminder] ✅ 主动获取到 CQHttp 实例"
                                )
                                break
                        if self._cqhttp_bot:
                            break
                    if self._cqhttp_bot:
                        break
            except Exception as e:
                logger.debug(f"[reminder] 搜索 bot 实例失败: {e}")

        if self._cqhttp_bot and not self._bot_qq_id:
            try:
                info = await self._cqhttp_bot.get_login_info()
                qq = str(info.get("user_id", ""))
                if qq and qq != "0":
                    self._bot_qq_id = qq
                    logger.info(
                        f"[reminder] ✅ 自动获取到机器人 QQ: {self._bot_qq_id}"
                    )
            except Exception as e:
                logger.debug(f"[reminder] get_login_info 失败: {e}")

        return self.has_bot()

    # ---------- 核心注入 ----------

    async def inject(self, umo: str, prompt_text: str) -> None:
        """向指定 umo 注入伪造消息。"""
        try:
            from aiocqhttp import Event as CQEvent
        except ImportError:
            raise RuntimeError("未安装 aiocqhttp，无法注入消息")

        if self._cqhttp_bot is None:
            raise RuntimeError("未获取到 CQHttp 实例")
        if not self._bot_qq_id:
            raise RuntimeError(
                "未检测到机器人 QQ 号（请在配置中填写 bot_qq_id 或先发一条消息）"
            )

        # 解析 umo，例如 aiocqhttp:FriendMessage:12345
        parts = umo.rsplit(":", 2)
        if len(parts) < 3:
            raise RuntimeError(f"无法解析 umo: {umo}")
        session_id = parts[2]
        msg_type_str = parts[1]
        is_group = "Group" in msg_type_str

        if is_group:
            if "_" in session_id:
                uid, gid = session_id.rsplit("_", 1)
            else:
                raise RuntimeError("非独立会话模式的群聊暂不支持")
            payload = {
                "post_type": "message",
                "message_type": "group",
                "sub_type": "normal",
                "message_id": int(time.time()) % 2147483647,
                "group_id": int(gid),
                "user_id": int(uid),
                "message": [
                    {"type": "text", "data": {"text": prompt_text}}
                ],
                "raw_message": prompt_text,
                "font": 0,
                "sender": {
                    "user_id": int(uid),
                    "nickname": "reminder",
                    "card": "",
                },
                "time": int(time.time()),
                "self_id": int(self._bot_qq_id),
            }
        else:
            payload = {
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "message_id": int(time.time()) % 2147483647,
                "user_id": int(session_id),
                "message": [
                    {"type": "text", "data": {"text": prompt_text}}
                ],
                "raw_message": prompt_text,
                "font": 0,
                "sender": {
                    "user_id": int(session_id),
                    "nickname": "reminder",
                    "sex": "unknown",
                    "age": 0,
                },
                "time": int(time.time()),
                "self_id": int(self._bot_qq_id),
            }

        fake_event = CQEvent.from_payload(payload)
        if fake_event is None:
            raise RuntimeError("CQEvent.from_payload 返回 None")

        handler = getattr(self._cqhttp_bot, "_handle_event", None)
        if handler is None:
            handler = getattr(self._cqhttp_bot, "handle_event", None)
        if handler is None:
            candidates = [
                a for a in dir(self._cqhttp_bot) if "event" in a.lower()
            ]
            raise RuntimeError(
                f"CQHttp 无可用的事件处理方法，相关属性: {candidates}"
            )

        await handler(fake_event)
        logger.info(f"[reminder] 📨 伪造消息已注入 pipeline | umo={umo}")
