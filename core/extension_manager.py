"""Lifecycle management for the optional LOL Hex Display process.

The extension stays a separate application. The Bili application owns its
process only when it starts it; an already-running standalone instance is
detected but is never terminated by the host.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class LolHexExtensionManager:
    def __init__(self, base_dir: Path, logger: Any) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.extension_dir = self.base_dir / "extensions" / "lol_hex_display"
        self.logger = logger
        self._lock = threading.RLock()
        self._process: subprocess.Popen[Any] | None = None
        self._owned = False
        self._last_error = ""
        self.host = self._read_host()
        self.port = self._read_port()
        formatted_host = f"[{self.host}]" if ":" in self.host else self.host
        self.base_url = f"http://{formatted_host}:{self.port}"

    def _read_config_value(self, name: str, default: str) -> str:
        config = self.extension_dir / "config.txt"
        try:
            for line in config.read_text(encoding="utf-8-sig").splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip() == name:
                    return value.strip()
        except OSError:
            pass
        return default

    def _read_host(self) -> str:
        host = self._read_config_value("host", "127.0.0.1").strip().strip("[]")
        return "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host

    def _read_port(self) -> int:
        try:
            candidate = int(self._read_config_value("port", "8767"))
            return candidate if 1 <= candidate <= 65535 else 8767
        except (TypeError, ValueError):
            return 8767

    def _request(self, path: str, payload: dict[str, Any] | None = None, timeout: float = 1.0) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        req = urllib.request.Request(self.base_url + path, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
        result = json.loads(raw.decode("utf-8")) if raw else {}
        return result if isinstance(result, dict) else {}

    def _is_healthy(self) -> bool:
        try:
            payload = self._request("/api/client-status", timeout=0.7)
            return "recognized" in payload
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            return False
        except Exception:
            return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is not None:
                code = process.returncode
                self.logger.warning("LOL Hex 子进程已退出，退出码：%s", code)
                self._process = None
                self._owned = False
                if code not in (0, None):
                    self._last_error = f"LOL Hex 进程异常退出（退出码 {code}）。"
            healthy = self._is_healthy()
            if healthy:
                return {
                    "ok": True,
                    "state": "running",
                    "running": True,
                    "owned": bool(self._process is not None and self._owned),
                    "external": not bool(self._process is not None and self._owned),
                    "port": self.port,
                    "base_url": self.base_url,
                    "error": self._last_error,
                }
            if self._process is not None and self._process.poll() is None:
                return {
                    "ok": True, "state": "starting", "running": False,
                    "owned": self._owned, "external": False,
                    "port": self.port, "base_url": self.base_url,
                    "error": self._last_error,
                }
            return {
                "ok": True, "state": "stopped", "running": False,
                "owned": False, "external": False,
                "port": self.port, "base_url": self.base_url,
                "error": self._last_error,
            }

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            executable = self.extension_dir / "lol_hex_display.exe"
            if not executable.is_file():
                raise FileNotFoundError(
                    "未找到打包后的 LOL Hex 程序，请重新运行主项目 build.bat。"
                )
            return [str(executable)]
        script = self.extension_dir / "main.py"
        if not script.is_file():
            raise FileNotFoundError(
                "未找到 LOL Hex 扩展源码，请执行 git submodule update --init --recursive。"
            )
        python = os.environ.get("LOL_HEX_PYTHON", "").strip()
        if python:
            return [python, str(script)]
        launcher = shutil.which("py") if os.name == "nt" else None
        if launcher:
            return [launcher, "-3", str(script)]
        return [sys.executable, str(script)]

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._is_healthy():
                return self.status()
            if self._process is not None and self._process.poll() is None:
                return self.status()

            command = self._command()
            env = os.environ.copy()
            env["LOL_HEX_MANAGED_BY_BILI"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self.logger.info("启动 LOL Hex 扩展进程：%s", command[0])
            self._process = subprocess.Popen(
                command,
                cwd=str(self.extension_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._owned = True
            self._last_error = ""

            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if self._is_healthy():
                    self.logger.info("LOL Hex 扩展已启动，地址：%s", self.base_url)
                    return self.status()
                if self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None
                    self._owned = False
                    self._last_error = f"LOL Hex 启动失败，进程退出码：{code}。"
                    raise RuntimeError(self._last_error)
                time.sleep(0.25)

            self._last_error = "等待 LOL Hex Web 服务启动超时。"
            process = self._process
            self._process = None
            self._owned = False
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            raise TimeoutError(self._last_error)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                if self._is_healthy():
                    self.logger.info("检测到独立运行的 LOL Hex，按用户的停止操作请求其退出。")
                    try:
                        self._request("/api/action", {"action": "shutdown"}, timeout=2.0)
                    except Exception as exc:
                        self.logger.info("LOL Hex 已收到停止请求或连接已关闭：%s", exc)
                    deadline = time.monotonic() + 8.0
                    while time.monotonic() < deadline:
                        if not self._is_healthy():
                            return self.status()
                        time.sleep(0.2)
                    raise TimeoutError("独立运行的 LOL Hex 未能在规定时间内退出。")
                self._process = None
                self._owned = False
                return self.status()

            if not self._owned:
                raise RuntimeError("当前 LOL Hex 进程不由主项目启动，无法从这里关闭。")

            self.logger.info("正在停止由主项目启动的 LOL Hex 扩展。")
            try:
                # Reuse the extension's existing manager shutdown action.
                self._request("/api/action", {"action": "shutdown"}, timeout=2.0)
            except Exception as exc:
                self.logger.warning("请求 LOL Hex 正常退出失败，将结束托管子进程：%s", exc)

            try:
                process.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)

            self._process = None
            self._owned = False
            self.logger.info("LOL Hex 托管进程已停止。")
            return self.status()

    def stop_if_owned(self) -> None:
        try:
            if self._process is not None and self._owned:
                self.stop()
        except Exception:
            self.logger.exception("退出主程序时停止 LOL Hex 扩展失败。")
