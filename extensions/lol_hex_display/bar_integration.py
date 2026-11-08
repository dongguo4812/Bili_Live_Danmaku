# -*- coding: utf-8 -*-
"""LOL 战绩条模块：供现有 Flask 海克斯展示项目接入。"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests
import urllib3
from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger(__name__)

# 普通空闲轮询、对局结束追赶窗口及追赶周期（秒）
IDLE_REFRESH_INTERVAL = 30
POST_GAME_CHASE_WINDOW = 5 * 60
POST_GAME_REFRESH_INTERVAL = 5

# 每个账号最多保留的本地对局数量
CACHE_MATCH_LIMIT = 100

REGION_NAMES = {
    "HN1": "艾欧尼亚", "HN2": "祖安", "HN3": "诺克萨斯",
    "HN4": "班德尔城", "HN5": "皮尔特沃夫", "HN6": "战争学院",
    "HN7": "巨神峰", "HN8": "裁决之地", "HN9": "裁决之地",
    "HN10": "黑色玫瑰", "HN11": "暗影岛", "HN12": "钢铁烈阳",
    "HN13": "水晶之痕", "HN14": "均衡教派", "HN15": "影流",
    "HN16": "守望之海", "HN17": "征服之海", "HN18": "卡拉曼达",
    "HN19": "皮城警备", "BGP1": "男爵领域", "BGP2": "峡谷之巅",
    "WT1": "比尔吉沃特", "WT2": "德玛西亚", "WT3": "弗雷尔卓德",
    "WT4": "无畏先锋", "WT5": "恕瑞玛", "WT6": "扭曲丛林",
    "WT7": "巨龙之巢", "EDU1": "教育网", "PBE": "体验服",
    "NJ100": "联盟一区", "GZ100": "联盟二区",
    "CQ100": "联盟三区", "TJ100": "联盟四区",
    "TJ101": "联盟五区",
}

DEFAULTS = {
    "max_games": 5,
    "mode_filter": 1,
    "batch_gap_min": 100000,
    "show_lobby_member_count": True,
}


def register_bar_routes(
    app, get_lcu_credentials: Callable, base_dir,
    client_is_recognized: Callable[[], bool] | None = None,
    lcu_is_available: Callable[[], bool] | None = None,
    get_lobby_member_count: Callable[[], int | None] | None = None,
):
    """
    接入现有 Flask app。

    get_lcu_credentials 必须是 main.py 中已有的函数，返回 (port, token)
    或 None。base_dir 使用 main.py 中的 BASE_DIR。
    """
    bp = Blueprint(
        "lol_match_bar",
        __name__,
        template_folder=str(Path(base_dir) / "templates"),
    )

    data_dir = Path(base_dir) / "data"
    config_path = data_dir / "bar_config.json"

    # 新版：每个 puuid 一份独立缓存。
    accounts_dir = data_dir / "bar_accounts"

    # 记录最近一次成功识别的账号，供网页快照和“开启新批次”使用。
    active_account_path = data_dir / "bar_active_account.json"

    lock = threading.RLock()
    # 串行化账号缓存的读取-修改-写入，避免自动刷新覆盖手动批次边界。
    cache_lock = threading.RLock()
    # 配置文件通常只在保存设置时变化；缓存其解析结果，避免请求时重复读盘。
    config_lock = threading.RLock()
    config_cache_state = object()
    config_cache_value = None
    status = {"busy": False, "message": ""}
    # snapshot 的静态战绩统计缓存；文件或相关配置变化时才重新计算。
    snapshot_cache_key = None
    snapshot_cache_result = None
    snapshot_cache_lock = threading.RLock()
    stop_event = threading.Event()
    game_end_event = threading.Event()
    # LCU 首次识别/重新识别时唤醒刷新线程，不改变对局结束追赶状态。
    refresh_wakeup_event = threading.Event()

    worker = None
    worker_lock = threading.Lock()

    # SSE 订阅者只在人数变化时收到通知，不进行浏览器定时轮询。
    lobby_event_lock = threading.Lock()
    lobby_event_subscribers = set()
    SSE_SHUTDOWN = object()

    def notify_lobby_member_count_changed(count):
        if stop_event.is_set():
            return

        payload = json.dumps({"lobby_member_count": count}, ensure_ascii=False)
        with lobby_event_lock:
            # 与关闭通知共用同一把锁，确保结束标记不会被并发人数事件覆盖。
            if stop_event.is_set():
                return
            for subscriber in list(lobby_event_subscribers):
                try:
                    if subscriber.full():
                        try:
                            subscriber.get_nowait()
                        except queue.Empty:
                            pass
                    subscriber.put_nowait(payload)
                except queue.Full:
                    pass
                except Exception:
                    log.debug("[战绩条] 推送组队人数变更失败", exc_info=True)

    def start_worker():
        """由主程序在取得单实例锁并完成初始化后启动自动刷新线程。"""
        nonlocal worker
        with worker_lock:
            if worker is not None:
                return
            if stop_event.is_set():
                log.warning("[战绩条] 停止信号已设置，跳过后台线程启动")
                return
            worker = threading.Thread(
                target=auto_refresh_worker,
                name="lol-match-bar-auto-refresh",
                daemon=True,
            )
            worker.start()

    def stop_worker():
        """通知刷新线程退出；有限等待当前缓存提交完成后再等待线程收尾。"""
        # 先发出停止信号，避免新刷新进入缓存提交区。
        stop_event.set()

        # 主动通知所有 SSE 订阅者结束连接，避免关闭期间继续保留长连接。
        with lobby_event_lock:
            subscribers = list(lobby_event_subscribers)
            for subscriber in subscribers:
                try:
                    while True:
                        subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(SSE_SHUTDOWN)
                except queue.Full:
                    log.debug("[战绩条] SSE 结束通知队列已满")

        # 尝试等待已开始的本地缓存提交完成，但绝不让锁等待拖住主程序关闭。
        # 若超时，当前提交可能仍在执行；它结束后，后续提交会被停止检查拦截。
        cache_lock_acquired = cache_lock.acquire(timeout=0.1)
        if cache_lock_acquired:
            cache_lock.release()
        else:
            log.debug(
                "[战绩条] 缓存提交仍在执行；不等待其完成，主程序继续关闭"
            )

        game_end_event.set()
        refresh_wakeup_event.set()

        current_worker = worker
        if (
            current_worker is None
            or current_worker is threading.current_thread()
            or not current_worker.is_alive()
        ):
            return

        # 不等待正在进行的网络请求或缓存刷新；线程为守护线程，不会阻塞进程退出。
        current_worker.join(timeout=0.2)

        if current_worker.is_alive():
            log.debug(
                "[战绩条] 后台自动刷新仍在收尾；主程序不再等待"
            )

    def notify_game_ended():
        """由主程序的唯一 gameflow 监控线程通知：已检测到离开对局。"""
        game_end_event.set()
        refresh_wakeup_event.set()
        log.info("[战绩条] 已收到离开对局事件；准备立即刷新并启动追赶窗口")

    def notify_lcu_recognized():
        """由 LCU 识别线程通知：凭据首次识别或重新识别成功。"""
        refresh_wakeup_event.set()

    # 用于避免网页频繁轮询时反复输出相同的完整诊断日志。
    last_snapshot_diag_signature = None

    def safe_int(value, default=0):
        """安全转换整数，避免异常数据导致整个快照生成失败。"""
        try:
            return int(value or 0)
        except (TypeError, ValueError, OverflowError):
            return default

    def format_timestamp(timestamp_ms):
        """将毫秒时间戳转换为本地时间字符串。"""
        timestamp_ms = safe_int(timestamp_ms)

        if timestamp_ms <= 0:
            return "无效时间"

        try:
            return datetime.fromtimestamp(
                timestamp_ms / 1000
            ).strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, OverflowError):
            return f"无效时间戳({timestamp_ms})"

    def read_json(path, default):
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError, TypeError):
            return default

    def file_state(path):
        """读取文件元数据，用于判断快照缓存是否仍然有效。"""
        try:
            stat = path.stat() if path else None
            return (stat.st_mtime_ns, stat.st_size) if stat else None
        except OSError:
            return None

    def write_json(path, obj):
        """原子写入 JSON，避免程序中断时留下半截配置文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")

        with tmp.open("w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.flush()

        tmp.replace(path)

    def account_cache_path(puuid):
        """根据 puuid 生成账号专属缓存路径。"""
        safe_puuid = str(puuid or "").strip()

        if not safe_puuid:
            raise ValueError("puuid 为空，无法生成账号缓存路径")

        # puuid 来自 LCU，但仍限制为安全文件名字符。
        safe_name = "".join(
            ch for ch in safe_puuid
            if ch.isalnum() or ch in ("-", "_")
        )

        if not safe_name:
            raise ValueError("puuid 不包含有效文件名字符")

        return accounts_dir / f"{safe_name}.json"

    def get_config():
        nonlocal config_cache_state, config_cache_value

        with config_lock:
            try:
                stat = config_path.stat()
                file_state = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                file_state = None
            except OSError:
                # 文件状态暂时不可读时，不复用缓存，保持原有读取兜底行为。
                file_state = object()

            if (
                config_cache_value is not None
                and file_state == config_cache_state
            ):
                return dict(config_cache_value)

            cfg = dict(DEFAULTS)
            cacheable = True

            try:
                with config_path.open("r", encoding="utf-8") as f:
                    saved = json.load(f)
            except FileNotFoundError:
                saved = {}
            except (OSError, ValueError, TypeError):
                saved = {}
                # 读取失败时不缓存兜底结果，下一次请求仍可重试。
                cacheable = False

            if isinstance(saved, dict):
                cfg.update(saved)

            try:
                cfg["max_games"] = max(1, min(100, int(cfg["max_games"])))
            except (TypeError, ValueError):
                cfg["max_games"] = DEFAULTS["max_games"]

            try:
                cfg["mode_filter"] = (
                    1 if int(cfg["mode_filter"]) == 1 else 2
                )
            except (TypeError, ValueError):
                cfg["mode_filter"] = 1

            try:
                cfg["batch_gap_min"] = max(
                    1, min(100000, int(cfg["batch_gap_min"]))
                )
            except (TypeError, ValueError):
                cfg["batch_gap_min"] = DEFAULTS["batch_gap_min"]

            cfg["show_lobby_member_count"] = (
                cfg.get("show_lobby_member_count") is not False
            )

            if cacheable:
                # 缺少配置文件时缓存默认值；文件创建后状态会从 None 变更。
                config_cache_state = file_state
                config_cache_value = dict(cfg)

            return cfg

    def save_active_account(puuid, account, platform):
        """仅在活动账号信息变化时更新指针文件。"""
        current = get_active_account()
        next_puuid = str(puuid or "")
        next_account = str(account or "")
        next_platform = str(platform or "")

        if (
            current.get("puuid") == next_puuid
            and str(current.get("account") or "") == next_account
            and str(current.get("platform") or "") == next_platform
        ):
            return False

        write_json(active_account_path, {
            "puuid": next_puuid,
            "account": next_account,
            "platform": next_platform,
            "updated_at": time.time(),
        })
        return True

    def get_active_account():
        saved = read_json(active_account_path, {})

        if not isinstance(saved, dict):
            return {}

        puuid = str(saved.get("puuid") or "").strip()

        if not puuid:
            return {}

        return saved

    def load_account_cache(puuid, account, platform):
        """读取指定账号的独立战绩缓存；不存在或账号不匹配时初始化空缓存。"""
        path = account_cache_path(puuid)
        old = read_json(path, None)

        if isinstance(old, dict) and str(old.get("puuid") or "") == puuid:
            return old, path

        if path.exists():
            log.warning(
                "[战绩条诊断] 账号缓存文件存在但 puuid 不匹配，"
                "将初始化该账号缓存：file=%s，当前 puuid=%s，"
                "缓存 puuid=%s",
                path,
                puuid,
                old.get("puuid") if isinstance(old, dict) else None,
            )

        fresh = {
            "puuid": puuid,
            "account": account,
            "platform": platform,
            "matches": [],
            "manual_boundary_ms": 0,
        }

        return fresh, path

    def lcu_get(path, timeout=(1.5, 3.0), credentials=None):
        # 在真正发起网络请求前再次检查，封住停止信号与请求启动之间的竞态窗口。
        if stop_event.is_set():
            raise RuntimeError("程序正在关闭，已取消新的 LCU 请求")

        if client_is_recognized is not None and not client_is_recognized():
            raise RuntimeError("英雄联盟客户端尚未识别，已跳过 LCU 请求")
        if lcu_is_available is not None and not lcu_is_available():
            raise RuntimeError("LCU 凭据尚未验证通过，已跳过请求")
        if credentials is None:
            credentials = get_lcu_credentials()

        if not credentials:
            raise RuntimeError("未发现正在运行的英雄联盟客户端")

        port, token = credentials

        response = requests.get(
            f"https://127.0.0.1:{int(port)}{path}",
            auth=("riot", str(token)),
            verify=False,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

        response.raise_for_status()
        return response.json()

    def refresh_matches():
        """从 LoL 客户端获取战绩，并更新当前账号自己的缓存。"""
        if stop_event.is_set():
            return False, "程序正在关闭，已取消战绩刷新", False

        if client_is_recognized is not None and not client_is_recognized():
            message = "英雄联盟客户端尚未识别，暂不刷新战绩；现有缓存保持不变"
            with lock:
                status["message"] = message
            return False, message, False
        if lcu_is_available is not None and not lcu_is_available():
            message = "LCU 凭据尚未验证通过，暂不刷新战绩；现有缓存保持不变"
            with lock:
                status["message"] = message
            return False, message, False
        with lock:
            if status["busy"]:
                return False, "战绩刷新正在进行，请稍后再试", False

            status["busy"] = True
            status["message"] = "正在刷新战绩……"

        try:
            # 固定本轮刷新使用的凭据，避免账号切换后两次顺序请求串用不同账号。
            refresh_credentials = get_lcu_credentials()
            if not refresh_credentials:
                raise RuntimeError("未发现正在运行的英雄联盟客户端")

            summoner = lcu_get(
                "/lol-summoner/v1/current-summoner",
                credentials=refresh_credentials,
            )
            if get_lcu_credentials() != refresh_credentials:
                raise RuntimeError("刷新期间 LCU 凭据已变化，已丢弃本轮结果")
            if stop_event.is_set():
                return False, "程序正在关闭，已取消战绩刷新", False

            puuid = str(summoner.get("puuid") or "").strip()
            game_name = str(
                summoner.get("gameName")
                or summoner.get("displayName")
                or ""
            )
            tag_line = str(summoner.get("tagLine") or "")
            account = game_name + (f"#{tag_line}" if tag_line else "")

            if not puuid:
                raise RuntimeError("当前账号没有 puuid，无法读取战绩")

            cfg = get_config()
            end_index = max(20, min(100, cfg["max_games"] + 20))

            log.info(
                "[战绩条诊断] 开始刷新：账号=%s，puuid=%s，模式筛选=%s，"
                "max_games=%s，查询区间=0..%s",
                account,
                puuid,
                cfg["mode_filter"],
                cfg["max_games"],
                end_index - 1,
            )

            history = lcu_get(
                f"/lol-match-history/v1/products/lol/{puuid}"
                f"/matches?begIndex=0&endIndex={end_index - 1}",
                credentials=refresh_credentials,
            )
            if get_lcu_credentials() != refresh_credentials:
                raise RuntimeError("刷新期间 LCU 凭据已变化，已丢弃本轮结果")
            if stop_event.is_set():
                return False, "程序正在关闭，已取消战绩刷新", False

            platform = str(history.get("platformId") or "")
            games = history.get("games", {}).get("games", [])

            if not isinstance(games, list):
                raise RuntimeError("战绩接口返回格式异常")

            log.info(
                "[战绩条诊断] LCU 战绩接口返回：platform=%s，"
                "本次返回对局数=%s",
                platform,
                len(games),
            )

            with cache_lock:
                # 关闭信号或账号切换可能在请求、解析期间发生；提交前再次确认。
                if stop_event.is_set():
                    return False, "程序正在关闭，已取消战绩刷新", False
                if get_lcu_credentials() != refresh_credentials:
                    raise RuntimeError("刷新期间 LCU 凭据已变化，已丢弃本轮结果")

                old, matches_path = load_account_cache(
                    puuid, account, platform
                )

                if not isinstance(old, dict):
                    old = {}

                old_matches = old.get("matches", [])

                if not isinstance(old_matches, list):
                    old_matches = []

                indexed = {
                    str(item.get("gameId")): item
                    for item in old_matches
                    if isinstance(item, dict) and item.get("gameId")
                }

                newly_seen_ids = set()
                seen_game_ids = set(indexed.keys())
                parsed_count = 0
                unable_to_locate_count = 0

                for game in games:
                    try:
                        game_id = str(game.get("gameId") or "")

                        if not game_id or game_id in seen_game_ids:
                            continue

                        # 即使本局后续无法定位玩家，也不重复处理响应中的同一 gameId。
                        seen_game_ids.add(game_id)

                        participants = game.get("participants") or []
                        identities = game.get("participantIdentities") or []
                        my_pid = None

                        for identity in identities:
                            player = identity.get("player") or {}

                            if player.get("puuid") == puuid:
                                my_pid = identity.get("participantId")
                                break

                        mine = None

                        if my_pid is not None:
                            mine = next(
                                (
                                    p for p in participants
                                    if p.get("participantId") == my_pid
                                ),
                                None,
                            )
                        elif len(participants) == 1:
                            mine = participants[0]

                        if not mine:
                            unable_to_locate_count += 1
                            log.debug(
                                "[战绩条诊断] 无法定位当前账号的对局：gameId=%s",
                                game_id,
                            )
                            continue

                        stats = mine.get("stats") or {}
                        start_ms = safe_int(game.get("gameCreation"))
                        duration = safe_int(game.get("gameDuration"))

                        match_record = {
                            "gameId": game_id,
                            "startMs": start_ms,
                            "endMs": start_ms + duration * 1000,
                            "durationS": duration,
                            "gameMode": str(game.get("gameMode") or ""),
                            "gameType": str(game.get("gameType") or ""),
                            "queueId": safe_int(game.get("queueId")),
                            "championId": safe_int(mine.get("championId")),
                            "win": bool(stats.get("win", False)),
                            "remake": bool(
                                stats.get("gameEndedInEarlySurrender", False)
                            ),
                            "kills": safe_int(stats.get("kills")),
                            "deaths": safe_int(stats.get("deaths")),
                            "assists": safe_int(stats.get("assists")),
                        }
                        indexed[game_id] = match_record
                        newly_seen_ids.add(game_id)
                        parsed_count += 1

                    except (TypeError, ValueError, AttributeError):
                        log.exception("[战绩条诊断] 解析单局战绩失败")

                # 解析对局期间也可能发生账号切换；写入任何缓存前再核对一次。
                if get_lcu_credentials() != refresh_credentials:
                    raise RuntimeError("刷新期间 LCU 凭据已变化，已丢弃本轮结果")

                evicted_count = 0

                if newly_seen_ids:
                    # 只有新增对局时才重新排序，并将每个账号的缓存限制为最近 100 局。
                    cached_matches = sorted(
                        indexed.values(),
                        key=lambda item: (
                            safe_int(item.get("startMs")),
                            str(item.get("gameId") or ""),
                        ),
                        reverse=True,
                    )
                    evicted_count = max(
                        0, len(cached_matches) - CACHE_MATCH_LIMIT
                    )

                    if evicted_count:
                        evicted_matches = cached_matches[CACHE_MATCH_LIMIT:]
                        log.info(
                            "[战绩条诊断] 账号缓存达到上限：保留最近 %s 局，"
                            "清理最旧的 %s 局；最旧清理对局 gameId=%s",
                            CACHE_MATCH_LIMIT,
                            evicted_count,
                            [
                                str(item.get("gameId") or "")
                                for item in evicted_matches[:5]
                            ],
                        )
                        cached_matches = cached_matches[:CACHE_MATCH_LIMIT]

                    # 只有新增对局时才写入账号缓存。
                    old.pop("manual_pins", None)
                    old.update({
                        "puuid": puuid,
                        "account": account,
                        "platform": platform,
                        "matches": cached_matches,
                        "manual_boundary_ms": safe_int(
                            old.get("manual_boundary_ms")
                        ),
                        "fetched_at": time.time(),
                    })
                    write_json(matches_path, old)
                    cached_match_count = len(cached_matches)
                else:
                    # 无新增时直接读取索引数量，不创建额外列表。
                    cached_match_count = len(indexed)

                    # 即使没有新增对局，也同步账号显示信息；仅变化时写盘。
                    metadata_changed = (
                        str(old.get("account") or "") != account
                        or str(old.get("platform") or "") != platform
                    )
                    if metadata_changed:
                        old["account"] = account
                        old["platform"] = platform
                        write_json(matches_path, old)

                # 活动账号指针只在账号信息发生变化时写入。
                save_active_account(puuid, account, platform)

            message = (
                f"战绩刷新完成，账号 {account}，缓存 {cached_match_count} 局，"
                f"新增 {len(newly_seen_ids)} 局，清理最旧 {evicted_count} 局"
            )

            with lock:
                status["message"] = message

            log.info(
                "[战绩条诊断] 刷新合并完成：账号=%s，缓存文件=%s，"
                "接口返回=%s，成功解析=%s，无法定位账号=%s，"
                "缓存总数=%s，新增=%s，手动边界=%s",
                account,
                matches_path,
                len(games),
                parsed_count,
                unable_to_locate_count,
                cached_match_count,
                len(newly_seen_ids),
                safe_int(old.get("manual_boundary_ms")),
            )

            if newly_seen_ids:
                log.info(
                    "[战绩条诊断] 本轮新增 gameId：%s",
                    ", ".join(sorted(newly_seen_ids)),
                )

            log.info("[战绩条] %s", message)
            return True, message, bool(newly_seen_ids)

        except Exception as exc:
            message = f"战绩刷新失败：{exc}"
            log.exception("[战绩条诊断] %s", message)

            with lock:
                status["message"] = message

            return False, message, False

        finally:
            with lock:
                status["busy"] = False

    def auto_refresh_worker():
        """
        战绩自动刷新调度：
        - 启动时立即刷新；空闲状态每30秒完整刷新战绩并同步账号信息；
        - 收到主程序的离开对局事件后立即刷新，开启5分钟追赶窗口；
        - 追赶期间每5秒刷新；一旦发现新增 gameId，立即结束追赶；
        - 追赶窗口到期或发现新对局后，恢复30秒空闲轮询。
        """
        log.info(
            "[战绩条] 自动刷新线程启动：空闲%s秒；对局结束追赶%s分钟，"
            "追赶间隔%s秒；发现新对局后立即恢复空闲轮询",
            IDLE_REFRESH_INTERVAL,
            POST_GAME_CHASE_WINDOW // 60,
            POST_GAME_REFRESH_INTERVAL,
        )

        chase_deadline = None

        while not stop_event.is_set():
            # 本轮刷新会立即处理此前的唤醒；刷新期间新到的唤醒保留到等待阶段。
            refresh_wakeup_event.clear()

            # 结束事件可打断空闲等待；若刷新进行中收到事件，
            # 事件会保留到本轮结束后，再立即启动新一轮追赶。
            if game_end_event.is_set():
                game_end_event.clear()
                chase_deadline = time.monotonic() + POST_GAME_CHASE_WINDOW
                log.info(
                    "[战绩条] 检测到离开对局：立即刷新，开启%d秒追赶窗口",
                    POST_GAME_CHASE_WINDOW,
                )

            now = time.monotonic()
            chasing = (
                chase_deadline is not None
                and now < chase_deadline
            )

            # 每轮都执行完整战绩刷新：空闲时检查新对局并同步账号信息，
            # 追赶期间则按5秒间隔持续检查。
            try:
                ok, message, has_new_matches = refresh_matches()
                if ok and has_new_matches and chasing:
                    chase_deadline = None
                    log.info(
                        "[战绩条] 已发现新增对局，结束追赶，恢复%d秒空闲轮询",
                        IDLE_REFRESH_INTERVAL,
                    )
            except Exception:
                log.exception("[战绩条] 自动刷新出现未处理异常")

            now = time.monotonic()
            chasing = (
                chase_deadline is not None
                and now < chase_deadline
            )

            if chase_deadline is not None and not chasing:
                log.info(
                    "[战绩条] 5分钟追赶窗口到期，恢复%d秒空闲轮询",
                    IDLE_REFRESH_INTERVAL,
                )
                chase_deadline = None

            interval = (
                POST_GAME_REFRESH_INTERVAL
                if chase_deadline is not None
                else IDLE_REFRESH_INTERVAL
            )

            # 等待期间可被 LCU 识别成功或离开对局事件立即唤醒。
            refresh_wakeup_event.wait(interval)

        log.info("[战绩条] 后台自动刷新线程已退出")

    def snapshot():
        """根据最近成功识别的账号生成战绩条快照。"""
        nonlocal last_snapshot_diag_signature, snapshot_cache_key, snapshot_cache_result

        # 串行化快照生成：并发请求等待首个请求完成，随后复用其计算结果。
        with snapshot_cache_lock:
            cfg = get_config()

            # 先检查活动账号和文件元数据；快照命中时不读取、解析战绩 JSON。
            with cache_lock:
                active = get_active_account()
                puuid = str(active.get("puuid") or "").strip()
                matches_path = account_cache_path(puuid) if puuid else None

                # 文件元数据可检测对局缓存和手动边界的变化。
                cache_key = (
                    str(matches_path or ""),
                    file_state(matches_path),
                    str(active.get("puuid") or ""),
                    str(active.get("account") or ""),
                    str(active.get("platform") or ""),
                    tuple(sorted((str(k), repr(v)) for k, v in cfg.items())),
                )

                cache_hit = (
                    cache_key == snapshot_cache_key
                    and snapshot_cache_result is not None
                )

                if not cache_hit:
                    saved = (
                        read_json(matches_path, {})
                        if matches_path is not None else {}
                    )
                    if (
                        not isinstance(saved, dict)
                        or str(saved.get("puuid") or "") != puuid
                    ):
                        saved = {}

                    raw_matches = saved.get("matches", [])
                    if not isinstance(raw_matches, list):
                        raw_matches = []

            if cache_hit:
                result = dict(snapshot_cache_result)
                with lock:
                    result["status"] = status["message"]
                    result["busy"] = status["busy"]
                result["lobby_member_count"] = (
                    get_lobby_member_count()
                    if (
                        cfg["show_lobby_member_count"]
                        and callable(get_lobby_member_count)
                    ) else None
                )
                return result

            matches = [
                item for item in raw_matches
                if isinstance(item, dict)
            ]

            raw_count = len(matches)
            # 保留筛选前的有效对局列表，供诊断日志按需统计。
            unfiltered_matches = matches
            account = str(
                (saved.get("account") if isinstance(saved, dict) else "")
                or active.get("account")
                or ""
            )
            platform = str(
                (saved.get("platform") if isinstance(saved, dict) else "")
                or active.get("platform")
                or ""
            )
            puuid = str(active.get("puuid") or "")
            boundary = safe_int(
                saved.get("manual_boundary_ms")
                if isinstance(saved, dict) else 0
            )

            # 第一步：先按模式筛选。
            if cfg["mode_filter"] == 1:
                mode_rule = "gameMode 以 KIWI 开头"
                matches = [
                    m for m in matches
                    if str(m.get("gameMode") or "").startswith("KIWI")
                ]
            else:
                mode_rule = "gameType 等于 MATCHED_GAME"
                matches = [
                    m for m in matches
                    if m.get("gameType") == "MATCHED_GAME"
                ]

            mode_filtered_count = len(matches)

            # 第二步：应用该账号自己的手动边界。
            before_boundary_count = len(matches)

            if boundary > 0:
                matches = [
                    m for m in matches
                    if safe_int(m.get("startMs")) >= boundary
                ]

            after_boundary_count = len(matches)
            boundary_filtered_count = (
                before_boundary_count - after_boundary_count
            )

            # 第三步：排序。
            matches.sort(
                key=lambda m: (
                    safe_int(m.get("startMs")),
                    safe_int(m.get("gameId")),
                )
            )

            # 第四步：按相邻对局间隔划分批次。
            gap_ms = cfg["batch_gap_min"] * 60 * 1000
            batches = []
            i = 0

            while i < len(matches):
                j = i + 1

                while j < len(matches):
                    gap = (
                        safe_int(matches[j].get("startMs"))
                        - safe_int(matches[j - 1].get("endMs"))
                    )

                    if gap > gap_ms:
                        break

                    j += 1

                batches.append((i, j))
                i = j

            current_batch = len(batches) - 1 if batches else -1

            shown = []
            wins = 0
            losses = 0
            remake_count = 0
            current_batch_count = 0

            if current_batch >= 0:
                begin, end = batches[current_batch]
                batch = matches[begin:end]
                current_batch_count = len(batch)

                remake_count = sum(
                    1 for m in batch if m.get("remake")
                )

                valid = [
                    m for m in batch
                    if not m.get("remake")
                ]

                shown = valid[-cfg["max_games"]:]

                wins = sum(1 for m in shown if m.get("win"))
                losses = sum(1 for m in shown if not m.get("win"))

            has_games = bool(shown)
            region = REGION_NAMES.get(platform, platform) if platform else ""

            result_games = []

            for m in shown:
                result_games.append({
                    "win": bool(m.get("win")),
                    "champion_id": safe_int(m.get("championId")),
                    "k": safe_int(m.get("kills")),
                    "d": safe_int(m.get("deaths")),
                    "a": safe_int(m.get("assists")),
                    "gameId": str(m.get("gameId") or ""),
                })

            with lock:
                message = status["message"]
                busy = status["busy"]

            # 仅当关键快照数据变化时，输出完整诊断日志。
            signature = (
                puuid,
                account,
                platform,
                cfg["mode_filter"],
                cfg["max_games"],
                cfg["batch_gap_min"],
                raw_count,
                tuple(
                    (
                        str(m.get("gameId") or ""),
                        safe_int(m.get("startMs")),
                        str(m.get("gameMode") or ""),
                        str(m.get("gameType") or ""),
                        bool(m.get("remake")),
                    )
                    for m in matches
                ),
                boundary,
                tuple(m["gameId"] for m in result_games),
                current_batch,
            )

            if signature != last_snapshot_diag_signature:
                last_snapshot_diag_signature = signature

                # 仅在需要输出诊断日志时统计筛选前的模式分布。
                mode_stats = {}
                for m in unfiltered_matches:
                    mode = str(m.get("gameMode") or "(空)")
                    mode_stats[mode] = mode_stats.get(mode, 0) + 1

                log.info(
                    "[战绩条诊断] 快照开始：file=%s，puuid=%s，"
                    "缓存记录=%s，有效字典记录=%s，账号=%r，平台=%r，"
                    "mode_filter=%s，max_games=%s，batch_gap_min=%s",
                    matches_path,
                    puuid,
                    len(raw_matches),
                    raw_count,
                    account,
                    platform,
                    cfg["mode_filter"],
                    cfg["max_games"],
                    cfg["batch_gap_min"],
                )

                log.info(
                    "[战绩条诊断] 筛选前 gameMode 统计：%s",
                    mode_stats,
                )

                log.info(
                    "[战绩条诊断] 模式筛选完成：规则=%s，"
                    "筛选前=%s，筛选后=%s，过滤掉=%s",
                    mode_rule,
                    raw_count,
                    mode_filtered_count,
                    raw_count - mode_filtered_count,
                )

                log.info(
                    "[战绩条诊断] 手动边界检查：boundary_ms=%s，"
                    "boundary_time=%s",
                    boundary,
                    format_timestamp(boundary) if boundary else "未设置",
                )

                if boundary > 0:
                    log.info(
                        "[战绩条诊断] 边界筛选完成：筛选前=%s，"
                        "边界后保留=%s，边界前过滤=%s",                    before_boundary_count,
                        after_boundary_count,
                        boundary_filtered_count,
                    )
                else:
                    log.info(
                        "[战绩条诊断] 未设置有效手动边界，"
                        "未执行边界过滤；当前候选对局=%s",
                        after_boundary_count,
                    )

                if matches:
                    first = matches[0]
                    latest = matches[-1]

                    log.info(
                        "[战绩条诊断] 排序完成：最早对局 ID=%s，时间=%s；"
                        "最新对局 ID=%s，时间=%s",
                        first.get("gameId"),
                        format_timestamp(first.get("startMs")),
                        latest.get("gameId"),
                        format_timestamp(latest.get("startMs")),
                    )
                else:
                    log.info(
                        "[战绩条诊断] 当前没有符合模式及手动边界条件的对局"
                    )

                log.info(
                    "[战绩条诊断] 批次划分完成：筛选后对局=%s，"
                    "批次数=%s，阈值=%s 分钟",
                    len(matches),
                    len(batches),
                    cfg["batch_gap_min"],
                )

                for batch_index, (begin, end) in enumerate(batches, start=1):
                    batch = matches[begin:end]

                    log.info(
                        "[战绩条诊断] 批次 #%s：局数=%s，"
                        "首局 ID=%s（%s），末局 ID=%s（%s）",
                        batch_index,
                        len(batch),
                        batch[0].get("gameId"),
                        format_timestamp(batch[0].get("startMs")),
                        batch[-1].get("gameId"),
                        format_timestamp(batch[-1].get("startMs")),
                    )

                log.info(
                    "[战绩条诊断] 当前批次选择：current_batch=%s，"
                    "批次数=%s，当前批次局数=%s",
                    current_batch,
                    len(batches),
                    current_batch_count,
                )

                log.info(
                    "[战绩条诊断] 最终结果：当前批次局数=%s，"
                    "remake 数=%s，展示局数=%s，胜=%s，负=%s，"
                    "展示 gameId=%s",
                    current_batch_count,
                    remake_count,
                    len(result_games),
                    wins,
                    losses,
                    [m["gameId"] for m in result_games],
                )

                if not result_games:
                    log.warning(
                        "[战绩条诊断] 最终结果为空！缓存记录=%s，"
                        "模式筛选后=%s，边界后=%s，批次数=%s，"
                        "手动边界=%s，当前批次=%s，"
                        "当前批次局数=%s，remake 数=%s，最终展示=0",
                        raw_count,
                        mode_filtered_count,
                        after_boundary_count,
                        len(batches),
                        (
                            format_timestamp(boundary)
                            if boundary else "未设置"
                        ),
                        current_batch,
                        current_batch_count,
                        remake_count,
                    )

            result = {
                "games": result_games if has_games else [],
                "account": account,
                "region": region,
                "lobby_member_count": (
                    get_lobby_member_count()
                    if (
                        cfg["show_lobby_member_count"]
                        and callable(get_lobby_member_count)
                    ) else None
                ),
                "wins": wins,
                "losses": losses,
                "config": cfg,
                "status": message,
                "busy": busy,
                "fetched_at": (
                    saved.get("fetched_at", 0)
                    if isinstance(saved, dict) else 0
                ),
            }

            # 只缓存静态统计结果；运行状态和组队人数在每次请求时实时更新。
            snapshot_cache_key = cache_key
            snapshot_cache_result = dict(result)

            return result

    @bp.get("/bar")
    def bar_page():
        return render_template("bar.html")

    @bp.get("/api/snapshot")
    def bar_snapshot_api():
        return jsonify(snapshot())

    @bp.get("/api/lobby-events")
    def bar_lobby_events():
        subscriber = queue.Queue(maxsize=1)

        @stream_with_context
        def event_stream():
            with lobby_event_lock:
                shutting_down = stop_event.is_set()
                if not shutting_down:
                    lobby_event_subscribers.add(subscriber)
            try:
                if shutting_down:
                    yield "event: shutdown\ndata: {}\n\n"
                    return

                # 连接建立后先同步一次，避免页面断线期间错过最后一次变更。
                yield "event: lobby-change\ndata: {}\n\n"
                while True:
                    try:
                        payload = subscriber.get(timeout=15)
                        if payload is SSE_SHUTDOWN:
                            yield "event: shutdown\ndata: {}\n\n"
                            return
                        yield f"event: lobby-change\ndata: {payload}\n\n"
                    except queue.Empty:
                        yield ": keep-alive\n\n"
            except GeneratorExit:
                raise
            finally:
                with lobby_event_lock:
                    lobby_event_subscribers.discard(subscriber)

        return Response(
            event_stream(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @bp.get("/api/bar-config")
    def bar_config_api():
        return jsonify(get_config())

    @bp.post("/api/bar-config")
    def bar_save_config_api():
        body = request.get_json(silent=True) or {}

        try:
            cfg = dict(DEFAULTS)
            cfg.update({
                "max_games": body.get(
                    "max_games", DEFAULTS["max_games"]
                ),
                "mode_filter": body.get(
                    "mode_filter", DEFAULTS["mode_filter"]
                ),
                "batch_gap_min": body.get(
                    "batch_gap_min", DEFAULTS["batch_gap_min"]
                ),
                "show_lobby_member_count": (
                    body.get("show_lobby_member_count", True) is True
                ),
            })

            cfg["max_games"] = max(
                1, min(100, int(cfg["max_games"]))
            )
            cfg["mode_filter"] = (
                1 if int(cfg["mode_filter"]) == 1 else 2
            )
            cfg["batch_gap_min"] = max(
                1, min(100000, int(cfg["batch_gap_min"]))
            )

            nonlocal config_cache_state, config_cache_value
            with config_lock:
                write_json(config_path, cfg)
                # 文件内容已更新，丢弃旧缓存；下一次读取会重新加载并校验。
                config_cache_state = object()
                config_cache_value = None

            log.info(
                "[战绩条诊断] 配置已保存：max_games=%s，"
                "mode_filter=%s，batch_gap_min=%s，"
                "show_lobby_member_count=%s",
                cfg["max_games"],
                cfg["mode_filter"],
                cfg["batch_gap_min"],
                cfg["show_lobby_member_count"],
            )

            return jsonify({"ok": True, "config": cfg})

        except (TypeError, ValueError, OSError) as exc:
            log.exception("[战绩条诊断] 保存配置失败")
            return jsonify({"ok": False, "error": str(exc)}), 400

    @bp.post("/api/bar-action")
    def bar_action_api():
        body = request.get_json(silent=True) or {}
        action = str(body.get("action") or "")

        if action == "refresh":
            ok, message, _ = refresh_matches()

            return jsonify({
                "ok": ok,
                "message": message,
            }), (200 if ok else 503)

        if action == "new_batch":
            with cache_lock:
                active = get_active_account()
                puuid = str(active.get("puuid") or "").strip()

                if not puuid:
                    return jsonify({
                        "ok": False,
                        "error": "尚未识别当前游戏账号，请等待战绩刷新完成后再开启新批次",
                    }), 409

                matches_path = account_cache_path(puuid)
                saved = read_json(matches_path, {})

                if not isinstance(saved, dict) or str(saved.get("puuid") or "") != puuid:
                    saved = {
                        "puuid": puuid,
                        "account": str(active.get("account") or ""),
                        "platform": str(active.get("platform") or ""),
                        "matches": [],
                        "manual_boundary_ms": 0,
                    }

                now_ms = int(time.time() * 1000)
                saved["manual_boundary_ms"] = now_ms

                # 兼容并清理旧缓存中的无用字段，不影响有效的时间边界。
                saved.pop("manual_pins", None)

                write_json(matches_path, saved)

            with lock:
                status["message"] = "已开启新批次，等待下一局结算"

            log.info(
                "[战绩条诊断] 手动开启新批次：账号=%s，puuid=%s，"
                "缓存文件=%s，boundary_ms=%s，boundary_time=%s；"
                "此前的对局将不再显示，只展示边界时间及之后符合筛选条件的对局",
                active.get("account", ""),
                puuid,
                matches_path,
                now_ms,
                format_timestamp(now_ms),
            )

            return jsonify({
                "ok": True,
                "message": status["message"],
            })

        return jsonify({"ok": False, "error": "未知操作"}), 400

    app.register_blueprint(bp)

    # 这里只注册路由和准备启动入口；后台线程由主程序取得单实例锁后启动。
    log.info(
        "[战绩条] 已注册路由：/bar、/api/snapshot、/api/lobby-events、"
        "/api/bar-config、/api/bar-action；账号缓存目录=%s",
        accounts_dir,
    )
    return (
        notify_game_ended,
        start_worker,
        stop_worker,
        notify_lobby_member_count_changed,
        notify_lcu_recognized,
    )