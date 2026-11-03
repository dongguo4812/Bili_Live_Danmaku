# 核心基础设施

- `ConfigManager`：从 `config/default/*.json` 读取默认模板，并将实际配置统一读写到 `config/local/*.json`；拒绝 Cookie、密码、Token、API Key 和授权头等敏感字段。
- `CredentialStore`：单独保存 B 站 Cookie 与 OBS 密码等敏感凭据，不写入普通配置。
- `EventBus`：在统一直播连接与业务模块之间分发标准化事件。
- `ModuleManager`：统一管理 Live2D、语音、歌曲和视频模块的生命周期。
- `ApplicationRuntime`：承载后台异步任务，并为 Flask 页面/API 提供线程安全的调用入口。

配置模板由 Git 跟踪；本机配置、凭据、队列状态和日志均不应提交到仓库。
