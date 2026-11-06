"""Lifecycle management for the optional LOL Hex Display process."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.config_manager import ConfigManager


class LolHexExtensionManager:
    def __init__(self, base_dir: Path, logger: Any) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.extension_dir = self.base_dir / "extensions" / "lol_hex_display"
        self.logger = logger
        self.config_manager = ConfigManager(
            directory=self.base_dir / "config" / "local",
            default_directory=self.base_dir / "config" / "default",
        )
        self.config_path = self.base_dir / "config" / "local" / "lol_hex_display.json"
        self._migrate_legacy_config()
        self.config = self.config_manager.load("lol_hex_display", {
            "screenshot_dir": "screenshots/inbox",
            "host": "127.0.0.1",
            "port": 8767,
            "scan_interval": 1,
            "asset_check_interval": 3600,
            "version_check_interval": 3600,
            "gameflow_check_interval": 10,
            "lol_client_path": r"D:\WeGameApps\英雄联盟（含经典模式）",
        })
        # ConfigManager 的自定义目录模式只负责读取；这里将合并后的完整配置写回，
        # 确保新增默认字段也会自动补齐到本机 JSON，且本机已有值优先。
        self.config_manager.save("lol_hex_display", self.config)
        self._lock = threading.RLock()
        self._process: subprocess.Popen[Any] | None = None
        self._owned = False
        self._elevated_managed = False
        self._last_error = ""
        self.host = self._read_host()
        self.port = self._read_port()
        self.pid_file = self.extension_dir / "lol_hex_display.pid"
        formatted_host = f"[{self.host}]" if ":" in self.host else self.host
        self.base_url = f"http://{formatted_host}:{self.port}"

    def _migrate_legacy_config(self) -> None:
        """首次升级时将旧扩展目录中的 config.txt 迁移到主项目本机 JSON 配置。"""
        if self.config_path.exists():
            return
        legacy = self.extension_dir / "config.txt"
        if not legacy.is_file():
            return
        migrated: dict[str, Any] = {}
        integer_keys = {
            "port", "scan_interval", "asset_check_interval",
            "version_check_interval", "gameflow_check_interval",
        }
        try:
            for line in legacy.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                key, separator, value = line.partition("=")
                if not separator:
                    continue
                key, value = key.strip(), value.strip()
                if not key or key.startswith("#"):
                    continue
                if key in integer_keys:
                    try:
                        migrated[key] = int(value)
                    except ValueError:
                        continue
                elif key in {"screenshot_dir", "host", "lol_client_path"}:
                    migrated[key] = value
            if migrated:
                self.config_manager.save("lol_hex_display", migrated)
                self.logger.info("已将旧 LOL Hex config.txt 迁移到 config/local/lol_hex_display.json")
        except Exception:
            self.logger.exception("迁移旧 LOL Hex 配置失败；将使用默认 JSON 配置")

    def _read_host(self) -> str:
        host = str(self.config.get("host", "127.0.0.1")).strip().strip("[]")
        return "127.0.0.1" if host in {"", "0.0.0.0", "::"} else host

    def _read_port(self) -> int:
        try:
            candidate = int(self.config.get("port", 8767))
            return candidate if 1 <= candidate <= 65535 else 8767
        except (TypeError, ValueError):
            return 8767

    def _read_pid(self) -> int | None:
        try:
            pid = int(self.pid_file.read_text(encoding="ascii").strip())
            return pid if pid > 0 else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name != "nt":
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                # ACCESS_DENIED 代表进程仍存在，只是当前权限无法查询。
                return ctypes.get_last_error() == 5
            try:
                return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True

    def _clear_stale_elevated_process(self) -> None:
        """清理已退出的管理员 Hex 进程标记，避免状态永久停留在启动中。"""
        if not self._elevated_managed:
            return
        pid = self._read_pid()
        if pid is not None and self._pid_is_running(pid):
            return

        self._elevated_managed = False
        if pid is not None:
            try:
                if self._read_pid() == pid:
                    self.pid_file.unlink(missing_ok=True)
            except OSError:
                self.logger.warning("清理已退出 LOL Hex 的 PID 文件失败。", exc_info=True)
        self.logger.warning("检测到 LOL Hex 管理员进程已退出，已清理托管状态。")

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
            return "recognized" in self._request("/api/client-status", timeout=0.7)
        except Exception:
            return False

    def _wait_for_process_exit(self, pid: int | None, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if pid is not None:
                if not self._pid_is_running(pid):
                    return True
            elif not self._is_healthy():
                return True
            time.sleep(0.2)
        return pid is not None and not self._pid_is_running(pid)

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
            was_elevated_managed = self._elevated_managed
            self._clear_stale_elevated_process()
            healthy = self._is_healthy()
            if not healthy and was_elevated_managed and not self._elevated_managed and not self._last_error:
                self._last_error = "LOL Hex 管理员进程已退出。"
            if healthy:
                managed = bool((self._process is not None and self._owned) or self._elevated_managed)
                return {
                    "ok": True, "state": "running", "running": True,
                    "owned": managed, "external": not managed,
                    "port": self.port, "base_url": self.base_url, "error": self._last_error,
                }
            if (self._process is not None and self._process.poll() is None) or self._elevated_managed:
                return {
                    "ok": True, "state": "starting", "running": False,
                    "owned": bool(self._owned or self._elevated_managed), "external": False,
                    "port": self.port, "base_url": self.base_url, "error": self._last_error,
                }
            return {
                "ok": True, "state": "stopped", "running": False,
                "owned": False, "external": False,
                "port": self.port, "base_url": self.base_url, "error": self._last_error,
            }

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            executable = self.extension_dir / "lol_hex_display.exe"
            if not executable.is_file():
                raise FileNotFoundError("未找到打包后的 LOL Hex 程序，请重新运行主项目 build.bat。")
            return [str(executable), "--config", str(self.config_path)]
        script = self.extension_dir / "main.py"
        if not script.is_file():
            raise FileNotFoundError("未找到 LOL Hex 扩展源码，请检查 extensions/lol_hex_display 目录。")
        # 源码模式下，扩展必须复用主程序当前解释器，避免混用 venv 与系统 Python。
        return [sys.executable, str(script), "--config", str(self.config_path)]

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._is_healthy():
                return self.status()
            self._clear_stale_elevated_process()
            if self._process is not None and self._process.poll() is None:
                return self.status()
            command = self._command()
            self._last_error = ""
            if os.name == "nt":
                executable = command[0]
                parameters = subprocess.list2cmdline(command[1:])
                self.logger.info("请求以 Windows 管理员权限启动 LOL Hex：%s", executable)
                shell_execute = ctypes.windll.shell32.ShellExecuteW
                shell_execute.restype = ctypes.c_void_p
                result = shell_execute(None, "runas", executable, parameters, str(self.extension_dir), 0)
                if not result or result <= 32:
                    error_code = int(result or 0)
                    self._last_error = (
                        "LOL Hex 管理员权限启动失败（用户取消 UAC 或 Windows 拒绝启动，"
                        f"错误码：{error_code}）。"
                    )
                    self.logger.error(self._last_error)
                    return {
                        "ok": False, "state": "stopped", "running": False,
                        "owned": False, "external": False, "port": self.port,
                        "base_url": self.base_url, "error": self._last_error,
                    }
                self._elevated_managed = True
            else:
                env = os.environ.copy()
                env["LOL_HEX_MANAGED_BY_BILI"] = "1"
                env["PYTHONUNBUFFERED"] = "1"
                self._process = subprocess.Popen(
                    command, cwd=str(self.extension_dir), env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._owned = True

            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                if self._is_healthy():
                    self.logger.info("LOL Hex 扩展已启动，地址：%s，PID：%s", self.base_url, self._read_pid())
                    return self.status()
                if self._process is not None and self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None
                    self._owned = False
                    self._last_error = f"LOL Hex 启动失败，进程退出码：{code}。"
                    raise RuntimeError(self._last_error)
                time.sleep(0.25)

            self._last_error = "等待 LOL Hex Web 服务启动超时，请检查 UAC 是否完成以及 Hex 日志。"
            self._clear_stale_elevated_process()
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
            pid = self._read_pid()
            if process is not None and process.poll() is not None:
                self._process = None
                self._owned = False
                process = None
            if process is not None and not self._owned:
                raise RuntimeError("当前 LOL Hex 进程不由主项目启动，无法从这里关闭。")
            if pid is None and not self._is_healthy() and process is None:
                self._elevated_managed = False
                self._owned = False
                return self.status()

            self.logger.info("正在请求 LOL Hex 管理员进程执行有序退出，PID：%s", pid or "未知")
            try:
                self._request("/api/action", {"action": "shutdown"}, timeout=2.0)
            except Exception as exc:
                self.logger.warning("发送 Hex 有序退出请求时连接中断：%s", exc)

            if self._wait_for_process_exit(pid, 12.0):
                if process is not None:
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
                self._process = None
                self._owned = False
                self._elevated_managed = False
                self.logger.info("已确认 LOL Hex 进程退出，PID：%s", pid or "未知")
                return self.status()

            self.logger.error(
                "LOL Hex 收到有序退出请求后仍未退出；PID=%s，服务健康=%s。",
                pid, self._is_healthy(),
            )
            raise TimeoutError(
                f"LOL Hex 管理员进程仍在运行（PID：{pid or '未知'}），未确认退出；请检查 Hex 日志。"
            )

    def stop_if_owned(self) -> None:
        try:
            if (self._process is not None and self._owned) or self._elevated_managed:
                self.stop()
        except Exception:
            self.logger.exception("退出主程序时停止 LOL Hex 扩展失败。")
