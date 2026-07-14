"""aiohttp Web 服务应用工厂 + 启停管理。"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from aiohttp import web
from astrbot.api import logger

from ..core.store import ReminderStore
from .api import register_routes
from .auth import basic_auth_middleware


class WebServer:
    """内嵌 Web 服务。"""

    def __init__(
        self,
        store: ReminderStore,
        host: str,
        port: int,
        username: str,
        password: str,
        base_path: str,
        static_dir: str,
    ):
        self.store = store
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.base_path = base_path.rstrip("/") or ""
        self.static_dir = static_dir

        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.BaseSite] = None
        self._app: Optional[web.Application] = None

    def _build_app(self) -> web.Application:
        middlewares = [
            basic_auth_middleware(self.username, self.password)
        ]
        app = web.Application(middlewares=middlewares)

        async def index_handler(request: web.Request) -> web.Response:
            index_path = os.path.join(self.static_dir, "index.html")
            if not os.path.exists(index_path):
                return web.Response(text="index.html 缺失", status=500)
            with open(index_path, "r", encoding="utf-8") as f:
                html = f.read()
            html = html.replace("__BASE_PATH__", self.base_path or "")
            return web.Response(
                text=html, content_type="text/html", charset="utf-8"
            )

        async def manifest_handler(request: web.Request) -> web.Response:
            manifest_path = os.path.join(self.static_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                return web.Response(text="manifest.json 缺失", status=500)
            with open(manifest_path, "r", encoding="utf-8") as f:
                text = f.read()
            text = text.replace("__BASE_PATH__", self.base_path or "")
            return web.Response(
                text=text, content_type="application/manifest+json",
                charset="utf-8",
            )

        # 主页：根路径 + base_path 都指向 index
        # base_path 为空时只挂 "/"；不为空时挂 base_path/ 并把 base_path 重定向到带斜杠版本
        if self.base_path:
            async def redirect_root(request: web.Request) -> web.Response:
                raise web.HTTPFound(self.base_path + "/")
            app.router.add_get("/", redirect_root)
            app.router.add_get(self.base_path, redirect_root)
            app.router.add_get(self.base_path + "/", index_handler)
            # manifest.json 需要动态注入 base_path，因此单独处理
            app.router.add_get(
                self.base_path + "/static/manifest.json", manifest_handler
            )
            # 兜底：/static/ 光目录访问 → 跳回主页（防 PWA 老图标 403）
            app.router.add_get(self.base_path + "/static", redirect_root)
            app.router.add_get(self.base_path + "/static/", redirect_root)
            app.router.add_static(
                self.base_path + "/static/",
                path=self.static_dir,
                name="static",
                show_index=False,
            )
        else:
            async def redirect_slash(request: web.Request) -> web.Response:
                raise web.HTTPFound("/")
            app.router.add_get("/", index_handler)
            app.router.add_get("/static/manifest.json", manifest_handler)
            # 兜底：/static/ 光目录访问 → 跳回主页（防 PWA 老图标 403）
            app.router.add_get("/static", redirect_slash)
            app.router.add_get("/static/", redirect_slash)
            app.router.add_static(
                "/static/",
                path=self.static_dir,
                name="static",
                show_index=False,
            )

        # API 路由（带前缀）
        register_routes(app, self.store, prefix=self.base_path)

        return app

    async def start(self) -> bool:
        if not self.password:
            logger.warning(
                "[reminder] Web 密码为空，Web 面板不启动。"
                "请在插件配置中填写 web_password 后重载插件。"
            )
            return False
        try:
            self._app = self._build_app()
            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            self._site = web.TCPSite(
                self._runner, host=self.host, port=self.port
            )
            await self._site.start()
            logger.info(
                f"[reminder] 🌐 Web 面板已启动: "
                f"http://{self.host}:{self.port}{self.base_path}/"
            )
            return True
        except OSError as e:
            logger.error(
                f"[reminder] Web 端口 {self.port} 无法监听: {e}"
            )
            return False
        except Exception:
            logger.exception("[reminder] Web 启动失败")
            return False

    async def stop(self) -> None:
        try:
            if self._site is not None:
                await self._site.stop()
            if self._runner is not None:
                await self._runner.cleanup()
        except Exception:
            logger.exception("[reminder] Web 停止时出错")
        finally:
            self._site = None
            self._runner = None
            self._app = None
