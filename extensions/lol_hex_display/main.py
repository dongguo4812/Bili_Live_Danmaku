# -*- coding: utf-8 -*-
"""lol_hex_display：F1 自由区域截图 + 本地 OCR + 海克斯强化卡片展示。Python 3.10+"""
from __future__ import annotations
import argparse, ctypes, subprocess, hashlib, io, json, logging, os, re, shutil, sys, threading, time , atexit, queue
from logging.handlers import QueueHandler, QueueListener
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from bar_integration import register_bar_routes

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from PIL import Image, ImageTk, ImageDraw, ImageFont

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = ROOT / 'data'; ICON_DIR = DATA / 'hex_icons'; SHOTS = ROOT / 'screenshots'; PROCESSED = SHOTS / 'processed'; LOG_DIR = ROOT / 'logs'
for p in (DATA, ICON_DIR, SHOTS, PROCESSED, LOG_DIR): p.mkdir(parents=True, exist_ok=True)
_config_parser = argparse.ArgumentParser(add_help=False)
_config_parser.add_argument("--config", type=Path)
_config_args, _ = _config_parser.parse_known_args()
CONFIG_PATH = (_config_args.config.resolve() if _config_args.config else ROOT / "config.json")
ASSET_CACHE = DATA / 'hex_assets.json'; STATE_PATH = DATA / 'state.json'; ICON_SOURCE_PATH = DATA / 'hex_icon_sources.json'
AUGMENTS_URL = 'https://game.gtimg.cn/images/lol/act/img/js/kiwi/kiwi_augments.json'
# 日志配置与主项目 config/default/local/logging.json 保持一致。
def _load_logging_settings():
    settings = {"retention_days": 7, "debug": False}
    try:
        # 主项目通过 --config config/local/lol_hex_display.json 启动 Hex。
        # 独立启动时若目录结构不同，则使用与主项目相同的默认值。
        project_root = CONFIG_PATH.parent.parent.parent
        default_path = project_root / "config" / "default" / "logging.json"
        local_path = project_root / "config" / "local" / "logging.json"
        for path in (default_path, local_path):
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(payload, dict):
                    settings.update(payload)
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return settings


log = logging.getLogger("lol_hex_display")
_logging_settings = _load_logging_settings()
# 日常固定为 INFO；仅通过 debug 开关额外启用 DEBUG。
LOG_LEVEL = logging.DEBUG if _logging_settings.get("debug") is True else logging.INFO
try:
    LOG_RETENTION_DAYS = max(0, int(_logging_settings.get("retention_days", 7)))
except (TypeError, ValueError):
    LOG_RETENTION_DAYS = 7

def cleanup_old_logs():
    """按主项目的 retention_days 规则清理日期日志；0 表示不自动清理。"""
    if LOG_RETENTION_DAYS <= 0:
        return
    from datetime import timedelta

    cutoff = datetime.now().date() - timedelta(days=LOG_RETENTION_DAYS - 1)
    for path in LOG_DIR.glob("*.log"):
        match = re.fullmatch(
            r"(?:app_|thread_dump_)?(\d{4}-\d{2}-\d{2})",
            path.stem,
        )
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            if file_date < cutoff:
                path.unlink()
        except (ValueError, OSError):
            continue


class DailyFileHandler(logging.Handler):
    """与主项目一致，每天写入独立的 YYYY-MM-DD.log 文件。"""

    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._current_date = date.today()
        self._stream = None
        self._open_stream()

    def _open_stream(self):
        if self._stream:
            self._stream.close()
        path = self.directory / f"{self._current_date.isoformat()}.log"
        self._stream = path.open("a", encoding="utf-8", buffering=1)

    def emit(self, record):
        try:
            today = date.today()
            if today != self._current_date:
                self._current_date = today
                self._open_stream()
            self._stream.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)

    def close(self):
        try:
            if self._stream:
                self._stream.close()
                self._stream = None
        finally:
            super().close()


class SecretRedactionFilter(logging.Filter):
    """与主项目一致，对常见凭据字段进行日志脱敏。"""

    _secret_pattern = re.compile(
        r"(?i)(SESSDATA|bili_jct|buvid3|buvid4|ac_time_value|DedeUserID|cookie|password|passwd|token|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token)(\s*[:=]\s*)([\"']?)([^\s,;}\"']+)"
    )
    _header_pattern = re.compile(
        r"(?i)((?:Cookie|Authorization)\s*:\s*)([^\r\n]+)"
    )

    def filter(self, record):
        try:
            message = record.getMessage()
            message = self._secret_pattern.sub(
                lambda match: match.group(1) + match.group(2) + "***",
                message,
            )
            message = self._header_pattern.sub(
                lambda match: match.group(1) + "***",
                message,
            )
            record.msg = message
            record.args = ()
            if record.exc_info:
                formatted = logging.Formatter().formatException(record.exc_info)
                record.exc_text = self._secret_pattern.sub(
                    lambda match: match.group(1) + match.group(2) + "***",
                    formatted,
                )
                record.exc_info = None
        except Exception:
            record.msg = "[log message redacted]"
            record.args = ()
        return True


# Hex 是独立进程，使用独立文件，避免与主项目同时写入同一个日志文件；
# 文件命名、格式、级别和保留策略遵循主项目规范。
cleanup_old_logs()
log_queue = queue.SimpleQueue()
file_handler = DailyFileHandler(LOG_DIR)
file_handler.setLevel(LOG_LEVEL)
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
file_handler.setFormatter(formatter)

log_listener = QueueListener(
    log_queue,
    file_handler,
    respect_handler_level=True,
)

root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
    try:
        handler.close()
    except Exception:
        pass

queue_handler = QueueHandler(log_queue)
queue_handler.setLevel(LOG_LEVEL)
queue_handler.addFilter(SecretRedactionFilter())
root_logger.addHandler(queue_handler)
log_listener.start()

# 程序退出时停止日志监听线程
def stop_async_logging():
    try:
        if log_listener:
            log_listener.stop()
    except Exception:
        pass

atexit.register(stop_async_logging)



DEFAULT_CONFIG = {
    'screenshot_dir': 'screenshots/inbox',
    'host': '127.0.0.1',
    'port': 8767,
    'scan_interval': 1,
    'asset_check_interval': 3600,
    'version_check_interval': 3600,
    'gameflow_check_interval': 10,
    'ocr_intra_op_num_threads': 2,
    'ocr_inter_op_num_threads': 1,
    'lol_client_path': r'D:\WeGameApps\英雄联盟（含经典模式）',
}
def check_admin_privileges():
    """源码运行时确保 LOL Hex 以管理员权限启动；打包版由 EXE 清单强制提权。"""
    if os.name != "nt":
        return True

    try:
        if ctypes.windll.shell32.IsUserAnAdmin() != 0:
            log.debug("[权限检查] 当前以管理员身份运行")
            return True
    except Exception:
        log.exception("[权限检查] 无法检查 Windows 管理员权限")
        return False

    # PyInstaller 打包版由 requireAdministrator 清单负责触发 UAC。
    # 若清单未生效，仍使用此处作为兜底，避免普通权限继续运行。
    try:
        if getattr(sys, "frozen", False):
            executable = sys.executable
            parameters = subprocess.list2cmdline(sys.argv[1:])
        else:
            executable = sys.executable
            parameters = subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]])

        shell_execute = ctypes.windll.shell32.ShellExecuteW
        shell_execute.restype = ctypes.c_void_p
        # SW_HIDE keeps the elevated background extension from opening a console window.
        result = shell_execute(None, "runas", executable, parameters, str(ROOT), 0)
        if result and result > 32:
            log.info("[权限检查] 已请求 Windows 以管理员权限重新启动 LOL Hex")
            return False

        error_code = int(result or 0)
        log.error("[权限检查] Windows 管理员启动失败，ShellExecuteW 返回：%s", error_code)
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                "LOL Hex 必须以管理员身份运行，才能保证游戏内 F1 截图正常工作。\n"
                "管理员启动未成功，程序将退出。",
                "LOL Hex 管理员权限",
                0x10,
            )
        except Exception:
            pass
    except Exception as exc:
        log.exception("[权限检查] 请求管理员启动失败：%s", exc)
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                f"LOL Hex 必须以管理员身份运行。\n启动失败：{exc}",
                "LOL Hex 管理员权限",
                0x10,
            )
        except Exception:
            pass
    return False

