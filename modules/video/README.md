# 视频模块（整合版）

从原独立视频点播项目迁移，保留：
- B 站 BV 号格式校验、公开视频信息查询、标题/时长缓存。
- 默认 30 条待播队列、普通观众 60 秒冷却、普通观众最长 300 秒视频限制。
- 本直播间粉丝勋章严格校验；管理员 UID 豁免粉丝牌、冷却和视频时长限制。
- Selenium + Chrome 独立 Profile，打开 B 站视频、网页全屏、播放位置重置。
- HTML5 video 播放状态监控、结束自动切换、合集自动跳转识别。
- 手动切换、删除队列项、清空队列。
- OBS WebSocket 来源显隐控制。

整合边界：
- 不创建自己的 B 站登录、Cookie 文件、LiveDanmaku 连接、HTTP 服务或日志线程。
- 从主程序统一登录凭据注入 Cookie，只用于粉丝牌校验。
- 视频模块启动时才创建 Chrome；关闭时关闭 Chrome 会话并清空视频队列。
- 暂停/恢复只操作 Chrome HTML5 video，保留当前 BV 和播放进度。
- 不调用洛雪音乐 API；歌曲和视频模块完全独立。

控制接口：
- `POST /api/modules/video/start|pause|resume|close`
- `GET /api/video/queue`
- `POST /api/video/action`（next/delete/clear）

OBS 密码由 `CredentialStore` 从 `credentials/obs.json` 读取；普通 OBS 地址、端口和来源名称来自 `config/obs.json`。
