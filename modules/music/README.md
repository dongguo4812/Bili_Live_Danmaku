# 歌曲模块（整合版）

歌曲业务逻辑从原独立点歌项目迁移，保留：
- QQ → 酷狗 → 网易云 → 酷我搜索顺序，每个平台最多 20 条（由配置控制）。
- 歌名完全匹配、歌手完全匹配优先、歌手包含匹配回退。
- Live/DJ/Cover/翻唱/伴奏/纯音乐等版本过滤。
- 将已匹配的完整音源信息和歌曲 ID 存入队列，播放时不重新搜索。
- 洛雪播放调用、播放状态核验、错误曲目暂停。
- 当前歌曲、队列查重、冷却、粉丝牌校验、管理员切歌、点歌开关和展示通知。

整合边界：
- 不创建自己的 B 站二维码登录、Cookie 文件或 LiveDanmaku 连接。
- 不创建独立 HTTP 服务或强制重设根日志。
- 通过统一事件总线接收 `danmaku` 标准事件。
- 歌曲模块启动默认关闭；生命周期由统一 ModuleManager 控制。
- 断线暂停与重连恢复只作用于歌曲模块自身。

控制接口：
- `POST /api/modules/music/start|pause|resume|close`
- `GET /api/music/queue`
- `POST /api/music/action`（保留 skip/delete/clear/set_song_request）

洛雪官方开放 API 提供 `/play` 和 `/pause`，没有独立停止接口。因此关闭歌曲模块时会暂停当前歌曲、取消点播 worker 并清空机器人队列；不会调用不存在的停止 API。
