# 核心服务

核心层已接入主程序，负责：
- `ConfigManager`：读写 `config/*.json`，拒绝 Cookie、密码、Token、API Key 和授权头等敏感字段；首次运行时一次性导入旧版 `config.json` 普通设置。
- `CredentialStore`：Windows 使用 DPAPI 保存 OBS 等敏感凭据；接口只返回配置状态，不回显秘密原文。
- `EventBus`：把主项目 WebSocket 事件统一为 `type/uid/nickname/text/timestamp`，并异步分发给歌曲、视频等模块。
- `AsyncRuntime`：为业务模块提供唯一的专用 asyncio 运行时，关闭时清理任务、异步生成器和默认线程池。
- `ModuleManager`：统一管理模块启动、暂停、恢复、关闭、断线状态快照和手动操作优先级。
- `ApplicationRuntime`：聚合事件总线、模块管理器和异步运行时，并由主程序统一启动/关闭。

核心层不创建第二个 B 站连接，也不依赖歌曲/视频模块的私有实现。
