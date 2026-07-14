"""REST API 路由：list / add / update / delete / skip。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from aiohttp import web
from astrbot.api import logger

from ..core.models import (
    Reminder,
    build_daily_reminder,
    build_once_reminder,
    parse_daily_time,
    parse_once_time,
    parse_date_str,
)
from ..core.store import ReminderStore


def _reminder_to_json(r: Reminder) -> Dict[str, Any]:
    fire_dt = datetime.fromtimestamp(r.next_fire_ts)
    return {
        "id": r.id,
        "umo": r.umo,
        "type": r.type,
        "content": r.content,
        "next_fire_ts": r.next_fire_ts,
        "next_fire_str": fire_dt.strftime("%Y-%m-%d %H:%M"),
        "hour": r.hour,
        "minute": r.minute,
        "skip_dates": r.skip_dates,
        "created_at": r.created_at,
        "created_by": r.created_by,
        "note": r.note,
        "completed_at": r.completed_at,
        "completed": r.is_completed(),
    }


def register_routes(
    app: web.Application, store: ReminderStore, prefix: str = ""
) -> None:
    """把所有 API 路由挂到 app 上。prefix 用于统一加前缀（如 /reminder）。"""
    prefix = (prefix or "").rstrip("/")

    async def api_list(request: web.Request) -> web.Response:
        umo_filter = request.query.get("umo")
        items = store.all()
        if umo_filter:
            items = [r for r in items if r.umo == umo_filter]
        return web.json_response(
            {"ok": True, "items": [_reminder_to_json(r) for r in items]}
        )

    async def api_umos(request: web.Request) -> web.Response:
        """列出所有出现过的 umo，供前端筛选。"""
        umos = sorted({r.umo for r in store.all() if r.umo})
        return web.json_response({"ok": True, "umos": umos})

    async def api_add(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "msg": "无效 JSON"}, status=400
            )
        try:
            umo = (data.get("umo") or "").strip()
            rtype = (data.get("type") or "").strip()
            content = (data.get("content") or "").strip()
            fire_time = (data.get("fire_time") or "").strip()
            if not umo or not content or not fire_time:
                return web.json_response(
                    {"ok": False, "msg": "umo/content/fire_time 必填"},
                    status=400,
                )
            if rtype == "daily":
                h, m = parse_daily_time(fire_time)
                r = build_daily_reminder(
                    umo=umo, content=content, hour=h, minute=m,
                    created_by="web",
                )
            elif rtype == "once":
                dt = parse_once_time(fire_time)
                if dt.timestamp() <= datetime.now().timestamp():
                    return web.json_response(
                        {"ok": False, "msg": "单次提醒的时间必须在未来"},
                        status=400,
                    )
                r = build_once_reminder(
                    umo=umo, content=content, fire_dt=dt,
                    created_by="web",
                )
            else:
                return web.json_response(
                    {"ok": False, "msg": "type 必须是 once 或 daily"},
                    status=400,
                )
            await store.add(r)
            logger.info(
                f"[reminder] Web 新增 | id={r.id} | type={r.type} | content={r.content}"
            )
            return web.json_response(
                {"ok": True, "item": _reminder_to_json(r)}
            )
        except ValueError as e:
            return web.json_response(
                {"ok": False, "msg": str(e)}, status=400
            )
        except Exception:
            logger.exception("[reminder] web add 失败")
            return web.json_response(
                {"ok": False, "msg": "服务器错误"}, status=500
            )

    async def api_update(request: web.Request) -> web.Response:
        rid = request.match_info["rid"]
        r = store.get(rid)
        if r is None:
            return web.json_response(
                {"ok": False, "msg": "未找到该提醒"}, status=404
            )
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "msg": "无效 JSON"}, status=400
            )
        try:
            if "content" in data:
                r.content = (data["content"] or "").strip() or r.content
            if "note" in data:
                r.note = data["note"]

            new_type = data.get("type") or r.type
            new_time = (data.get("fire_time") or "").strip()

            if new_type == "daily":
                if new_time:
                    h, m = parse_daily_time(new_time)
                    r.hour, r.minute = h, m
                if r.hour is not None and r.minute is not None:
                    r.type = "daily"
                    r.bump_to_next_daily()
            elif new_type == "once":
                if new_time:
                    dt = parse_once_time(new_time)
                    if dt.timestamp() <= datetime.now().timestamp():
                        return web.json_response(
                            {"ok": False, "msg": "单次时间必须在未来"},
                            status=400,
                        )
                    r.type = "once"
                    r.next_fire_ts = dt.timestamp()
                    r.hour = None
                    r.minute = None

            await store.update(r)
            return web.json_response(
                {"ok": True, "item": _reminder_to_json(r)}
            )
        except ValueError as e:
            return web.json_response(
                {"ok": False, "msg": str(e)}, status=400
            )
        except Exception:
            logger.exception("[reminder] web update 失败")
            return web.json_response(
                {"ok": False, "msg": "服务器错误"}, status=500
            )

    async def api_delete(request: web.Request) -> web.Response:
        rid = request.match_info["rid"]
        ok = await store.remove(rid)
        if ok:
            logger.info(f"[reminder] Web 删除 | id={rid}")
            return web.json_response({"ok": True})
        return web.json_response(
            {"ok": False, "msg": "未找到该提醒"}, status=404
        )

    async def api_skip(request: web.Request) -> web.Response:
        rid = request.match_info["rid"]
        r = store.get(rid)
        if r is None:
            return web.json_response(
                {"ok": False, "msg": "未找到该提醒"}, status=404
            )
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            date_str = (data.get("date") or "").strip()
            if not date_str:
                return web.json_response(
                    {"ok": False, "msg": "date 必填 (YYYY-MM-DD)"},
                    status=400,
                )
            date_str = parse_date_str(date_str)
            r.add_skip_date(date_str)
            await store.update(r)
            return web.json_response(
                {"ok": True, "item": _reminder_to_json(r)}
            )
        except ValueError as e:
            return web.json_response(
                {"ok": False, "msg": str(e)}, status=400
            )

    async def api_unskip(request: web.Request) -> web.Response:
        rid = request.match_info["rid"]
        r = store.get(rid)
        if r is None:
            return web.json_response(
                {"ok": False, "msg": "未找到该提醒"}, status=404
            )
        try:
            data = await request.json()
        except Exception:
            data = {}
        date_str = (data.get("date") or "").strip()
        if not date_str:
            return web.json_response(
                {"ok": False, "msg": "date 必填"}, status=400
            )
        if date_str in r.skip_dates:
            r.skip_dates.remove(date_str)
            await store.update(r)
        return web.json_response(
            {"ok": True, "item": _reminder_to_json(r)}
        )

    async def api_clear_completed(request: web.Request) -> web.Response:
        """一键删除所有已完成的单次提醒。可按 umo 过滤。"""
        try:
            data = await request.json()
        except Exception:
            data = {}
        umo_filter = (data.get("umo") or "").strip() if isinstance(data, dict) else ""
        removed = 0
        for r in list(store.all()):
            if r.type == "once" and r.is_completed():
                if umo_filter and r.umo != umo_filter:
                    continue
                if await store.remove(r.id):
                    removed += 1
        logger.info(f"[reminder] Web 清理已完成 | removed={removed}")
        return web.json_response({"ok": True, "removed": removed})

    app.router.add_get(prefix + "/api/list", api_list)
    app.router.add_get(prefix + "/api/umos", api_umos)
    app.router.add_post(prefix + "/api/add", api_add)
    app.router.add_post(prefix + "/api/update/{rid}", api_update)
    app.router.add_delete(prefix + "/api/delete/{rid}", api_delete)
    app.router.add_post(prefix + "/api/skip/{rid}", api_skip)
    app.router.add_post(prefix + "/api/unskip/{rid}", api_unskip)
    app.router.add_post(prefix + "/api/clear_completed", api_clear_completed)
