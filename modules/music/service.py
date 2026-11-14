# -*- coding: utf-8 -*-
"""整合后的歌曲点播模块。保留原项目匹配、播放核验、队列和权限业务逻辑。"""
from __future__ import annotations

import asyncio
import ast
import html
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
import uuid
import webbrowser
import threading
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Optional

import requests

from core.config_manager import ConfigManager
from core.paths import get_base_dir
from core.blocking import run_blocking

log = logging.getLogger("bili_live_danmaku.music")
BASE_DIR = get_base_dir()
CONFIG_MANAGER = ConfigManager()
_MUSIC_CONFIG = CONFIG_MANAGER.load("music", {})
LX_API = str(_MUSIC_CONFIG.get("lx_music", {}).get("base_url", "http://127.0.0.1:23330")).rstrip("/")
HTTP_TIMEOUT = 8
MEDAL_CACHE_SECONDS = 60
MEDAL_CACHE_MAX_ENTRIES = 2048
MEDAL_CACHE_PRUNE_INTERVAL = 30.0
ROOM_ID = 0
COOKIE_DICT: dict[str, str] = {}
MEDAL_CACHE: OrderedDict[int, tuple[float, bool, str]] = OrderedDict()
MEDAL_CACHE_LOCK = threading.RLock()
_MEDAL_CACHE_LAST_PRUNE = 0.0
ADMIN_UIDS: set[int] = set()
REQUIRE_FANS_MEDAL = bool(_MUSIC_CONFIG.get("requests", {}).get("require_fans_medal", False))
COOLDOWN_SECONDS = max(0, int(_MUSIC_CONFIG.get("requests", {}).get("cooldown_seconds", 10)))
QUEUE_LIMIT = max(1, int(_MUSIC_CONFIG.get("requests", {}).get("queue_limit", 100)))
SEARCH_LIMIT = min(20, max(1, int(_MUSIC_CONFIG.get("search", {}).get("max_results_per_platform", 20))))
DEFAULT_ENABLED_PROVIDERS = ("tx", "kg", "wy", "kw")
ENABLED_PROVIDERS = tuple(
    key for key in DEFAULT_ENABLED_PROVIDERS
    if key in (_MUSIC_CONFIG.get("search", {}).get("enabled_platforms") or DEFAULT_ENABLED_PROVIDERS)
) or DEFAULT_ENABLED_PROVIDERS
STATUS_TITLE_KEYS = ("name", "title", "song", "songName", "musicName", "trackName", "music_name")
STATUS_ARTIST_KEYS = ("singer", "artist", "artists", "artistName", "singerName", "author", "musicArtist", "music_artist")



def _clear_medal_cache() -> None:
    global _MEDAL_CACHE_LAST_PRUNE
    with MEDAL_CACHE_LOCK:
        MEDAL_CACHE.clear()
        _MEDAL_CACHE_LAST_PRUNE = 0.0


def _get_medal_cache(uid: int, now: float) -> tuple[float, bool, str] | None:
    global _MEDAL_CACHE_LAST_PRUNE
    with MEDAL_CACHE_LOCK:
        if now - _MEDAL_CACHE_LAST_PRUNE >= MEDAL_CACHE_PRUNE_INTERVAL:
            expired = [
                cached_uid for cached_uid, item in MEDAL_CACHE.items()
                if now - item[0] >= MEDAL_CACHE_SECONDS
            ]
            for cached_uid in expired:
                MEDAL_CACHE.pop(cached_uid, None)
            _MEDAL_CACHE_LAST_PRUNE = now
        cached = MEDAL_CACHE.get(uid)
        if cached is None:
            return None
        if now - cached[0] >= MEDAL_CACHE_SECONDS:
            MEDAL_CACHE.pop(uid, None)
            return None
        MEDAL_CACHE.move_to_end(uid)
        return cached


def _store_medal_cache(uid: int, value: tuple[float, bool, str]) -> None:
    with MEDAL_CACHE_LOCK:
        MEDAL_CACHE[uid] = value
        MEDAL_CACHE.move_to_end(uid)
        while len(MEDAL_CACHE) > MEDAL_CACHE_MAX_ENTRIES:
            MEDAL_CACHE.popitem(last=False)


def _require_fans_medal(value: Any) -> bool:
    """兼容旧版 0/1，同时统一为布尔值：true=要求，false=不要求。"""
    if isinstance(value, bool):
        return value
    if value == 0 or (isinstance(value, str) and value.strip() == "0"):
        return True
    if value == 1 or (isinstance(value, str) and value.strip() == "1"):
        return False
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return False


def configure_music_module(config: dict[str, Any], cookie_provider=None, room_id: int = 0) -> None:
    """由主程序注入普通配置和统一登录态；本模块不读取/保存 Cookie。"""
    global LX_API, HTTP_TIMEOUT, ROOM_ID, COOKIE_DICT, REQUIRE_FANS_MEDAL
    global COOLDOWN_SECONDS, QUEUE_LIMIT, SEARCH_LIMIT, ADMIN_UIDS, ENABLED_PROVIDERS
    lx = config.get("lx_music", {}) if isinstance(config.get("lx_music"), dict) else {}
    req = config.get("requests", {}) if isinstance(config.get("requests"), dict) else {}
    search = config.get("search", {}) if isinstance(config.get("search"), dict) else {}
    LX_API = str(lx.get("base_url", "http://127.0.0.1:23330")).rstrip("/")
    HTTP_TIMEOUT = max(2, int(lx.get("timeout_seconds", 5)))
    ROOM_ID = max(0, int(room_id or 0))
    REQUIRE_FANS_MEDAL = _require_fans_medal(req.get("require_fans_medal", False))
    COOLDOWN_SECONDS = max(0, int(req.get("cooldown_seconds", 10)))
    QUEUE_LIMIT = max(1, int(req.get("queue_limit", 100)))
    SEARCH_LIMIT = min(20, max(1, int(search.get("max_results_per_platform", 20))))
    raw_enabled = search.get("enabled_platforms", list(DEFAULT_ENABLED_PROVIDERS))
    if isinstance(raw_enabled, (list, tuple, set)):
        selected = tuple(key for key in DEFAULT_ENABLED_PROVIDERS if key in raw_enabled)
        ENABLED_PROVIDERS = selected or DEFAULT_ENABLED_PROVIDERS
    else:
        ENABLED_PROVIDERS = DEFAULT_ENABLED_PROVIDERS
    ADMIN_UIDS = set()
    configured_uids = config.get("admin_uids", [])
    if isinstance(configured_uids, list):
        for value in configured_uids:
            try:
                parsed_uid = int(value)
                if parsed_uid > 0:
                    ADMIN_UIDS.add(parsed_uid)
            except (TypeError, ValueError):
                continue
    values = cookie_provider() if callable(cookie_provider) else {}
    COOKIE_DICT = {
        "SESSDATA": str(values.get("sessdata", "")),
        "bili_jct": str(values.get("bili_jct", "")),
        "buvid3": str(values.get("buvid3", "")),
        "buvid4": str(values.get("buvid4", "")),
        "DedeUserID": str(values.get("dedeuserid", "")),
    }
    _clear_medal_cache()


def update_music_room_id(room_id: int) -> None:
    global ROOM_ID
    ROOM_ID = max(0, int(room_id or 0))
    _clear_medal_cache()


def _lx_control(action: str, timeout: float | None = None) -> tuple[bool, str]:
    # 洛雪官方开放 API：播放使用 /play，暂停使用 /pause；没有独立停止接口。
    endpoint = {"pause": "/pause", "resume": "/play", "stop": "/pause"}.get(action)
    if not endpoint:
        return False, "不支持的洛雪控制操作"
    try:
        response = requests.get(LX_API + endpoint, timeout=HTTP_TIMEOUT if timeout is None else timeout)
        response.raise_for_status()
        if action == "stop":
            return True, "洛雪开放 API 没有停止接口；已暂停当前歌曲并结束机器人点播流程"
        return True, f"洛雪接口已接受 {action} 请求（HTTP {response.status_code}）"
    except Exception as exc:
        return False, f"洛雪 {action} 请求失败：{type(exc).__name__}"



