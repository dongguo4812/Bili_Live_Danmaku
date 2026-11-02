# -*- coding: utf-8 -*-
"""Bilibili QR login and live-room WebSocket client.

QR polling is implemented here instead of relying on bilibili-api-python's
17.4.2 QR parser: Bilibili changed the successful WEB QR response to a
cross-domain ticket flow in 2026. The ticket must be followed immediately so
the response cookies can be captured before the QR key expires.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import qrcode
import requests
from bilibili_api import Credential
from bilibili_api.live import LiveDanmaku

logger = logging.getLogger("bili_live_danmaku.client")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"


class BilibiliClientError(RuntimeError):
    pass


def _cookie_values(session: requests.Session) -> dict[str, str]:
    cookies = session.cookies.get_dict()
    return {
        "sessdata": cookies.get("SESSDATA", ""),
        "bili_jct": cookies.get("bili_jct", ""),
        "buvid3": cookies.get("buvid3", ""),
        "buvid4": cookies.get("buvid4", ""),
        "dedeuserid": cookies.get("DedeUserID", ""),
        "ac_time_value": cookies.get("ac_time_value", ""),
    }


def validate_cookie_file(path: Path) -> tuple[bool, str, dict[str, str] | None]:
    if not path.is_file():
        return False, "尚未登录，请使用 B 站二维码登录。", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("凭据文件格式错误")
        cookies = data.get("cookies", data)
        if not isinstance(cookies, dict) or not cookies.get("sessdata"):
            return False, "本地登录凭据不完整，请重新扫码。", None
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"})
        mapping = {
            "SESSDATA": cookies.get("sessdata", ""),
            "bili_jct": cookies.get("bili_jct", ""),
            "buvid3": cookies.get("buvid3", ""),
            "buvid4": cookies.get("buvid4", ""),
            "DedeUserID": cookies.get("dedeuserid", ""),
            "ac_time_value": cookies.get("ac_time_value", ""),
        }
        for key, value in mapping.items():
            if value:
                session.cookies.set(key, str(value), domain=".bilibili.com")
        response = session.get(NAV_URL, timeout=12)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0 or not payload.get("data", {}).get("isLogin"):
            return False, "B 站登录已失效，请重新扫码。", None
        refreshed = _cookie_values(session)
        for key, value in cookies.items():
            if value and not refreshed.get(key):
                refreshed[key] = str(value)
        return True, "B 站登录有效。", refreshed
    except Exception as exc:
        logger.warning("验证 B 站登录凭据失败：%s", exc)
        return False, f"无法验证登录凭据：{exc}", None


class QRLogin:
    """Stateful WEB QR login flow. Poll is called by the management page."""

    def __init__(self, credential_path: Path):
        self.credential_path = credential_path
        self.lock = threading.RLock()
        self.session: requests.Session | None = None
        self.qrcode_key = ""
        self.state = "idle"
        self.message = "尚未开始扫码登录。"
        self.expires_at = 0.0

    def start(self) -> dict[str, Any]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        })
        response = session.get(QR_GENERATE_URL, timeout=15)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        key = data.get("qrcode_key")
        url = data.get("url")
        if payload.get("code") != 0 or not key or not url:
            raise BilibiliClientError(payload.get("message") or "B 站二维码申请失败。")
        image = qrcode.make(url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        with self.lock:
            self.session = session
            self.qrcode_key = str(key)
            self.state = "waiting"
            self.message = "请使用 B 站手机客户端扫描二维码。"
            self.expires_at = time.time() + 175
        return {
            "ok": True,
            "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            "expires_in": 175,
            "message": self.message,
        }

    def poll(self) -> dict[str, Any]:
        with self.lock:
            session = self.session
            key = self.qrcode_key
            state = self.state
            expires_at = self.expires_at
        if state not in {"waiting", "scanned"} or session is None or not key:
            return self.status()
        if time.time() >= expires_at:
            with self.lock:
                self.state = "expired"
                self.message = "二维码已过期，请重新获取。"
            return self.status()
        try:
            response = session.get(QR_POLL_URL, params={"qrcode_key": key}, timeout=12)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            code = data.get("code")
            if code == 86101:
                pass
            elif code == 86090:
                with self.lock:
                    self.state = "scanned"
                    self.message = "已扫码，请在手机上确认登录。"
            elif code == 86038:
                with self.lock:
                    self.state = "expired"
                    self.message = "二维码已过期，请重新获取。"
            elif code == 0:
                ticket_url = data.get("url")
                if not ticket_url:
                    raise BilibiliClientError("扫码成功响应缺少跨域登录票据。")
                ticket_parts = urlparse(str(ticket_url))
                if ticket_parts.scheme != "https" or not (
                    ticket_parts.hostname == "bilibili.com"
                    or (ticket_parts.hostname or "").endswith(".bilibili.com")
                    or ticket_parts.hostname == "biligame.com"
                    or (ticket_parts.hostname or "").endswith(".biligame.com")
                ):
                    raise BilibiliClientError("B 站返回了不受信任的跨域登录地址。")
                # The WEB QR flow may deliver cookies on the poll response
                # and/or on the cross-domain ticket response.
                ticket_response = session.get(ticket_url, timeout=15, allow_redirects=True)
                ticket_response.raise_for_status()
                cookies = _cookie_values(session)
                if not cookies.get("sessdata"):
                    for item in [response, *ticket_response.history, ticket_response]:
                        for raw in item.headers.get("Set-Cookie", "").splitlines():
                            first = raw.split(";", 1)[0]
                            if "=" not in first:
                                continue
                            name, value = first.split("=", 1)
                            if name in {"SESSDATA", "bili_jct", "buvid3", "buvid4", "DedeUserID", "ac_time_value"}:
                                cookies[{"SESSDATA": "sessdata", "DedeUserID": "dedeuserid"}.get(name, name)] = value
                                session.cookies.set(name, value, domain=".bilibili.com")
                if not cookies.get("sessdata"):
                    raise BilibiliClientError("扫码接口返回成功，但没有取得 SESSDATA；凭据未保存。")
                nav = session.get(NAV_URL, timeout=12)
                nav.raise_for_status()
                nav_data = nav.json()
                if nav_data.get("code") != 0 or not nav_data.get("data", {}).get("isLogin"):
                    raise BilibiliClientError("扫码返回的 Cookie 未通过 B 站登录验证。")
                self._save(cookies)
                with self.lock:
                    self.state = "success"
                    self.message = "扫码登录成功，Cookie 已安全保存。"
                    self.session = None
                    self.qrcode_key = ""
            else:
                message = payload.get("message") or f"二维码轮询返回未知状态：{code}"
                logger.warning("B 站二维码轮询状态异常：%s", message)
                with self.lock:
                    self.message = str(message)
        except Exception as exc:
            logger.exception("B 站二维码登录轮询失败")
            with self.lock:
                self.state = "error"
                self.message = f"扫码登录失败：{exc}"
        return self.status()

    def _save(self, cookies: dict[str, str]) -> None:
        self.credential_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"cookies": cookies, "saved_at": int(time.time())}
        temporary = self.credential_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.credential_path)
        # Restrict permissions on platforms that implement POSIX mode bits.
        try:
            os.chmod(self.credential_path.parent, 0o700)
            os.chmod(self.credential_path, 0o600)
        except OSError:
            logger.debug("当前系统不支持 POSIX 凭据文件权限设置。")

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {"state": self.state, "message": self.message}


class LiveRoomClient:
    """Owns one LiveDanmaku instance on a dedicated asyncio thread."""

    def __init__(
        self,
        on_event: Callable[[dict[str, Any]], None],
        on_state: Callable[[str, str], None],
        reconnect: bool = True,
        reconnect_interval: int = 5,
    ):
        self.on_event = on_event
        self.on_state = on_state
        self.reconnect = reconnect
        self.reconnect_interval = max(2, int(reconnect_interval))
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.room: LiveDanmaku | None = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.room_id = 0
        self.credential_data: dict[str, str] = {}

    def start(self, room_id: int, cookies: dict[str, str]) -> None:
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise BilibiliClientError("直播连接任务已经在运行。")
            self.room_id = int(room_id)
            self.credential_data = dict(cookies)
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._thread_main, name="bili-live-websocket", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        loop = self.loop
        room = self.room
        if loop and room:
            try:
                asyncio.run_coroutine_threadsafe(room.disconnect(), loop)
            except Exception:
                logger.exception("请求断开 B 站直播连接失败")
        self.on_state("disconnected", "已请求断开直播间连接。")

    def _thread_main(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        self.loop = asyncio.get_running_loop()
        cookies = self.credential_data
        credential = Credential(
            sessdata=cookies.get("sessdata", ""),
            bili_jct=cookies.get("bili_jct", ""),
            buvid3=cookies.get("buvid3", ""),
            buvid4=cookies.get("buvid4", ""),
        )
        while not self.stop_event.is_set():
            self.on_state("connecting", f"正在连接直播间 {self.room_id}…")
            try:
                room = LiveDanmaku(
                    room_display_id=self.room_id,
                    credential=credential,
                    should_reconnect=False,
                )
                self.room = room

                @room.on("VERIFICATION_SUCCESSFUL")
                async def verified(event: Any) -> None:
                    self.on_state("connected", f"已通过 B 站 WebSocket 认证，直播间 {self.room_id}。")

                @room.on("DANMU_MSG")
                async def danmaku(event: Any) -> None:
                    self._dispatch("danmaku", event)

                @room.on("SEND_GIFT")
                async def gift(event: Any) -> None:
                    self._dispatch("gift", event)

                @room.on("SUPER_CHAT_MESSAGE")
                async def super_chat(event: Any) -> None:
                    self._dispatch("super_chat", event)

                @room.on("DISCONNECT")
                async def disconnected(event: Any) -> None:
                    self.on_state("disconnected", "B 站 WebSocket 已断开。")

                await room.connect()
                if not self.stop_event.is_set():
                    self.on_state("disconnected", "B 站 WebSocket 连接结束。")
            except Exception as exc:
                if not self.stop_event.is_set():
                    logger.exception("直播间 WebSocket 连接异常")
                    self.on_state("error", f"直播连接失败：{exc}")
            finally:
                self.room = None
            if self.stop_event.is_set():
                break
            if not self.reconnect:
                break
            self.on_state("reconnecting", f"{self.reconnect_interval} 秒后尝试重新连接…")
            try:
                await asyncio.wait_for(self._wait_stop(), timeout=self.reconnect_interval)
            except asyncio.TimeoutError:
                pass
        self.loop = None
        self.on_state("disconnected", "直播间连接任务已停止。")

    async def _wait_stop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(0.2)

    def _dispatch(self, kind: str, event: Any) -> None:
        try:
            wrapper = event if isinstance(event, dict) else {}
            payload = wrapper.get("data", wrapper)
            if isinstance(payload, dict) and "data" in payload and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            if kind == "danmaku":
                info = payload.get("info") if isinstance(payload, dict) else None
                if not isinstance(info, list) or len(info) < 3:
                    return
                text = str(info[1] or "").strip()
                user_info = info[2] if isinstance(info[2], list) else []
                nickname = str(user_info[1] if len(user_info) > 1 else "观众")
                uid = int(user_info[0]) if user_info and str(user_info[0]).isdigit() else 0
                if not text:
                    return
                self.on_event({
                    "type": "danmaku",
                    "nickname": nickname[:80],
                    "uid": uid,
                    "text": text[:500],
                    "timestamp": int(time.time() * 1000),
                })
            elif isinstance(payload, dict):
                data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                if kind == "gift":
                    nickname = str(data.get("uname") or "观众")
                    text = f"赠送 {data.get('giftName') or '礼物'} ×{data.get('num') or 1}"
                else:
                    user = data.get("user_info") or {}
                    nickname = str(user.get("uname") or "观众")
                    text = str(data.get("message") or "醒目留言")
                self.on_event({
                    "type": kind,
                    "nickname": nickname[:80],
                    "uid": 0,
                    "text": text[:500],
                    "timestamp": int(time.time() * 1000),
                })
        except Exception:
            logger.exception("解析 B 站直播事件失败")
