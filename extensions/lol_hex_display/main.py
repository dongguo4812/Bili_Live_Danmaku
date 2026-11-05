# -*- coding: utf-8 -*-
"""lol_hex_display：F1 自由区域截图 + 本地 OCR + 海克斯强化卡片展示。Python 3.10+"""
from __future__ import annotations
import ctypes, subprocess, faulthandler, hashlib, io, json, logging, os, re, shutil, sys, threading, time , atexit, queue
from logging.handlers import QueueHandler, QueueListener
from datetime import datetime
from pathlib import Path
from typing import Any
from bar_integration import register_bar_routes

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from PIL import Image, ImageTk, ImageDraw, ImageFont

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
DATA = ROOT / 'data'; ICON_DIR = DATA / 'hex_icons'; SHOTS = ROOT / 'screenshots'; PROCESSED = SHOTS / 'processed'; LOG_DIR = ROOT / 'logs'
for p in (DATA, ICON_DIR, SHOTS, PROCESSED, LOG_DIR): p.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = ROOT / 'config.txt'; ASSET_CACHE = DATA / 'hex_assets.json'; STATE_PATH = DATA / 'state.json'; ICON_SOURCE_PATH = DATA / 'hex_icon_sources.json'
AUGMENTS_URL = 'https://game.gtimg.cn/images/lol/act/img/js/kiwi/kiwi_augments.json'
# 日志格式
LOG_FORMAT = '%(asctime)s [%(levelname)s] %(message)s'

# 日志级别
LOG_LEVEL = logging.INFO

# 日志文件：按启动日期归档
LOG_DATE = datetime.now().strftime('%Y-%m-%d')
LOG_FILE = LOG_DIR / f'app_{LOG_DATE}.log'

# 清理五天以前的日志
def cleanup_old_logs():
    """
    保留今天及之前四天的日志。
    只清理本项目 logs 目录中符合命名规则的归档文件。
    """
    from datetime import timedelta

    cutoff = datetime.now().date() - timedelta(days=4)
    deleted = []
    failed = []

    patterns = (
        re.compile(r'app_(\d{4}-\d{2}-\d{2})\.log'),
        re.compile(r'thread_dump_(\d{4}-\d{2}-\d{2})\.log'),
    )

    for path in LOG_DIR.iterdir():
        if not path.is_file():
            continue

        for pattern in patterns:
            match = pattern.fullmatch(path.name)
            if not match:
                continue

            try:
                file_date = datetime.strptime(
                    match.group(1), '%Y-%m-%d'
                ).date()

                if file_date < cutoff:
                    path.unlink()
                    deleted.append(path.name)
            except Exception as exc:
                failed.append(f'{path.name}: {exc}')

            break

    return deleted, failed



# 创建日志队列
# 使用 SimpleQueue，避免队列满导致业务线程阻塞
log_queue = queue.SimpleQueue()

# 创建文件输出处理器
file_handler = logging.FileHandler(
    LOG_FILE,
    encoding='utf-8',
    delay=False
)
file_handler.setLevel(LOG_LEVEL)

# 统一设置日志格式
formatter = logging.Formatter(LOG_FORMAT)
file_handler.setFormatter(formatter)

# 暂时关闭控制台输出，只保留文件输出
# 本次实验用于判断 StreamHandler 是否导致日志监听线程阻塞

# 创建日志监听线程
log_listener = QueueListener(
    log_queue,
    file_handler,
    respect_handler_level=True
)

# 配置 Root Logger
root_logger = logging.getLogger()
root_logger.setLevel(LOG_LEVEL)

# 清理原有处理器
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)
    try:
        handler.close()
    except Exception:
        pass

# 业务线程只向队列写入日志
queue_handler = QueueHandler(log_queue)
queue_handler.setLevel(LOG_LEVEL)
root_logger.addHandler(queue_handler)

# 启动独立日志线程
log_listener.start()

# 启动时清理五天以前的日志
deleted_logs, failed_logs = cleanup_old_logs()

if deleted_logs:
    logging.info('[日志清理] 已删除过期日志：%s', ', '.join(deleted_logs))

if failed_logs:
    for item in failed_logs:
        logging.warning('[日志清理] 删除日志失败：%s', item)

logging.info('[日志系统] 普通日志文件：%s', LOG_FILE)

# 程序退出时停止日志监听线程
def stop_async_logging():
    try:
        if log_listener:
            log_listener.stop()
    except Exception:
        pass

