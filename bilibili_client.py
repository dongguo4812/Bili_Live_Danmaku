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
from urllib.parse import parse_qs, urlparse

import qrcode
import requests
from bilibili_api import Credential
from bilibili_api.live import LiveDanmaku
from core.credential_store import CredentialStore

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


def _close_http_session(session: requests.Session | None) -> None:
    if session is None:
        return
    try:
        session.close()
    except Exception:
        logger.debug("关闭 B 站 HTTP Session 时发生异常", exc_info=True)


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


def _dpapi_transform(data: bytes, protect: bool) -> bytes:
    """Use Windows DPAPI so the credential file is decryptable only by this user."""
    if os.name != "nt":
        raise BilibiliClientError("Windows DPAPI 只能在 Windows 系统上使用。")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.POINTER(DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def validate_cookie_file(path: Path) -> tuple[bool, str, dict[str, str] | None]:
    if not path.is_file():
        return False, "尚未登录，请使用 B 站二维码登录。", None
    session: requests.Session | None = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("凭据文件格式错误")
        if data.get("format") == "dpapi-v1":
            encrypted = base64.b64decode(data.get("payload", ""), validate=True)
            data = json.loads(_dpapi_transform(encrypted, protect=False).decode("utf-8"))
        elif data.get("format") == "plain-local-v1" and os.name != "nt":
            raw = base64.b64decode(data.get("payload", ""), validate=True)
            data = json.loads(raw.decode("utf-8"))
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
    finally:
        _close_http_session(session)


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
        self._generation = 0
        self._poll_lock = threading.Lock()
        self._active_poll_session: requests.Session | None = None
        self._sessions: set[requests.Session] = set()
        self._closed_sessions: set[requests.Session] = set()
        self._shutting_down = False

    def start(self) -> dict[str, Any]:
        # 立即使旧二维码的轮询结果失效，避免旧请求晚返回时覆盖新二维码状态。
        with self.lock:
            if self._shutting_down:
                return {"ok": False, "error": "程序正在退出，无法申请登录二维码。"}
            self._generation += 1
            generation = self._generation
            old_session = self.session
            self.session = None
            self.qrcode_key = ""
            self.state = "starting"
            self.message = "正在申请 B 站登录二维码。"
            self.expires_at = 0.0
            close_old_session = old_session is not None and old_session is not self._active_poll_session
        if close_old_session:
            _close_http_session(old_session)
        try:
            session = requests.Session()
            with self.lock:
                if self._shutting_down:
                    self._close_session(session)
                    return {"ok": False, "error": "程序正在退出，已取消二维码申请。"}
                self._sessions.add(session)
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
        except Exception as exc:
            if "session" in locals():
                self._close_session(session)
            with self.lock:
                if generation == self._generation:
                    self.state = "error"
                    self.message = f"申请 B 站登录二维码失败：{exc}"
            raise
        with self.lock:
            if self._shutting_down or generation != self._generation:
                self._close_session(session)
                return {"ok": False, "error": "二维码申请已被新的请求替代或程序正在退出。"}
            self.session = session
            self.qrcode_key = str(key)
            self.state = "waiting"
            self.message = "请使用 B 站手机客户端扫描二维码。"
            self.expires_at = time.time() + 175
            message = self.message
        return {
            "ok": True,
            "image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"),
            "expires_in": 175,
            "message": message,
        }

    def poll(self) -> dict[str, Any]:
        # 管理页面可能因多次打开或网络延迟产生重叠轮询；同一 Session 只允许一个 poll。
        with self.lock:
            if self._shutting_down:
                return self.status()
        if not self._poll_lock.acquire(blocking=False):
            return self.status()
        with self.lock:
            if self._shutting_down:
                self._poll_lock.release()
                return self.status()
            session = self.session
            self._active_poll_session = session
        try:
            return self._poll_once()
        finally:
            close_session = None
            with self.lock:
                active_session = self._active_poll_session
                self._active_poll_session = None
                if active_session is not None and (
                    self.session is not active_session
                    or self.state not in {"waiting", "scanned"}
                ):
                    if self.session is active_session:
                        self.session = None
                        self.qrcode_key = ""
                    close_session = active_session
            if close_session is not None:
                self._close_session(close_session)
            self._poll_lock.release()

    def _poll_once(self) -> dict[str, Any]:
        with self.lock:
            session = self.session
            key = self.qrcode_key
            state = self.state
            expires_at = self.expires_at
            generation = self._generation
        if state not in {"waiting", "scanned"} or session is None or not key:
            return self.status()
        if time.time() >= expires_at:
            self._set_state_if_current(generation, "expired", "二维码已过期，请重新获取。")
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
                self._set_state_if_current(generation, "scanned", "已扫码，请在手机上确认登录。")
            elif code == 86038:
                self._set_state_if_current(generation, "expired", "二维码已过期，请重新获取。")
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
                    # Retain compatibility with older QR responses that
                    # carried cookie values in the ticket URL query string.
                    query_cookies = parse_qs(ticket_parts.query)
                    for name, key_name in {
                        "SESSDATA": "sessdata",
                        "bili_jct": "bili_jct",
                        "buvid3": "buvid3",
                        "buvid4": "buvid4",
                        "DedeUserID": "dedeuserid",
                        "ac_time_value": "ac_time_value",
                    }.items():
                        values = query_cookies.get(name)
                        if values and values[0]:
                            cookies[key_name] = values[0]
                            session.cookies.set(name, values[0], domain=".bilibili.com")
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
                with self.lock:
                    if generation != self._generation:
                        return {"state": self.state, "message": self.message}
                    # 在同一把锁内校验 generation 并保存凭据，防止旧二维码覆盖新登录流程。
                    self._save(cookies)
                    self.state = "success"
                    self.message = "扫码登录成功，Cookie 已安全保存。"
                    self.session = None
                    self.qrcode_key = ""
            else:
                message = payload.get("message") or f"二维码轮询返回未知状态：{code}"
                logger.warning("B 站二维码轮询状态异常：%s", message)
                self._set_state_if_current(generation, None, str(message))
        except Exception as exc:
            logger.exception("B 站二维码登录轮询失败")
            self._set_state_if_current(generation, "error", f"扫码登录失败：{exc}")
        return self.status()

    def _close_session(self, session: requests.Session) -> None:
        """关闭并登记 HTTP Session，避免退出与轮询清理重复关闭。"""
        with self.lock:
            if session in self._closed_sessions:
                return
            self._closed_sessions.add(session)
            self._sessions.discard(session)
            if self.session is session:
                self.session = None
            if self._active_poll_session is session:
                self._active_poll_session = None
        _close_http_session(session)

    def shutdown(self) -> None:
        """封锁新的二维码请求并关闭所有已登记的 HTTP Session，不等待网络请求返回。"""
        with self.lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            self._generation += 1
            self.state = "closed"
            self.message = "程序正在退出，二维码登录已停止。"
            self.expires_at = 0.0
            self.qrcode_key = ""
            self.session = None
            sessions = tuple(self._sessions)
        for session in sessions:
            self._close_session(session)

    def _set_state_if_current(self, generation: int, state: str | None, message: str) -> bool:
        with self.lock:
            if self._shutting_down or generation != self._generation:
                return False
            if state is not None:
                self.state = state
            self.message = message
            return True

    def _save(self, cookies: dict[str, str]) -> None:
        """通过统一凭据存储写入 B 站 Cookie；不向普通配置写入秘密值。"""
        payload = {"cookies": cookies, "saved_at": int(time.time())}
        store = CredentialStore(directory=self.credential_path.parent)
        store.save(self.credential_path.stem, payload)

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

    def stop(self, timeout: float = 10.0) -> bool:
        """请求断开并等待工作线程退出；超时则保留 stopping 状态，不伪报断开成功。"""
        with self.lock:
            thread = self.thread
            if thread is None or not thread.is_alive():
                self.stop_event.set()
                self.loop = None
                self.room = None
                self.on_state("disconnected", "直播间连接任务已停止。")
                return True

            self.stop_event.set()
            self.on_state("stopping", "正在断开直播间连接…")
            loop = self.loop
            room = self.room
            if loop and room:
                try:
                    asyncio.run_coroutine_threadsafe(room.disconnect(), loop)
                except Exception:
                    logger.exception("请求断开 B 站直播连接失败")

            if thread is threading.current_thread():
                logger.warning("直播连接线程正在请求自身停止，无法在当前线程等待退出")
                return False

            thread.join(timeout=max(0.0, float(timeout)))
            if thread.is_alive():
                logger.warning("等待 B 站直播连接线程退出超时（%.1f 秒）", timeout)
                return False

            self.loop = None
            self.room = None
            self.on_state("disconnected", "直播间连接任务已停止。")
            return True

    def _thread_main(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        self.loop = asyncio.get_running_loop()
        cookies = self.credential_data
        credential = Credential(
            sessdata=cookies.get("sessdata", ""),
            bili_jct=cookies.get("bili_jct", ""),
            buvid3=cookies.get("buvid3", ""),
        )
        while not self.stop_event.is_set():
            self.on_state("connecting", f"正在连接直播间 {self.room_id}…")
            try:
                room = LiveDanmaku(
                    room_display_id=self.room_id,
                    credential=credential,
                    max_retry=1,
                    retry_after=1,
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
                    if not self.stop_event.is_set():
                        self.on_state("disconnected", "B 站 WebSocket 已断开。")

                # stop() 可能恰好发生在 room 创建期间；停止请求已到达时，
                # 不再启动新的 WebSocket 连接，避免无意义地进入 connect()。
                if self.stop_event.is_set():
                    break
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
                extra: dict[str, Any] = {}
                if kind == "gift":
                    nickname = str(data.get("uname") or "观众")
                    gift_name = str(data.get("giftName") or "礼物")
                    gift_num = max(1, int(data.get("num") or 1))
                    text = f"赠送 {gift_name} ×{gift_num}"
                    coin_type = str(data.get("coin_type") or "gold").lower()
                    try:
                        total_coin = float(data.get("total_coin"))
                    except (TypeError, ValueError):
                        try:
                            total_coin = float(data.get("price") or 0) * gift_num
                        except (TypeError, ValueError):
                            total_coin = 0.0
                    gift_value_yuan = total_coin / 1000.0 if coin_type == "gold" else 0.0
                    extra = {
                        "gift_name": gift_name[:100],
                        "gift_num": gift_num,
                        "gift_coin_type": coin_type,
                        "gift_value_yuan": round(max(0.0, gift_value_yuan), 2),
                    }
                else:
                    user = data.get("user_info") or {}
                    nickname = str(user.get("uname") or "观众")
                    text = str(data.get("message") or "醒目留言")
                    try:
                        amount_yuan = float(data.get("price") or 0)
                    except (TypeError, ValueError):
                        amount_yuan = 0.0
                    extra = {"amount_yuan": round(max(0.0, amount_yuan), 2)}
                self.on_event({
                    "type": kind,
                    "nickname": nickname[:80],
                    "uid": 0,
                    "text": text[:500],
                    "timestamp": int(time.time() * 1000),
                    **extra,
                })
        except Exception:
            logger.exception("解析 B 站直播事件失败")