def read_config():
    """读取主项目传入的 JSON 配置；独立启动时使用程序默认值。"""
    cfg = DEFAULT_CONFIG.copy()
    try:
        if CONFIG_PATH.is_file():
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                cfg.update({key: value for key, value in payload.items() if key in DEFAULT_CONFIG})
    except (OSError, json.JSONDecodeError) as exc:
        log.error("[配置] 读取 JSON 配置失败 %s：%s；使用默认值", CONFIG_PATH, exc)
    return cfg

_screenshot_log_lock = threading.Lock()
_screenshot_log_states = {}


def _log_screenshot_issue(key, message, exc):
    signature = f"{type(exc).__name__}: {exc}"
    with _screenshot_log_lock:
        if key in _screenshot_log_states:
            return
        _screenshot_log_states[key] = signature
    log.warning("%s：%s", message, exc)


def _clear_screenshot_issue(key, message):
    with _screenshot_log_lock:
        existed = _screenshot_log_states.pop(key, None) is not None
    if existed:
        log.info("%s", message)


def screenshot_dir(config=None):
    config = config if config is not None else read_config()
    raw=str(config.get('screenshot_dir','screenshots/inbox') or 'screenshots/inbox')
    p=Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute(): p=ROOT/p
    try:
        p.mkdir(parents=True, exist_ok=True)
        _clear_screenshot_issue("configured_directory", "截图目录已恢复可用")
        return p.resolve()
    except Exception as e:
        _log_screenshot_issue(
            "configured_directory",
            f"截图目录不可用 {p}；回退到默认目录",
            e,
        )
        fallback=SHOTS/'inbox'; fallback.mkdir(parents=True,exist_ok=True); return fallback.resolve()

