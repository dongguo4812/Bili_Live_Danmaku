# 视频模块（整合版）

从原独立视频点播项目迁移，保留：
- B 站 BV 号格式校验、公开视频信息查询、标题/时长缓存。
- 默认 30 条待播队列、普通观众 60 秒冷却、普通观众最长 300 秒视频限制。
- 按 `config/video.json` 的 `requests.require_fans_medal` 开关执行本直播间粉丝勋章严格校验；仓库默认关闭，与旧版 `room_config.txt` 的默认配置一致。管理员 UID 豁免粉丝牌、冷却和视频时长限制。
- Selenium + Chrome 独立 Profile，打开 B 站视频、重置播放位置。
- 仅在 HTML5 视频确认产生播放进度后，尝试点击 B 站播放器网页全屏 W；不使用 F/F11。
- HTML5 video 播放状态监控、结束自动切换、合集自动跳转识别。
- 手动切换、删除队列项、清空队列。
- OBS WebSocket 来源显隐控制。

整合边界：
- 不创建自己的 B 站登录、Cookie 文件、LiveDanmaku 连接、HTTP 服务或日志线程。
- 从主程序统一登录凭据注入 Cookie，只用于粉丝牌校验；重连恢复时刷新 Cookie。
- 视频模块启动时才创建 Chrome；关闭时关闭 Chrome 会话并清空视频队列。
- 暂停/恢复只操作 Chrome HTML5 video，保留当前 BV 和播放进度；暂停时不会因为进度接近结尾而自动切换。
- 不调用洛雪音乐 API；歌曲和视频模块完全独立。
- 暂停/关闭或粉丝牌、视频信息查询尚未完成时，不允许过期弹幕在模块关闭后重新入队。

控制接口：
- `POST /api/modules/video/start|pause|resume|close`
- `GET /api/video/queue`
- `POST /api/video/action`（next/delete/clear）

配置与管理员：
- 普通设置：`config/video.json`；默认冷却 60 秒、队列 30 条、普通观众最长 300 秒、默认不要求粉丝牌。
- 管理员 UID 文件：`data/video/uid.txt`，每行一个 UID；该目录属于本地运行数据，不提交到 Git。
- 若旧项目存在 `room_config.txt`，首次整合启动前将其复制到整合项目根目录；程序会一次性迁移粉丝牌要求、冷却、视频时长和队列上限，不会覆盖主程序统一房间号。
- 从旧独立视频项目迁移时，请将旧项目根目录的 `uid.txt` 手动复制到整合项目的 `data/video/uid.txt`；管理员列表不会从旧公开仓库自动下载或提交。
- OBS 密码由 `CredentialStore` 从 `credentials/obs.json` 读取；普通 OBS 地址、端口和来源名称来自 `config/obs.json`。
