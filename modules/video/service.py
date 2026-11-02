# -*- coding: utf-8 -*-
"""整合后的 B 站视频点播模块。保留 Selenium 播放、BV 校验、队列和 OBS 显隐。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.common.exceptions import NoSuchWindowException, InvalidSessionIdException
from selenium.webdriver.chrome.options import Options

try:
    import obsws_python
except ImportError:
    obsws_python = None

from core.config_manager import ConfigManager
from core.paths import get_base_dir
from core.credential_store import CredentialStore

logger = logging.getLogger("bili_live_danmaku.video")
BASE_DIR = get_base_dir()
CONFIG_MANAGER = ConfigManager()
VIDEO_CONFIG = CONFIG_MANAGER.load("video", {})
LIVE_CONFIG = CONFIG_MANAGER.load("live", {})
roomid = int(LIVE_CONFIG.get("room_id", 0) or 0)
REQUIRE_FAN_MEDAL = bool(VIDEO_CONFIG.get("requests", {}).get("require_fans_medal", True))
MAX_VIDEO_DURATION_SEC = int(VIDEO_CONFIG.get("requests", {}).get("max_video_duration_seconds", 300))
COOLDOWN_SEC = int(VIDEO_CONFIG.get("requests", {}).get("cooldown_seconds", 60))
MAX_QUEUE_SIZE = int(VIDEO_CONFIG.get("requests", {}).get("queue_limit", 30))
PAGE_LOAD_TIMEOUT = int(VIDEO_CONFIG.get("chrome", {}).get("page_load_timeout_seconds", 45))
CHROME_PROFILE = BASE_DIR / str(VIDEO_CONFIG.get("chrome", {}).get("profile_dir", "data/video/chrome_profile"))
COOKIE_VALUES: dict[str, str] = {}
ADMIN_UIDS: set[int] = set()
module_enabled = False
module_paused = False
_streamer_uid_cache = None
_streamer_uid_cache_at = 0.0
STREAMER_UID_CACHE_SEC = 600
video_titles: dict[str, str] = {}
video_durations: dict[str, int] = {}
operation_messages: list[dict[str, Any]] = []
OPERATION_MESSAGE_LIMIT = 10
OBS_CONFIG: dict[str, Any] = {"enabled": False}


def load_cookie_values_for_api():
    """从主程序注入的统一登录态读取 Cookie，不读取独立 Cookie 文件。"""
    if not COOKIE_VALUES.get("SESSDATA"):
        return None
    return dict(COOKIE_VALUES)


def load_admin_uids(config: dict[str, Any]) -> set[int]:
    values: set[int] = set()
    raw_values = config.get("admin_uids", [])
    if isinstance(raw_values, list):
        for raw in raw_values:
            try:
                uid = int(raw)
                if uid > 0:
                    values.add(uid)
            except (TypeError, ValueError):
                continue
    file_name = config.get("admins_file", "data/video/uid.txt")
    if file_name:
        path = (BASE_DIR / str(file_name)).resolve()
        try:
            path.relative_to(BASE_DIR.resolve())
            for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
                line = raw_line.split("#", 1)[0].strip()
                for value in re.split(r"[,，;；\s]+", line):
                    if value.isdigit():
                        values.add(int(value))
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("读取视频模块管理员列表失败：%s", type(exc).__name__)
    return values


def load_obs_config() -> dict[str, Any]:
    config = CONFIG_MANAGER.load("obs", {})
    try:
        secret = CredentialStore().load("obs") or {}
    except Exception as exc:
        logger.warning("读取 OBS 凭据失败：%s", type(exc).__name__)
        secret = {}
    host = str(config.get("host", "")).strip()
    video_obs = VIDEO_CONFIG.get("obs", {}) if isinstance(VIDEO_CONFIG.get("obs"), dict) else {}
    source_name = str(config.get("source_name") or video_obs.get("source_name", "b站视频播放器")).strip()
    try:
        port = int(config.get("port", 4455))
    except (TypeError, ValueError):
        port = 0
    password = str(secret.get("password", "") or "")
    return {
        "host": host,
        "port": port,
        "password": password,
        "source_name": source_name,
        "enabled": bool(host and 1 <= port <= 65535 and source_name and password),
        "timeout": float(config.get("timeout", 2.5) or 2.5),
    }


def configure_video_module(config: dict[str, Any], cookie_provider=None, room_id: int | None = None) -> None:
    """由主程序注入统一 Cookie 和普通模块配置；不创建第二个登录或连接。"""
    global VIDEO_CONFIG, roomid, REQUIRE_FAN_MEDAL, MAX_VIDEO_DURATION_SEC
    global COOLDOWN_SEC, MAX_QUEUE_SIZE, PAGE_LOAD_TIMEOUT, CHROME_PROFILE
    global COOKIE_VALUES, ADMIN_UIDS, OBS_CONFIG, chrome_options
    VIDEO_CONFIG = dict(config or {})
    requests_config = VIDEO_CONFIG.get("requests", {}) if isinstance(VIDEO_CONFIG.get("requests"), dict) else {}
    chrome_config = VIDEO_CONFIG.get("chrome", {}) if isinstance(VIDEO_CONFIG.get("chrome"), dict) else {}
    REQUIRE_FAN_MEDAL = bool(requests_config.get("require_fans_medal", True))
    MAX_VIDEO_DURATION_SEC = max(1, int(requests_config.get("max_video_duration_seconds", 300)))
    COOLDOWN_SEC = max(0, int(requests_config.get("cooldown_seconds", 60)))
    MAX_QUEUE_SIZE = max(1, int(requests_config.get("queue_limit", 30)))
    PAGE_LOAD_TIMEOUT = max(5, int(chrome_config.get("page_load_timeout_seconds", 45)))
    if room_id is not None:
        roomid = max(0, int(room_id))
    ADMIN_UIDS = load_admin_uids(VIDEO_CONFIG)
    values = cookie_provider() if callable(cookie_provider) else {}
    COOKIE_VALUES = {
        "SESSDATA": str(values.get("sessdata", "")),
        "bili_jct": str(values.get("bili_jct", "")),
        "buvid3": str(values.get("buvid3", "")),
        "buvid4": str(values.get("buvid4", "")),
        "DedeUserID": str(values.get("dedeuserid", "")),
    }
    CHROME_PROFILE = (BASE_DIR / str(chrome_config.get("profile_dir", "data/video/chrome_profile"))).resolve()
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    chrome_options = Options()
    chrome_options.add_argument(f"--user-data-dir={CHROME_PROFILE}")
    chrome_options.add_argument("--profile-directory=Default")
    chrome_options.page_load_strategy = "eager"
    binary_path = str(chrome_config.get("binary_path", "") or "").strip()
    if binary_path and Path(binary_path).is_file():
        chrome_options.binary_location = binary_path
    OBS_CONFIG = load_obs_config()


def add_operation_message(message):
    operation_messages.append({
        "time": time.strftime("%H:%M:%S"),
        "timestamp": time.time(),
        "message": str(message),
    })
    del operation_messages[:-OPERATION_MESSAGE_LIMIT]

panel_server = None
panel_thread = None
main_async_loop = None
main_task = None
# 仅在主程序完成 Chrome 初始化后置为 True；与队列数据接口分离。
program_ready = False


def fetch_video_info_sync(bv):
    """通过 B 站公开视频信息接口获取标题和时长。"""
    url = "https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode(
        {"bvid": bv}
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        data = json.loads(response.read().decode("utf-8"))

    if data.get("code") != 0 or not data.get("data"):
        raise ValueError(
            f"B站接口未返回视频信息：{data.get('message', data.get('code'))}"
        )

    info = data["data"]
    title = str(info.get("title") or bv)
    duration = int(info.get("duration") or 0)
    return title, duration


async def ensure_video_info(bv):
    """异步读取标题与时长，不阻塞弹幕监听和播放。"""
    if bv in video_titles and bv in video_durations:
        return video_titles[bv], video_durations[bv]

    try:
        title, duration = await asyncio.to_thread(fetch_video_info_sync, bv)
        video_titles[bv] = title
        video_durations[bv] = duration
        logger.info("已获取视频信息：%s - %s（%s 秒）", bv, title, duration)
        return title, duration
    except Exception as exc:
        logger.warning("获取视频信息失败（%s）：%s", bv, exc)
        raise


async def ensure_video_title(bv):
    """异步更新标题缓存，供网页队列面板显示。"""
    if bv in video_titles:
        return
    try:
        await ensure_video_info(bv)
    except Exception:
        video_titles.setdefault(bv, bv)


def build_queue_snapshot():
    """生成供 OBS 网页读取的队列快照。"""
    current = None
    if current_bv:
        current = {
            "bvid": current_bv,
            "title": video_titles.get(current_bv, "正在获取视频标题…"),
        }

    queued = []
    for index, bv in enumerate(list(video_queue), start=1):
        queued.append({
            "index": index,
            "bvid": bv,
            "title": video_titles.get(bv, "正在获取视频标题…"),
        })

    return {
        "current": current,
        "queue": queued,
        "queue_length": len(video_queue),
        "updated_at": time.strftime("%H:%M:%S"),
        "operations": list(operation_messages),
    }


def set_obs_source_visible(visible):
    """控制所有场景中同名来源的可见性，只修改实际状态不一致的场景项目。"""
    visible = bool(visible)

    # 配置无效时完全跳过 WebSocket 请求。
    if not OBS_CONFIG or not OBS_CONFIG.get("enabled", False):
        return False

    if obsws_python is None:
        logger.error(
            "缺少 obsws-python 依赖，无法控制 OBS。"
            "请安装：python -m pip install obsws-python"
        )
        return False

    client = None
    try:
        client = obsws_python.ReqClient(
            host=OBS_CONFIG["host"],
            port=OBS_CONFIG["port"],
            password=OBS_CONFIG["password"],
            timeout=3,
        )

        scene_response = client.get_scene_list()
        scenes = getattr(scene_response, "scenes", None)
        if scenes is None and isinstance(scene_response, dict):
            scenes = scene_response.get("scenes", [])
        if not scenes:
            logger.warning("控制 OBS 来源失败：OBS 未返回任何场景。")
            return False

        target_name = OBS_CONFIG["source_name"]
        matched_count = 0
        changed_count = 0
        changed_scenes = []

        for scene in scenes:
            if isinstance(scene, dict):
                scene_name = (
                    scene.get("sceneName")
                    or scene.get("scene_name")
                    or scene.get("name")
                )
            else:
                scene_name = (
                    getattr(scene, "scene_name", None)
                    or getattr(scene, "sceneName", None)
                    or getattr(scene, "name", None)
                )

            if not scene_name:
                continue

            item_response = client.get_scene_item_list(scene_name)
            items = getattr(item_response, "scene_items", None)
            if items is None and isinstance(item_response, dict):
                items = item_response.get(
                    "sceneItems", item_response.get("scene_items", [])
                )
            if not items:
                continue

            for item in items:
                if isinstance(item, dict):
                    item_name = item.get("sourceName") or item.get("source_name")
                    item_id = item.get("sceneItemId")
                    if item_id is None:
                        item_id = item.get("scene_item_id")
                    item_enabled = item.get("sceneItemEnabled")
                    if item_enabled is None:
                        item_enabled = item.get("scene_item_enabled")
                else:
                    item_name = (
                        getattr(item, "source_name", None)
                        or getattr(item, "sourceName", None)
                    )
                    item_id = getattr(item, "scene_item_id", None)
                    if item_id is None:
                        item_id = getattr(item, "sceneItemId", None)
                    item_enabled = getattr(item, "scene_item_enabled", None)
                    if item_enabled is None:
                        item_enabled = getattr(item, "sceneItemEnabled", None)

                if item_name != target_name or item_id is None:
                    continue

                matched_count += 1

                # 只有读取到状态且与目标状态不同，才跳过/执行设置。
                # 如果此版本返回对象不含 enabled 字段，则保守地执行设置请求。
                if item_enabled is not None and bool(item_enabled) == visible:
                    continue

                client.set_scene_item_enabled(scene_name, int(item_id), visible)
                changed_count += 1
                if scene_name not in changed_scenes:
                    changed_scenes.append(scene_name)

        if matched_count == 0:
            logger.warning(
                "OBS 中未找到名为“%s”的来源场景项目；"
                "请检查所有场景中的来源名称是否完全一致。",
                target_name,
            )
            return False

        # 状态已符合要求时，不重复发送设置请求，也不重复打印显隐日志。
        if changed_count == 0:
            return True

        logger.info(
            "OBS 来源已%s：来源=%s；本次修改 %s 个场景项目（场景：%s）",
            "显示" if visible else "隐藏",
            target_name,
            changed_count,
            "、".join(changed_scenes),
        )
        return True

    except Exception as exc:
        logger.warning(
            "控制 OBS 来源失败：source=%s；错误类型=%s。请检查 OBS WebSocket 配置。",
            OBS_CONFIG.get("source_name"), type(exc).__name__,
        )
        return False
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


async def set_obs_visibility_async(visible):
    """在线程中执行 OBS 请求，避免阻塞弹幕事件循环。"""
    return await asyncio.to_thread(set_obs_source_visible, visible)


def fetch_streamer_uid_sync():
    """通过 room_config.txt 中的直播间号查询直播间主播 UID。"""
    global _streamer_uid_cache, _streamer_uid_cache_at
    now = time.monotonic()
    if _streamer_uid_cache and now - _streamer_uid_cache_at < STREAMER_UID_CACHE_SEC:
        return _streamer_uid_cache

    url = "https://api.live.bilibili.com/room/v1/Room/get_info?" + urllib.parse.urlencode(
        {"room_id": int(roomid)}
    )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://live.bilibili.com/"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))

    data = payload.get("data")
    if payload.get("code") != 0 or not isinstance(data, dict):
        raise ValueError("直播间接口未返回有效数据")
    owner_uid = data.get("uid")
    if not isinstance(owner_uid, int) or owner_uid <= 0:
        raise ValueError("直播间接口未返回有效主播 UID")

    _streamer_uid_cache = owner_uid
    _streamer_uid_cache_at = now
    return owner_uid


def check_fan_medal_sync(viewer_uid):
    """严格校验观众是否拥有本直播间主播的粉丝勋章，且该勋章当前已点亮。

    MedalWall 查询的是观众公开的勋章墙；medal_info.target_id 标识勋章所属主播，
    uinfo_medal.is_light 标识该勋章的点亮状态。这里不检查 wearing_status，
    因此未佩戴但已点亮的粉丝勋章仍然允许点播。任何无法确认的情况均拒绝放行。
    返回 (allowed, reason)。
    """
    cookies = load_cookie_values_for_api()
    if not cookies:
        return False, "没有可用 Cookie（bili_cookie.txt 缺失或必需字段不完整）"

    try:
        streamer_uid = fetch_streamer_uid_sync()
    except Exception as exc:
        logger.warning("粉丝勋章校验失败：无法查询本直播间主播 UID（%s）", type(exc).__name__)
        return False, "无法确认本直播间主播 UID"

    url = "https://api.live.bilibili.com/xlive/web-ucenter/user/MedalWall?" + urllib.parse.urlencode(
        {"target_id": int(viewer_uid)}
    )
    cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36",
            "Referer": "https://live.bilibili.com/",
            "Cookie": cookie_header,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("粉丝勋章校验失败：勋章墙接口请求异常（%s）", type(exc).__name__)
        return False, "勋章墙查询失败"

    if not isinstance(payload, dict) or not isinstance(payload.get("code"), int):
        return False, "勋章墙接口返回格式异常"
    if payload.get("code") != 0:
        code = payload.get("code")
        if code in (-101, -111, -400):
            return False, f"Cookie 无效或登录状态失效（接口码 {code}）"
        return False, f"勋章墙接口返回错误（接口码 {code}）"

    data = payload.get("data")
    if not isinstance(data, dict) or "list" not in data or not isinstance(data.get("list"), list):
        return False, "勋章墙查询结果不完整（缺少有效 list）"

    # 只有公开完整的勋章墙才可用于判断；隐藏或只显示佩戴中勋章时不作推断。
    returned_uid = data.get("uid")
    if not isinstance(returned_uid, (int, str)) or not str(returned_uid).isdigit() or int(returned_uid) != int(viewer_uid):
        return False, "勋章墙查询结果不完整（返回 UID 与查询对象不一致）"

    count = data.get("count")
    if not isinstance(count, int) or count < 0:
        return False, "勋章墙查询结果不完整（缺少有效 count）"

    close_space_medal = data.get("close_space_medal")
    only_show_wearing = data.get("only_show_wearing")
    if close_space_medal not in (0, "0"):
        return False, "勋章墙不可公开查询或公开状态无法确认"
    if only_show_wearing not in (0, "0"):
        return False, "勋章墙仅显示佩戴中的勋章，无法确认完整勋章列表"

    medal_list = data["list"]
    if count != len(medal_list):
        return False, f"勋章墙查询结果不完整（接口声明 {count} 枚，实际返回 {len(medal_list)} 枚）"

    target_medal = None
    for index, item in enumerate(medal_list):
        if not isinstance(item, dict):
            return False, f"勋章墙查询结果不完整（第 {index + 1} 项格式异常）"

        medal_info = item.get("medal_info")
        if not isinstance(medal_info, dict):
            return False, f"勋章墙查询结果不完整（第 {index + 1} 项缺少 medal_info）"

        target_id = medal_info.get("target_id")
        if not isinstance(target_id, (int, str)) or not str(target_id).isdigit():
            return False, f"勋章墙查询结果不完整（第 {index + 1} 项缺少有效 target_id）"

        # B站 MedalWall 的点亮状态位于 uinfo_medal.is_light，而不是 medal_info.wearing_status。
        user_medal_info = item.get("uinfo_medal")
        if not isinstance(user_medal_info, dict):
            return False, f"勋章墙查询结果不完整（第 {index + 1} 项缺少 uinfo_medal 点亮状态）"

        if int(target_id) == streamer_uid:
            light_status = user_medal_info.get("is_light")
            if light_status not in (0, 1, "0", "1"):
                return False, "本直播间粉丝勋章的点亮状态缺失或无法识别"
            target_medal = int(light_status)

    if target_medal is None:
        return False, "未查询到本直播间主播的粉丝勋章"
    if target_medal != 1:
        return False, "拥有本直播间主播的粉丝勋章，但当前处于熄灭状态"
    return True, "拥有本直播间主播的粉丝勋章且当前已点亮"


async def verify_fan_medal(viewer_uid):
    """在线程中查询勋章墙，避免网络请求阻塞弹幕事件循环。"""
    return await asyncio.to_thread(check_fan_medal_sync, viewer_uid)



# Selenium browser and video state
SELENIUM_LOCK = threading.RLock()

def selenium_locked(func):
    """在共享锁内串行执行 Selenium 操作，避免多个线程同时访问 WebDriver。"""
    def wrapper(*args, **kwargs):
        with SELENIUM_LOCK:
            return func(*args, **kwargs)
    wrapper.__name__ = getattr(func, "__name__", "selenium_operation")
    wrapper.__doc__ = func.__doc__
    return wrapper

@selenium_locked
def execute_script_sync(script, *args):
    """在线程安全锁内执行 JavaScript。由 asyncio.to_thread 调用。"""
    if driver is None:
        raise RuntimeError("Selenium 浏览器尚未初始化")
    return driver.execute_script(script, *args)


CHROME_PROFILE.mkdir(parents=True, exist_ok=True)

chrome_options = Options()

chrome_options.add_argument(
    f"--user-data-dir={CHROME_PROFILE.resolve()}"
)

chrome_options.add_argument("--profile-directory=Default")
chrome_options.page_load_strategy = "eager"

driver = None


@selenium_locked
def _pause_video_for_close_sync():
    if driver is None:
        return False
    try:
        driver.execute_script(
            "const v=document.querySelector('video'); if(v){try{v.pause();}catch(e){}} window.stop();"
        )
        return True
    except Exception:
        return False


@selenium_locked
def _quit_browser_sync(browser):
    if browser is not None:
        browser.quit()


@selenium_locked
def create_browser():
    """创建 Chrome 浏览器实例。"""

    browser = webdriver.Chrome(options=chrome_options)
    browser.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

    return browser


@selenium_locked
def ensure_browser_window():
    """确保 Selenium 绑定到一个仍然存在的 Chrome 标签页。

    用户手动关闭正在播放视频的标签页时，如果 Chrome 还有其他标签页，
    Selenium 的当前窗口句柄会失效。此时切换到一个仍然存在的标签页，
    不关闭用户的其他标签页，也不重启整个浏览器。
    """
    global driver

    if driver is None:
        logger.warning("Selenium 浏览器尚未初始化，正在重新创建")
        driver = create_browser()
        return

    try:
        handles = driver.window_handles
        if handles:
            try:
                current_handle = driver.current_window_handle
            except Exception:
                current_handle = None

            if current_handle not in handles:
                # 先切换到一个仍存在的标签页，再新建视频专用标签页，
                # 避免覆盖用户正在使用的其他网页。
                driver.switch_to.window(handles[0])
                driver.switch_to.new_window("tab")
                logger.info(
                    "检测到当前视频标签页已关闭，已新建视频标签页；"
                    "其他 Chrome 标签页保持不变"
                )
            return

        # 没有任何可用标签页，尝试重建 Selenium 会话。
        logger.warning("Chrome 当前没有可用标签页，正在重建浏览器会话")

    except Exception as exc:
        logger.warning("检查 Chrome 标签页失败，将尝试恢复：%s", exc)

    old_driver = driver
    driver = None
    try:
        old_driver.quit()
    except Exception:
        pass

    driver = create_browser()
    logger.info("Chrome 浏览器会话已重新创建")




@selenium_locked
def try_enter_video_fullscreen(bv, timeout=10):
    """
    进入 B 站播放器网页全屏（W）。

    使用实际 DOM：
      .bpx-player-ctrl-web
      .bpx-player-ctrl-web-enter
      .bpx-player-ctrl-web-leave

    每次调用最多点击一次，防止反复切换全屏状态。
    不触发 F/F11。
    """
    deadline = time.monotonic() + timeout
    clicked = False

    state_js = """
        const player = document.querySelector(
            '.bpx-player-container'
        );

        const button = player
            ? player.querySelector('.bpx-player-ctrl-web')
            : null;

        const enterIcon = player
            ? player.querySelector('.bpx-player-ctrl-web-enter')
            : null;

        const leaveIcon = player
            ? player.querySelector('.bpx-player-ctrl-web-leave')
            : null;

        function visible(el) {
            if (!el) return false;

            const style = getComputedStyle(el);
            const rect = el.getBoundingClientRect();

            return (
                el.getClientRects().length > 0 &&
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                rect.width > 0 &&
                rect.height > 0
            );
        }

        return {
            playerFound: !!player,
            buttonFound: !!button,
            buttonVisible: visible(button),
            enterVisible: visible(enterIcon),
            leaveVisible: visible(leaveIcon),
            buttonLabel: button
                ? button.getAttribute('aria-label')
                : null,
            playerClass: player
                ? player.className
                : '',
            playerWidth: player
                ? Math.round(player.getBoundingClientRect().width)
                : 0,
            playerHeight: player
                ? Math.round(player.getBoundingClientRect().height)
                : 0
        };
    """

    while time.monotonic() < deadline:
        try:
            state = driver.execute_script(state_js)

            if not state.get("playerFound"):
                time.sleep(0.5)
                continue

            # 退出图标可见，表示已经进入网页全屏。
            if state.get("leaveVisible"):
                logging.info(
                    "已确认 B 站网页全屏成功：%s", bv
                )
                return True

            # 进入图标可见，表示当前尚未进入网页全屏。
            if clicked:
                # 已经点击过，不再点击，避免反复切换。
                logging.warning(
                    "已点击 W 按钮，但尚未确认网页全屏；"
                    "为避免反复切换，不再点击：%s；状态=%s",
                    bv,
                    state
                )
                return False

            if not state.get("buttonVisible"):
                time.sleep(0.5)
                continue

            # 仅当进入图标可见、退出图标不可见时才点击。
            if (
                state.get("enterVisible")
                and not state.get("leaveVisible")
            ):
                button = driver.find_element(
                    "css selector",
                    ".bpx-player-container .bpx-player-ctrl-web"
                )

                # 先确认目标仍然是网页全屏按钮，避免页面布局变化时
                # Selenium 按坐标点击到旁边的字幕等控制按钮。
                is_web_fullscreen_button = driver.execute_script(
                    """
                    const el = arguments[0];
                    return !!el && el.isConnected &&
                        el.classList.contains('bpx-player-ctrl-web') &&
                        !!el.closest('.bpx-player-container') &&
                        !!el.querySelector('.bpx-player-ctrl-web-enter');
                    """,
                    button
                )
                if not is_web_fullscreen_button:
                    logging.warning(
                        "目标元素未通过网页全屏按钮校验，已取消点击，避免误触字幕等按钮：%s",
                        bv
                    )
                    time.sleep(0.5)
                    continue

                clicked = True
                # 使用 DOM click，不使用 Selenium 的坐标式点击，避免控件紧邻时误触其他按钮。
                driver.execute_script("arguments[0].click();", button)

                logging.info(
                    "已触发 B 站网页全屏按钮的 DOM click（W）：%s", bv
                )

                # 等待播放器切换状态。
                time.sleep(1)

                state = driver.execute_script(state_js)

                if state.get("leaveVisible"):
                    logging.info(
                        "已确认 B 站网页全屏成功：%s", bv
                    )
                    return True

                logging.warning(
                    "点击后尚未确认网页全屏；"
                    "不会再次点击：%s；状态=%s",
                    bv,
                    state
                )
                return False

            # 状态不明确时不点击，稍后重新检查。
            time.sleep(0.5)

        except Exception:
            logging.exception(
                "检查或进入 B 站网页全屏失败：%s", bv
            )
            time.sleep(0.5)

    logging.warning(
        "等待 %s 秒后仍未确认 B 站网页全屏；"
        "未触发 F/F11：%s",
        timeout,
        bv
    )
    return False




@selenium_locked
def open_video_page(bv, url):
    """打开视频页面；若当前标签页刚被关闭，则恢复后重试一次，并尝试全屏。"""
    global driver

    # 首先处理最常见的情况：视频标签页被手动关闭，但其他标签页仍在。
    ensure_browser_window()

    try:
        driver.get(url)
    except (NoSuchWindowException, InvalidSessionIdException) as exc:
        logger.warning(
            "打开 %s 时发现 Selenium 标签页/会话已失效，正在恢复并重试：%s",
            bv, exc,
        )
        # 若导航期间标签页又被关闭，再恢复一次并重试。
        ensure_browser_window()
        driver.get(url)

    # 视频页面打开后，尝试将 HTML5 视频播放位置重置到开头。
    # 只执行一次，不循环反复重置；等待播放器元数据就绪后设置 currentTime=0。
    try:
        reset_script = r"""
            const v = document.querySelector('video');
            if (!v) return {ok: false, reason: 'video_element_not_found'};
            if (v.readyState < 1 || !Number.isFinite(v.duration)) {
                return {ok: false, reason: 'metadata_not_ready', readyState: v.readyState};
            }
            try {
                v.currentTime = 0;
                return {ok: true, currentTime: v.currentTime, duration: v.duration};
            } catch (e) {
                return {ok: false, reason: String(e)};
            }
        """
        reset_deadline = time.monotonic() + 10.0
        reset_result = None
        while time.monotonic() < reset_deadline:
            reset_result = driver.execute_script(reset_script)
            if isinstance(reset_result, dict) and reset_result.get("ok"):
                logger.info(
                    "已将点播视频播放位置重置到开头：%s（当前 %.2f 秒）",
                    bv, float(reset_result.get("currentTime") or 0),
                )
                break
            time.sleep(0.5)
        else:
            reason = reset_result.get("reason", "unknown") if isinstance(reset_result, dict) else "no_result"
            logger.warning("未能确认视频播放位置已重置为 0 秒：%s（原因：%s）", bv, reason)
    except Exception as exc:
        logger.warning("重置视频播放位置失败（不影响点播队列）：%s；%s", bv, exc)

    # 视频进度重置尝试完成后，再执行原有的 B 站网页全屏逻辑。
    try:
        try_enter_video_fullscreen(bv)
    except Exception as exc:
        logger.warning("自动网页全屏失败（不影响点播队列）：%s", exc)


# ====================== 队列及播放状态 ======================

video_queue = []

# 当前打开的视频
current_bv = None

# 是否正在处理播放任务
is_playing = False

# 当前视频的播放结束监控任务
playback_monitor_task = None

# 每位观众的点播冷却时间
last_order_time = {}

# 播放状态锁
play_lock = asyncio.Lock()

# 手动切换锁：防止弹幕和网页同时触发重复切换
skip_lock = asyncio.Lock()

# ====================== 播放结束监控 ======================

async def monitor_video_end(bv):
    """监控当前 B 站视频是否播放结束，结束后自动清状态并播放队列下一条。"""
    global is_playing, current_bv, playback_monitor_task

    saw_video = False
    last_time = -1.0
    saw_progress = False

    logger.info("开始监控视频播放状态和结束：%s", bv)
    await set_obs_visibility_async(False)
    last_obs_visible = False
    status_task = None
    status_timeout_count = 0
    status_script = """
        const v = document.querySelector('video');
        const player = document.querySelector('.bpx-player-container, .bilibili-player');
        const button = player ? player.querySelector(
            '.bpx-player-ctrl-web, .bilibili-player-video-btn-web-fullscreen'
        ) : null;
        const buttonLabel = button ? [
            button.innerText || '',
            button.getAttribute('aria-label') || '',
            button.getAttribute('title') || '',
            button.getAttribute('data-title') || ''
        ].join(' ') : '';
        const playerClass = player ? String(player.className || '') : '';
        const bodyClass = String(document.body ? document.body.className : '');
        const webFullscreen = /退出网页全屏|取消网页全屏/.test(buttonLabel) ||
            /bpx-state-webscreen|bpx-state-web-fullscreen|bilibili-player-video-web-fullscreen/.test(playerClass + ' ' + bodyClass);
        return {
          url: location.href,
          exists: !!v,
          ended: v ? v.ended : false,
          paused: v ? v.paused : true,
          duration: v ? v.duration : 0,
          currentTime: v ? v.currentTime : 0,
          readyState: v ? v.readyState : 0,
          fullscreen: webFullscreen
        };
    """
    try:
        while module_enabled and is_playing and current_bv == bv:
            await asyncio.sleep(1)

            if driver is None:
                continue

            try:
                # 超时后保留同一个任务，不重复提交新的 Selenium 线程任务。
                # asyncio.wait 不会取消后台线程；若 WebDriver 卡住，只允许这一条状态读取继续等待。
                if status_task is None:
                    status_task = asyncio.create_task(
                        asyncio.to_thread(execute_script_sync, status_script)
                    )

                wait_limit = 5.0 if status_timeout_count == 0 else 1.0
                done, _ = await asyncio.wait({status_task}, timeout=wait_limit)
                if not done:
                    status_timeout_count += 1
                    continue

                completed_task = status_task
                status_task = None
                status_timeout_count = 0
                status = completed_task.result()
                if module_paused:
                    if last_obs_visible:
                        await set_obs_visibility_async(False)
                        last_obs_visible = False
                    continue
            except (NoSuchWindowException, InvalidSessionIdException) as exc:
                # 当前视频标签页被用户手动关闭，或关闭标签页时导致 WebDriver 会话失效。
                # 将当前视频从播放状态中清除，再恢复浏览器并播放队列下一条。
                logger.warning("检测到当前视频标签页/浏览器会话已关闭（%s）：%s", bv, exc)
                await set_obs_visibility_async(False)
                if current_bv == bv and is_playing:
                    is_playing = False
                    current_bv = None
                    try:
                        await asyncio.to_thread(ensure_browser_window)
                        logger.info("浏览器已恢复，准备播放队列中的下一条视频")
                    except Exception as recovery_exc:
                        logger.exception("恢复浏览器失败：%s", recovery_exc)
                    await play_next()
                return
            except Exception as exc:
                # 某些 Selenium 版本会把标签页关闭报告为通用 WebDriver 异常。
                # 只在错误文本明显指向窗口/会话已关闭时执行恢复，避免把普通短暂错误误判为关闭。
                error_text = str(exc).lower()
                closed_markers = ("no such window", "target window already closed", "invalid session id", "web view not found")
                if any(marker in error_text for marker in closed_markers):
                    logger.warning("检测到视频标签页/浏览器会话已关闭（%s）：%s", bv, exc)
                    await set_obs_visibility_async(False)
                    if current_bv == bv and is_playing:
                        is_playing = False
                        current_bv = None
                        try:
                            await asyncio.to_thread(ensure_browser_window)
                            logger.info("浏览器已恢复，准备播放队列中的下一条视频")
                        except Exception as recovery_exc:
                            logger.exception("恢复浏览器失败：%s", recovery_exc)
                        await play_next()
                    return
                logger.debug("读取视频播放状态失败（%s）：%s", bv, exc)
                continue

            # 注意：播放监控中绝不再次调用全屏函数。
            # 全屏只在 open_video_page() 中尝试一次，避免 W 开关反复切换。

            # B 站合集可能在当前视频结束后自动跳转到合集下一集。
            # 如果已经确认原视频开始播放，且页面 URL 已切换到其他视频，
            # 就把它视为当前点播结束，转而播放观众队列中的下一条。
            page_url = (status or {}).get("url", "")
            if page_url and f"/video/{bv}" not in page_url:
                if saw_video and saw_progress and current_bv == bv and is_playing:
                    logger.info(
                        "检测到当前点播页面已切换（可能是合集自动播放下一集）：%s；切换到点播队列下一条",
                        bv,
                    )
                    await set_obs_visibility_async(False)
                    is_playing = False
                    current_bv = None
                    # 队列为空时，不让 B 站合集继续自行播放下一集。
                    if not video_queue and driver is not None:
                        try:
                            await asyncio.wait_for(
                                asyncio.to_thread(
                                    execute_script_sync,
                                    "const v = document.querySelector('video'); if (v) v.pause(); window.stop();",
                                ),
                                timeout=5,
                            )
                        except Exception as exc:
                            logger.warning("停止合集自动播放失败：%s", exc)
                    await play_next()
                    return
                # 尚未确认原视频实际播放前，不把页面 URL 变化误判为播放结束。
                continue

            if not status or not status.get("exists"):
                if last_obs_visible:
                    await set_obs_visibility_async(False)
                    last_obs_visible = False
                continue

            duration = status.get("duration") or 0
            current_time = status.get("currentTime") or 0
            ready_state = status.get("readyState") or 0
            if ready_state < 2 or duration <= 0:
                if last_obs_visible:
                    await set_obs_visibility_async(False)
                    last_obs_visible = False
                continue

            if ready_state >= 2 and duration > 0:
                saw_video = True
                if current_time > last_time + 0.2:
                    saw_progress = True
                last_time = current_time

                # 只有视频真正处于播放状态时才显示 OBS 来源；暂停/结束时隐藏。
                actually_playing = not bool(status.get("paused")) and not bool(status.get("ended"))
                if actually_playing != last_obs_visible:
                    changed = await set_obs_visibility_async(actually_playing)
                    if changed:
                        last_obs_visible = actually_playing

                ended = bool(status.get("ended"))
                # ended 是主要判断；时间兜底用于少数 ended 状态未及时更新的情况。
                reached_end = (
                    saw_progress and duration > 10 and current_time >= duration - 1.0
                )
                if (ended or reached_end) and not module_paused:
                    logger.info("检测到视频播放结束：%s（%s/%s 秒）", bv, current_time, duration)
                    await set_obs_visibility_async(False)
                    last_obs_visible = False
                    if current_bv == bv and is_playing:
                        is_playing = False
                        current_bv = None
                        await play_next()
                    return

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("监控视频结束时发生异常：%s", bv)
    finally:
        if status_task is not None and not status_task.done():
            # 不能杀死已经运行的 Python 工作线程；保留任务并读取最终异常，避免未取回异常警告。
            status_task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
        if playback_monitor_task is asyncio.current_task():
            playback_monitor_task = None


async def play_next():
    """取出队列第一条视频并打开页面。"""

    global is_playing, current_bv, driver, playback_monitor_task

    async with play_lock:

        if not module_enabled or module_paused:
            return

        if is_playing:
            logger.info("已有视频正在处理，忽略重复播放请求")
            return

        if not video_queue:
            is_playing = False
            current_bv = None
            return

        bv = video_queue.pop(0)
        asyncio.create_task(ensure_video_title(bv))

        is_playing = True
        current_bv = bv

        url = f"https://www.bilibili.com/video/{bv}"

        try:
            logger.info("正在打开视频：%s", bv)

            # Selenium 的 driver.get / window_handles 等均为同步调用。
            # 必须放到工作线程，避免 Chrome 无响应时堵住 asyncio 事件循环。
            await asyncio.to_thread(open_video_page, bv, url)

            if not module_enabled:
                await asyncio.to_thread(_pause_video_for_close_sync)
                is_playing = False
                current_bv = None
                return

            # 暂停可能发生在 driver.get() 正在执行期间；页面加载完成后补充暂停，
            # 并继续创建监控任务，确保恢复后仍能监测当前视频结束。
            if module_paused:
                try:
                    await asyncio.to_thread(
                        execute_script_sync,
                        "const v=document.querySelector('video'); if(v){v.pause();}",
                    )
                except Exception as pause_exc:
                    logger.warning("视频加载期间补充暂停失败：%s", type(pause_exc).__name__)

            logger.info("视频页面已打开：%s", bv)

            # 若此前有旧视频监控任务，先取消；只保留当前 BV 的监控。
            if (
                playback_monitor_task is not None
                and playback_monitor_task is not asyncio.current_task()
                and not playback_monitor_task.done()
            ):
                playback_monitor_task.cancel()

            playback_monitor_task = asyncio.create_task(
                monitor_video_end(bv)
            )

        except Exception:
            logger.exception("打开视频失败：%s", bv)
            await set_obs_visibility_async(False)

            is_playing = False
            current_bv = None

            add_operation_message(
                f"视频打开失败：{bv}，正在尝试播放下一条"
            )

            # create_task 避免在持有 play_lock 时直接递归调用 play_next。
            if video_queue:
                asyncio.create_task(play_next())

@selenium_locked
def stop_current_page_sync():
    """在工作线程中暂停当前视频并停止页面加载，不关闭 Chrome 或其他标签页。"""
    if driver is None:
        return False

    ensure_browser_window()
    # pause() 停止视频播放，window.stop() 停止页面后续加载；不关闭标签页。
    driver.execute_script(
        "const v = document.querySelector('video'); "
        "if (v) { try { v.pause(); } catch (e) {} } "
        "window.stop();"
    )
    return True


async def skip_current_video():
    """统一处理管理员手动切换视频，供弹幕和网页控制面板共用。"""

    global is_playing, current_bv, playback_monitor_task

    # 如果另一个手动切换操作正在执行，不再重复切换。
    if skip_lock.locked():
        return {
            "ok": False,
            "message": "正在切换视频，请勿重复操作"
        }

    async with skip_lock:
        # 先取消当前视频的播放监控任务，避免旧监控任务同时触发自动切换。
        monitor_task = playback_monitor_task

        if monitor_task is not None:
            if (
                monitor_task is not asyncio.current_task()
                and not monitor_task.done()
            ):
                monitor_task.cancel()

                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug(
                        "等待旧播放监控任务结束时出现异常",
                        exc_info=True
                    )

            if playback_monitor_task is monitor_task:
                playback_monitor_task = None

        # 停止当前视频页面，不关闭 Chrome 或标签页。
        try:
            await asyncio.to_thread(stop_current_page_sync)
        except Exception:
            logger.warning(
                "手动切换时停止旧视频失败",
                exc_info=True
            )

        # 清理当前播放状态。
        is_playing = False
        current_bv = None

        # 隐藏 OBS 视频来源。
        await set_obs_visibility_async(False)

        # 启动队列中的下一条视频。
        try:
            await play_next()
        except Exception:
            logger.exception("手动切换后启动下一条视频失败")

            return {
                "ok": False,
                "message": "已尝试停止当前视频，但启动下一条视频失败"
            }

        if current_bv is not None:
            message = f"已切换到下一个视频：{current_bv}"
        else:
            message = "已停止当前视频，队列暂无待播放内容"

        logger.info("手动切换结果：%s", message)

        return {
            "ok": True,
            "message": message
        }

async def handle_panel_action(payload):
    """执行网页中的下一个、删除、清空操作。"""

    action = payload.get("action")

    if action == "next":
        return await skip_current_video()

    if action == "delete":
        try:
            index = int(payload.get("index")) - 1
        except (TypeError, ValueError):
            return {
                "ok": False,
                "message": "删除序号无效"
            }

        if index < 0 or index >= len(video_queue):
            return {
                "ok": False,
                "message": "该队列序号已不存在，请刷新后重试"
            }

        removed = video_queue.pop(index)

        return {
            "ok": True,
            "message": f"已删除：{video_titles.get(removed, removed)}"
        }

    if action == "clear":
        count = len(video_queue)
        video_queue.clear()

        return {
            "ok": True,
            "message": f"已清空待播放队列，共清除 {count} 条；当前视频不受影响"
        }

    return {
        "ok": False,
        "message": "不支持的操作"
    }


ORDER_PATTERN = re.compile(
    r"点播\s*(BV[0-9A-Za-z]{10})",
    re.IGNORECASE,
)



async def on_dan_mu(msg_json):
    """处理直播间弹幕事件。"""

    global is_playing, current_bv, driver

    if not module_enabled or module_paused or not isinstance(msg_json, dict):
        return
    if str(msg_json.get("type", "")) != "danmaku":
        return
    try:
        uid = int(msg_json.get("uid", 0) or 0)
    except (TypeError, ValueError):
        uid = 0
    uname = str(msg_json.get("nickname", "") or "观众")
    com = str(msg_json.get("text", "") or "").strip()
    if not com:
        return

    # ---------- 1. 管理员切换下一条 ----------

    if com == "下一个":
        if uid not in ADMIN_UIDS:
            logger.info(
                "忽略普通观众的下一个指令：%s（UID: %s）",
                uname,
                uid
            )
            return

        logger.info(
            "管理员执行弹幕切换下一条：%s（UID: %s）",
            uname,
            uid
        )

        result = await skip_current_video()

        if result["ok"]:
            add_operation_message(result["message"])
        else:
            logger.info("管理员弹幕切换未执行：%s", result["message"])

        return

    # ---------- 2. 点播 ----------

    m_order = ORDER_PATTERN.fullmatch(com)
    if m_order:
        logger.info("【%s】%s (UID: %s)", uname, com, uid)
        bv = "BV" + m_order.group(1)[2:]

        # 重复视频优先判断：当前正在播放或已在待播队列中，都不重复添加。
        # 此检查放在冷却判断之前，重复点播会明确提示“已经在点播队列中”。
        if bv == current_bv or bv in video_queue:
            message = f"点播失败：{uname}，视频 {bv} 已经在点播队列中"
            logger.info(message)
            add_operation_message(message)
            return

        is_admin = uid in ADMIN_UIDS

        # 管理员 UID 始终跳过粉丝勋章校验。普通观众是否需要粉丝牌由 room_config.txt 控制。
        # REQUIRE_FAN_MEDAL=0 时跳过勋章查询，但冷却、时长、队列等其他限制仍然生效。
        if is_admin:
            logger.info("管理员点播：跳过粉丝勋章校验（UID: %s）", uid)
        elif REQUIRE_FAN_MEDAL:
            # 没有 Cookie、Cookie 无效、查询失败或数据不完整时一律拒绝点播，并记录原因。
            try:
                medal_allowed, medal_reason = await verify_fan_medal(uid)
            except Exception as exc:
                logger.warning(
                    "普通观众点播被拒绝：粉丝勋章校验发生异常（UID: %s，%s）",
                    uid,
                    type(exc).__name__
                )
                add_operation_message(f"{uname} 点播被拒绝：粉丝勋章校验失败")
                return

            if not medal_allowed:
                logger.info(
                    "普通观众点播被拒绝：%s（UID: %s）",
                    medal_reason,
                    uid
                )
                add_operation_message(f"{uname} 点播被拒绝：{medal_reason}")
                return

            # 网络校验期间模块可能已暂停或关闭，不能让过期弹幕在其后重新入队。
            if not module_enabled or module_paused:
                return

            logger.info(
                "粉丝勋章校验通过：用户拥有本直播间主播的粉丝勋章且当前已点亮（UID: %s）",
                uid
            )
        else:
            logger.info("当前配置不要求粉丝牌，跳过勋章校验（UID: %s）", uid)

        # 管理员 UID 不受每人 60 秒冷却限制；普通观众仍按原规则执行。
        if not is_admin:
            now = time.monotonic()
            previous_time = last_order_time.get(uid, 0)
            elapsed = now - previous_time

            if elapsed < COOLDOWN_SEC:
                remaining = max(1, int(COOLDOWN_SEC - elapsed))
                logger.info(
                    "忽略 %s 的点播请求：冷却中，还需 %s 秒",
                    uname,
                    remaining
                )
                add_operation_message(
                    f"{uname} 点播冷却中，还需要等待 {remaining} 秒"
                )
                return

        if len(video_queue) >= MAX_QUEUE_SIZE:
            logger.info("忽略 %s 的点播请求：队列已满", uname)
            add_operation_message(
                f"{uname} 点播失败：队列已满（最多 {MAX_QUEUE_SIZE} 条）"
            )
            return

        # 先通过 B 站接口核验视频是否存在，并获取时长。
        try:
            title, duration = await ensure_video_info(bv)
        except Exception:
            message = (
                f"点播失败：{uname}，无法获取视频信息，请检查 BV 号是否正确"
            )
            logger.info(message)
            add_operation_message(message)
            return

        if not module_enabled or module_paused:
            return

        if duration <= 0:
            message = f"点播失败：{uname}，无法确认视频时长（{bv}）"
            logger.info(message)
            add_operation_message(message)
            return

        # 管理员 UID 不受 300 秒视频时长限制；普通观众仍最多 5 分钟。
        if not is_admin and duration > MAX_VIDEO_DURATION_SEC:
            minutes, seconds = divmod(duration, 60)
            message = (
                f"点播失败：{uname}，视频时长为 {minutes} 分 {seconds} 秒，"
                f"超过 5 分钟限制（{bv}）"
            )
            logger.info(message)
            add_operation_message(message)
            return

        # 再检查一次，防止在获取视频信息期间状态已发生变化。
        if bv == current_bv or bv in video_queue:
            message = f"点播失败：{uname}，视频 {bv} 已经在点播队列中"
            logger.info(message)
            add_operation_message(message)
            return

        if not module_enabled or module_paused:
            return

        video_queue.append(bv)

        if not is_admin:
            last_order_time[uid] = time.monotonic()

        logger.info(
            "点播已加入待播放队列：%s（标题=%s，身份=%s，当前播放=%s，队列待播=%s）",
            bv,
            title,
            "管理员" if is_admin else "观众",
            current_bv or "无",
            len(video_queue)
        )

        add_operation_message(
            f"已加入点播队列：{title}（{bv}），当前排队：{len(video_queue)} 个"
        )

        if not is_playing:
            asyncio.create_task(play_next())

        return

    # ---------- 3. 查看队列 ----------

    if com == "查看队列":
        if not video_queue:
            logger.info(
                "观众请求查看队列：当前队列为空；当前播放：%s",
                current_bv or "无"
            )
        else:
            logger.info(
                "观众请求查看队列：当前播放=%s，排队=%s",
                current_bv or "无",
                list(video_queue)
            )

        return

    # 其他弹幕指令不处理。


class VideoModuleFacade:
    """视频模块生命周期与统一事件总线适配器。"""

    def __init__(self, event_bus, config: dict[str, Any], cookie_provider=None, room_id: int = 0):
        self.event_bus = event_bus
        self.config = dict(config)
        self.cookie_provider = cookie_provider
        self.room_id = max(0, int(room_id or 0))
        configure_video_module(self.config, None, self.room_id)
        self.event_bus.subscribe("danmaku", on_dan_mu)

    async def start(self):
        global driver, module_enabled, module_paused
        configure_video_module(self.config, self.cookie_provider, self.room_id)
        if module_enabled:
            if module_paused:
                await self.resume()
            return
        await set_obs_visibility_async(False)
        driver = await asyncio.to_thread(create_browser)
        module_enabled = True
        module_paused = False
        logger.info("视频模块已启动，Chrome 播放会话已初始化。")

    async def pause(self):
        global module_paused
        if not module_enabled or module_paused:
            return
        # 先标记暂停，阻止事件和播放任务继续启动下一条视频。
        module_paused = True
        try:
            if current_bv and driver is not None:
                await asyncio.to_thread(
                    execute_script_sync,
                    "const v=document.querySelector('video'); if(v){v.pause();}",
                )
        except Exception:
            module_paused = False
            raise
        logger.info("视频模块已暂停，保留当前 BV、播放进度和待播队列。")

    async def resume(self):
        global module_paused
        if not module_enabled or not module_paused:
            return
        if current_bv and driver is not None:
            await asyncio.to_thread(
                execute_script_sync,
                "const v=document.querySelector('video'); if(v){const p=v.play(); if(p&&p.catch)p.catch(()=>{});}",
            )
            module_paused = False
        else:
            module_paused = False
            if video_queue:
                await play_next()
        logger.info("视频模块已恢复。")

    async def close(self):
        global driver, module_enabled, module_paused, is_playing, current_bv, playback_monitor_task
        module_enabled = False
        module_paused = False
        monitor_task = playback_monitor_task
        playback_monitor_task = None
        if monitor_task is not None and not monitor_task.done():
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("关闭视频监控任务时发生异常。", exc_info=True)
        if driver is not None:
            try:
                await asyncio.to_thread(_pause_video_for_close_sync)
            except Exception as exc:
                logger.warning("暂停当前视频失败：%s", type(exc).__name__)
        try:
            await set_obs_visibility_async(False)
        except Exception as exc:
            logger.warning("隐藏 OBS 视频来源失败：%s", type(exc).__name__)
        video_queue.clear()
        is_playing = False
        current_bv = None
        if driver is not None:
            old_driver = driver
            driver = None
            try:
                await asyncio.to_thread(_quit_browser_sync, old_driver)
            except Exception as exc:
                logger.warning("关闭 Chrome 会话失败：%s", type(exc).__name__)
        logger.info("视频模块已关闭，Chrome 会话已释放，待播队列已清空。")

    def update_room_id(self, room_id: int) -> None:
        global roomid, _streamer_uid_cache, _streamer_uid_cache_at
        self.room_id = max(0, int(room_id or 0))
        roomid = self.room_id
        _streamer_uid_cache = None
        _streamer_uid_cache_at = 0.0

    def reload_config(self, config: dict[str, Any]) -> None:
        if module_enabled or driver is not None:
            raise RuntimeError("请先关闭视频模块，再修改视频配置。")
        self.config = dict(config)
        configure_video_module(self.config, None, self.room_id)

    def reload_obs_settings(self) -> None:
        global OBS_CONFIG
        OBS_CONFIG = load_obs_config()

    def queue_snapshot(self) -> dict[str, Any]:
        snapshot = build_queue_snapshot()
        snapshot.update({"enabled": module_enabled, "paused": module_paused})
        return snapshot

    async def panel_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await handle_panel_action(payload)

    @property
    def status(self) -> dict[str, Any]:
        return {
            "enabled": module_enabled,
            "paused": module_paused,
            "current_bv": current_bv,
            "queue_count": len(video_queue),
        }