def read_json(path, default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception: return default

def atomic_json(path, obj):
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(path)

def normalize_assets(payload):
    rows=payload.get('data') if isinstance(payload,dict) else payload
    if isinstance(payload,dict) and not isinstance(rows,list): rows=payload.get('list')
    if not isinstance(rows,list): raise ValueError('腾讯数据中没有 data/list 数组')
    out={}
    for r in rows:
        if not isinstance(r,dict): continue
        aid=str(r.get('augmentID') or r.get('id') or '').strip()
        name=str(r.get('name_cn') or r.get('name') or '').strip()
        if not aid or not name: continue
        icon=str(r.get('small_Icon') or r.get('icon') or '').strip()
        if icon.startswith('//'): icon='https:'+icon
        if icon and not icon.startswith(('http://','https://')): icon=''
        out[aid]={'id':aid,'name':name,'icon_url':icon,'level':str(r.get('level') or '')}
    if not out: raise ValueError('远端数据没有有效海克斯记录')
    return out

assets_lock=threading.RLock(); assets=read_json(ASSET_CACHE,{})
if not isinstance(assets,dict): assets={}
state_lock=threading.RLock(); state=read_json(STATE_PATH,{'cards':[],'updated_at':0})
if not isinstance(state,dict) or not isinstance(state.get('cards'),list): state={'cards':[],'updated_at':0}
# One-time migration: previous versions stored newest cards first. Convert to oldest-first
# so newly captured cards can be appended at the end without changing the existing order again.
if state.get('display_order_version') != 2:
    state['cards'].reverse()
    state['display_order_version'] = 2
    state['updated_at'] = time.time()
    atomic_json(STATE_PATH, state)
icon_sources=read_json(ICON_SOURCE_PATH,{})


def _update_assets_impl(force=False):
    global assets, icon_sources
    try:
        r=requests.get(AUGMENTS_URL,timeout=20,headers={'User-Agent':'Mozilla/5.0'}); r.raise_for_status()
        new=normalize_assets(r.json())
        digest=lambda x: hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        # assets 只会整体替换，不会原地修改；在锁内取得当前字典引用后，
        # 可在锁外完成序列化和摘要计算，避免阻塞截图匹配读取。
        with assets_lock:
            current_assets = assets
        changed = digest(new) != digest(current_assets)
        if changed:
            # 先在锁外持久化；写入失败时保留旧内存索引，避免内存与缓存不一致。
            atomic_json(ASSET_CACHE, new)
            with assets_lock:
                assets = new
            log.info('海克斯索引更新：%d 条', len(new))
        else:
            log.debug('海克斯索引无变化：%d 条', len(new))
        icon_sources=read_json(ICON_SOURCE_PATH,{})
        icon_sources_changed = False
        for aid,item in new.items():
            url=item.get('icon_url',''); dest=ICON_DIR/f'{aid}.png'
            if not url: continue
            if dest.exists() and icon_sources.get(aid)==url:
                try:
                    with Image.open(dest) as im: im.verify()
                    continue
                except Exception: pass
            try:
                ir=requests.get(url,timeout=15,headers={'User-Agent':'Mozilla/5.0'}); ir.raise_for_status()
                with Image.open(io.BytesIO(ir.content)) as im:
                    im.convert('RGBA').save(dest,format='PNG')
                icon_sources[aid]=url
                icon_sources_changed = True
            except Exception as e: log.warning('图标下载失败 id=%s：%s',aid,e)
        if icon_sources_changed:
            atomic_json(ICON_SOURCE_PATH,icon_sources)
        return True
    except Exception as e:
        log.warning('海克斯索引更新失败，保留本地缓存：%s',e); return False

_asset_update_mutex=threading.Lock()
def update_assets(force=False):
    # 自动检查与管理页手动检查串行执行，避免同时写入缓存或重复下载同一图标。
    with _asset_update_mutex:
        return _update_assets_impl(force)

def find_game_version():
    """使用配置目录对应的 LCU 连接信息读取游戏版本。"""
    credentials = get_lcu_credentials()
    if not credentials:
        return ''

    port, token = credentials

    try:
        response = requests.get(
            f'https://127.0.0.1:{port}/lol-patch/v1/game-version',
            auth=('riot', token),
            verify=False,
            timeout=3
        )

        if response.ok:
            return str(response.json())

        log.debug(
            '[LCU调试] 读取游戏版本失败，HTTP=%s',
            response.status_code
        )

    except Exception as e:
        log.debug('[LCU调试] 读取游戏版本异常：%s', e)

    return ''



# LCU 凭据缓存：只有识别线程负责扫描进程/日志，其他模块只读取缓存。
_lcu_lock = threading.RLock()
_lcu_credentials = None
_lcu_status = {
    "recognized": False, "available": False, "source": "",
    "message": "正在识别英雄联盟客户端", "checked_at": 0, "account": "",
}
_lcu_scan_interval_unrecognized = 5
_lcu_scan_interval_recognized = 10


def _validate_lcu_credentials(port, token):
    """通过当前账号接口验证端口和 Token；只有 HTTP 200 才视为可用。"""
    try:
        response = requests.get(
            f"https://127.0.0.1:{int(port)}/lol-summoner/v1/current-summoner",
            auth=("riot", str(token)), verify=False, timeout=3,
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            return False, None
        payload = response.json()
        return True, payload if isinstance(payload, dict) else {}
    except Exception as exc:
        log.debug("[LCU调试] 凭据验证失败：%s", exc)
        return False, None


def _extract_credentials(command):
    port_match = re.search(r'--app-port(?:=|\s+)(\d+)', command or "")
    token_match = re.search(r'--remoting-auth-token(?:=|\s+)([^\s"]+)', command or "")
    if not port_match or not token_match:
        return None
    port, token = int(port_match.group(1)), token_match.group(1).rstrip("\\")
    return (port, token) if port > 0 and token else None


def _client_directories(client_path):
    """兼容 lol_client_path 指向安装根目录或 LeagueClient 子目录。"""
    root = Path(client_path)
    candidates = (root,) if root.name.lower() == "leagueclient" else (root, root / "LeagueClient")
    result = []
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


_lcu_lockfile_cache_lock = threading.RLock()
# 路径 -> (文件状态指纹, 解析结果)。文件状态未变化时不重复打开/读取 Lockfile。
_lcu_lockfile_cache = {}
_LCU_LOCKFILE_CACHE_MISS = object()


def _read_lcu_lockfile(client_dirs):
    """读取 LeagueClient lockfile；未变化的缺失/空/无效文件使用负缓存。"""
    for directory in client_dirs:
        lockfile = directory / "lockfile"
        cache_key = os.path.normcase(str(lockfile))

        try:
            stat_result = lockfile.stat()
        except FileNotFoundError:
            signature = None
            is_file = False
        except OSError as exc:
            log.debug("[LCU调试] 检查 Lockfile 状态失败：路径=%s，原因=%s", lockfile, exc)
            continue
        else:
            signature = (
                stat_result.st_size,
                stat_result.st_mtime_ns,
                stat_result.st_ctime_ns,
                getattr(stat_result, "st_ino", 0),
            )
            is_file = lockfile.is_file()

        with _lcu_lockfile_cache_lock:
            cached = _lcu_lockfile_cache.get(cache_key, _LCU_LOCKFILE_CACHE_MISS)

        if cached is not _LCU_LOCKFILE_CACHE_MISS and cached[0] == signature:
            # 缓存的 None 表示该文件状态下无法取得凭据；不再重复读取。
            if cached[1] is not None:
                return cached[1]
            continue

        if not is_file:
            log.debug("[LCU调试] Lockfile 不存在或不是普通文件：%s", lockfile)
            with _lcu_lockfile_cache_lock:
                _lcu_lockfile_cache[cache_key] = (signature, None)
            continue

        if stat_result.st_size == 0:
            log.debug("[LCU调试] Lockfile 文件为空，已缓存文件状态：%s", lockfile)
            with _lcu_lockfile_cache_lock:
                _lcu_lockfile_cache[cache_key] = (signature, None)
            continue

        log.debug("[LCU调试] Lockfile 文件存在，读取内容：%s", lockfile)
        try:
            file_content = lockfile.read_text(
                encoding="utf-8", errors="replace"
            ).strip("\x00\r\n ")
        except (OSError, UnicodeError) as exc:
            log.debug("[LCU调试] Lockfile 读取失败：路径=%s，原因=%s", lockfile, exc)
            continue

        credentials = None
        parts = file_content.split(":")
        if len(parts) < 5 or parts[0].lower() != "leagueclient":
            log.debug("[LCU调试] Lockfile 解析失败：格式不符合预期，路径=%s", lockfile)
        else:
            pid, port, token, protocol = parts[1:5]
            if not pid.isdigit() or not port.isdigit() or not token:
                log.debug("[LCU调试] Lockfile 解析失败：PID、端口或 Token 字段无效，路径=%s", lockfile)
            else:
                port_number = int(port)
                if not 1 <= port_number <= 65535 or protocol.lower() not in ("http", "https"):
                    log.debug("[LCU调试] Lockfile 解析失败：端口或协议无效，路径=%s", lockfile)
                else:
                    credentials = (port_number, token)
                    log.debug("[LCU调试] Lockfile 解析成功，端口=%s，路径=%s", port_number, lockfile)

        with _lcu_lockfile_cache_lock:
            _lcu_lockfile_cache[cache_key] = (signature, credentials)

        if credentials is not None:
            return credentials

    return None


def _scan_lcu_process_wmi(client_path):
    """通过原生 WMI 查询客户端进程，返回凭据、进程存在标记和查询成功标记。"""
    if os.name != "nt":
        return None, False, False

    pythoncom = None
    locator = None
    services = None
    processes = None
    item = None
    initialized = False
    process_found = False
    query_succeeded = False
    credentials_found = None
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        initialized = True

        locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        services = locator.ConnectServer(".", r"root\cimv2")
        services.Security_.ImpersonationLevel = 3
        processes = services.ExecQuery(
            "SELECT ExecutablePath, CommandLine FROM Win32_Process "
            "WHERE Name='LeagueClientUx.exe'",
            "WQL",
            0x30,
        )
        query_succeeded = True

        for item in processes:
            exe_path = str(item.ExecutablePath or "").strip()
            command = str(item.CommandLine or "")
            if not exe_path:
                continue

            actual = os.path.normcase(os.path.abspath(os.path.expandvars(exe_path)))
            try:
                if os.path.commonpath([client_path, actual]) != client_path:
                    continue
            except (ValueError, OSError):
                continue

            process_found = True
            if not command:
                continue

            credentials = _extract_credentials(command)
            if credentials:
                credentials_found = credentials
                log.debug("[LCU调试] 通过原生 WMI 从 LeagueClientUx.exe 读取到候选凭据")
                break
    except Exception as exc:
        log.debug("[LCU调试] 原生 WMI 查询客户端进程失败：%s", exc)
    finally:
        item = None
        processes = None
        services = None
        locator = None
        if initialized:
            pythoncom.CoUninitialize()

    if query_succeeded and not process_found:
        log.debug("[LCU调试] WMI 查询成功：配置安装目录下未发现 LeagueClientUx.exe")
    elif query_succeeded and process_found and not credentials_found:
        log.debug("[LCU调试] WMI 已发现 LeagueClientUx.exe，但未能从启动参数提取凭据")

    return credentials_found, process_found, query_succeeded


def _scan_lcu_credentials():
    """按 Lockfile、原生 WMI、客户端日志顺序查找并验证 LCU 凭据。"""
    if os.name != "nt":
        return None, "", None

    config = read_config()
    configured_path = str(config.get("lol_client_path", "")).strip().strip('"')
    if not configured_path:
        log.debug("[LCU调试] 未配置 lol_client_path")
        return None, "", None

    client_path = os.path.normcase(
        os.path.abspath(os.path.expandvars(os.path.expanduser(configured_path)))
    )
    client_dirs = _client_directories(client_path)

    def validate_candidate(credentials, source):
        if not credentials:
            return None
        valid, account_data = _validate_lcu_credentials(*credentials)
        if valid:
            log.debug("[LCU调试] %s 候选凭据 HTTP 验证成功，端口=%s", source, credentials[0])
            return credentials, source, account_data
        log.debug(
            "[LCU调试] %s 候选凭据 HTTP 验证失败，端口=%s；继续尝试下一来源",
            source, credentials[0],
        )
        return None

    result = validate_candidate(_read_lcu_lockfile(client_dirs), "Lockfile")
    if result:
        return result

    process_credentials, process_found, wmi_query_succeeded = _scan_lcu_process_wmi(client_path)
    result = validate_candidate(process_credentials, "WMI")
    if result:
        return result

    if wmi_query_succeeded and not process_found:
        log.debug("[LCU调试] 当前未运行英雄联盟客户端，跳过历史客户端日志扫描")
        return None, "", None

    log_files = []
    for directory in client_dirs:
        try:
            log_files.extend(directory.glob("*_LeagueClientUx.log"))
        except OSError as exc:
            log.debug("[LCU调试] 枚举客户端日志失败 %s：%s", directory, exc)

    try:
        log_files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError as exc:
        log.debug("[LCU调试] 排序客户端日志失败：%s", exc)

    max_log_candidates = 3
    if len(log_files) > max_log_candidates:
        log.debug(
            "[LCU调试] 客户端日志共找到 %s 个，仅检查最新 %s 个",
            len(log_files), max_log_candidates,
        )

    for log_file in log_files[:max_log_candidates]:
        try:
            with log_file.open("r", encoding="utf-8", errors="replace") as stream:
                content = stream.read(64 * 1024)
            credentials = _extract_credentials(content)
            if credentials:
                log.debug(
                    "[LCU调试] 从客户端日志读取到候选凭据：%s，端口=%s",
                    log_file.name, credentials[0],
                )
                result = validate_candidate(credentials, "客户端日志")
                if result:
                    return result
            else:
                log.debug("[LCU调试] 客户端日志未解析出凭据：%s", log_file.name)
        except OSError as exc:
            log.debug("[LCU调试] 读取客户端日志失败 %s：%s", log_file, exc)

    log.debug("[LCU调试] 当前可用凭据均未通过验证，或日志回退未找到有效凭据")
    return None, "", None


def get_lcu_credentials():
    """只返回识别线程已验证并缓存的凭据，不在调用处扫描进程或日志。"""
    with _lcu_lock:
        return _lcu_credentials


def client_status():
    with _lcu_lock:
        return dict(_lcu_status)


def lcu_is_available():
    with _lcu_lock:
        return bool(_lcu_status.get("recognized") and _lcu_status.get("available") and _lcu_credentials)


def lcu_recognition_worker():
    """优先验证缓存凭据；凭据失效或尚未识别时才扫描客户端进程/日志。"""
    global _lcu_credentials
    log.info("客户端识别服务已启动")
    last_logged_status = None
    while not stop_event.is_set():
        # 客户端运行期间端口和 Token 通常保持不变。
        # 先验证缓存值，只有缓存失效时才重新读取 Lockfile、查询 WMI 或检查日志。
        with _lcu_lock:
            cached_credentials = _lcu_credentials
            cached_source = _lcu_status.get("source") or "cache"

        credentials = cached_credentials
        source = cached_source
        valid, account_data = (False, None)

        if credentials:
            valid, account_data = _validate_lcu_credentials(*credentials)

        # 首次识别，或缓存凭据已失效时，才按 Lockfile、WMI、日志顺序重新发现凭据。
        if not valid:
            credentials, source, account_data = _scan_lcu_credentials()
            valid = credentials is not None

        with _lcu_lock:
            if valid:
                _lcu_credentials = credentials
                data = account_data or {}
                game_name = str(data.get("gameName") or data.get("displayName") or "")
                tag_line = str(data.get("tagLine") or "")
                account = game_name + (f"#{tag_line}" if tag_line else "")
                _lcu_status.update({
                    "recognized": True, "available": True, "source": source,
                    "message": "客户端已识别，LCU 连接可用",
                    "checked_at": time.time(), "account": account,
                })
            else:
                _lcu_credentials = None
                account = ""
                _lcu_status.update({
                    "recognized": False, "available": False, "source": "",
                    "message": "未识别到可用的英雄联盟客户端凭据",
                    "checked_at": time.time(), "account": "",
                })

        current_status = (bool(valid), source if valid else "", account)
        if current_status != last_logged_status:
            if valid:
                log.info(
                    "客户端已识别，LCU 连接可用：账号=%s，来源=%s",
                    account or "未知账号",
                    source,
                )
            elif last_logged_status is None:
                log.info("未检测到可用的英雄联盟客户端，等待客户端启动")
            elif last_logged_status[0]:
                log.warning("LCU 连接已断开，正在重新识别客户端")
            last_logged_status = current_status

        interval = (
            _lcu_scan_interval_recognized
            if valid else _lcu_scan_interval_unrecognized
        )

        if stop_event.wait(interval):
            break
    log.info("客户端识别服务已停止")



_gameflow_last_error = None


def get_gameflow_phase():
    """读取 LCU 当前对局阶段；记录失败原因，避免断连时误判对局结束。"""
    global _gameflow_last_error
    credentials = get_lcu_credentials()
    if not credentials:
        return None

    port, token = credentials
    url = f'https://127.0.0.1:{port}/lol-gameflow/v1/gameflow-phase'

    try:
        response = requests.get(
            url,
            auth=('riot', token),
            verify=False,
            timeout=3,
            headers={'Accept': 'application/json'}
        )

        # 正常轮询每10/30秒都会经过这里；完整响应仅保留在 DEBUG，
        # 避免持续写入重复的成功诊断日志。
        response.raise_for_status()
        phase = response.json()

        if isinstance(phase, str):
            phase = phase.strip()
            if phase:
                if _gameflow_last_error is not None:
                    log.info("LCU 对局阶段接口已恢复")
                    _gameflow_last_error = None
                # LCU 返回字符串 "None" 表示当前没有对局流程，是有效状态。
                return 'None' if phase.lower() == 'none' else phase

        error = f"无效阶段响应：{type(phase).__name__}"
        if error != _gameflow_last_error:
            log.warning("LCU 对局阶段读取异常：%s", error)
        _gameflow_last_error = error
        return None

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if error != _gameflow_last_error:
            log.warning("LCU 对局阶段读取失败：%s", error)
        _gameflow_last_error = error
        return None


SCREENSHOT_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
cleanup_lock = threading.Lock()
processed_files_lock = threading.Lock()
cleanup_generation = 0

def cleanup_match_data(reason):
    """清空卡片与截图目录图片；返回是否所有清理操作均成功。"""
    global cleanup_generation
    with cleanup_lock:
        with state_lock:
            cleanup_generation += 1
            state['cards'] = []
            state['updated_at'] = time.time()
            atomic_json(STATE_PATH, state)

        deleted = 0
        failures = 0
        folders = (screenshot_dir(), PROCESSED)
        seen = set()

        for folder in folders:
            try:
                resolved = folder.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)

                for candidate in folder.rglob('*'):
                    if (
                        candidate.is_file()
                        and candidate.suffix.lower() in SCREENSHOT_EXTENSIONS
                    ):
                        try:
                            candidate.unlink()
                            deleted += 1
                        except OSError as exc:
                            failures += 1
                            log.warning(
                                '清理截图失败 %s：%s',
                                candidate,
                                exc
                            )
            except OSError as exc:
                failures += 1
                log.warning(
                    '扫描清理目录失败 %s：%s',
                    folder,
                    exc
                )

        with processed_files_lock:
            processed_files.clear()
        log.info(
            '[对局清理] %s；已删除图片 %d 张，失败 %d 项',
            reason,
            deleted,
            failures
        )
        return failures == 0


def gameflow_worker():
    """状态1每10秒检查并清理一次，清理成功后本轮不再重复；状态2每30秒仅检查阶段。"""

    state1_phases = {'Matchmaking', 'ReadyCheck', 'ChampSelect'}
    gaming_phases = {
        'GameStart',
        'InProgress',
        'WaitingForStats',
        'PreEndOfGame',
        'EndOfGame',
        'Reconnect',
    }
    last_phase = None
    last_monitor_state = None
    was_gaming = False
    cleanup_completed = False

    while not stop_event.is_set():
        try:
            phase = get_gameflow_phase()

            if phase is None:
                # 读取失败不改变阶段、监控状态或清理标记。
                pass
            else:
                if phase != last_phase:
                    log.info(
                        '[对局阶段] %s -> %s',
                        last_phase or '未知/未连接',
                        phase
                    )

                is_gaming = phase in gaming_phases
                if was_gaming and not is_gaming:
                    log.info(
                        '[对局阶段] 检测到离开对局：%s -> %s；通知战绩条立即刷新',
                        last_phase or '未知阶段',
                        phase
                    )
                    try:
                        notify_bar_game_ended()
                    except Exception:
                        log.exception('[战绩条] 发送对局结束通知失败')

                was_gaming = is_gaming
                monitor_state = '状态1' if phase in state1_phases else '状态2'

                if monitor_state != last_monitor_state:
                    # 每次重新进入状态1，都开启新一轮清理；
                    # 离开状态1进入状态2时，也重置标记。
                    cleanup_completed = False

                # 先记录本次有效阶段。即使清理过程抛出异常，
                # 也能保留状态1并在10秒后重试，而不是退回30秒。
                last_phase = phase
                last_monitor_state = monitor_state

                if monitor_state == '状态1' and not cleanup_completed:
                    completed = cleanup_match_data(
                        '检测到进入状态1，清理上一局或启动前残留'
                    )
                    if completed:
                        cleanup_completed = True
                    else:
                        log.warning(
                            '[选英雄清理] 本次清理未完全成功；将在下一次状态1检测时重试'
                        )

        except Exception:
            log.exception('[对局阶段] 检测过程中发生异常')

        # LCU 读取失败时沿用上一次成功识别的监控状态；
        # 程序启动后尚无有效状态时，按状态2的30秒周期重试。
        interval = 10 if last_monitor_state == '状态1' else 30
        stop_event.wait(interval)


def asset_worker():
    # 配置没有运行时编辑入口，启动时读取一次；修改 JSON 后重启生效。
    cfg = read_config()
    interval = max(60, int(cfg.get('asset_check_interval', '3600') or 3600))
    version_interval = max(10, int(cfg.get('version_check_interval', '3600') or 3600))
    last = 0
    last_version_check = 0
    version = ''
    retry_delay = 60

    while not stop_event.is_set():
        now = time.time()

        if now - last_version_check >= version_interval:
            last_version_check = now
            current = find_game_version()

            if current and version and current != version:
                log.info(
                    '检测到游戏版本变化：%s -> %s，立即刷新索引',
                    version, current
                )
                update_assets(True)
                last = now
                retry_delay = 60

            if current:
                version = current

        if now - last >= interval:
            if update_assets():
                last = now
                retry_delay = 60
            else:
                # 失败后按递增退避时间重试，不再额外等待完整资源检查周期。
                if stop_event.wait(retry_delay):
                    break
                retry_delay = min(retry_delay * 5, 1800)
                last = time.time() - interval

        # 配置仅在启动时读取；直接等待下一项任务到期，不做配置轮询。
        next_version_check = last_version_check + version_interval
        next_asset_check = last + interval
        wait_seconds = max(
            0,
            min(next_version_check, next_asset_check) - time.time()
        )
        if stop_event.wait(wait_seconds):
            break

# OCR model is initialized lazily, so the UI can start even if OCR dependency/model is unavailable.
_ocr=None; _ocr_lock=threading.Lock()
def ocr_text(image_path):
    global _ocr
    try:
        with _ocr_lock:
            if _ocr is None:
                from rapidocr_onnxruntime import RapidOCR

                cfg = read_config()
                cpu_count = os.cpu_count() or 1

                try:
                    intra_threads = min(
                        cpu_count,
                        max(1, int(cfg.get('ocr_intra_op_num_threads', 2)))
                    )
                except (TypeError, ValueError):
                    intra_threads = min(2, cpu_count)

                try:
                    inter_threads = min(
                        cpu_count,
                        max(1, int(cfg.get('ocr_inter_op_num_threads', 1)))
                    )
                except (TypeError, ValueError):
                    inter_threads = 1

                _ocr = RapidOCR(
                    intra_op_num_threads=intra_threads,
                    inter_op_num_threads=inter_threads,
                )
                log.info(
                    '[OCR] ONNX Runtime 线程配置：intra_op=%s，inter_op=%s，CPU逻辑处理器=%s',
                    intra_threads,
                    inter_threads,
                    cpu_count,
                )
        result,_=_ocr(str(image_path))
        if not result: return '', '未识别到文字'
        texts=[]
        for row in result:
            try:
                text=str(row[1]).strip()
                if text: texts.append(text)
            except Exception: pass
        return ''.join(texts), ''
    except Exception as e: return '', f'OCR 初始化或识别失败：{type(e).__name__}: {e}'

def norm_name(s): return re.sub(r'[\s\W_]+','',str(s or '').lower(),flags=re.UNICODE)

def match_asset(text, is_mutated=False):
    if not text:
        return None, 'OCR 未识别到名称'

    text = str(text).strip()

    # 兼容 OCR 偶尔识别出质变前缀的情况
    text = re.sub(
        r'^已质变\s*[:：]\s*',
        '',
        text
    ).strip()

    if not text:
        return None, 'OCR 预处理后名称为空'

    # 资源索引只整体替换、不原地修改；保留当前字典引用即可，
    # 无需每次匹配都复制完整 values 列表。
    with assets_lock:
        items = assets

    # 优先按原始名称精确匹配，保留标点差异。
    exact_raw = [
        a for a in items.values()
        if str(a['name']).strip() == text
    ]

    if len(exact_raw) == 1:
        asset = dict(exact_raw[0])
        asset['display_name'] = (
            f"已质变：{asset['name']}"
            if is_mutated
            else asset['name']
        )
        return asset, ''

    # 原有规范化精确匹配及兼容匹配规则保持不变。
    norm = norm_name(text)
    # 完全匹配
    exact = [
        a for a in items.values()
        if norm_name(a['name']) == norm
    ]

    if len(exact) == 1:
        asset = dict(exact[0])
        asset['display_name'] = (
            f"已质变：{asset['name']}"
            if is_mutated
            else asset['name']
        )
        return asset, ''

    # 安全的包含匹配
    candidates = [
        a for a in items.values()
        if len(norm_name(a['name'])) >= 3
        and (
            norm_name(a['name']) in norm
            or norm in norm_name(a['name'])
        )
    ]

    uniq = {
        a['id']: a
        for a in candidates
    }

    if len(uniq) == 1:
        asset = dict(next(iter(uniq.values())))
        asset['display_name'] = (
            f"已质变：{asset['name']}"
            if is_mutated
            else asset['name']
        )
        return asset, ''

    if len(uniq) > 1:
        return None, '名称匹配存在歧义，未自动猜测'

    return None, 'OCR 文本未匹配到海克斯名称'

def save_state():
    with state_lock: state['updated_at']=time.time(); atomic_json(STATE_PATH,state)

def process_screenshot(path):
    # 若清理发生在 OCR 期间，丢弃旧任务结果，避免上一局卡片重新出现。
    with state_lock: generation_at_start = cleanup_generation
    # Wait until file size stabilizes to avoid reading a partial PNG.
    old=-1; stable=0
    for _ in range(30):
        try: size=path.stat().st_size
        except OSError: return
        if size>0 and size==old: stable+=1
        else: stable=0
        if stable>=2: break
        old=size; time.sleep(.15)

    text, err = ocr_text(path)
    item, match_err = match_asset(text)

    # OCR 保留原始名称，展示名称添加质变前缀
    display_name = f"已质变：{item['name']}" if item else ''

    card={'key':f'{path.name}-{time.time_ns()}','filename':path.name,'ocr_text':text,'name':item['name'] if item else '', 'augment_id':item['id'] if item else '', 'reason':err or match_err, 'level': item.get('level', '') if item else '', 'created_at':datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

    with state_lock:
        if generation_at_start != cleanup_generation or not path.exists():
            log.debug('截图处理结果已因对局清理而丢弃：%s', path.name)
            return

        # 防止程序重启后，将已有截图再次加入卡片列表。
        if any(
            str(existing.get('filename', '')) == path.name
            for existing in state['cards']
        ):
            log.debug('截图已存在于卡片列表，跳过重复添加：%s', path.name)
            return

        state['cards'].append(card)
        state['cards'] = state['cards'][-100:]
        state['updated_at'] = time.time()
        atomic_json(STATE_PATH, state)

    try:
        if path.exists(): shutil.copy2(path,PROCESSED/path.name)
    except Exception: pass
    log_method = log.error if err.startswith('OCR 初始化或识别失败：') else log.info
    log_method(
        '截图识别：%s | OCR=%s | 匹配=%s | 原因=%s',
        path.name,
        text,
        card['name'] or '未匹配',
        card['reason'],
    )

stop_event=threading.Event(); processed_files=OrderedDict(); PROCESSED_FILES_LIMIT=100; update_status_lock=threading.Lock(); update_status={'running':False,'message':'尚未手动检查','updated_at':0}; manual_update_lock=threading.Lock()
def _mark_processed_file(name):
    with processed_files_lock:
        processed_files[name] = None
        processed_files.move_to_end(name)
        while len(processed_files) > PROCESSED_FILES_LIMIT:
            processed_files.popitem(last=False)


def _is_processed_file(name):
    with processed_files_lock:
        return name in processed_files


def _forget_processed_file(name):
    with processed_files_lock:
        processed_files.pop(name, None)


def screenshot_watcher():
    """仅在截图目录发生变化时枚举文件；配置在启动时读取，修改后重启生效。"""
    cfg = read_config()
    folder = screenshot_dir(cfg)
    try:
        scan_interval = max(
            0.2,
            float(cfg.get('scan_interval', '1') or 1)
        )
    except (TypeError, ValueError) as exc:
        log.warning('[截图监控] 配置的扫描间隔无效，使用默认值1秒：%s', exc)
        scan_interval = 1.0
    directory_signature = None

    while not stop_event.is_set():
        try:
            stat_result = folder.stat()
            _clear_screenshot_issue("watcher_stat", "截图目录状态读取已恢复")
            current_signature = (
                stat_result.st_mtime_ns,
                stat_result.st_ctime_ns,
            )

            if current_signature != directory_signature:
                # 在枚举前记录指纹。若枚举期间又有新截图写入，
                # 目录时间会再次变化，下一轮仍会触发扫描。
                directory_signature = current_signature
                files = [
                    p for p in folder.glob('GameShot_*.png')
                    if not _is_processed_file(p.name)
                ]
                files.sort(key=lambda p: p.stat().st_mtime)
                for p in files:
                    # If using processed folder itself, avoid feedback. Files are marked in-memory after handling.
                    process_screenshot(p)
                    _mark_processed_file(p.name)
                _clear_screenshot_issue("watcher_scan", "截图目录扫描已恢复")
        except OSError as exc:
            # 无法读取目录元数据时退回原有扫描方式，避免漏掉截图。
            directory_signature = None
            _log_screenshot_issue(
                "watcher_stat",
                "[截图监控] 读取目录状态失败，执行完整扫描",
                exc,
            )
            try:
                files = [
                    p for p in folder.glob('GameShot_*.png')
                    if not _is_processed_file(p.name)
                ]
                files.sort(key=lambda p: p.stat().st_mtime)
                for p in files:
                    process_screenshot(p)
                    _mark_processed_file(p.name)
                _clear_screenshot_issue("watcher_scan", "截图目录扫描已恢复")
            except Exception as scan_exc:
                _log_screenshot_issue("watcher_scan", "扫描截图目录异常", scan_exc)
        except Exception as exc:
            directory_signature = None
            _log_screenshot_issue("watcher_scan", "扫描截图目录异常", exc)

        stop_event.wait(scan_interval)

BASE_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, 'frozen', False)
    else os.path.dirname(os.path.abspath(__file__))
)

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)

