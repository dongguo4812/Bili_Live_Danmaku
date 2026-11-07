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
        self._elevated_process_handle: int | None = None
        self._elevated_pid: int | None = None
        self._elevated_creation_time: int | None = None
        # UAC 二次提权时，实际服务进程可能不同于 ShellExecuteEx 启动进程。
        self._worker_pid: int | None = None
        self._worker_creation_time: int | None = None
        # 重复实例启动尝试已退出，但互斥锁持有者可能仍在启动；不得认领或清理它。
        self._duplicate_instance_conflict = False
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

    def _read_pid_record_state(self) -> tuple[str, dict[str, int | None] | None]:
        """区分 PID 文件缺失、有效、内容无效和暂时无法读取。"""
        try:
            raw = self.pid_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "missing", None
        except OSError:
            return "unknown", None

        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return "invalid", None
            pid = payload.get("pid")
            creation_time = payload.get("creation_time")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                return "invalid", None
            if os.name == "nt" and (not isinstance(creation_time, int) or creation_time <= 0):
                return "invalid", None
            if creation_time is not None and (not isinstance(creation_time, int) or creation_time <= 0):
                return "invalid", None
            return "valid", {"pid": pid, "creation_time": creation_time}
        except (ValueError, TypeError, UnicodeError):
            return "invalid", None

    def _read_pid_record(self) -> dict[str, int | None] | None:
        """读取有效的进程身份记录；无效或无法读取时返回 None。"""
        state, record = self._read_pid_record_state()
        return record if state == "valid" else None

    def _read_pid(self) -> int | None:
        record = self._read_pid_record()
        return record["pid"] if record else None

    @staticmethod
    def _creation_time_from_handle(handle: int) -> int | None:
        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetProcessTimes.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ]
            kernel32.GetProcessTimes.restype = ctypes.c_bool
            creation = FILETIME()
            exit_time = FILETIME()
            kernel_time = FILETIME()
            user_time = FILETIME()
            if not kernel32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time),
            ):
                return None
            return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        except Exception:
            return None

    @staticmethod
    def _process_identity_state(pid: int, expected_creation_time: int | None = None) -> str:
        """返回 running/exited/unknown；无法查询时绝不把 unknown 当作已退出。"""
        if pid <= 0:
            return "unknown"
        if os.name != "nt":
            try:
                os.kill(pid, 0)
                return "running"
            except ProcessLookupError:
                return "exited"
            except PermissionError:
                return "running"
            except OSError:
                return "unknown"
        if expected_creation_time is None:
            return "unknown"
        try:
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetProcessTimes.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
                ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
            ]
            kernel32.GetProcessTimes.restype = ctypes.c_bool
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel32.WaitForSingleObject.restype = ctypes.c_ulong
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_bool
            handle = kernel32.OpenProcess(0x00101000, False, pid)
            if not handle:
                return "exited" if kernel32.GetLastError() in (87, 1168) else "unknown"
            try:
                result = kernel32.WaitForSingleObject(handle, 0)
                if result == 0x00000000:
                    return "exited"
                if result != 0x00000102:
                    return "unknown"
                creation = FILETIME()
                exit_time = FILETIME()
                kernel_time = FILETIME()
                user_time = FILETIME()
                if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel_time), ctypes.byref(user_time),
                ):
                    return "unknown"
                actual = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                return "running" if actual == expected_creation_time else "exited"
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return "unknown"

    @staticmethod
    def _pid_is_running(pid: int, expected_creation_time: int | None = None) -> bool:
        return LolHexExtensionManager._process_identity_state(pid, expected_creation_time) == "running"

    def _capture_worker_identity(self) -> bool:
        record = self._read_pid_record()
        if record is None:
            return False
        if self._worker_pid is None:
            self._worker_pid = record["pid"]
            self._worker_creation_time = record["creation_time"]
            return True
        return self._worker_pid == record["pid"] and self._worker_creation_time == record["creation_time"]

    def _launcher_process_state(self) -> str:
        if self._elevated_process_handle:
            try:
                kernel32 = ctypes.windll.kernel32
                kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                kernel32.WaitForSingleObject.restype = ctypes.c_ulong
                result = kernel32.WaitForSingleObject(self._elevated_process_handle, 0)
                if result == 0x00000000:
                    return "exited"
                if result == 0x00000102:
                    return "running"
                return "unknown"
            except Exception:
                return "unknown"
        if self._elevated_pid is not None:
            return self._process_identity_state(self._elevated_pid, self._elevated_creation_time)
        return "unknown"

    def _launcher_exit_code(self) -> int | None:
        """读取 ShellExecuteEx 启动目标的退出码；调用前应已确认句柄 signaled。"""
        if not self._elevated_process_handle:
            return None
        try:
            exit_code = ctypes.c_ulong()
            kernel32 = ctypes.windll.kernel32
            kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            kernel32.GetExitCodeProcess.restype = ctypes.c_bool
            if not kernel32.GetExitCodeProcess(self._elevated_process_handle, ctypes.byref(exit_code)):
                return None
            return int(exit_code.value)
        except Exception:
            self.logger.debug("读取 LOL Hex 启动进程退出码失败。", exc_info=True)
            return None

    def _release_duplicate_instance_attempt(self) -> None:
        """释放已确认只是重复启动尝试的句柄，不触碰已有实例及其 PID 文件。"""
        self._close_elevated_process_handle()
        self._elevated_pid = None
        self._elevated_creation_time = None
        self._worker_pid = None
        self._worker_creation_time = None
        self._elevated_managed = False
        self._duplicate_instance_conflict = False

    def _elevated_process_running(self) -> bool:
        self._capture_worker_identity()
        if self._worker_pid is not None:
            return self._process_identity_state(self._worker_pid, self._worker_creation_time) != "exited"
        return self._launcher_process_state() != "exited"

    def _close_elevated_process_handle(self) -> None:
        handle = self._elevated_process_handle
        self._elevated_process_handle = None
        if handle:
            try:
                kernel32 = ctypes.windll.kernel32
                kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel32.CloseHandle.restype = ctypes.c_bool
                kernel32.CloseHandle(handle)
            except Exception:
                self.logger.debug("关闭 LOL Hex 进程句柄失败。", exc_info=True)

    def _clear_stale_elevated_process(self) -> None:
        """仅在进程退出得到确认、且 PID 文件状态可解释时清理托管状态。"""
        if not self._elevated_managed or self._duplicate_instance_conflict:
            return

        pid_file_state, record = self._read_pid_record_state()
        if pid_file_state not in {"missing", "valid"}:
            # 文件存在但内容无效，或文件暂时无法读取，都不能当作“尚未生成”。
            return

        self._capture_worker_identity()
        worker_state = (
            self._process_identity_state(self._worker_pid, self._worker_creation_time)
            if self._worker_pid is not None else "unknown"
        )
        launcher_state = self._launcher_process_state()

        if pid_file_state == "missing" and self._worker_pid is None:
            # 服务尚未写出 PID 文件时，ShellExecuteEx 句柄是本次启动目标的
            # 唯一可靠身份；只有句柄明确 signaled（已退出）才可回收。
            if launcher_state != "exited":
                return
        else:
            # 已捕获过工作进程，或 PID 文件当前有记录时，必须同时核实两边退出。
            if worker_state != "exited" or launcher_state != "exited":
                return
            if pid_file_state == "valid" and (
                self._worker_pid is None
                or record["pid"] != self._worker_pid
                or record["creation_time"] != self._worker_creation_time
            ):
                return

        self._close_elevated_process_handle()
        if (
            pid_file_state == "valid"
            and record is not None
            and self._worker_pid is not None
            and record["pid"] == self._worker_pid
            and record["creation_time"] == self._worker_creation_time
        ):
            try:
                current_state, current_record = self._read_pid_record_state()
                if current_state == "valid" and current_record == record:
                    self.pid_file.unlink(missing_ok=True)
            except OSError:
                self.logger.warning("清理已退出 LOL Hex 的 PID 文件失败。", exc_info=True)
        self._elevated_pid = None
        self._elevated_creation_time = None
        self._worker_pid = None
        self._worker_creation_time = None
        self._elevated_managed = False
        self.logger.warning("已确认 LOL Hex 本次托管进程退出，已清理托管状态。")

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
        """等待工作进程退出；无 PID 文件时仅接受启动句柄明确退出作为证明。"""
        deadline = time.monotonic() + timeout
        while True:
            self._capture_worker_identity()
            pid_file_state, record = self._read_pid_record_state()
            if self._worker_pid is None and pid is not None and not self._elevated_managed:
                if pid_file_state == "valid" and record is not None and record["pid"] == pid:
                    self._worker_pid = record["pid"]
                    self._worker_creation_time = record["creation_time"]

            launcher_state = self._launcher_process_state() if self._elevated_managed else "exited"
            if self._worker_pid is not None:
                worker_state = self._process_identity_state(
                    self._worker_pid, self._worker_creation_time
                )
                if worker_state == "exited" and launcher_state == "exited":
                    return True
            elif self._elevated_managed and pid_file_state == "missing":
                # PID 文件从未写出时，不存在可核验的 worker 身份；
                # 只有本次启动句柄明确退出，才能确认该启动目标已经结束。
                if launcher_state == "exited":
                    return True
            elif not self._elevated_managed and self._process is not None:
                # 非提权托管模式下，Popen 句柄是直接启动的工作进程身份；
                # 只有 poll() 确认退出后才可结束等待。
                if self._process.poll() is not None:
                    return True

            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)

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
            if healthy and self._duplicate_instance_conflict:
                # 健康服务是 mutex 的现有持有者，不是本次已退出的启动尝试。
                self._release_duplicate_instance_attempt()
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
                    "owned": bool(self._owned or (self._elevated_managed and not self._duplicate_instance_conflict)), "external": False,
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
            # Windows 管理员启动由 ShellExecuteEx 管理；即使 HTTP 健康检查尚未就绪，
            # 只要旧启动/工作进程尚未确认退出，就不能再次拉起一个实例。
            if self._elevated_managed:
                return self.status()
            if self._process is not None and self._process.poll() is None:
                return self.status()
            command = self._command()
            self._last_error = ""
            if os.name == "nt":
                executable = command[0]
                parameters = subprocess.list2cmdline(command[1:])
                self.logger.info("请求以 Windows 管理员权限启动 LOL Hex：%s", executable)
                class ShellExecuteInfo(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong),
                        ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
                        ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                        ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
                        ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                        ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                        ("dwHotKey", ctypes.c_ulong), ("hIcon", ctypes.c_void_p),
                        ("hProcess", ctypes.c_void_p),
                    ]
                info = ShellExecuteInfo()
                info.cbSize = ctypes.sizeof(ShellExecuteInfo)
                info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
                info.lpVerb = "runas"
                info.lpFile = executable
                info.lpParameters = parameters
                info.lpDirectory = str(self.extension_dir)
                info.nShow = 0
                shell_execute = ctypes.windll.shell32.ShellExecuteExW
                shell_execute.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
                shell_execute.restype = ctypes.c_bool
                if not shell_execute(ctypes.byref(info)) or not info.hProcess:
                    error_code = ctypes.get_last_error()
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
                self._elevated_process_handle = int(info.hProcess)
                get_process_id = ctypes.windll.kernel32.GetProcessId
                get_process_id.argtypes = [ctypes.c_void_p]
                get_process_id.restype = ctypes.c_ulong
                self._elevated_pid = int(get_process_id(self._elevated_process_handle)) or None
                self._elevated_creation_time = self._creation_time_from_handle(self._elevated_process_handle)
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
                    if self._duplicate_instance_conflict:
                        self._release_duplicate_instance_attempt()
                    self.logger.info("LOL Hex 扩展已启动，地址：%s，PID：%s", self.base_url, self._read_pid())
                    return self.status()

                if self._elevated_managed and self._launcher_process_state() == "exited":
                    exit_code = self._launcher_exit_code()
                    self._capture_worker_identity()
                    if exit_code == 17:  # LOL Hex EXIT_ALREADY_RUNNING
                        # 先再次检查服务健康；若尚未就绪，保留冲突状态，避免误启动第二份。
                        if self._is_healthy():
                            self._release_duplicate_instance_attempt()
                            return self.status()
                        self._duplicate_instance_conflict = True
                        self._last_error = "检测到已有 LOL Hex 实例持有单实例锁，但服务暂不可用；为避免重复启动，已保留冲突状态。"
                        raise RuntimeError(self._last_error)

                    if self._worker_pid is not None:
                        worker_state = self._process_identity_state(
                            self._worker_pid, self._worker_creation_time
                        )
                        if worker_state == "running":
                            # 启动器与实际服务进程可能分离；继续等待服务健康，不回收身份。
                            time.sleep(0.25)
                            continue
                        if worker_state == "unknown":
                            self._last_error = "LOL Hex 启动进程已退出，但实际工作进程身份无法确认；已保留托管状态。"
                            raise RuntimeError(self._last_error)
                    else:
                        pid_state, _ = self._read_pid_record_state()
                        if pid_state != "missing":
                            self._last_error = "LOL Hex 启动进程已退出，但工作进程记录无效或无法读取；已保留托管状态。"
                            raise RuntimeError(self._last_error)

                    self._last_error = f"LOL Hex 启动失败，启动进程退出码：{exit_code if exit_code is not None else \"未知\"}。"
                    self._clear_stale_elevated_process()
                    raise RuntimeError(self._last_error)

                if self._process is not None and self._process.poll() is not None:
                    code = self._process.returncode
                    self._process = None
                    self._owned = False
                    self._last_error = f"LOL Hex 启动失败，进程退出码：{code}。"
                    raise RuntimeError(self._last_error)
                time.sleep(0.25)

            self._last_error = "等待 LOL Hex Web 服务启动超时，请检查 UAC 是否完成以及 Hex 日志。"
            process = self._process
            if self._elevated_process_handle:
                # 只向本次 ShellExecuteEx 返回的确切进程请求退出，避免 PID 文件尚未生成时留下孤儿进程。
                if self._is_healthy():
                    try:
                        self._request("/api/action", {"action": "shutdown"}, timeout=2.0)
                    except Exception as exc:
                        self.logger.warning("启动超时后发送 Hex 有序退出请求失败：%s", exc)
                if not self._wait_for_process_exit(self._elevated_pid, 3.0):
                    # 只有句柄身份与实际服务进程身份完全一致，才允许强制终止。
                    same_process = (
                        self._worker_pid is not None
                        and self._elevated_pid == self._worker_pid
                        and self._elevated_creation_time is not None
                        and self._elevated_creation_time == self._worker_creation_time
                    )
                    if same_process and self._launcher_process_state() == "running":
                        try:
                            terminate_process = ctypes.windll.kernel32.TerminateProcess
                            terminate_process.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                            terminate_process.restype = ctypes.c_bool
                            if not terminate_process(self._elevated_process_handle, 1):
                                raise ctypes.WinError(ctypes.get_last_error())
                        except Exception:
                            self.logger.exception("启动超时后终止已核实的 LOL Hex 工作进程失败。")
                        self._wait_for_process_exit(self._elevated_pid, 3.0)
                    else:
                        self.logger.error("启动超时后无法确认启动进程就是 Hex 工作进程；保留托管身份，不执行强制终止。")
                self._clear_stale_elevated_process()
            else:
                self._clear_stale_elevated_process()
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
            record = self._read_pid_record()
            if record is not None and self._worker_pid is None:
                self._worker_pid = record["pid"]
                self._worker_creation_time = record["creation_time"]
            pid = (record["pid"] if record else None) or self._worker_pid or self._elevated_pid
            if process is not None and process.poll() is not None:
                self._process = None
                self._owned = False
                process = None
            if process is not None and not self._owned:
                raise RuntimeError("当前 LOL Hex 进程不由主项目启动，无法从这里关闭。")
            if pid is None and not self._is_healthy() and process is None and not self._elevated_process_handle:
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
                self._close_elevated_process_handle()
                self._elevated_pid = None
                self._elevated_creation_time = None
                self._worker_pid = None
                self._worker_creation_time = None
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
