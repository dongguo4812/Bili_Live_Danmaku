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
import time
import urllib.parse
import webbrowser
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

import requests

from core.config_manager import ConfigManager
from core.paths import get_base_dir

log = logging.getLogger("bili_live_danmaku.music")
BASE_DIR = get_base_dir()
CONFIG_MANAGER = ConfigManager()
_MUSIC_CONFIG = CONFIG_MANAGER.load("music", {})
LX_API = str(_MUSIC_CONFIG.get("lx_music", {}).get("base_url", "http://127.0.0.1:23330")).rstrip("/")
HTTP_TIMEOUT = 8
MEDAL_CACHE_SECONDS = 60
ROOM_ID = 0
COOKIE_DICT: dict[str, str] = {}
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132 Safari/537.36",
    "Referer": "https://live.bilibili.com/",
})
MEDAL_CACHE: dict[int, tuple[float, bool, str]] = {}
UID_FILE = BASE_DIR / "uid.txt"
ADMIN_UIDS: set[int] = set()
REQUIRE_FANS_MEDAL = str(_MUSIC_CONFIG.get("requests", {}).get("require_fans_medal", 0))
COOLDOWN_SECONDS = max(0, int(_MUSIC_CONFIG.get("requests", {}).get("cooldown_seconds", 10)))
QUEUE_LIMIT = max(1, int(_MUSIC_CONFIG.get("requests", {}).get("queue_limit", 100)))
SEARCH_LIMIT = max(1, int(_MUSIC_CONFIG.get("search", {}).get("max_results_per_platform", 20)))
STATUS_TITLE_KEYS = ("name", "title", "song", "songName", "musicName", "trackName", "music_name")
STATUS_ARTIST_KEYS = ("singer", "artist", "artists", "artistName", "singerName", "author", "musicArtist", "music_artist")


def load_admin_uids(path: Path = UID_FILE) -> set[int]:
    values: set[int] = set()
    try:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.split("#", 1)[0].strip()
            if line:
                for part in re.split(r"[,;\s]+", line):
                    if part.isdigit():
                        values.add(int(part))
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("读取管理员 UID 文件失败。")
    return values


def configure_music_module(config: dict[str, Any], cookie_provider=None, room_id: int = 0) -> None:
    """由主程序注入普通配置和统一登录态；本模块不读取/保存 Cookie。"""
    global LX_API, HTTP_TIMEOUT, ROOM_ID, COOKIE_DICT, REQUIRE_FANS_MEDAL
    global COOLDOWN_SECONDS, QUEUE_LIMIT, SEARCH_LIMIT, ADMIN_UIDS, SESSION
    lx = config.get("lx_music", {}) if isinstance(config.get("lx_music"), dict) else {}
    req = config.get("requests", {}) if isinstance(config.get("requests"), dict) else {}
    search = config.get("search", {}) if isinstance(config.get("search"), dict) else {}
    LX_API = str(lx.get("base_url", "http://127.0.0.1:23330")).rstrip("/")
    HTTP_TIMEOUT = max(2, int(lx.get("timeout_seconds", 5)))
    ROOM_ID = max(0, int(room_id or 0))
    REQUIRE_FANS_MEDAL = str(req.get("require_fans_medal", 0))
    COOLDOWN_SECONDS = max(0, int(req.get("cooldown_seconds", 10)))
    QUEUE_LIMIT = max(1, int(req.get("queue_limit", 100)))
    SEARCH_LIMIT = max(1, int(search.get("max_results_per_platform", 20)))
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
    admin_file = config.get("admins_file", "data/music/uid.txt")
    if admin_file:
        ADMIN_UIDS.update(load_admin_uids(BASE_DIR / str(admin_file)))
    values = cookie_provider() if callable(cookie_provider) else {}
    COOKIE_DICT = {
        "SESSDATA": str(values.get("sessdata", "")),
        "bili_jct": str(values.get("bili_jct", "")),
        "buvid3": str(values.get("buvid3", "")),
        "buvid4": str(values.get("buvid4", "")),
        "DedeUserID": str(values.get("dedeuserid", "")),
    }
    SESSION = requests.Session()
    SESSION.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132 Safari/537.36",
        "Referer": "https://live.bilibili.com/",
    })
    SESSION.cookies.update({k: v for k, v in COOKIE_DICT.items() if v})
    MEDAL_CACHE.clear()