def _find_medal_list(data: Any) -> Optional[list]:
    """仅在响应结构明确包含勋章列表时返回列表；无法确认则返回 None。"""
    if not isinstance(data, dict):
        return None
    for key in ("list", "medal_list", "fans_medal_list"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    # 有些响应把列表放在 data.data 下
    nested = data.get("data")
    if isinstance(nested, dict):
        return _find_medal_list(nested)
    return None


def _medal_matches_room(item: dict[str, Any], room_id: int) -> tuple[bool, Optional[bool]]:
    """返回(房间匹配, 是否明确点亮)。缺少点亮字段视为无法确认。"""
    candidates = [item]
    for key in ("medal", "medal_info", "fans_medal", "target_medal"):
        value = item.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    room_values = []
    light_values = []
    for obj in candidates:
        for key in ("roomid", "room_id", "target_roomid", "target_room_id"):
            if key in obj and obj[key] not in (None, ""):
                try:
                    room_values.append(int(obj[key]))
                except (TypeError, ValueError):
                    pass
        for key in ("is_light", "is_lighted", "light_status", "is_wearing"):
            if key in obj:
                val = obj[key]
                if isinstance(val, bool):
                    light_values.append(val)
                elif str(val).strip() in ("0", "1"):
                    light_values.append(str(val).strip() == "1")
    room_matches = room_id in room_values
    # 若同一条记录中任一明确点亮字段为 false，则按未点亮处理；必须明确 true 才通过。
    is_lit: Optional[bool] = None
    if light_values:
        is_lit = all(light_values)
    return room_matches, is_lit


def check_fan_medal_sync(uid: int) -> tuple[bool, str]:
    """检查当前直播间粉丝牌且处于点亮状态。失败关闭，绝不因接口异常放行。"""
    if not COOKIE_DICT.get("SESSDATA"):
        return False, "主程序登录凭据缺少有效的 SESSDATA，无法核验粉丝牌"
    now = time.time()
    cached = _get_medal_cache(uid, now)
    if cached:
        return cached[1], cached[2]
    try:
        # 每次查询使用独立 Session；不同观众的粉丝牌核验会在线程池并发执行，
        # 不共享 requests.Session，避免 CookieJar/连接状态的跨线程竞争。
        with requests.Session() as session:
            session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132 Safari/537.36",
                "Referer": "https://live.bilibili.com/",
            })
            session.cookies.update({k: v for k, v in COOKIE_DICT.items() if v})
            response = session.get(
                "https://api.live.bilibili.com/xlive/web-ucenter/user/MedalWall",
                params={"target_id": uid}, timeout=HTTP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            reason = "粉丝牌接口返回异常，无法确认粉丝牌状态"
            _store_medal_cache(uid, (now, False, reason))
            return False, reason
        medals = _find_medal_list(payload["data"])
        if medals is None:
            reason = "粉丝牌接口缺少完整勋章列表，拒绝点歌"
            _store_medal_cache(uid, (now, False, reason))
            return False, reason
        found_room_medal = False
        found_lit_medal = False
        for item in medals:
            if not isinstance(item, dict):
                continue
            matches, is_lit = _medal_matches_room(item, ROOM_ID)
            if matches:
                found_room_medal = True
                if is_lit is True:
                    found_lit_medal = True
                    break
        if found_lit_medal:
            result = (now, True, "已确认拥有本直播间且已点亮的粉丝牌")
        elif found_room_medal:
            result = (now, False, "检测到本直播间粉丝牌，但无法确认其处于点亮状态")
        else:
            result = (now, False, "未找到本直播间粉丝牌")
        _store_medal_cache(uid, result)
        return result[1], result[2]
    except Exception as exc:
        reason = f"粉丝牌查询失败：{type(exc).__name__}，拒绝点歌"
        _store_medal_cache(uid, (now, False, reason))
        log.warning("UID %s %s", uid, reason)
        return False, reason


async def check_fan_medal(uid: int) -> tuple[bool, str]:
    return await run_blocking(check_fan_medal_sync, uid)

def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def get_status_text(status: dict[str, Any], keys: tuple[str, ...]) -> str:
    """从洛雪状态 API 的常见字段名及嵌套对象中读取文本字段。"""
    wanted = {key.casefold() for key in keys}
    stack: list[dict[str, Any]] = [status]
    seen: set[int] = set()
    while stack:
        obj = stack.pop(0)
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        for key, value in obj.items():
            if str(key).casefold() in wanted and isinstance(value, (str, int, float)):
                text_value = str(value).strip()
                if text_value:
                    return text_value
        for value in obj.values():
            if isinstance(value, dict):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(x for x in value if isinstance(x, dict))
    return ""


STATUS_TITLE_KEYS = ("name", "title", "song", "songName", "musicName", "trackName", "music_name")
STATUS_ARTIST_KEYS = ("singer", "artist", "artists", "artistName", "singerName", "author", "musicArtist", "music_artist")


def parse_request(text: str) -> Optional[tuple[str, str]]:
    """解析点播指令。

    支持：
    1. 点歌 歌名
    2. 点歌 歌名-歌手
    3. 点歌 歌名－歌手

    未使用分隔符时，整段内容作为歌名，避免多词歌名被错误拆分。
    """
    content = text.strip()
    log.info("解析点播指令 %s", content)
    if not content.startswith("点歌"):
        return None

    body = content[len("点歌"):].strip()
    if not body:
        return None

    # 使用最后一个横杠分隔歌名和歌手
    for separator in ("-", "－"):
        if separator in body:
            title, artist = body.rsplit(separator, 1)
            title = title.strip()
            artist = artist.strip()

            if title and artist:
                return title, artist

    # 没有有效分隔符时，整段作为歌名
    return body, ""


BAD_VERSION_PATTERN = re.compile(
    r"live|dj|r&b版|cover|翻唱|新版|合唱|空灵鼓|练习曲|"
    r"remix|mix|片段|现场|串烧|dj版|慢摇|演唱会|伴奏|"
    r"纯音乐|instrumental",
    re.IGNORECASE,
)

SEARCH_LIMIT = min(20, max(1, SEARCH_LIMIT))


def compact_music_name(value: str) -> str:
    """与搜索测试工具保持一致：忽略空白，统一中英文括号，保留其他标点。"""
    value = str(value or "")
    value = re.sub(r"\s+", "", value)
    value = value.translate(str.maketrans({"（": "(", "）": ")"}))
    return value.casefold()


def is_bad_version(item: dict[str, Any], requested_title: str = "") -> bool:
    """过滤非目标版本；用户明确指定的版本标记不应被过滤。"""
    candidate_title = str(item.get("title", ""))
    artist = str(item.get("artist", ""))

    requested_markers = {
        compact_music_name(match.group(0))
        for match in BAD_VERSION_PATTERN.finditer(str(requested_title or ""))
    }
    candidate_markers = {
        compact_music_name(match.group(0))
        for match in BAD_VERSION_PATTERN.finditer(candidate_title)
    }
    # 用户明确指定的版本标记可以保留，但候选中其他未指定的禁用版本标记
    # 仍然过滤；歌手名称命中禁用词也不会因歌名中的指定版本而豁免。
    if candidate_markers - requested_markers:
        return True
    return bool(BAD_VERSION_PATTERN.search(artist))


def _music_get_json(
    url: str,
    *,
    referer: str = "https://www.kuwo.cn/",
    verify: bool = True,
    allow_python_literal: bool = False,
) -> Any:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/132 Safari/537.36"
        ),
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    }
    response = requests.get(url, headers=headers, timeout=10, verify=verify)
    response.raise_for_status()
    text = response.text.strip()

    if text.startswith("{") or text.startswith("["):
        try:
            return response.json()
        except ValueError:
            if not allow_python_literal:
                raise

    left, right = text.find("("), text.rfind(")")
    if left >= 0 and right > left:
        content = text[left + 1:right]
        try:
            return json.loads(content)
        except ValueError:
            if allow_python_literal:
                return ast.literal_eval(content)
            raise

    try:
        return json.loads(text)
    except ValueError:
        if allow_python_literal:
            return ast.literal_eval(text)
        raise


def _types_default() -> list[dict[str, Any]]:
    return [{"type": "128k", "size": None}]


# QQ音乐（tx）
def search_qq(keyword: str) -> list[dict[str, Any]]:
    """使用 QQ 音乐当前网页端搜索接口；匿名搜索，无需登录 Cookie。"""
    payload = {
        "music.search.SearchCgiService": {
            "module": "music.search.SearchCgiService",
            "method": "DoSearchForQQMusicDesktop",
            "param": {
                "search_type": 0,
                "query": keyword,
                "page_num": 1,
                "num_per_page": SEARCH_LIMIT,
            },
        },
    }
    response = requests.post(
        "https://u.y.qq.com/cgi-bin/musicu.fcg",
        json=payload,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36"
            ),
            "Referer": "https://y.qq.com/",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(
            f"QQ 音乐搜索接口返回格式异常：type={type(data).__name__}"
        )

    req = data.get("req_1")
    if not isinstance(req, dict):
        # 兼容接口偶尔按 module 名称作为响应键的格式。
        req = data.get("music.search.SearchCgiService")
    if not isinstance(req, dict):
        raise ValueError("QQ 音乐搜索响应缺少 req_1 数据")

    top_code = data.get("code")
    module_code = req.get("code")
    if top_code not in (None, 0) or module_code not in (None, 0):
        raise RuntimeError(
            f"QQ 音乐搜索业务失败：code={top_code!r}, req_code={module_code!r}"
        )

    req_data = req.get("data")
    if not isinstance(req_data, dict):
        req_data = {}
    body = req_data.get("body")
    if not isinstance(body, dict):
        body = {}
    song_data = body.get("song")
    if not isinstance(song_data, dict):
        song_data = {}
    rows = song_data.get("list")
    if not isinstance(rows, list):
        rows = []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # 当前 QQ 搜索接口的歌曲标题字段为 title，兼容旧字段名。
        title = str(row.get("title") or row.get("name") or row.get("songname") or "").strip()
        singers = row.get("singer", [])
        artist = "、".join(
            str(x.get("name", "")).strip()
            for x in singers if isinstance(x, dict) and x.get("name")
        ) if isinstance(singers, list) else ""
        song_id = str(row.get("mid") or row.get("songmid") or "").strip()
        file_info = row.get("file", {})
        if not isinstance(file_info, dict):
            file_info = {}
        media_mid = str(
            file_info.get("media_mid")
            or row.get("strMediaMid")
            or row.get("media_mid")
            or ""
        ).strip()
        album = row.get("album", {})
        if not isinstance(album, dict):
            album = {}
        album_mid = str(album.get("mid") or row.get("albummid") or "").strip()
        album_id = str(album.get("id") or row.get("albumid") or "")
        album_name = str(album.get("name") or row.get("albumname") or "").strip()
        if not title or not artist or not song_id:
            continue
        out.append({
            "title": title, "artist": artist, "source": "tx", "songmid": song_id,
            "strMediaMid": media_mid, "albumMid": album_mid, "albumId": album_id,
            "albumName": album_name,
            "interval": str(row.get("interval") or row.get("duration") or ""),
            "types": _types_default(), "_search_source": "tx",
        })
    # 再做一次数量上限保护，确保每个平台最多保留 SEARCH_LIMIT 条有效候选。
    return out[:SEARCH_LIMIT]


