# 统一管理页面

统一管理服务由主程序现有 Flask 应用承载，不另起第二个 HTTP 服务。

## 管理页面

- `/`：接入中心，B 站二维码登录、房间号、WebSocket 连接状态和模块生命周期控制。
- `/live2d`：跳转到首页的 Live2D 设置区域。
- `/music`、`/video`：歌曲/视频独立队列管理。
- `/music/settings`、`/video/settings`：独立模块配置；模块运行时禁止修改其配置。
- `/voice`：语音模块设置。
- `/settings`：系统设置和 OBS 普通配置。
- `/credentials`：B 站登录凭据状态与 OBS 密码管理。
- `/dashboard`：综合展示布局编辑器，支持添加/移除、拖动、调整大小和保存。

## OBS 展示入口

- `/live2d/display`：Live2D、弹幕和 TTS 综合展示。
- `/live2d/moc2/display`：仅 Live2D MOC2 展示。
- `/danmaku/display`：仅弹幕展示。
- `/voice/display`：仅语音展示。
- `/music/display`：歌曲队列展示。
- `/music/widget`：歌曲信息组件。
- `/video/display`：视频队列展示。
- `/display/combined`：默认综合展示布局。
- `/display/combined/<layout_id>`：指定综合展示布局。

旧的 `/display`、`/display/live2d`、`/display/danmaku`、`/display/tts`、`/display/music`、`/display/music-widget` 和 `/display/video` 页面入口已退役，不应再用于 OBS 浏览器源。Live2D 设置 API 使用 `/api/live2d/*`；综合布局数据 API 使用 `/api/display/layout*`。
