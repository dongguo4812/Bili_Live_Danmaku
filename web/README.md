# 统一管理页面

统一管理服务由主程序现有 Flask 应用承载，不另起第二个 HTTP 服务。页面和路由：
- `/`：接入中心，B 站二维码登录、房间号、WebSocket 连接状态和模块生命周期控制。
- `/music`、`/video`：歌曲/视频独立队列管理。
- `/music/settings`、`/video/settings`：独立模块配置；模块运行时禁止修改其配置。
- `/settings`：系统设置和 OBS 凭据管理。
- `/dashboard`：综合展示布局编辑器，支持添加/移除、拖动、调整大小和保存。
- `/display/combined`：OBS 综合展示输出；视频只显示当前/下一条/队列/通知，不嵌入视频画面。

原有 `/display` 和 `/api/live2d/*` 等 Live2D 接口继续由主 Flask 应用提供，避免破坏现有 OBS 浏览器源。