# 酷狗音乐（kg）
def search_kugou(keyword: str) -> list[dict[str, Any]]:
    url = "https://mobilecdn.kugou.com/api/v3/search/song?" + urllib.parse.urlencode({
        "format": "json", "keyword": keyword, "page": 1, "pagesize": SEARCH_LIMIT,
    })
    data = _music_get_json(url, referer="https://www.kugou.com/", verify=False)
    rows = ((data.get("data") or {}).get("info") or []) if isinstance(data, dict) else []
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = re.sub(r"<[^>]+>", "", str(row.get("songname", ""))).strip()
        artist = re.sub(r"<[^>]+>", "", str(row.get("singername", ""))).strip()
        song_hash = str(row.get("hash", "")).strip()
        if not title or not artist or not song_hash:
            continue
        out.append({
            "title": title, "artist": artist, "source": "kg", "songmid": song_hash,
            "hash": song_hash, "albumId": str(row.get("album_id", "")),
            "albumName": str(row.get("album_name", "")),
            "interval": str(row.get("duration", "")),
            "types": [{"type": "128k", "size": None, "hash": song_hash}],
            "_search_source": "kg",
        })
    return out


# 网易云音乐（wy）
def search_netease(keyword: str) -> list[dict[str, Any]]:
    url = "https://music.163.com/api/search/get/web?" + urllib.parse.urlencode({
        "s": keyword, "type": 1, "offset": 0, "total": "true", "limit": SEARCH_LIMIT,
    })
    data = _music_get_json(url, referer="https://music.163.com/")
    rows = ((data.get("result") or {}).get("songs") or []) if isinstance(data, dict) else []
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("name", "")).strip()
        artists = row.get("artists", row.get("ar", []))
        artist = "、".join(
            str(x.get("name", "")).strip()
            for x in artists if isinstance(x, dict) and x.get("name")
        ) if isinstance(artists, list) else ""
        song_id = str(row.get("id", "")).strip()
        album = row.get("album", row.get("al", {}))
        if not isinstance(album, dict):
            album = {}
        if not title or not artist or not song_id:
            continue
        out.append({
            "title": title, "artist": artist, "source": "wy", "songmid": song_id,
            "albumId": str(album.get("id", "")), "albumName": str(album.get("name", "")),
            "interval": str(row.get("duration", "")), "types": _types_default(),
            "_search_source": "wy",
        })
    return out


# 酷我音乐（kw）
# 酷我网页搜索已改为带会话签名的接口。旧 search.kuwo.cn/r.s 在部分网络环境
# 会返回空响应或拦截页，不能再依赖 JSON/Python 字面量解析兜底。
_KUWO_HOME = "https://www.kuwo.cn/"
_KUWO_SEARCH_URL = "https://www.kuwo.cn/search/searchMusicBykeyWord"
_KUWO_SESSION_COOKIE = "Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324"
_KUWO_SESSION_TTL = 30 * 60
_KUWO_SESSION = requests.Session()
_KUWO_SESSION_LOCK = threading.RLock()
_KUWO_SESSION_VALUE = ""
_KUWO_SESSION_EXPIRES = 0.0
_KUWO_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


def _kuwo_valid_session_cookie(value: str) -> bool:
    return 16 <= len(value) <= 128 and all(ch.isascii() and ch.isalnum() for ch in value)