def update_music_room_id(room_id: int) -> None:
    global ROOM_ID
    ROOM_ID = max(0, int(room_id or 0))
    MEDAL_CACHE.clear()


def _lx_control(action: str) -> tuple[bool, str]:
    # 洛雪官方开放 API：播放使用 /play，暂停使用 /pause；没有独立停止接口。
    endpoint = {"pause": "/pause", "resume": "/play", "stop": "/pause"}.get(action)
    if not endpoint:
        return False, "不支持的洛雪控制操作"
    try:
        response = requests.get(LX_API + endpoint, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        if action == "stop":
            return True, "洛雪开放 API 没有停止接口；已暂停当前歌曲并结束机器人点播流程"
        return True, f"洛雪接口已接受 {action} 请求（HTTP {response.status_code}）"
    except Exception as exc:
        return False, f"洛雪 {action} 请求失败：{type(exc).__name__}"


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

SEARCH_LIMIT = 20


def compact_music_name(value: str) -> str:
    """与搜索测试工具保持一致：忽略空白，统一中英文括号，保留其他标点。"""
    value = str(value or "")
    value = re.sub(r"\s+", "", value)
    value = value.translate(str.maketrans({"（": "(", "）": ")"}))
    return value.casefold()


def is_bad_version(item: dict[str, Any]) -> bool:
    title = str(item.get("title", ""))
    artist = str(item.get("artist", ""))
    return bool(BAD_VERSION_PATTERN.search(title) or BAD_VERSION_PATTERN.search(artist))


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
    url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp?" + urllib.parse.urlencode({
        "ct": 24, "qqmusic_ver": 1298,
        # 不添加 new_json=1，否则该接口可能返回空列表。
        "remoteplace": "txt.yqq.song", "t": 0, "aggr": 1, "cr": 1,
        "catZhida": 1, "lossless": 0, "flag_qc": 0, "p": 1,
        "n": SEARCH_LIMIT, "w": keyword, "format": "json",
    })
    data = _music_get_json(url, referer="https://y.qq.com/")
    rows = (((data.get("data") or {}).get("song") or {}).get("list") or []) if isinstance(data, dict) else []
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("songname") or row.get("name") or "").strip()
        singers = row.get("singer", [])
        artist = "、".join(
            str(x.get("name", "")).strip()
            for x in singers if isinstance(x, dict) and x.get("name")
        ) if isinstance(singers, list) else ""
        song_id = str(row.get("songmid") or row.get("mid") or "").strip()
        file_info = row.get("file", {})
        if not isinstance(file_info, dict):
            file_info = {}
        media_mid = str(file_info.get("media_mid") or row.get("strMediaMid") or row.get("media_mid") or "").strip()
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
            "albumName": album_name, "interval": str(row.get("interval", "")),
            "types": _types_default(), "_search_source": "tx",
        })
    return out


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
def search_kuwo(keyword: str) -> list[dict[str, Any]]:
    url = "https://search.kuwo.cn/r.s?" + urllib.parse.urlencode({
        "all": keyword, "ft": "music", "itemset": "web_2013", "client": "kt",
        "pn": 0, "rn": SEARCH_LIMIT, "rformat": "json", "encoding": "utf8",
    })
    data = _music_get_json(url, allow_python_literal=True)
    rows = data.get("abslist", []) if isinstance(data, dict) else []
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = html.unescape(str(row.get("SONGNAME", ""))).strip()
        artist = html.unescape(str(row.get("ARTIST", ""))).strip()
        song_id = str(row.get("MUSICRID", row.get("MUSICID", ""))).strip()
        if song_id.startswith("MUSIC_"):
            song_id = song_id[6:]
        if not title or not artist or not song_id:
            continue
        out.append({
            "title": title, "artist": artist, "source": "kw", "songmid": song_id,
            "albumId": str(row.get("ALBUMID", "")),
            "albumName": html.unescape(str(row.get("ALBUM", ""))).strip(),
            "interval": str(row.get("DURATION", "")), "types": _types_default(),
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
    wanted_title = compact_music_name(title)
    wanted_artist = compact_music_name(artist)
    stage_one: list[dict[str, Any]] = []
    stage_two: list[dict[str, Any]] = []
    if not wanted_title:
        return stage_one, stage_two
    for item in candidates:
        candidate_title = compact_music_name(str(item.get("title", "")))
        candidate_artist = compact_music_name(str(item.get("artist", "")))
        if candidate_title != wanted_title or is_bad_version(item):
            continue
        if not wanted_artist:
            stage_one.append(item)
        elif candidate_artist == wanted_artist:
            stage_one.append(item)
        elif wanted_artist in candidate_artist:
            stage_two.append(item)
    return stage_one, stage_two


def find_matching_song(title: str, artist: str) -> tuple[Optional[dict[str, Any]], str]:
    """按 QQ→酷狗→网易云→酷我逐个平台搜索，当前平台匹配成功即停止。"""
    keyword = f"{title} {artist}".strip()
    errors: list[str] = []
    total_candidates = 0
    for provider_name, source, search_func in PROVIDERS:
        try:
            candidates = search_func(keyword)
            total_candidates += len(candidates)
            stage_one, stage_two = match_candidates(candidates, title, artist)
            if stage_one:
                selected = stage_one[0]
                log.info("音乐匹配成功：平台=%s（%s），级别=完全匹配，歌曲=%s - %s", provider_name, source, selected.get("title", ""), selected.get("artist", ""))
                return selected, f"匹配成功（{provider_name}，完全匹配）"
            if stage_two:
                selected = stage_two[0]
                log.info("音乐匹配成功：平台=%s（%s），级别=歌手包含匹配，歌曲=%s - %s", provider_name, source, selected.get("title", ""), selected.get("artist", ""))
                return selected, f"匹配成功（{provider_name}，歌手包含匹配）"
            log.info("音乐平台无符合条件结果：%s（%s），有效候选=%s", provider_name, source, len(candidates))
        except Exception as exc:
            errors.append(f"{source}:{type(exc).__name__}: {exc}")
            log.exception("音乐平台搜索失败：%s（%s）", provider_name, source)
            continue
    if errors and total_candidates == 0:
        return None, "音乐源搜索请求失败（" + ", ".join(errors) + "）"
    if artist:
        return None, f"未找到歌名完全匹配且歌手完全匹配/包含“{artist}”的歌曲"
    return None, "未找到歌名完全匹配且通过版本过滤的歌曲"


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
        log.info(
            "洛雪播放调用已发起：目标=%s - %s；source=%s；songmid=%s。此日志仅表示系统已调用 Scheme，不代表洛雪已成功播放。",
            song.get("title", ""), song.get("artist", ""), song.get("source", ""), song.get("songmid", ""),
        )
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
                        "status,name,singer,albumName,"
                        "duration,progress,playbackRate,"
                        "picUrl,lyricLineText"
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




def is_lx_playing(status: Optional[dict[str, Any]]) -> bool:
    """根据洛雪 /status 接口判断当前是否正在播放。"""
    if not isinstance(status, dict):
        return False

    status_text = str(
        status.get("status", status.get("playStatus", status.get("state", "")))
    ).strip().lower()

    return (
        status_text in ("playing", "play", "1", "true")
        or status.get("playing") is True
        or status.get("isPlaying") is True
    )


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
    timeout: float = 15.0,
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
        self.queue_lock = asyncio.Lock()
        self.play_lock = asyncio.Lock()
        self.current: Optional[dict[str, Any]] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._skip_requested = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        self._medal_enabled = REQUIRE_FANS_MEDAL == "0"
        self._restore_snapshot: Optional[dict[str, Any]] = None
        self.enabled = False
        self.paused = False
        self._closing = False
        self.display_events: deque[dict[str, Any]] = deque(maxlen=30)
        # 每次主程序启动默认开启点歌请求开关，但歌曲模块本身默认关闭。
        self.song_request_enabled = True

    async def start(self) -> None:
        if self.enabled and not self.paused:
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
        ok, detail = await asyncio.to_thread(_lx_control, "pause")
        if not ok:
            raise RuntimeError(detail)
        self.paused = True
        log.info("歌曲模块已暂停，保留当前歌曲进度和待播队列。")

    async def resume(self) -> None:
        if not self.enabled:
            await self.start()
            return
        if not self.paused:
            return
        ok, detail = await asyncio.to_thread(_lx_control, "resume")
        if not ok:
            raise RuntimeError(detail)
        self.paused = False
        log.info("歌曲模块已恢复播放。")
        if self.queue and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self.play_worker(), name="MusicPlayWorker")

    async def close(self) -> None:
        self._closing = True
        self.enabled = False
        self.paused = False
        self._skip_requested.set()
        worker = self._worker
        if worker and not worker.done() and worker is not asyncio.current_task():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        async with self.queue_lock:
            count = len(self.queue)
            had_current_song = self.current is not None
            self.queue.clear()
            self.queued_keys.clear()
            self.current = None
        if had_current_song:
            ok, detail = await asyncio.to_thread(_lx_control, "stop")
            if not ok:
                log.warning("关闭歌曲模块时暂停当前点播失败：%s", detail)
            else:
                log.info("关闭歌曲模块：%s", detail)
        log.info("歌曲模块已关闭；清空待播队列 %s 首。", count)


    def add_display_event(self, message: str, kind: str) -> None:
        """将点歌成功/失败提示推送到展示页，网页端显示 5 秒。"""
        self.display_events.append({
            "message": str(message),
            "kind": "success" if kind == "success" else "failure",
            "timestamp": time.time(),
        })

    def queue_snapshot(self) -> dict[str, Any]:
        now = time.time()
        events = [
            item for item in self.display_events
            if now - float(item.get("timestamp", 0)) < 5.0
        ]
        current = None
        if self.current:
            # 若观众点歌时没有填写歌手，展示页使用洛雪状态 API 确认的实际歌手。
            current = {
                "title": self.current.get("title", ""),
                "artist": self.current.get("artist", "") or self.current.get("actual_artist", ""),
                "uname": self.current.get("uname", ""),
            }
        waiting = []
        for i, item in enumerate(list(self.queue), 1):
            waiting.append({
                "index": i,
                "title": item.get("title", ""),
                "artist": item.get("artist", ""),
                "uname": item.get("uname", ""),
            })
        return {"ok": True, "current": current, "queue": waiting, "queue_count": len(waiting), "queue_limit": QUEUE_LIMIT, "events": events, "song_request_enabled": self.song_request_enabled}

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
            ok, detail = await asyncio.to_thread(skip_lx_next)
            if not ok:
                log.error("控制面板切歌失败：%s", detail)
                return {"ok": False, "message": detail}
            self._skip_requested.set()
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
        if not self.enabled or self.paused or not isinstance(event, dict):
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
        log.info(
            "[弹幕诊断] 收到弹幕：用户=%s，UID=%s，内容=%r",
            uname, uid, message
        )
        title, artist = parsed
        if uid not in self.admin_uids:
            now = time.monotonic()
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
            self.cooldowns[uid] = now
        await self.enqueue(title, artist, uname, uid)

    async def enqueue(self, title: str, artist: str, uname: str, uid: int) -> None:
        # 先搜索并核验，只有拿到明确的歌曲 ID/音源后才继续。
        if not self.enabled or self._closing:
            return
        matched, reason = await asyncio.to_thread(find_matching_song, title, artist)
        if not self.enabled or self._closing:
            return

        if not matched:
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

        actual_title = str(matched.get("title", "")).strip()
        actual_artist = str(matched.get("artist", "")).strip()

        key = (
            normalize_name(actual_title),
            normalize_name(actual_artist),
        )
        log.info(
            "[查重诊断] 点歌人=%s，原始请求=%s - %s，"
            "实际匹配=%s - %s，查重KEY=%r",
            uname,
            title,
            artist,
            actual_title,
            actual_artist,
            key,
        )
        item: dict[str, Any] = {
            "title": actual_title,
            "artist": actual_artist,
            "requested_title": title,
            "requested_artist": artist,
            "source": str(matched.get("source", "")),
            "songmid": str(matched.get("songmid", "")),
            "music_info": matched,
            "uname": uname,
            "uid": uid,
            "key": key,
            "added_at": time.time(),
        }

        # 第一步：检查洛雪当前正在播放的歌曲。
        # 只检查 status=playing 的歌曲，暂停或已经结束的歌曲不参与重复判断。
        lx_status = await asyncio.to_thread(get_lx_status)
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
                while self.paused and self.enabled and not self._closing:
                    await asyncio.sleep(0.25)
                if not self.enabled or self._closing:
                    return
                async with self.queue_lock:
                    if not self.queue:
                        self.current = None
                        return
                    item = self.queue.popleft()
                    self.queued_keys.discard(item["key"])
                    self.current = item
                self._skip_requested.clear()
                if self._restore_snapshot is None:
                    self._restore_snapshot = await asyncio.to_thread(get_lx_status)
                # 每首歌播放前单独保存状态，便于区分“搜索失败仍停留在旧歌”和“切换到了错误歌曲”。
                before_status = await asyncio.to_thread(get_lx_status)
                while self.paused and self.enabled and not self._closing:
                    await asyncio.sleep(0.25)
                if not self.enabled or self._closing:
                    return
                log.info("开始播放点歌：%s%s", item["title"], f" - {item['artist']}" if item["artist"] else "")
                started = await asyncio.to_thread(play_song_in_lx, item)
                if not started:
                    log.error("调用音乐失败，跳过本首：%s", item["title"])
                    continue
                verified, verify_message = await asyncio.to_thread(verify_requested_song, item, before_status)
                if not verified:
                    log.error("点歌歌曲核验失败：%s%s；%s", item["title"], f" - {item['artist']}" if item.get("artist") else "", verify_message)
                    continue
                log.info("点歌歌曲核验：%s", verify_message)

                # 如果管理员在 URI Scheme 启动或核验期间切歌，wait_song_end 会立即返回，
                # 随后播放 worker 处理下一首；这里绝不补发暂停，避免把切歌变成暂停。
                if self._skip_requested.is_set():
                    log.info("检测到播放启动/核验期间收到切歌请求；将直接进入下一步播放流程，不发送暂停命令")
                # URI Scheme 发起播放后等待用户切歌或当前曲目结束。
                # status API 的字段因本机插件版本而异，采用多种常见字段判断。
                await self.wait_song_end()
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
            status = await asyncio.to_thread(get_lx_status)
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

    async def start(self):
        # 每次启动前刷新统一登录态，确保扫码登录后粉丝牌检查使用最新 Cookie。
        configure_music_module(self.config, self.cookie_provider, self.room_id)
        self.bot.admin_uids = set(ADMIN_UIDS)
        self.bot._medal_enabled = REQUIRE_FANS_MEDAL == "0"
        await self.bot.start()

    async def pause(self):
        await self.bot.pause()

    async def resume(self):
        await self.bot.resume()

    async def close(self):
        # 保留事件订阅，模块重新启动后可继续接收新弹幕。
        await self.bot.close()

    def update_room_id(self, room_id: int) -> None:
        self.room_id = max(0, int(room_id or 0))
        update_music_room_id(self.room_id)

    def queue_snapshot(self) -> dict[str, Any]:
        return self.bot.queue_snapshot()

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