atexit.register(stop_async_logging)



# 项目日志对象
log = logging.getLogger('lol_hex_display')

log.info('异步日志系统初始化完成')

# 独立线程堆栈诊断：不经过 logging，避免主日志输出阻塞时失去诊断能力。
_THREAD_DUMP_FILE = None
_THREAD_DUMP_INTERVAL = 30


def start_thread_diagnostics():
    """启动独立的周期性线程堆栈转储。"""
    global _THREAD_DUMP_FILE
    if _THREAD_DUMP_FILE is not None:
        return
    try:
        dump_path = LOG_DIR / f'thread_dump_{LOG_DATE}.log'
        
        _THREAD_DUMP_FILE = dump_path.open('a', encoding='utf-8', buffering=1)
        _THREAD_DUMP_FILE.write(
            f"\n\n===== 线程诊断启动 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"PID={os.getpid()}；每 {_THREAD_DUMP_INTERVAL} 秒记录一次 =====\n"
        )
        faulthandler.enable(file=_THREAD_DUMP_FILE, all_threads=True)
        faulthandler.dump_traceback_later(
            _THREAD_DUMP_INTERVAL, repeat=True,
            file=_THREAD_DUMP_FILE, exit=False,
        )
        log.info('[线程诊断] 已启动，堆栈文件：%s，间隔：%s 秒', dump_path, _THREAD_DUMP_INTERVAL)
    except Exception:
        log.exception('[线程诊断] 启动失败')
        if _THREAD_DUMP_FILE is not None:
            try:
                _THREAD_DUMP_FILE.close()
            except Exception:
                pass
            _THREAD_DUMP_FILE = None


def stop_thread_diagnostics():
    """停止周期性转储并关闭独立诊断文件。"""
    global _THREAD_DUMP_FILE
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    try:
        faulthandler.disable()
    except Exception:
        pass
    if _THREAD_DUMP_FILE is not None:
        try:
            _THREAD_DUMP_FILE.write(
                f"\n===== 线程诊断停止 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n"
            )
            _THREAD_DUMP_FILE.flush()
            _THREAD_DUMP_FILE.close()
        except Exception:
            pass
        _THREAD_DUMP_FILE = None
DEFAULT_CONFIG = {
    'screenshot_dir': 'screenshots/inbox',
    'host': '127.0.0.1',
    'port': '8767',
    'scan_interval': '1',
    'asset_check_interval': '3600',
    'version_check_interval': '60',
    'gameflow_check_interval': '10',
    'lol_client_path': r'D:\WeGameApps\英雄联盟（含经典模式）',
}
def check_admin_privileges():
    """源码运行时确保 LOL Hex 以管理员权限启动；打包版由 EXE 清单强制提权。"""
    if os.name != "nt":
        return True

    try:
        if ctypes.windll.shell32.IsUserAnAdmin() != 0:
            logging.info("[权限检查] 当前以管理员身份运行")
            return True
    except Exception:
        logging.exception("[权限检查] 无法检查 Windows 管理员权限")
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
            logging.info("[权限检查] 已请求 Windows 以管理员权限重新启动 LOL Hex")
            return False

        error_code = int(result or 0)
        logging.error("[权限检查] Windows 管理员启动失败，ShellExecuteW 返回：%s", error_code)
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
        logging.exception("[权限检查] 请求管理员启动失败：%s", exc)
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
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        for line in CONFIG_PATH.read_text(encoding='utf-8-sig', errors='replace').splitlines():
            s=line.strip()
            if not s or s.startswith('#') or '=' not in s: continue
            k,v=s.split('=',1); cfg[k.strip()]=v.strip()
    return cfg

def screenshot_dir():
    raw=read_config().get('screenshot_dir','screenshots/inbox')
    p=Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute(): p=ROOT/p
    try: p.mkdir(parents=True, exist_ok=True); return p.resolve()
    except Exception as e:
        log.error('截图目录不可用 %s：%s；回退到默认目录', p, e)
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
        with assets_lock:
            changed=digest(new)!=digest(assets)
            if changed:
                assets=new; atomic_json(ASSET_CACHE,assets); log.info('海克斯索引更新：%d 条',len(assets))
            else: log.info('海克斯索引无变化：%d 条',len(assets))
        icon_sources=read_json(ICON_SOURCE_PATH,{})
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
                atomic_json(ICON_SOURCE_PATH,icon_sources)
            except Exception as e: log.warning('图标下载失败 id=%s：%s',aid,e)
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
            '[LCU诊断] 读取游戏版本失败，HTTP=%s',
            response.status_code
        )

    except Exception as e:
        log.debug('[LCU诊断] 读取游戏版本异常：%s', e)

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
        log.debug("[LCU诊断] 凭据验证失败：%s", exc)
        return False, None


