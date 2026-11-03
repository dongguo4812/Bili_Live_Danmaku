# -*- coding: utf-8 -*-
"""敏感凭据本地存储。Windows 使用 DPAPI，其他平台使用受限文件权限。"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path
from typing import Any

from core.paths import get_base_dir

BASE_DIR = get_base_dir()
CREDENTIAL_DIR = BASE_DIR / "credentials"
_LOCK = threading.RLock()


class CredentialStoreError(RuntimeError):
    pass


def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("当前平台不支持 Windows DPAPI。")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

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
        ok = crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


class CredentialStore:
    """每种服务凭据独立文件；API 不提供明文回显接口。"""

    def __init__(self, directory: Path | None = None):
        self.directory = (directory or CREDENTIAL_DIR).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            if os.name != "nt":
                os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def _path(self, name: str) -> Path:
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", name):
            raise CredentialStoreError("凭据名称无效。")
        path = (self.directory / f"{name}.json").resolve()
        if path.parent != self.directory:
            raise CredentialStoreError("凭据路径越界。")
        return path

    def save(self, name: str, secrets: dict[str, Any]) -> Path:
        if not isinstance(secrets, dict):
            raise CredentialStoreError("凭据内容必须是 JSON 对象。")
        raw = json.dumps(secrets, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if os.name == "nt":
            stored = {"format": "dpapi-v1", "payload": base64.b64encode(_dpapi(raw, True)).decode("ascii")}
        else:
            # 非 Windows 开发环境只允许受限权限文件；部署到 Windows 时会自动使用 DPAPI。
            stored = {"format": "plain-local-v1", "payload": base64.b64encode(raw).decode("ascii")}
        path = self._path(name)
        temporary = path.with_suffix(".json.tmp")
        with _LOCK:
            try:
                temporary.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, path)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        return path

    def load(self, name: str) -> dict[str, Any] | None:
        path = self._path(name)
        if not path.is_file():
            return None
        with _LOCK:
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if stored.get("format") == "dpapi-v1":
                    raw = _dpapi(base64.b64decode(stored["payload"], validate=True), False)
                elif stored.get("format") == "plain-local-v1" and os.name != "nt":
                    raw = base64.b64decode(stored["payload"], validate=True)
                else:
                    raise CredentialStoreError("凭据格式与当前系统不匹配。")
                data = json.loads(raw.decode("utf-8"))
            except CredentialStoreError:
                raise
            except Exception as exc:
                raise CredentialStoreError(f"读取凭据失败：{exc}") from exc
        if not isinstance(data, dict):
            raise CredentialStoreError("凭据文件内容格式无效。")
        return data

    def status(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        return {"configured": path.is_file(), "file": path.name if path.is_file() else None}