# 由 Hex 自身记录 PID，并持有 Werkzeug 服务对象以便执行有序关闭。
PID_FILE = Path(BASE_DIR) / 'lol_hex_display.pid'
HTTP_SERVER = None
_shutdown_lock = threading.Lock()
_shutdown_started = False


def _current_process_creation_time():
    """读取 Windows 内核记录的进程创建时间，用于识别 PID 是否被复用。"""
    if os.name != 'nt':
        return None

    class FILETIME(ctypes.Structure):
        _fields_ = [('dwLowDateTime', ctypes.c_ulong), ('dwHighDateTime', ctypes.c_ulong)]

    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
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
            kernel32.GetCurrentProcess(), ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel_time), ctypes.byref(user_time),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    except Exception:
        log.exception('读取 Hex 进程创建时间失败，PID 文件将无法用于跨进程身份核验')
        return None


def write_pid_file():
    """记录 PID 和内核进程创建时间，防止 PID 复用导致误认其他进程。"""
    record = {
        'pid': os.getpid(),
        'creation_time': _current_process_creation_time(),
    }
    temp_path = PID_FILE.with_suffix('.pid.tmp')
    temp_path.write_text(json.dumps(record, separators=(',', ':')), encoding='utf-8')
    os.replace(temp_path, PID_FILE)
    log.debug('Hex 进程 PID 和身份信息已记录：%s', os.getpid())