def _extract_credentials(command):
    port_match = re.search(r'--app-port(?:=|\s+)(\d+)', command or "")
    token_match = re.search(r'--remoting-auth-token(?:=|\s+)([^\s"]+)', command or "")
    if not port_match or not token_match:
        return None
    port, token = int(port_match.group(1)), token_match.group(1).rstrip("\\")
    return (port, token) if port > 0 and token else None


def _scan_lcu_credentials():
    """先读运行进程参数；进程不存在/参数不可用时回退到客户端日志。"""
    if os.name != "nt":
        return None, ""

    import subprocess
    import base64

    config = read_config()
    client_path = str(config.get("lol_client_path", "")).strip().strip('"')
    if not client_path:
        log.warning("[LCU诊断] config.txt 未配置 lol_client_path")
        return None, ""
    client_path = os.path.normcase(os.path.abspath(os.path.expandvars(client_path)))

    ps = r"""
$ErrorActionPreference = 'Stop'
$items = Get-CimInstance Win32_Process -Filter "Name = 'LeagueClientUx.exe'"
foreach ($item in $items) {
    $exe = [string]$item.ExecutablePath
    $cmd = [string]$item.CommandLine
    $exe64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($exe))
    $cmd64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($cmd))
    Write-Output ($exe64 + "`t" + $cmd64)
}
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                parts = line.split("\t", 1)
                if len(parts) != 2:
                    continue
                try:
                    exe_path = base64.b64decode(parts[0], validate=True).decode("utf-8", errors="replace")
                    command = base64.b64decode(parts[1], validate=True).decode("utf-8", errors="replace")
                except Exception:
                    continue
                if exe_path:
                    actual = os.path.normcase(os.path.abspath(exe_path))
                    try:
                        if os.path.commonpath([client_path, actual]) != client_path:
                            continue
                    except (ValueError, OSError):
                        continue
                credentials = _extract_credentials(command)
                if credentials:
                    log.info("[LCU诊断] 从 LeagueClientUx.exe 启动参数读取到凭据")
                    return credentials, "process"
    except subprocess.TimeoutExpired:
        log.warning("[LCU诊断] 查询 LeagueClientUx.exe 进程超时")
    except Exception as exc:
        log.warning("[LCU诊断] 查询客户端进程失败：%s", exc)

    # WeGame 国服实际日志目录：<lol_client_path>\LeagueClient
    log_dir = Path(client_path) / "LeagueClient"
    if not log_dir.is_dir():
        log.warning("[LCU诊断] 客户端日志目录不存在：%s", log_dir)
        return None, ""
    try:
        log_files = sorted(
            log_dir.glob("*_LeagueClientUx.log"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:
        log.warning("[LCU诊断] 枚举客户端日志失败：%s", exc)
        return None, ""

    for log_file in log_files:
        try:
            with log_file.open("r", encoding="utf-8", errors="replace") as stream:
                content = stream.read(64 * 1024)
            credentials = _extract_credentials(content)
            if credentials:
                log.info("[LCU诊断] 从客户端日志读取凭据：%s，端口=%s", log_file.name, credentials[0])
                return credentials, "log"
        except OSError as exc:
            log.debug("[LCU诊断] 读取日志失败 %s：%s", log_file, exc)

    log.warning("[LCU诊断] 客户端日志中未找到端口和 Token 参数")
    return None, ""


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
    """未识别时每 5 秒检查；识别成功后每 10 秒检查。"""
    global _lcu_credentials
    log.info("[LCU识别] 客户端识别线程已启动")
    while not stop_event.is_set():
        credentials, source = _scan_lcu_credentials()
        valid, account_data = (False, None)
        if credentials:
            valid, account_data = _validate_lcu_credentials(*credentials)

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
                _lcu_status.update({
                    "recognized": False, "available": False, "source": "",
                    "message": "未识别到可用的英雄联盟客户端凭据",
                    "checked_at": time.time(), "account": "",
                })

        if valid:
            log.info("[LCU识别] 验证成功：来源=%s，端口=%s", source, credentials[0])
            interval = _lcu_scan_interval_recognized
        else:
            log.info("[LCU识别] 暂未识别到有效客户端，保留现有卡片和截图")
            interval = _lcu_scan_interval_unrecognized

        if stop_event.wait(interval):
            break
    log.info("[LCU识别] 客户端识别线程已退出")



def get_gameflow_phase():
    """读取 LCU 当前对局阶段；记录失败原因，避免断连时误判对局结束。"""
    credentials = get_lcu_credentials()
    if not credentials:
        log.warning('[LCU诊断] 未获取到客户端端口或认证令牌')
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

        log.info(
            '[LCU诊断] gameflow-phase HTTP=%s，响应正文=%r',
            response.status_code,
            response.text[:200]
        )

        response.raise_for_status()
        phase = response.json()

        if isinstance(phase, str):
            phase = phase.strip()
            if phase and phase.lower() != 'none':
                return phase

        log.warning(
            '[LCU诊断] 返回值不是有效阶段字符串：类型=%s，值=%r',
            type(phase).__name__,
            phase
        )
        return None

    except Exception as exc:
        log.warning(
            '[LCU诊断] 读取 gameflow-phase 失败：%s: %s',
            type(exc).__name__,
            exc
        )
        return None



SCREENSHOT_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
cleanup_lock = threading.Lock()
cleanup_generation = 0

def cleanup_match_data(reason):
    """清空卡片与截图目录图片；保留配置、日志、海克斯图标缓存等非截图数据。"""
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
                    if candidate.is_file() and candidate.suffix.lower() in SCREENSHOT_EXTENSIONS:
                        try:
                            candidate.unlink()
                            deleted += 1
                        except OSError as exc:
                            failures += 1
                            log.warning('清理截图失败 %s：%s', candidate, exc)
            except OSError as exc:
                failures += 1
                log.warning('扫描清理目录失败 %s：%s', folder, exc)
        processed_files.clear()
        log.info('[对局清理] %s；已删除图片 %d 张，失败 %d 项', reason, deleted, failures)






def gameflow_worker():
    """按固定时间间隔检测 LCU 阶段；仅在进入 ChampSelect 时清理残留数据。"""
    log.info('[对局阶段] gameflow_worker 已启动')

    last_phase = None
    next_check = time.monotonic()

    while not stop_event.is_set():
        cfg = read_config()

        try:
            interval = max(
                2.0,
                float(cfg.get('gameflow_check_interval', '10') or 10)
            )
        except (TypeError, ValueError):
            interval = 10.0

        log.info('[对局阶段] 开始检测 LCU 阶段')

        try:
            phase = get_gameflow_phase()

            if phase is None:
                log.info(
                    '[对局阶段] 暂时无法读取 LCU 阶段；不会执行清理'
                )
            else:
                if phase != last_phase:
                    log.info(
                        '[对局阶段] %s -> %s',
                        last_phase or '未知/未连接',
                        phase
                    )

                if phase == 'ChampSelect':
                    with state_lock:
                        has_cards = bool(state.get('cards'))

                    folders = (screenshot_dir(), PROCESSED)
                    has_screenshots = False
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
                                    and candidate.suffix.lower()
                                    in SCREENSHOT_EXTENSIONS
                                ):
                                    has_screenshots = True
                                    break

                            if has_screenshots:
                                break

                        except OSError as exc:
                            log.warning(
                                '[选英雄清理] 检查截图目录失败 %s：%s',
                                folder,
                                exc
                            )

                    if has_cards or has_screenshots:
                        cleanup_match_data(
                            '检测到进入选英雄阶段，清理上一局或启动前残留'
                        )
                    else:
                        log.info(
                            '[选英雄清理] 卡片和截图均已清空，跳过清理'
                        )

                last_phase = phase

        except Exception:
            log.exception('[对局阶段] 检测过程中发生异常')

        # 按固定周期安排下一轮，不再额外等待完整的 interval 秒。
        next_check += interval
        now = time.monotonic()

        # 如果本轮执行超过了下次计划时间，跳过已经错过的周期，
        # 避免连续补跑造成请求拥挤。
        if next_check <= now:
            missed = int((now - next_check) // interval) + 1
            next_check += missed * interval

        stop_event.wait(max(0.0, next_check - time.monotonic()))

    log.info('[对局阶段] gameflow_worker 已退出')



def asset_worker():
    last=0; last_version_check=0; version=''; retry_delay=60
    while True:
        now=time.time(); cfg=read_config()
        interval=max(60,int(cfg.get('asset_check_interval','3600') or 3600)); vinterval=max(10,int(cfg.get('version_check_interval','60') or 60))
        if now-last_version_check>=vinterval:
            last_version_check=now; current=find_game_version()
            if current and version and current!=version:
                log.info('检测到游戏版本变化：%s -> %s，立即刷新索引',version,current); update_assets(True); last=now; retry_delay=60
            if current: version=current
        if now-last>=interval:
            if update_assets(): last=now; retry_delay=60
            else: time.sleep(retry_delay); retry_delay=min(retry_delay*5,1800); last=time.time()
        time.sleep(2)

# OCR model is initialized lazily, so the UI can start even if OCR dependency/model is unavailable.
_ocr=None; _ocr_lock=threading.Lock()
def ocr_text(image_path):
    global _ocr
    try:
        with _ocr_lock:
            if _ocr is None:
                from rapidocr_onnxruntime import RapidOCR
                _ocr=RapidOCR()
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

    with assets_lock:
        items = list(assets.values())

    norm = norm_name(text)

    # 完全匹配
    exact = [
        a for a in items
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
        a for a in items
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
            log.info('截图处理结果已因对局清理而丢弃：%s', path.name)
            return

        # 防止程序重启后，将已有截图再次加入卡片列表。
        if any(
            str(existing.get('filename', '')) == path.name
            for existing in state['cards']
        ):
            log.info('截图已存在于卡片列表，跳过重复添加：%s', path.name)
            return

        state['cards'].append(card)
        state['cards'] = state['cards'][-100:]
        state['updated_at'] = time.time()
        atomic_json(STATE_PATH, state)

    try:
        if path.exists(): shutil.copy2(path,PROCESSED/path.name)
    except Exception: pass
    log.info('截图识别：%s | OCR=%s | 匹配=%s | 原因=%s',path.name,text,card['name'] or '未匹配',card['reason'])

stop_event=threading.Event(); processed_files=set(); update_status_lock=threading.Lock(); update_status={'running':False,'message':'尚未手动检查','updated_at':0}; manual_update_lock=threading.Lock()
def screenshot_watcher():
    while not stop_event.is_set():
        folder=screenshot_dir()
        try:
            files=sorted(folder.glob('GameShot_*.png'),key=lambda p:p.stat().st_mtime)
            for p in files:
                if p.name in processed_files: continue
                # If using processed folder itself, avoid feedback. Files are marked in-memory after handling.
                process_screenshot(p); processed_files.add(p.name)
        except Exception as e: log.warning('扫描截图目录异常：%s',e)
        stop_event.wait(max(.2,float(read_config().get('scan_interval','1') or 1)))

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


def write_pid_file():
    """记录当前管理员 Hex 进程 PID，供主程序退出时精确核验。"""
    temp_path = PID_FILE.with_suffix('.pid.tmp')
    temp_path.write_text(str(os.getpid()), encoding='ascii')
    os.replace(temp_path, PID_FILE)
    log.info('Hex 进程 PID 已记录：%s', os.getpid())


def remove_pid_file():
    """只删除属于当前进程的 PID 文件，避免误删新实例记录。"""
    try:
        if PID_FILE.is_file() and PID_FILE.read_text(encoding='ascii').strip() == str(os.getpid()):
            PID_FILE.unlink()
            log.info('Hex PID 文件已清理')
    except OSError:
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
register_bar_routes(
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
    # 新卡片在 process_screenshot 中保存 level；
    # 旧 state.json 中的卡片则按 augment_id 从索引补齐。
    with state_lock:
        cards = [dict(c) for c in state['cards']]
        updated_at = state.get('updated_at', 0)

    with assets_lock:
        asset_levels = {
            str(aid): str(item.get('level') or '')
            for aid, item in assets.items()
            if isinstance(item, dict)
        }
        count = len(assets)

    for card in cards:
        if not card.get('level'):
            card['level'] = asset_levels.get(
                str(card.get('augment_id') or ''),
                ''
            )

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
            state['cards']=[]
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
        if removed: processed_files.discard(removed[0].name)
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
            # 如果 active 状态残留，但线程已经退出，自动修复。
            if self.active:
                if self.thread is None or not self.thread.is_alive():
                    log.warning(
                        '[截图诊断] 检测到截图状态残留，自动重置'
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
                else:
                    self.cancel_requested.set()
                    log.info(
                        '[热键] F1 已触发：请求取消当前截图'
                    )
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
                log.info(
                    '[热键] F1 已触发：启动截图任务，任务编号=%s',
                    generation
                )
            except Exception:
                self.active = False
                self.thread = None
                self.cancel_requested.clear()
                log.exception('[截图诊断] 截图线程启动失败')

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

            log.info(
                '[截图诊断] 截图线程开始运行，任务编号=%s',
                generation
            )

            # 捕获当前屏幕画面
            try:
                ctypes.windll.dwmapi.DwmFlush()
            except Exception:
                pass

            time.sleep(0.03)

            log.info('[截图诊断] 开始捕获屏幕')

            img, mon = self.capture()

            log.info(
                '[截图诊断] 屏幕捕获完成：%dx%d',
                img.width,
                img.height
            )

            # 捕获过程中如果收到取消请求，则不再创建窗口。
            if self.cancel_requested.is_set():
                log.info('[截图诊断] 屏幕捕获后收到取消请求')
                return

            with self._lock:
                if generation != self._generation or not self.active:
                    log.info('[截图诊断] 截图任务已失效，不再创建窗口')
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

            log.info(
                '[截图诊断] 截图框已创建，等待用户选择区域'
            )

            # 不绑定 ESC。
            # 再次按 F1 请求取消。
            root.after(
                30,
                lambda: self.poll_cancel(generation)
            )

            root.mainloop()

        except Exception as e:
            log.exception(
                '[截图诊断] 截图任务异常退出：%s',
                e
            )

        finally:
            # 无论正常结束还是异常退出，都必须复位状态。
            self.finish(generation)

            log.info(
                '[截图诊断] 截图任务结束，任务编号=%s',
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
            log.info('[截图诊断] 检测到取消请求，关闭截图框')
            self.finish(generation)
            return

        try:
            root.after(
                30,
                lambda: self.poll_cancel(generation)
            )
        except Exception:
            log.debug('[截图诊断] 截图窗口已关闭')

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
            log.info('选区太小，已取消')
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

            log.info(
                '截图已保存：%s (%dx%d)',
                path,
                crop.width,
                crop.height
            )

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
            self.thread = None
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

        log.info('已请求取消截图')


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
        log.info('全局热键已启用：F1 开始/取消截图；管理员权限检查通过')
        return True

    except Exception as e:
        F1_HOTKEY_ERROR = f'{type(e).__name__}: {e}'
        log.exception('F1 全局热键注册失败：%s', e)
        return False


def main():
    start_thread_diagnostics()
        # Windows 控制台：关闭 QuickEdit，避免误操作导致日志输出暂停
    if os.name == 'nt':
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)

            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                new_mode = mode.value & ~0x0040  # 关闭 QuickEdit
                new_mode |= 0x0080  # 启用扩展控制台标志
                kernel32.SetConsoleMode(handle, new_mode)
        except Exception as e:
            log.warning('设置控制台模式失败：%s', e)
    log.info('lol_hex_display 启动；截图目录：%s', screenshot_dir())
    threading.Thread(target=screenshot_watcher,daemon=True,name='ScreenshotWatcher').start()
    threading.Thread(target=asset_worker,daemon=True,name='AssetUpdater').start()
    cfg_for_gameflow = read_config()
    log.info('LCU 对局阶段检测间隔：%s 秒（config.txt: gameflow_check_interval）', cfg_for_gameflow.get('gameflow_check_interval', '10'))
        # 程序启动时只输出一次客户端安装目录
    client_path = str(
        cfg_for_gameflow.get('lol_client_path', '')
    ).strip().strip('"')
    log.info('[LCU诊断] 配置的客户端安装目录：%s', client_path or '(未配置)')
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
                log.info('F1 全局热键已注销')
            except Exception:
                log.exception('注销 F1 全局热键失败')
        try:
            if HTTP_SERVER is not None:
                HTTP_SERVER.server_close()
        except Exception:
            log.exception('关闭 Hex HTTP 服务失败')
        HTTP_SERVER = None
        stop_thread_diagnostics()
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
