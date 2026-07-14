"""Basic Auth 中间件。"""

from __future__ import annotations

import base64
from typing import Callable

from aiohttp import web
from astrbot.api import logger


# 这些路径豁免认证：让浏览器/系统能抓取 PWA 元数据和图标
# 否则「添加到主屏幕」会因为拿不到 manifest/图标而使用默认图标
_PUBLIC_SUFFIXES = (
    "/manifest.json",
    "/icon.svg",
    "/icon-180.png",
    "/icon-192.png",
    "/icon-512.png",
    "/favicon.ico",
    "/favicon-32.png",
)


def _is_public(path: str) -> bool:
    return any(path.endswith(s) for s in _PUBLIC_SUFFIXES)


def basic_auth_middleware(username: str, password: str):
    """返回一个 aiohttp middleware，要求 Basic Auth。"""

    if not username or not password:
        raise ValueError("basic_auth 用户名/密码不能为空")

    expected = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")

    @web.middleware
    async def middleware(request: web.Request, handler: Callable):
        # PWA 相关资源豁免认证（浏览器抓 manifest/图标不带 Auth）
        if _is_public(request.path):
            return await handler(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return _unauthorized()
        token = auth[len("Basic "):].strip()
        if token != expected:
            logger.warning(
                f"[reminder] Web 认证失败 | ip={request.remote}"
            )
            return _unauthorized()
        return await handler(request)

    return middleware


def _unauthorized() -> web.Response:
    return web.Response(
        status=401,
        headers={
            "WWW-Authenticate": 'Basic realm="xavier_reminder"',
        },
        text="Authentication required",
    )