def remove_pid_file():
    """只删除 PID 和进程创建时间均属于当前进程的记录，避免误删新实例记录。"""
    try:
        if not PID_FILE.is_file():
            return
        payload = json.loads(PID_FILE.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('pid') != os.getpid():
            return
        current_creation_time = _current_process_creation_time()
        if os.name == 'nt':
            if current_creation_time is None or payload.get('creation_time') != current_creation_time:
                return
        elif payload.get('creation_time') is not None:
            return
        PID_FILE.unlink()
        log.debug('Hex PID 文件已清理')
    except (OSError, ValueError, TypeError):
        log.exception('清理 Hex PID 文件失败')


def request_server_shutdown():
    """在独立线程调用 server.shutdown，避免 HTTP 请求线程自身死锁。"""
    global _shutdown_started
    with _shutdown_lock:
        if _shutdown_started:
            return
        _shutdown_started = True
    server = HTTP_SERVER
    if server is not None:
        threading.Thread(target=server.shutdown, daemon=True, name='HexHttpShutdown').start()

atexit.register(remove_pid_file)
notify_bar_game_ended = register_bar_routes(
    app, get_lcu_credentials, BASE_DIR,
    client_is_recognized=lambda: client_status()["recognized"],
    lcu_is_available=lcu_is_available,
)
# 关闭 Flask/Werkzeug 的 HTTP 请求访问日志
logging.getLogger('werkzeug').setLevel(logging.WARNING)

@app.get('/')
def manager(): return render_template('manager.html')
@app.get('/display')
def display(): return render_template('display.html')
@app.get('/api/client-status')
def api_client_status():
    return jsonify(client_status())


@app.get('/api/runtime-status')
def api_runtime_status():
    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == 'nt' else True
    except Exception:
        is_admin = False
    return jsonify({
        'is_admin': is_admin,
        'f1_registered': F1_HOTKEY_REGISTERED,
        'f1_error': F1_HOTKEY_ERROR,
    })


@app.get('/api/state')
def api_state():
    with state_lock:
        cards = [dict(card) for card in state['cards']]
        updated_at = state.get('updated_at', 0)

    with assets_lock:
        count = len(assets)

    return jsonify({
        'cards': cards,
        'updated_at': updated_at,
        'asset_count': count,
        'screenshot_dir': str(screenshot_dir())
    })
@app.post('/api/action')
def api_action():
    data=request.get_json(silent=True) or {}; action=data.get('action'); key=str(data.get('key',''))
    removed=[]
    with state_lock:
        cards=state['cards']
        if action == 'shutdown':
            log.info('收到主程序关闭请求，Hex 管理员进程开始执行有序退出')
            request_server_shutdown()
            return jsonify({'ok': True, 'message': 'Hex 正在退出'})
        if action=='clear':
            # cleanup_match_data() 统一清空状态并持久化，避免重复写入。
            pass
        elif action=='delete':
            target=next((c for c in cards if str(c.get('key'))==key),None)
            if target is None: return jsonify({'ok':False,'error':'找不到要删除的卡片记录'}),404
            state['cards']=[c for c in cards if str(c.get('key'))!=key]
            filename=Path(str(target.get('filename',''))).name
            if filename and filename==str(target.get('filename','')):
                removed.append(screenshot_dir()/filename)
        elif action in ('up','down'):
            idx=next((i for i,c in enumerate(cards) if str(c.get('key'))==key),-1)
            j=idx+(-1 if action=='up' else 1)
            if idx>=0 and 0<=j<len(cards): cards[idx],cards[j]=cards[j],cards[idx]
        else: return jsonify({'ok':False,'error':'未知操作'}),400
        if action != 'clear':
            state['updated_at']=time.time(); atomic_json(STATE_PATH,state)
    deleted=[]; failures=[]
    if action=='clear':
        cleanup_match_data('用户在管理页手动清空')
    else:
        for candidate in removed:
            try:
                # Only unlink a direct child of the configured screenshot directory.
                if candidate.parent.resolve()==screenshot_dir().resolve() and candidate.is_file():
                    candidate.unlink(); deleted.append(str(candidate))
            except OSError as e: failures.append(f'{candidate}: {e}')
            # Keep the legacy processed copy in sync too, if present.
            try:
                archived=PROCESSED/candidate.name
                if archived.is_file(): archived.unlink()
            except OSError as e: failures.append(f'{PROCESSED/candidate.name}: {e}')
        if removed: _forget_processed_file(removed[0].name)
    return jsonify({'ok':True,'deleted_count':len(deleted),'delete_failures':failures})

@app.post('/api/update-assets')
def api_update_assets():
    # 如果自动检查或其他手动检查正在运行，不再排队启动重复任务。
    if not _asset_update_mutex.acquire(blocking=False):
        return jsonify({'ok':True,'running':True,'message':'自动或手动更新任务正在执行，请稍候'}),202
    with update_status_lock:
        if update_status['running']:
            _asset_update_mutex.release()
            return jsonify({'ok':True,'running':True,'message':'更新任务已在执行，请稍候'}),202
        update_status.update({'running':True,'message':'正在请求并检查腾讯海克斯数据接口…','updated_at':time.time()})
    def worker():
        try:
            ok=_update_assets_impl(False)
            message='检查完成；请查看日志了解数据是否变化及图标下载情况。' if ok else '请求或数据校验失败，已保留上一份有效缓存。'
        except Exception as e:
            log.exception('手动更新海克斯数据失败')
            ok=False; message=f'更新失败：{type(e).__name__}: {e}'
        finally:
            _asset_update_mutex.release()
        with update_status_lock:
            update_status.update({'running':False,'message':message,'updated_at':time.time(),'ok':ok})
    threading.Thread(target=worker,daemon=True,name='ManualAssetUpdater').start()
    return jsonify({'ok':True,'running':True,'message':'已开始检查海克斯数据接口'})

@app.get('/api/update-status')
def api_update_status():
    with update_status_lock: result=dict(update_status)
    return jsonify(result)

@app.get('/asset/hex/<aid>.png')
def hex_asset(aid):
    p=ICON_DIR/f'{aid}.png'
    if p.is_file(): return send_from_directory(ICON_DIR,p.name)
    return ('',404)
@app.get('/asset/screenshot/<path:filename>')
def screenshot_asset(filename):
    p=screenshot_dir()/Path(filename).name
    if p.is_file(): return send_from_directory(p.parent,p.name)
    p=PROCESSED/Path(filename).name
    if p.is_file(): return send_from_directory(p.parent,p.name)
    return ('',404)



# Global hotkey + frozen-frame selection UI (Windows).
# Only F1 is registered as a global hotkey.

class ScreenshotSelector:
    def __init__(self):
        self.active = False
        self.root = None
        self.image = None
        self.tkimg = None
        self.canvas = None
        self.start = None
        self.rect = None
        self.size_text = None

        self._lock = threading.RLock()
        self.cancel_requested = threading.Event()

        # 当前截图线程
        self.thread = None

        # 截图任务代次，防止旧任务影响新任务
        self._generation = 0

    def toggle(self):
        """F1：未截图则开始；截图中则取消。"""

        with self._lock:
            # 只有确认旧线程已经退出，才允许释放线程引用并启动新任务。
            if self.thread is not None and not self.thread.is_alive():
                if self.active:
                    log.debug(
                        '[截图调试] 检测到截图状态残留，自动重置'
                    )
                self.active = False
                self.root = None
                self.tkimg = None
                self.canvas = None
                self.rect = None
                self.size_text = None
                self.start = None
                self.image = None
                self.thread = None
                self.cancel_requested.clear()

            if self.thread is not None and self.thread.is_alive():
                if self.active:
                    self.cancel_requested.set()
                    log.debug("F1 已触发：取消截图任务")
                else:
                    log.debug("截图任务正在完成收尾，暂不启动新任务")
                return

            # 启动新截图任务
            self.active = True
            self.cancel_requested.clear()
            self._generation += 1
            generation = self._generation

            thread = threading.Thread(
                target=self.begin,
                args=(generation,),
                daemon=True,
                name='ScreenshotSelector'
            )

            self.thread = thread

            try:
                thread.start()
                log.debug("F1 已触发：启动截图任务，任务编号=%s", generation)
            except Exception:
                self.active = False
                self.thread = None
                self.cancel_requested.clear()
                log.exception('截图线程启动失败')

    def capture(self):
        """捕获整个虚拟桌面。"""
        import mss

        with mss.mss() as sct:
            mon = sct.monitors[0]
            raw = sct.grab(mon)
            img = Image.frombytes(
                'RGB',
                raw.size,
                raw.rgb
            )
            return img, mon

    def begin(self, generation):
        """在独立线程创建截图窗口。"""

        root = None

        try:
            if os.name != 'nt':
                log.warning('全局截图热键只支持 Windows')
                return

            log.debug(
                '[截图调试] 截图线程开始运行，任务编号=%s',
                generation
            )

            # 捕获当前屏幕画面
            try:
                ctypes.windll.dwmapi.DwmFlush()
            except Exception:
                pass

            time.sleep(0.03)

            log.debug('[截图调试] 开始捕获屏幕')

            img, mon = self.capture()

            log.debug(
                '[截图调试] 屏幕捕获完成：%dx%d',
                img.width,
                img.height
            )

            # 捕获过程中如果收到取消请求，则不再创建窗口。
            if self.cancel_requested.is_set():
                log.debug('[截图调试] 屏幕捕获后收到取消请求')
                return

            with self._lock:
                if generation != self._generation or not self.active:
                    log.debug('[截图调试] 截图任务已失效，不再创建窗口')
                    return

                self.image = img

            import tkinter as tk

            root = tk.Tk()

            root.overrideredirect(True)
            root.attributes('-topmost', True)
            root.configure(bg='black')

            root.geometry(
                f"{img.width}x{img.height}"
                f"+{mon['left']}+{mon['top']}"
            )

            tkimg = ImageTk.PhotoImage(img)

            canvas = tk.Canvas(
                root,
                width=img.width,
                height=img.height,
                highlightthickness=0,
                cursor='crosshair'
            )

            canvas.pack(fill='both', expand=True)

            canvas.create_image(
                0,
                0,
                anchor='nw',
                image=tkimg
            )

            with self._lock:
                if generation != self._generation or not self.active:
                    try:
                        root.destroy()
                    except Exception:
                        pass
                    return

                self.root = root
                self.tkimg = tkimg
                self.canvas = canvas

            canvas.bind(
                '<ButtonPress-1>',
                self.on_down
            )

            canvas.bind(
                '<B1-Motion>',
                self.on_move
            )

            canvas.bind(
                '<ButtonRelease-1>',
                lambda e: self.on_up(e, generation)
            )

            log.debug(
                '[截图调试] 截图框已创建，等待用户选择区域'
            )

            # 不绑定 ESC。
            # 再次按 F1 请求取消。
            root.after(
                30,
                lambda: self.poll_cancel(generation)
            )

            root.mainloop()

        except Exception as e:
            log.exception("截图任务异常退出：%s", e)

        finally:
            # 无论正常结束还是异常退出，都必须复位状态。
            self.finish(generation)

            log.debug(
                '[截图调试] 截图任务结束，任务编号=%s',
                generation
            )

    def poll_cancel(self, generation):
        """由 Tk 主循环定期检查取消请求。"""

        with self._lock:
            if generation != self._generation:
                return

            if not self.active:
                return

            root = self.root

        if root is None:
            return

        if self.cancel_requested.is_set():
            log.debug('[截图调试] 检测到取消请求，关闭截图框')
            self.finish(generation)
            return

        try:
            root.after(
                30,
                lambda: self.poll_cancel(generation)
            )
        except Exception:
            log.debug('[截图调试] 截图窗口已关闭')

    def on_down(self, e):
        if self.canvas is None:
            return

        self.start = (e.x, e.y)

        if self.rect is not None:
            self.canvas.delete(self.rect)

        if self.size_text is not None:
            self.canvas.delete(self.size_text)

        self.rect = self.canvas.create_rectangle(
            e.x,
            e.y,
            e.x,
            e.y,
            outline='#FF3030',
            width=3
        )

        self.size_text = self.canvas.create_text(
            e.x + 6,
            e.y + 6,
            anchor='nw',
            fill='white',
            font=('Microsoft YaHei', 12),
            text='0 × 0'
        )

    def on_move(self, e):
        if self.start is None or self.canvas is None:
            return

        x1, y1 = self.start
        x2, y2 = e.x, e.y

        self.canvas.coords(
            self.rect,
            x1,
            y1,
            x2,
            y2
        )

        self.canvas.coords(
            self.size_text,
            min(x1, x2) + 6,
            min(y1, y2) + 6
        )

        self.canvas.itemconfigure(
            self.size_text,
            text=f'{abs(x2 - x1)} × {abs(y2 - y1)}'
        )

    def on_up(self, e, generation=None):
        if self.start is None or self.image is None:
            return

        if generation is None:
            generation = self._generation

        x1, y1 = self.start
        x2, y2 = e.x, e.y

        box = (
            min(x1, x2),
            min(y1, y2),
            max(x1, x2),
            max(y1, y2)
        )

        # 保存截图所需图像引用
        img = self.image

        # 先关闭截图框
        self.finish(generation)

        if (
            box[2] - box[0] < 5
            or box[3] - box[1] < 5
        ):

            return

        try:
            crop = img.crop(box)

            folder = screenshot_dir()

            stamp = (
                datetime.now().strftime('%Y%m%d_%H%M%S_')
                + f'{int(datetime.now().microsecond / 1000):03d}'
            )

            path = folder / f'GameShot_{stamp}.png'

            crop.save(path, 'PNG')


        except Exception as ex:
            log.exception('保存截图失败：%s', ex)

    def finish(self, generation=None):
        """关闭当前选择窗口并恢复 F1 的下一次启动能力。"""

        with self._lock:
            # 防止旧截图任务关闭新截图任务的窗口。
            if generation is not None and generation != self._generation:
                return

            root = self.root

            self.root = None
            self.tkimg = None
            self.canvas = None
            self.rect = None
            self.size_text = None
            self.start = None
            self.image = None

            self.active = False
            # 保留线程引用，直到下一次 F1 确认该线程已真正退出。
            self.cancel_requested.clear()

        if root is not None:
            try:
                root.quit()
            except Exception:
                pass

            try:
                root.destroy()
            except Exception:
                pass

    def cancel(self):
        """保留接口，但不绑定全局 ESC。"""

        with self._lock:
            if not self.active:
                return

            self.cancel_requested.set()




selector = ScreenshotSelector()
F1_HOTKEY_REGISTERED = False
F1_HOTKEY_ERROR = ''
F1_HOTKEY_HANDLE = None


def start_hotkey():
    """仅在管理员进程中注册 F1；注册失败时明确报告，不静默降级。"""
    global F1_HOTKEY_REGISTERED, F1_HOTKEY_ERROR, F1_HOTKEY_HANDLE

    F1_HOTKEY_REGISTERED = False
    F1_HOTKEY_ERROR = ''
    F1_HOTKEY_HANDLE = None

    if os.name != 'nt':
        return True

    try:
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise PermissionError('LOL Hex 当前不是 Windows 管理员进程，不能启用游戏内 F1 截图')

        import keyboard

        F1_HOTKEY_HANDLE = keyboard.add_hotkey(
            'F1',
            selector.toggle,
            suppress=True,
            trigger_on_release=False
        )
        if F1_HOTKEY_HANDLE is None:
            raise RuntimeError('keyboard 未返回有效的 F1 热键注册句柄')

        F1_HOTKEY_REGISTERED = True
        log.info('全局热键已启用：F1 开始/取消截图')
        return True

    except Exception as e:
        F1_HOTKEY_ERROR = f'{type(e).__name__}: {e}'
        log.exception('F1 全局热键注册失败：%s', e)
        return False


def main():
        # Windows 控制台：关闭 QuickEdit，避免误操作导致日志输出暂停
    if os.name == 'nt':
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)

            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                new_mode = mode.value & ~0x0040  # 关闭 QuickEdit
                new_mode |= 0x0080  # 启用扩展控制台标志
                if not kernel32.SetConsoleMode(handle, new_mode):
                    error_code = kernel32.GetLastError()
                    log.debug('设置控制台模式失败，Windows 错误码：%s', error_code)
        except Exception as e:
            log.debug('设置控制台模式失败：%s', e)
    log.info('lol_hex_display 启动；截图目录：%s', screenshot_dir())
    threading.Thread(target=screenshot_watcher,daemon=True,name='ScreenshotWatcher').start()
    threading.Thread(target=asset_worker,daemon=True,name='AssetUpdater').start()
    threading.Thread(target=lcu_recognition_worker, daemon=True, name='LcuRecognition').start()
    threading.Thread(target=gameflow_worker,daemon=True,name='GameflowWatcher').start()
    if os.name == 'nt' and not start_hotkey():
        log.critical('F1 截图热键未能注册，LOL Hex 不会以不完整状态启动：%s', F1_HOTKEY_ERROR)
        raise RuntimeError('F1 截图热键注册失败：' + F1_HOTKEY_ERROR)
    cfg=read_config(); host=cfg.get('host','127.0.0.1'); port=int(cfg.get('port','8767'))
    log.info('管理页：http://%s:%d/ | OBS 展示页：http://%s:%d/display',host,port,host,port)
    global HTTP_SERVER
    write_pid_file()
    try:
        from werkzeug.serving import make_server
        HTTP_SERVER = make_server(host, port, app, threaded=True)
        HTTP_SERVER.serve_forever()
    finally:
        stop_event.set()
        try:
            selector.cancel()
        except Exception:
            pass
        if os.name == 'nt' and F1_HOTKEY_HANDLE is not None:
            try:
                import keyboard
                keyboard.remove_hotkey(F1_HOTKEY_HANDLE)
                log.debug('F1 全局热键已注销')
            except Exception:
                log.exception('注销 F1 全局热键失败')
        try:
            if HTTP_SERVER is not None:
                HTTP_SERVER.server_close()
        except Exception:
            log.exception('关闭 Hex HTTP 服务失败')
        HTTP_SERVER = None
        remove_pid_file()
        log.info('Hex 管理员进程退出清理完成')
        try:
            log_listener.stop()
        except Exception:
            pass

if __name__ == "__main__":
    if check_admin_privileges():
        main()
    else:
        # 提权成功时由新的管理员进程接管；失败或用户拒绝时不继续运行。
        sys.exit(0)