def _kuwo_build_secret(cookie: str, nonce: int) -> str:
    state = 59910100
    encoded = bytearray()
    for value in cookie.encode("ascii"):
        state = (state * 9253 + 23) % 2147483647
        encoded.append(value ^ (state * 255 // 2147483647))
    return encoded.hex() + f"{nonce:08x}"


def _kuwo_get_search_payload(keyword: str) -> dict[str, Any]:
    """通过酷我当前网页搜索协议获取 JSON，自动刷新失效会话一次。"""
    global _KUWO_SESSION_VALUE, _KUWO_SESSION_EXPIRES

    with _KUWO_SESSION_LOCK:
        for attempt in range(2):
            if not _kuwo_valid_session_cookie(_KUWO_SESSION_VALUE) or time.time() >= _KUWO_SESSION_EXPIRES:
                home_response = _KUWO_SESSION.get(
                    _KUWO_HOME,
                    headers={"User-Agent": _KUWO_USER_AGENT},
                    timeout=10,
                )
                home_response.raise_for_status()
                cookie = (
                    home_response.cookies.get(_KUWO_SESSION_COOKIE)
                    or _KUWO_SESSION.cookies.get(_KUWO_SESSION_COOKIE)
                    or ""
                )
                if not _kuwo_valid_session_cookie(cookie):
                    raise RuntimeError(
                        "酷我首页未下发有效搜索会话 Cookie，可能暂时无法访问酷我接口"
                    )
                _KUWO_SESSION_VALUE = cookie
                _KUWO_SESSION_EXPIRES = time.time() + _KUWO_SESSION_TTL

            nonce = secrets.randbelow(90000000) + 10000000
            params = {
                "vipver": "1",
                "client": "kt",
                "ft": "music",
                "cluster": "0",
                "strategy": "2012",
                "encoding": "utf8",
                "rformat": "json",
                "mobi": "1",
                "issubtitle": "1",
                "show_copyright_off": "1",
                "pn": "0",
                "rn": str(SEARCH_LIMIT),
                "all": keyword,
                "reqId": str(uuid.uuid4()),
            }
            headers = {
                "User-Agent": _KUWO_USER_AGENT,
                "Referer": "https://www.kuwo.cn/search/list?key="
                + urllib.parse.quote(keyword, safe=""),
                "Secret": _kuwo_build_secret(_KUWO_SESSION_VALUE, nonce),
            }
            response = _KUWO_SESSION.get(
                _KUWO_SEARCH_URL,
                params=params,
                headers=headers,
                cookies={_KUWO_SESSION_COOKIE: _KUWO_SESSION_VALUE},
                timeout=10,
            )
            body = response.text.strip()
            illegal = "the request is illegal" in body.lower()
            if response.status_code in (401, 403) or illegal:
                _KUWO_SESSION_VALUE = ""
                _KUWO_SESSION_EXPIRES = 0.0
                if attempt == 0:
                    continue
            response.raise_for_status()
            if not body:
                raise ValueError(
                    f"酷我搜索接口返回空内容（HTTP {response.status_code}）"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                content_type = response.headers.get("Content-Type", "未知")
                preview = body[:160].replace("\\r", " ").replace("\\n", " ")
                raise ValueError(
                    f"酷我搜索接口未返回有效 JSON（HTTP {response.status_code}，"
                    f"Content-Type={content_type}，响应开头={preview!r}）"
                ) from exc
            if not isinstance(payload, dict):
                raise ValueError("酷我搜索接口返回的数据结构不是对象")
            return payload

        raise RuntimeError("酷我搜索会话刷新后仍被酷我接口拒绝")


def search_kuwo(keyword: str) -> list[dict[str, Any]]:
    data = _kuwo_get_search_payload(keyword)
    data_section = data.get("data") if isinstance(data.get("data"), dict) else {}
    rows = data_section.get("list") or data.get("abslist") or []
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = html.unescape(str(row.get("name") or row.get("SONGNAME") or "")).strip()
        artist = html.unescape(str(row.get("artist") or row.get("ARTIST") or "")).strip()
        song_id = str(
            row.get("rid") or row.get("MUSICRID") or row.get("id")
            or row.get("MUSICID") or ""
        ).strip()
        if song_id.upper().startswith("MUSIC_"):
            song_id = song_id[6:]
        if not title or not artist or not song_id:
            continue
        out.append({
            "title": title, "artist": artist, "source": "kw", "songmid": song_id,
            "albumId": str(row.get("albumid") or row.get("ALBUMID") or ""),
            "albumName": html.unescape(str(row.get("album") or row.get("ALBUM") or "")).strip(),
            "interval": str(
                row.get("duration") or row.get("songTimeMinutes") or row.get("DURATION") or ""
            ),
            "types": _types_default(),
            "_search_source": "kw",
        })
    return out


PROVIDERS = (
    ("QQ音乐", "tx", search_qq),
    ("酷狗音乐", "kg", search_kugou),
    ("网易云音乐", "wy", search_netease),
    ("酷我音乐", "kw", search_kuwo),
)


def match_candidates(
    candidates: list[dict[str, Any]], title: str, artist: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """先按歌名匹配等级分组，再在组内优先选择歌手完全匹配项。

    返回值第一组为歌名完全匹配候选，第二组为候选歌名包含点播歌名的候选。
    每组内部均按歌手完全匹配优先排序；不接受点播歌名包含候选歌名的反向匹配。
    """
    wanted_title = compact_music_name(title)
    wanted_artist = compact_music_name(artist)
    exact_title: list[dict[str, Any]] = []
    contains_title: list[dict[str, Any]] = []
    if not wanted_title:
        return exact_title, contains_title

    exact_artist_exact_title: list[dict[str, Any]] = []
    contains_artist_exact_title: list[dict[str, Any]] = []
    exact_artist_contains_title: list[dict[str, Any]] = []
    contains_artist_contains_title: list[dict[str, Any]] = []

    for item in candidates:
        candidate_title = compact_music_name(str(item.get("title", "")))
        candidate_artist = compact_music_name(str(item.get("artist", "")))
        if not candidate_title:
            continue

        # 先确认歌名完全匹配或候选歌名包含点播歌名，再过滤不合适版本。
        # 歌名包含属于合法匹配，不应被误当成版本不匹配。
        if candidate_title == wanted_title:
            title_group = "exact"
        elif wanted_title in candidate_title:
            title_group = "contains"
        else:
            continue

        if is_bad_version(item, title):
            continue

        if not wanted_artist or candidate_artist == wanted_artist:
            artist_exact = True
        elif wanted_artist in candidate_artist:
            artist_exact = False
        else:
            continue

        if title_group == "exact":
            (exact_artist_exact_title if artist_exact else contains_artist_exact_title).append(item)
        else:
            (exact_artist_contains_title if artist_exact else contains_artist_contains_title).append(item)

    exact_title = exact_artist_exact_title + contains_artist_exact_title
    contains_title = exact_artist_contains_title + contains_artist_contains_title
    return exact_title, contains_title


def find_matching_songs(
    title: str,
    artist: str,
    after_source: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """按平台顺序搜索；命中当前平台的首个匹配后立即返回。

    after_source 用于播放核验失败后的延迟回退：从指定音源的下一个平台继续搜索，
    不会重复搜索已经尝试过的平台。
    """
    keyword = f"{title} {artist}".strip()
    errors: list[str] = []
    total_candidates = 0
    passed_source = not bool(after_source)

    for provider_name, source, search_func in PROVIDERS:
        if not passed_source:
            if source == after_source:
                passed_source = True
            continue
        if source not in ENABLED_PROVIDERS:
            continue
        candidates: list[dict[str, Any]] = []
        selected: Optional[dict[str, Any]] = None
        match_level = ""

        try:
            candidates = search_func(keyword)
            total_candidates += len(candidates)
            exact_title_matches, contains_title_matches = match_candidates(candidates, title, artist)
            selected = exact_title_matches[0] if exact_title_matches else (
                contains_title_matches[0] if contains_title_matches else None
            )
        except Exception as exc:
            # 完整关键词可能被上游接口拒绝（例如带空格的组合词）。
            # 仍继续尝试拆分后的纯歌名，不能因此跳过当前平台的后备匹配。
            errors.append(f"{source}:{type(exc).__name__}: {exc}")

        if not selected and not artist:
            parts = str(title or "").rsplit(None, 1)
            if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                fallback_title, fallback_artist = parts[0].strip(), parts[1].strip()
                try:
                    fallback_candidates = search_func(fallback_title)
                    total_candidates += len(fallback_candidates)
                    fallback_exact_title, fallback_contains_title = match_candidates(
                        fallback_candidates, fallback_title, fallback_artist
                    )
                    selected = fallback_exact_title[0] if fallback_exact_title else (
                        fallback_contains_title[0] if fallback_contains_title else None
                    )
                except Exception as exc:
                    errors.append(f"{source}(拆分搜索):{type(exc).__name__}: {exc}")
                    log.warning(
                        "音乐平台拆分歌名搜索失败：%s（%s），歌名=%s，原因=%s",
                        provider_name, source, fallback_title, exc,
                    )

        if selected:
            selected = dict(selected)
            log.info(
                "歌曲匹配成功：%s - %s（音源：%s）",
                selected.get("title", ""), selected.get("artist", ""), provider_name,
            )
            return [selected], f"已匹配音源：{provider_name}"

 
    if after_source and not passed_source:
        return [], f"未找到音源 {after_source}，无法继续后续平台搜索"
    if errors and total_candidates == 0:
        return [], "音乐源搜索请求失败（" + ", ".join(errors) + "）"
    if artist:
        return [], f"未找到歌名完全匹配且歌手完全匹配/包含“{artist}”的歌曲"
    return [], "未找到歌名完全匹配且通过版本过滤的歌曲"


def lx_scheme_play(song: dict[str, Any]) -> str:
    """用已验证的歌曲 ID 和音源调用官方 music/play Scheme，不让洛雪重新搜索选歌。"""
    info = song.get("music_info") if isinstance(song.get("music_info"), dict) else song
    payload: dict[str, Any] = {
        "name": str(info.get("title", info.get("name", song.get("title", "")))),
        "singer": str(info.get("artist", info.get("singer", song.get("artist", "")))),
        "source": str(info.get("source", "kw")),
        "songmid": str(info.get("songmid", info.get("id", ""))),
        "types": info.get("types") or [{"type": "128k", "size": None}],
    }
    for key in ("img", "albumId", "interval", "albumName", "hash", "strMediaMid", "albumMid", "copyrightId", "lrcUrl"):
        if info.get(key) not in (None, ""):
            payload[key] = info[key]
    if not payload["name"] or not payload["singer"] or not payload["songmid"]:
        raise ValueError("已匹配歌曲缺少播放所需的名称、歌手或歌曲 ID")
    encoded = urllib.parse.quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), safe="")
    return f"lxmusic://music/play?data={encoded}"


def play_song_in_lx(song: dict[str, Any]) -> bool:
    """通过官方 music/play Scheme 播放已匹配的具体歌曲。"""
    try:
        uri = lx_scheme_play(song)
        if os.name == "nt":
            os.startfile(uri)  # type: ignore[attr-defined]
        else:
            webbrowser.open(uri)
        return True
    except Exception:
        log.exception("调用已匹配歌曲播放失败：%s", song.get("title", "未知歌曲"))
        return False



def get_lx_status() -> Optional[dict[str, Any]]:
    """读取洛雪本地 API 状态，包含歌曲封面地址。"""
    for endpoint in ("/status", "/api/status"):
        try:
            response = requests.get(
                LX_API + endpoint,
                params={
                    "filter": (
                        "status,playStatus,state,playing,isPlaying,paused,isPaused,"
                        "name,singer,albumName,duration,progress,playbackRate,"
                        "picUrl,img,coverUrl,cover_url,lyricLineText"
                    )
                },
                timeout=2,
            )

            if response.ok:
                data = response.json()
                if isinstance(data, dict):
                    return data

        except Exception:
            continue

    return None




def get_lx_playback_state(status: Optional[dict[str, Any]]) -> str:
    """将洛雪状态归一为 playing / paused / stopped / unknown。"""
    if not isinstance(status, dict):
        return "unknown"

    raw = status.get("status", status.get("playStatus", status.get("state", "")))
    value = str(raw).strip().lower()
    if value in {"paused", "pause", "暂停", "已暂停"} or status.get("paused") is True or status.get("isPaused") is True:
        return "paused"
    if (
        value in {"playing", "play", "1", "true"}
        or status.get("playing") is True
        or status.get("isPlaying") is True
    ):
        return "playing"
    if value in {"stopped", "stop", "0", "false", "ended", "停止", "已停止"} or status.get("playing") is False:
        return "stopped"
    return "unknown"


def is_lx_playing(status: Optional[dict[str, Any]]) -> bool:
    """根据洛雪 /status 接口判断当前是否正在播放。"""
    return get_lx_playback_state(status) == "playing"


def get_lx_current_song_key(
    status: Optional[dict[str, Any]],
) -> Optional[tuple[str, str]]:
    """读取洛雪当前歌曲，仅在明确播放且歌名、歌手均存在时返回歌曲标识。"""
    if not isinstance(status, dict) or not is_lx_playing(status):
        return None

    # 你的洛雪 /status 返回的是顶层 name 和 singer。
    title = normalize_name(str(status.get("name", "") or ""))
    artist = normalize_name(str(status.get("singer", "") or ""))

    if not title or not artist:
        return None

    return title, artist

def verify_requested_song(
    song: dict[str, Any],
    before_status: Optional[dict[str, Any]] = None,
    timeout: float = 5.0,
    abort_event: Any = None,
) -> tuple[bool, str]:
    """核对洛雪实际播放结果，必须确认本次请求产生了新的播放变化。"""
    requested_title = normalize_name(str(song.get("title", "")))
    requested_artist = normalize_name(str(song.get("artist", "")))

    if not before_status:
        return False, "点歌前无法读取洛雪状态，不能确认本次播放是否成功，已拒绝本次点歌"

    before_title = normalize_name(
        str(before_status.get("name", "") or "")
    )
    before_artist = normalize_name(
        str(before_status.get("singer", "") or "")
    )
    before_playing = is_lx_playing(before_status)

    try:
        before_progress = float(before_status.get("progress"))
    except (TypeError, ValueError):
        before_progress = None

    deadline = time.monotonic() + timeout
    last_status: Optional[dict[str, Any]] = None
    saw_new_playback = False

    while time.monotonic() < deadline:
        if abort_event is not None and abort_event.is_set():
            return False, "检测到管理员切歌，已中止本次歌曲核验和音源回退"
        status = get_lx_status()

        if status:
            last_status = status

            raw_title = str(status.get("name", "") or "").strip()
            raw_artist = str(status.get("singer", "") or "").strip()

            actual_title = normalize_name(raw_title)
            actual_artist = normalize_name(raw_artist)

            playing = is_lx_playing(status)

            try:
                current_progress = float(status.get("progress"))
            except (TypeError, ValueError):
                current_progress = None

            title_matches = bool(actual_title and requested_title) and (
                actual_title == requested_title
                or requested_title in actual_title
                or actual_title in requested_title
            )

            artist_matches = True
            if requested_artist:
                artist_matches = bool(actual_artist) and (
                    actual_artist == requested_artist
                    or requested_artist in actual_artist
                    or actual_artist in requested_artist
                )

            # 判断是否出现了新的播放变化。
            changed_track = bool(
                actual_title
                and before_title
                and actual_title != before_title
            ) or bool(
                actual_artist
                and before_artist
                and actual_artist != before_artist
            )

            became_playing = not before_playing and playing

            # 同一首歌从较后进度重新开始，也视为播放发生变化。
            progress_reset = (
                bool(actual_title)
                and actual_title == before_title
                and current_progress is not None
                and before_progress is not None
                and current_progress + 3 < before_progress
            )

            fresh_playback = (
                changed_track
                or became_playing
                or progress_reset
            )

            if playing and fresh_playback:
                saw_new_playback = True

            if playing and title_matches and artist_matches and fresh_playback:
                if requested_artist:
                    return True, f"已确认本次播放成功：{raw_title} - {raw_artist}"

                song["actual_artist"] = raw_artist
                return True, (
                    f"已确认本次播放成功：{raw_title}"
                    + (f" - {raw_artist}" if raw_artist else "")
                )

            # 歌名相同但歌手不匹配，只有确认这是新播放的歌曲才暂停。
            if playing and fresh_playback and title_matches and requested_artist and not artist_matches:
                if abort_event is not None and abort_event.is_set():
                    return False, "检测到管理员切歌，已中止本次歌曲核验"
                ok, detail = pause_lx_playback()

                if ok:
                    return False, (
                        f"找到的歌曲歌手为“{raw_artist or '未知'}”，"
                        f"与要求的“{song.get('artist')}”不一致，已请求暂停。{detail}"
                    )

                return False, (
                    f"找到的歌曲歌手为“{raw_artist or '未知'}”，"
                    f"与要求的“{song.get('artist')}”不一致；暂停失败：{detail}"
                )

        time.sleep(1)

    if last_status is None:
        return False, "无法读取洛雪状态接口，不能确认歌曲是否存在，已拒绝本次点歌"

    if abort_event is not None and abort_event.is_set():
        return False, "检测到管理员切歌，已中止本次歌曲核验和音源回退"

    if saw_new_playback:
        ok, detail = pause_lx_playback()

        if ok:
            return False, (
                f"洛雪实际播放的歌曲与点歌名称“{song.get('title')}”不一致，"
                f"已请求暂停。{detail}"
            )

        return False, (
            f"洛雪实际播放的歌曲与点歌名称“{song.get('title')}”不一致；"
            f"暂停失败：{detail}"
        )

    return False, (
        f"未检测到本次点歌产生新的播放变化，"
        f"不能将洛雪原有歌曲误判为点歌成功：{song.get('title')}"
    )

def pause_lx_playback() -> tuple[bool, str]:
    """仅供歌曲版本核验发现错误曲目时使用；管理员切歌流程不得调用此函数。"""
    try:
        response = requests.get(LX_API + "/pause", timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        return True, f"API 已接受暂停请求（HTTP {response.status_code}）"
    except Exception as exc:
        return False, f"暂停请求失败：{type(exc).__name__}"


def skip_lx_next() -> tuple[bool, str]:
    """请求洛雪音乐切换到它自身播放列表的下一首；绝不使用 /pause 代替切歌。"""
    try:
        response = requests.get(LX_API + "/skip-next", timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        return True, f"洛雪下一曲接口已接受请求（HTTP {response.status_code}）；尚未确认下一首已开始播放"
    except Exception as exc:
        return False, f"调用洛雪下一曲接口失败：{type(exc).__name__}。请检查洛雪开放 API 是否已启用"



class MusicModule:
    def __init__(self, config: dict[str, Any] | None = None, cookie_provider=None, admin_uids: set[int] | None = None) -> None:
        self.config = config or {}
        self.cookie_provider = cookie_provider
        self.admin_uids = set(admin_uids or set())
        self.queue: deque[dict[str, Any]] = deque()
        self.queued_keys: set[tuple[str, str]] = set()
        self.cooldowns: dict[int, float] = {}
        # UID -> (per-user lock, active/waiting caller count); refcount avoids
        # removing a lock while another request is waiting to acquire it.
        self._user_request_locks: dict[int, tuple[asyncio.Lock, int]] = {}
        self._cooldown_prune_at = 0.0
        self.queue_lock = asyncio.Lock()
        self.play_lock = asyncio.Lock()
        # 队列为空时让 worker 保持待命；新请求或模块关闭时唤醒。
        self._queue_event = asyncio.Event()
        self.current: Optional[dict[str, Any]] = None
        # 只有机器人确实向洛雪发起过当前曲目的播放后，生命周期操作才可控制洛雪。
        # current 也可能只是已出队、正在查询音源的请求，不能据此暂停用户自己的歌曲。
        self._lx_playback_active = False
        # 视频点播期间由视频模块临时暂停音乐；只恢复本功能实际暂停且曲目未变化的歌曲。
        self._video_pause_active = False
        self._video_pause_track: Optional[tuple[str, str]] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._skip_requested = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        self._medal_enabled = REQUIRE_FANS_MEDAL
        self._restore_snapshot: Optional[dict[str, Any]] = None
        self.enabled = False
        self.paused = False
        self._closing = False
        self.display_events: deque[dict[str, Any]] = deque(maxlen=30)
        self._display_lock = threading.RLock()
        # 每次主程序启动默认开启点歌请求开关，但歌曲模块本身默认关闭。
        self.song_request_enabled = True


    def _acquire_user_request_lock(self, uid: int) -> asyncio.Lock:
        entry = self._user_request_locks.get(uid)
        if entry is None:
            lock = asyncio.Lock()
            self._user_request_locks[uid] = (lock, 1)
            return lock
        lock, users = entry
        self._user_request_locks[uid] = (lock, users + 1)
        return lock

    def _release_user_request_lock(self, uid: int, lock: asyncio.Lock) -> None:
        entry = self._user_request_locks.get(uid)
        if entry is None or entry[0] is not lock:
            return
        if entry[1] <= 1:
            self._user_request_locks.pop(uid, None)
        else:
            self._user_request_locks[uid] = (lock, entry[1] - 1)

    def _prune_user_cooldowns(self, now: float) -> None:
        if now - self._cooldown_prune_at < 30.0:
            return
        self._cooldown_prune_at = now
        if COOLDOWN_SECONDS <= 0:
            self.cooldowns.clear()
            return
        expired = [
            uid for uid, timestamp in self.cooldowns.items()
            if now - timestamp >= COOLDOWN_SECONDS
        ]
        for uid in expired:
            self.cooldowns.pop(uid, None)

    async def pause_for_video(self) -> bool:
        """视频点播实际播放时暂停洛雪；记录曲目，供视频结束后安全恢复。"""
        status = await run_blocking(get_lx_status)
        if self._video_pause_active:
            # 若有人在视频播放期间手动恢复音乐，再次暂停以维持视频优先级。
            if is_lx_playing(status):
                ok, detail = await run_blocking(_lx_control, "pause")
                if not ok:
                    log.warning("视频播放期间重新暂停洛雪音乐失败：%s", detail)
            return True
        if not is_lx_playing(status):
            return False
        title = normalize_name(str(status.get("name", "") or ""))
        artist = normalize_name(str(status.get("singer", "") or ""))
        ok, detail = await run_blocking(_lx_control, "pause")
        if not ok:
            log.warning("视频开始播放，但暂停洛雪音乐失败：%s", detail)
            return False
        self._video_pause_active = True
        self._video_pause_track = (title, artist)
        log.info("点播视频开始播放，已自动暂停洛雪音乐：%s%s", title or "当前歌曲", f" - {artist}" if artist else "")
        return True

    async def resume_after_video(self, force: bool = False) -> bool:
        """最后一条点播视频结束后，仅恢复本功能暂停且仍是原曲的音乐。"""
        if not self._video_pause_active:
            return False
        expected_track = self._video_pause_track
        if self.paused and not force:
            # 保留自动暂停标记，供程序退出时的 shutdown(force=True) 再尝试恢复。
            log.info("歌曲模块当前处于暂停状态，暂不恢复洛雪；保留视频自动暂停标记。")
            return False
        status = await run_blocking(get_lx_status)
        if get_lx_playback_state(status) != "paused":
            self._video_pause_active = False
            self._video_pause_track = None
            log.info("点播视频结束时洛雪音乐已不是暂停状态，不执行恢复。")
            return False
        actual_track = (
            normalize_name(str(status.get("name", "") or "")),
            normalize_name(str(status.get("singer", "") or "")),
        )
        if not expected_track or not expected_track[0] or actual_track != expected_track:
            self._video_pause_active = False
            self._video_pause_track = None
            log.info("点播视频结束，但洛雪当前曲目与自动暂停时不同，不自动恢复。")
            return False
        ok, detail = await run_blocking(_lx_control, "resume")
        if ok:
            self._video_pause_active = False
            self._video_pause_track = None
            log.info("点播视频已全部结束，已恢复洛雪音乐：%s", detail)
        else:
            log.warning("点播视频已全部结束，恢复洛雪音乐失败：%s；保留标记供退出清理重试。", detail)
        return ok

    async def start(self) -> None:
        if self.enabled:
            if self.paused:
                await self.resume()
            return
        self.loop = asyncio.get_running_loop()
        self.enabled = True
        self.paused = False
        self._closing = False
        log.info("歌曲模块已启动；直播连接由主程序统一管理。")
        if self.queue and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self.play_worker(), name="MusicPlayWorker")

    async def pause(self) -> None:
        if not self.enabled or self.paused:
            return
        # 先阻止播放 worker 启动下一首；只有机器人确实有当前点播时才控制洛雪，
        # 避免暂停空队列时误暂停用户自己播放的歌曲。
        self.paused = True
        if self._lx_playback_active:
            ok, detail = await run_blocking(_lx_control, "pause")
            if not ok:
                self.paused = False
                raise RuntimeError(detail)
        log.info("歌曲模块已暂停，保留当前歌曲进度和待播队列。")

    async def resume(self) -> None:
        if not self.enabled:
            await self.start()
            return
        if not self.paused:
            return
        # 当前没有机器人点播歌曲时，只解除模块暂停，不触碰洛雪中其他来源的歌曲。
        if self._lx_playback_active and not self._video_pause_active:
            ok, detail = await run_blocking(_lx_control, "resume")
            if not ok:
                raise RuntimeError(detail)
        self.paused = False
        log.info("歌曲模块已恢复播放。")
        if self.queue and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self.play_worker(), name="MusicPlayWorker")

    async def close(self, stop_playback: bool = True) -> None:
        self._closing = True
        self.enabled = False
        self.paused = False
        # 用户主动关闭歌曲模块时，不应在视频结束后重新启动其歌曲。
        self._video_pause_active = False
        self._video_pause_track = None
        self._skip_requested.set()
        self._queue_event.set()
        worker = self._worker
        if worker and not worker.done() and worker is not asyncio.current_task():
            # 不立即 cancel 正在执行的 to_thread(Scheme) 调用，避免暂停先于歌曲启动，
            # 导致模块关闭后后台线程又把歌曲启动起来。
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=12.0)
            except asyncio.TimeoutError:
                log.warning("等待歌曲播放工作线程收尾超时，将取消播放任务。")
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        async with self.queue_lock:
            count = len(self.queue)
            had_current_song = self._lx_playback_active
            self._lx_playback_active = False
            self.queue.clear()
            self.queued_keys.clear()
            self.current = None
        if had_current_song and stop_playback:
            ok, detail = await run_blocking(_lx_control, "stop")
            if not ok:
                log.warning("关闭歌曲模块时停止当前点播失败：%s", detail)
            else:
                log.info("关闭歌曲模块：%s", detail)
        elif had_current_song:
            log.info("程序退出：保留洛雪当前播放状态，不发送停止或暂停指令。")
        log.info("歌曲模块已关闭；清空待播队列 %s 首。", count)


    def add_display_event(self, message: str, kind: str) -> None:
        """将点歌成功/失败提示推送到展示页，网页端显示 5 秒。"""
        with self._display_lock:
            self.display_events.append({
                "message": str(message),
                "kind": "success" if kind == "success" else "failure",
                "timestamp": time.time(),
            })

    async def queue_snapshot(self) -> dict[str, Any]:
        # 展示页由 Flask 线程请求；切换到模块所属 asyncio 线程并加队列锁，
        # 避免与弹幕入队、播放出队、人工删除同时读写 deque。
        async with self.queue_lock:
            current = None
            if self.current:
                current = {
                    "title": self.current.get("title", ""),
                    "artist": self.current.get("artist", "") or self.current.get("actual_artist", ""),
                    "uname": self.current.get("uname", ""),
                }
            waiting = [
                {
                    "index": i,
                    "title": item.get("title", ""),
                    "artist": item.get("artist", ""),
                    "uname": item.get("uname", ""),
                }
                for i, item in enumerate(self.queue, 1)
            ]
            queue_count = len(waiting)
            module_paused = self.paused
            lx_playback_active = self._lx_playback_active
        # 空队列、模块暂停或当前歌曲并非由本模块实际启动时，
        # 无需访问洛雪状态接口；避免控制台轮询占用共享阻塞执行器。
        needs_lx_status = bool(current and lx_playback_active and not module_paused)
        lx_status = await run_blocking(get_lx_status) if needs_lx_status else None
        playback_state = (
            "module_paused" if current and module_paused
            else get_lx_playback_state(lx_status) if needs_lx_status
            else "stopped"
        )
        now = time.time()
        with self._display_lock:
            events = [
                dict(item) for item in self.display_events
                if now - float(item.get("timestamp", 0)) < 10.0
            ]
        return {
            "ok": True,
            "current": current,
            "playback_state": playback_state,
            "queue": waiting,
            "queue_count": queue_count,
            "queue_limit": QUEUE_LIMIT,
            "events": events,
            "song_request_enabled": self.song_request_enabled,
        }

    async def panel_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", "")).strip().lower()
        if action in ("skip", "next"):
            if self.current is None:
                return {"ok": False, "message": "当前没有正在处理的机器人点播歌曲，无法按当前点歌流程切歌"}

            # 先在队列锁内确认是否存在下一首机器人点播，避免判断过程中队列状态变化。
            async with self.queue_lock:
                has_next_request = bool(self.queue)
                current_title = str(self.current.get("title", "未知歌曲")) if self.current else "未知歌曲"
                next_title = str(self.queue[0].get("title", "未知歌曲")) if self.queue else ""

            if has_next_request:
                # 有下一首机器人点播时，只通知播放 worker 结束当前曲目等待，
                # 不调用洛雪 /pause，也不调用洛雪 /skip-next，避免跳过洛雪自己的列表歌曲。
                self._skip_requested.set()
                message = (
                    f"已提交切歌请求：准备从“{current_title}”切换到下一首点播“{next_title}”。"
                    "请求已交给机器人播放流程，尚未确认下一首已开始播放。"
                )
                log.info("控制面板提交切歌：当前=%s；下一首点播=%s", current_title, next_title)
                return {"ok": True, "message": message}

            # 机器人待播队列为空：改为请求洛雪自身播放列表的下一首。
            ok, detail = await run_blocking(skip_lx_next)
            if not ok:
                log.error("控制面板切歌失败：%s", detail)
                return {"ok": False, "message": detail}
            self._skip_requested.set()
            # 已请求洛雪自身列表下一曲；之后播放的曲目不再属于机器人点播模块。
            self._lx_playback_active = False
            log.info("机器人待播队列为空，已请求洛雪下一曲：%s", detail)
            return {"ok": True, "message": detail}
        if action == "delete":
            try:
                index = int(payload.get("index", 0))
            except (TypeError, ValueError):
                return {"ok": False, "message": "队列序号无效"}
            async with self.queue_lock:
                items = list(self.queue)
                if index < 1 or index > len(items):
                    return {"ok": False, "message": "该队列序号已不存在，请刷新后重试"}
                removed = items.pop(index - 1)
                self.queue = deque(items)
                self.queued_keys.discard(removed.get("key"))
            log.info("控制页面删除待播歌曲：%s%s", removed.get("title", ""), f" - {removed.get('artist')}" if removed.get("artist") else "")
            return {"ok": True, "message": f"已删除：{removed.get('title', '')}"}
        if action == "clear":
            async with self.queue_lock:
                count = len(self.queue)
                self.queue.clear()
                self.queued_keys = {self.current.get("key")} if self.current else set()
            log.info("控制页面清空待播队列，共移除 %s 首", count)
            return {"ok": True, "message": f"已清空待播队列（{count} 首）"}
            
        if action == "set_song_request":
            enabled = payload.get("enabled")

            if not isinstance(enabled, bool):
                return {
                    "ok": False,
                    "message": "点歌开关参数无效",
                }

            self.song_request_enabled = enabled

            status = "开启" if enabled else "关闭"
            logging.info("普通观众点歌功能已%s", status)

            return {
                "ok": True,
                "message": f"普通观众点歌已{status}",
                "song_request_enabled": self.song_request_enabled,
            }
        return {"ok": False, "message": "不支持的操作"}

    async def on_danmu(self, event: dict[str, Any]) -> None:
        if not self.enabled or not isinstance(event, dict):
            return
        if str(event.get("type", "")) != "danmaku":
            return
        message = str(event.get("text", "") or "").strip()
        try:
            uid = int(event.get("uid", 0) or 0)
        except (TypeError, ValueError):
            uid = 0
        uname = str(event.get("nickname", "") or "观众")
        if not message:
            return
        if message == "切歌":
            if uid not in self.admin_uids:
                log.info("【%s】尝试切歌但不是管理员，已拒绝 (UID:%s)", uname, uid)
                return
            log.info("管理员【%s】发出切歌指令", uname)
            if self.current is None:
                log.info("当前没有机器人正在处理的点播歌曲，无需切歌")
                return
            result = await self.panel_action({"action": "skip"})
            if result.get("ok"):
                log.info("已通知点歌队列结束当前歌曲，准备处理下一首")
            else:
                log.error("切歌失败，保留当前点播流程：%s", result.get("message", "未知错误"))
            return

        parsed = parse_request(message)

        if not parsed:
            return
        if self.paused:
            self.add_display_event(
                f"【{uname}】点歌失败：歌曲点播当前已暂停",
                "failure",
            )
            log.info("【%s】点歌失败：歌曲点播模块当前已暂停 (UID:%s)", uname, uid)
            return
        if uid not in self.admin_uids and not self.song_request_enabled:
            log.info(
                "【%s】点歌失败：直播间点歌功能当前已关闭 (UID:%s)",
                uname,
                uid,
            )
            self.add_display_event(
                f"【{uname}】点歌失败：当前点歌功能已关闭",
                "failure",
            )
            return
        title, artist = parsed
        if uid not in self.admin_uids:
            # 同一 UID 的多条弹幕可能并行进入事件总线；锁住冷却检查与粉丝牌核验，
            # 防止并发请求同时通过冷却窗口并重复点歌。
            user_lock = self._acquire_user_request_lock(uid)
            try:
                async with user_lock:
                    now = time.monotonic()
                    self._prune_user_cooldowns(now)
                    last = self.cooldowns.get(uid, 0.0)
                    if now - last < COOLDOWN_SECONDS:
                        remain = max(1, int(COOLDOWN_SECONDS - (now - last)))
                        log.info("【%s】点歌被冷却限制，剩余 %s 秒 (UID:%s)", uname, remain, uid)
                        self.add_display_event(f"【{uname}】点歌失败：请等待 {remain} 秒后再点歌", "failure")
                        return
                    if self._medal_enabled:
                        ok, reason = await check_fan_medal(uid)
                        if not ok:
                            log.info("【%s】点歌拒绝：%s (UID:%s)", uname, reason, uid)
                            self.add_display_event(f"【{uname}】点歌失败：{reason}", "failure")
                            return
                    if COOLDOWN_SECONDS > 0:
                        self.cooldowns[uid] = time.monotonic()
            finally:
                self._release_user_request_lock(uid, user_lock)
        await self.enqueue(title, artist, uname, uid)

    async def enqueue(self, title: str, artist: str, uname: str, uid: int) -> None:
        # 先搜索并核验，只有拿到明确的歌曲 ID/音源后才继续。
        if not self.enabled or self._closing:
            return
        matched_candidates, reason = await run_blocking(find_matching_songs, title, artist)
        if not self.enabled or self._closing:
            return

        if not matched_candidates:
            log.info(
                "【%s】点歌拒绝：%s%s；%s",
                uname,
                title,
                f" - {artist}" if artist else "",
                reason,
            )
            requested = f"{title} - {artist}" if artist else title
            self.add_display_event(
                f"【{uname}】点歌失败：{requested}；{reason}",
                "failure",
            )
            return

        matched = matched_candidates[0]
        actual_title = str(matched.get("title", "")).strip()
        actual_artist = str(matched.get("artist", "")).strip()

        key = (
            normalize_name(actual_title),
            normalize_name(actual_artist),
        )
        item: dict[str, Any] = {
            "title": actual_title,
            "artist": actual_artist,
            "requested_title": title,
            "requested_artist": artist,
            "source": str(matched.get("source", "")),
            "songmid": str(matched.get("songmid", "")),
            "music_info": matched,
            "fallback_candidates": matched_candidates[1:],
            "uname": uname,
            "uid": uid,
            "key": key,
            "added_at": time.time(),
        }

        # 第一步：检查洛雪当前正在播放的歌曲。
        # 只检查 status=playing 的歌曲，暂停或已经结束的歌曲不参与重复判断。
        lx_status = await run_blocking(get_lx_status)
        lx_current_key = get_lx_current_song_key(lx_status)

        if lx_current_key is not None and lx_current_key == key:
            log.info(
                "【%s】点歌重复，已拒绝：洛雪当前正在播放：%s - %s",
                uname,
                actual_title,
                actual_artist,
            )

            self.add_display_event(
                f"【{uname}】点歌失败：{actual_title} - {actual_artist} 正在播放",
                "failure",
            )
            return

        # 第二步：检查机器人自己的当前歌曲和待播列表。
        async with self.queue_lock:
            # 搜索和读取洛雪状态期间可能发生暂停/关闭；最终入队前必须再次确认生命周期。
            if not self.enabled or self._closing or self.paused:
                return

            # 检查当前正在处理的机器人点播歌曲。
            if self.current and self.current.get("key") == key:
                log.info(
                    "【%s】点歌重复，已拒绝：机器人当前正在播放：%s - %s",
                    uname,
                    actual_title,
                    actual_artist,
                )

                self.add_display_event(
                    f"【{uname}】点歌失败：{actual_title} - {actual_artist} 已在播放中",
                    "failure",
                )
                return

            # 直接检查待播列表，避免只依赖 queued_keys 导致队列状态不同步。
            duplicate_in_queue = any(
                queued_item.get("key") == key
                for queued_item in self.queue
            )

            if duplicate_in_queue or key in self.queued_keys:
                log.info(
                    "【%s】点歌重复，已拒绝：机器人待播队列中已有：%s - %s",
                    uname,
                    actual_title,
                    actual_artist,
                )

                self.add_display_event(
                    f"【{uname}】点歌失败：{actual_title} - {actual_artist} 已在待播队列中",
                    "failure",
                )
                return

            # 第三步：检查队列容量。
            if len(self.queue) >= QUEUE_LIMIT:
                log.info(
                    "【%s】点歌失败：队列已满（上限 %s 首）",
                    uname,
                    QUEUE_LIMIT,
                )

                self.add_display_event(
                    f"【{uname}】点歌失败：队列已满（上限 {QUEUE_LIMIT} 首）",
                    "failure",
                )
                return

            # 第四步：确认没有重复后，才加入机器人待播队列。
            self.queue.append(item)
            self.queued_keys.add(key)
            self._queue_event.set()

            log.info(
                "点歌匹配成功并入队：%s - %s（音源：%s；点歌人：%s，UID:%s；队列 %s/%s）",
                actual_title,
                actual_artist,
                item["source"],
                uname,
                uid,
                len(self.queue),
                QUEUE_LIMIT,
            )

            self.add_display_event(
                f"【{uname}】点歌成功：{actual_title} - {actual_artist}",
                "success",
            )

        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self.play_worker())

    async def play_worker(self) -> None:
        async with self.play_lock:
            while True:
                while (self.paused or self._video_pause_active) and self.enabled and not self._closing:
                    await asyncio.sleep(0.25)
                if not self.enabled or self._closing:
                    return
                async with self.queue_lock:
                    if not self.queue:
                        self.current = None
                        self._lx_playback_active = False
                        # 在锁内清除事件并检查队列，避免新请求入队时丢失唤醒。
                        self._queue_event.clear()
                        item = None
                    else:
                        item = self.queue.popleft()
                        self.queued_keys.discard(item["key"])
                        self.current = item
                        self._lx_playback_active = False

                if item is None:
                    if not self.enabled or self._closing:
                        return
                    await self._queue_event.wait()
                    continue

                self._skip_requested.clear()
                candidates = [item.get("music_info") or {}]
                candidates.extend(item.get("fallback_candidates") or [])
                verified = False

                for candidate_index, candidate in enumerate(candidates):
                    while self._video_pause_active and self.enabled and not self._closing:
                        await asyncio.sleep(0.25)
                    if self._skip_requested.is_set() or not self.enabled or self._closing:
                        break
                    candidate_song = dict(item)
                    candidate_song["music_info"] = candidate
                    candidate_song["title"] = str(candidate.get("title") or item.get("title") or "")
                    candidate_song["artist"] = str(candidate.get("artist") or item.get("artist") or "")
                    candidate_song["source"] = str(candidate.get("source") or "")
                    candidate_song["songmid"] = str(candidate.get("songmid") or "")

                    before_status = await run_blocking(get_lx_status)
                    if self._skip_requested.is_set() or not self.enabled or self._closing:
                        break

                    log.info(
                        "开始播放点歌：%s - %s（音源 %s；候选 %s/%s）",
                        candidate_song["title"], candidate_song["artist"],
                        candidate_song["source"], candidate_index + 1, len(candidates),
                    )
                    started = await run_blocking(play_song_in_lx, candidate_song)
                    if started:
                        # Scheme 调用已成功发起；后续即使在切歌/关闭窗口内，也需确保可暂停此曲。
                        self._lx_playback_active = True
                    if self._skip_requested.is_set():
                        break
                    if not started:
                        log.warning("调用洛雪播放失败，准备搜索下一个平台：%s（%s）", candidate_song["title"], candidate_song["source"])
                        next_candidates, next_reason = await run_blocking(
                            find_matching_songs,
                            str(item.get("requested_title") or item.get("title") or ""),
                            str(item.get("requested_artist") or ""),
                            candidate_song["source"],
                        )
                        if next_candidates:
                            candidates.extend(next_candidates)
                        continue

                    # 若暂停发生在 Scheme 启动途中，再补发一次暂停，确保新启动的曲目不会漏过暂停。
                    if (self.paused or self._video_pause_active) and self.enabled and not self._closing:
                        pause_ok, pause_detail = await run_blocking(_lx_control, "pause")
                        if not pause_ok:
                            log.warning("播放启动期间补充暂停失败：%s", pause_detail)
                        while (self.paused or self._video_pause_active) and self.enabled and not self._closing:
                            await asyncio.sleep(0.25)
                    if not self.enabled or self._closing or self._skip_requested.is_set():
                        break

                    # 用户在核验过程中暂停时，等待恢复后重新核验同一个已保存 ID，
                    # 不把“暂停状态”误判为音源播放失败并切换到下一平台。
                    while True:
                        ok, verify_message = await run_blocking(
                            verify_requested_song,
                            candidate_song,
                            before_status,
                            5.0,
                            self._skip_requested,
                        )
                        if self._skip_requested.is_set() or not self.enabled or self._closing:
                            break
                        if self.paused or self._video_pause_active:
                            while (self.paused or self._video_pause_active) and self.enabled and not self._closing:
                                await asyncio.sleep(0.25)
                            if self.enabled and not self._closing:
                                continue
                        break
                    if self._skip_requested.is_set() or not self.enabled or self._closing:
                        break
                    if ok:
                        item["music_info"] = candidate
                        item["title"] = candidate_song["title"]
                        item["artist"] = candidate_song["artist"]
                        item["source"] = candidate_song["source"]
                        item["songmid"] = candidate_song["songmid"]
                        if candidate_song.get("actual_artist"):
                            item["actual_artist"] = candidate_song["actual_artist"]
                        log.info("点歌歌曲核验成功：%s", verify_message)
                        verified = True
                        break

                    log.warning(
                        "音源播放核验失败，准备搜索下一个平台：歌曲=%s - %s；音源=%s；原因=%s",
                        candidate_song["title"], candidate_song["artist"],
                        candidate_song["source"], verify_message,
                    )
                    next_candidates, next_reason = await run_blocking(
                        find_matching_songs,
                        str(item.get("requested_title") or item.get("title") or ""),
                        str(item.get("requested_artist") or ""),
                        candidate_song["source"],
                    )
                    if next_candidates:
                        candidates.extend(next_candidates)

                if self._skip_requested.is_set():
                    # 关闭模块也会设置 skip_event；此时必须保留播放归属，
                    # 让 close() 在 worker 收尾后暂停当前机器人歌曲。
                    if not self._closing:
                        self._lx_playback_active = False
                    log.info("检测到管理员切歌，已停止当前歌曲核验/音源回退流程。")
                    continue
                if not verified:
                    # 未确认属于本次机器人点播的曲目时，不再将洛雪当前播放视为本模块拥有。
                    self._lx_playback_active = False
                    log.error("所有可用音源均未能确认播放成功：%s - %s", item.get("title", ""), item.get("artist", ""))
                    async with self.queue_lock:
                        if self.current is item:
                            self.current = None
                    continue

                await self.wait_song_end()
                # 管理员切歌、关闭模块时不额外推进洛雪列表。
                if self._skip_requested.is_set() or not self.enabled or self._closing:
                    if not self._closing:
                        self._lx_playback_active = False
                    continue

                # 机器人歌曲结束后，若队列为空则交还给洛雪自己的播放列表。
                # 如果洛雪已经自动切到列表下一首，不再重复调用 skip-next。
                async with self.queue_lock:
                    if not self.queue and self.enabled and not self._closing:
                        lx_status = await run_blocking(get_lx_status)
                        lx_key = get_lx_current_song_key(lx_status)
                        bot_key = (
                            normalize_name(str(item.get("title", ""))),
                            normalize_name(str(item.get("artist", ""))),
                        )
                        if lx_key is None or lx_key == bot_key:
                            ok, detail = await run_blocking(skip_lx_next)
                            if not ok:
                                log.warning("机器人队列为空，切换洛雪播放列表下一首失败：%s", detail)

                if not self._closing:
                    self._lx_playback_active = False
                log.info("机器人点播流程结束：%s", item["title"])

    async def wait_song_end(self) -> None:
        start = time.monotonic()
        saw_playing = False
        unchanged = 0
        previous_signature: Optional[str] = None
        while True:
            if self._skip_requested.is_set():
                return
            await asyncio.sleep(2)
            status = await run_blocking(get_lx_status)
            if not status:
                # 没有可用状态 API 时不猜测歌曲结束，允许管理员切歌控制。
                continue
            status_text = str(status.get("status", status.get("playStatus", status.get("state", "")))).lower()
            is_playing = status.get("playing") is True or status.get("isPlaying") is True or status_text in ("playing", "play", "1", "true")
            if is_playing:
                saw_playing = True

            # 只在洛雪当前播放曲名与本次点歌匹配时，记录实际歌手；不把旧曲目的歌手显示到新曲目上。
            if self.current and not self.current.get("artist"):
                actual_title = normalize_name(get_status_text(status, STATUS_TITLE_KEYS))
                requested_title = normalize_name(str(self.current.get("title", "")))
                actual_artist = get_status_text(status, STATUS_ARTIST_KEYS)
                title_matches = bool(actual_title and requested_title) and (
                    actual_title == requested_title
                    or actual_title in requested_title
                    or requested_title in actual_title
                )
                if title_matches and actual_artist:
                    self.current["actual_artist"] = actual_artist
            # 官方状态为 paused 时代表用户主动暂停，不代表歌曲结束。
            # 暂停期间必须继续等待；即使进度已经接近总时长，也不能提前清空当前点歌。
            if status_text in ("paused", "pause"):
                continue
            progress = status.get("progress", status.get("currentTime", status.get("position", None)))
            duration = status.get("duration", status.get("totalTime", status.get("length", None)))
            try:
                progress_num = float(progress) if progress is not None else None
                duration_num = float(duration) if duration is not None else None
            except (TypeError, ValueError):
                progress_num, duration_num = None, None
            signature = json.dumps({"status": status_text, "progress": progress, "duration": duration}, ensure_ascii=False, sort_keys=True)
            if signature == previous_signature:
                unchanged += 1
            else:
                unchanged = 0
            previous_signature = signature
            # paused/pause 已在上方单独处理。只有明确停止/结束状态才结束当前点歌流程。
            # 官方 API 的停止状态字段拼写为 stoped（单个 p），兼容该值。
            if saw_playing and not is_playing and status_text in ("stopped", "stoped", "stop", "ended", "idle"):
                return
            if saw_playing and progress_num is not None and duration_num is not None and duration_num > 0 and progress_num >= duration_num - 2:
                return
            # 若 API 有状态但没有可用播放状态字段，避免因启动前状态直接判断结束。
            if not saw_playing and time.monotonic() - start > 90:
                log.warning("状态 API 未确认播放状态；仍等待管理员发送“切歌”或状态恢复")
                start = time.monotonic()
            if unchanged >= 30 and saw_playing:
                log.warning("状态连续约 60 秒未变化；继续等待切歌指令")
                unchanged = 0


# 统一模块对外入口
class MusicModuleFacade:
    """持有事件总线订阅关系；不创建 B 站连接、HTTP 服务或独立日志系统。"""

    def __init__(self, event_bus, config: dict[str, Any], cookie_provider=None, room_id: int = 0):
        self.event_bus = event_bus
        self.config = dict(config)
        self.cookie_provider = cookie_provider
        self.room_id = max(0, int(room_id or 0))
        # 构造阶段不访问 B 站；只有用户启动歌曲模块时才刷新登录态。
        configure_music_module(self.config, None, self.room_id)
        self.bot = MusicModule(config=self.config, cookie_provider=cookie_provider, admin_uids=set(ADMIN_UIDS))
        self.event_bus.subscribe("danmaku", self.bot.on_danmu)

    def reload_config(self, config: dict[str, Any]) -> None:
        if self.bot.enabled:
            raise RuntimeError("请先关闭歌曲模块，再修改歌曲配置。")
        old_handler = self.bot.on_danmu
        self.event_bus.unsubscribe("danmaku", old_handler)
        self.config = dict(config)
        configure_music_module(self.config, None, self.room_id)
        self.bot = MusicModule(config=self.config, cookie_provider=self.cookie_provider, admin_uids=set(ADMIN_UIDS))
        self.event_bus.subscribe("danmaku", self.bot.on_danmu)

    async def start(self):
        # 每次启动前刷新统一登录态，确保扫码登录后粉丝牌检查使用最新 Cookie。
        configure_music_module(self.config, self.cookie_provider, self.room_id)
        self.bot.admin_uids = set(ADMIN_UIDS)
        self.bot._medal_enabled = REQUIRE_FANS_MEDAL
        await self.bot.start()

    async def pause(self):
        await self.bot.pause()

    async def resume(self):
        # 断线重连或手动恢复时重新读取主程序当前有效 Cookie，避免继续使用过期登录态。
        configure_music_module(self.config, self.cookie_provider, self.room_id)
        self.bot.admin_uids = set(ADMIN_UIDS)
        self.bot._medal_enabled = REQUIRE_FANS_MEDAL
        await self.bot.resume()

    async def close(self):
        # 手动关闭歌曲模块时，仍按原行为停止机器人当前点播歌曲。
        await self.bot.close(stop_playback=True)

    async def shutdown(self):
        # 程序退出时只清理本程序的点歌任务和内存队列。
        # 不调用洛雪 API：不恢复、不暂停、不停止，更不关闭洛雪播放器。
        await self.bot.close(stop_playback=False)

    def update_room_id(self, room_id: int) -> None:
        self.room_id = max(0, int(room_id or 0))
        update_music_room_id(self.room_id)

    async def queue_snapshot(self) -> dict[str, Any]:
        return await self.bot.queue_snapshot()

    async def panel_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.bot.panel_action(payload)

    @property
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.bot.enabled,
            "paused": self.bot.paused,
            "current": self.bot.current,
            "queue_count": len(self.bot.queue),
            "song_request_enabled": self.bot.song_request_enabled,
        }
