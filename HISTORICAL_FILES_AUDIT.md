# 历史文件与兼容逻辑审计

> 基准：`master` 在 2026-11-03 的代码状态（统一管理中心合并提交 `72e98090647caa41dc68fca885793903039bcc5c`）。
>
> 本文是清理台账，不代表已删除任何文件。所有删除必须先满足对应迁移条件，并完成代码、注释、打包脚本、README/配置说明及测试同步。

## 状态定义

- **待核实**：需要确认用户本地数据或运行行为，暂不删除。
- **待迁移**：仍存在实际迁移/兼容读取代码。
- **可清理（需先改代码）**：数据迁移完成后，先移除引用，再删除文件。
- **保留**：当前运行所需文件，不属于清理对象。
- **文档待更新**：代码与文档描述不一致，需在对应清理批次修正文档。

## A. 历史文件候选清单

| 候选路径 | 用途 / 历史来源 | 当前引用位置（master） | 迁移条件 | 删除状态 |
|---|---|---|---|---|
| 根目录 `user_settings.json`（本地文件，已被 .gitignore 排除） | 旧版房间号、Live2D 模型/模型偏好、idle_tips、TTS 设置 | `main.py`：41 行定义 `USER_SETTINGS_PATH`；175-224 行附近 `_load_user_settings()` 读取并迁移；`build.bat` 约 50 行仍复制到 EXE 输出目录；`.gitignore` 忽略 | 确认所有仍在使用的旧安装目录均已成功迁入 `config/live.json`、`config/live2d.json`、`config/voice.json`；确认无回滚需求；移除迁移代码及打包复制 | **待迁移**。不可直接删除；打包脚本不应继续复制它 |
| 根目录 `config.json`（本地文件，已被 .gitignore 排除） | 旧版聚合配置；OBS 旧密码迁移来源 | `main.py`：39 行定义；启动初始化约 81 行调用 `ConfigManager.migrate_legacy_config()`；1906-2000 行附近旧 OBS 凭据检查/迁移 API；`core/config_manager.py` 的 `migrate_legacy_config()` | 确认普通配置迁移已完成；旧 OBS 明文密码已通过凭据页迁移或由用户明确放弃；确定不再支持旧版本升级后，移除启动迁移及 OBS 旧凭据迁移流程 | **待迁移**。含潜在敏感信息，不能自动删除或复制 |
| `data/display_layout.json`（运行时本地文件，通常不入库） | 旧版单套综合展示布局 | `main.py`：42 行定义 `DISPLAY_LAYOUT_PATH`；约 1745-1765 行 `_load_display_layout_store()` 在新多布局仓库不存在时读取旧文件 | 确认 `data/display_layouts.json` 已创建且布局内容、组件位置和尺寸完整；必要时人工备份；移除旧布局读取分支 | **待迁移**。新文件存在后旧文件仍被代码保留，清理需分阶段 |
| `room_config.txt`（本地旧配置，.gitignore 忽略） | 独立视频点播程序旧房间/点播限制设置 | `modules/video/service.py`：34-98 行附近 `migrate_legacy_video_room_config()`；约 156 行启动调用；迁移标记 `data/video/legacy_room_config_migrated_v1.json` | 确认视频限制已迁入 `config/video.json`，迁移标记已生成且值正确；确认旧用户数据已备份 | **待迁移** |
| `obs_config.txt`（本地旧配置，.gitignore 忽略） | 独立视频点播程序旧 OBS host/port/source_name 等设置 | `modules/video/service.py`：101 行起 `migrate_legacy_video_obs_config()`；约 156 行启动调用；迁移标记 `data/video/legacy_obs_config_migrated_v1.json`；`main.py` 凭据状态 API 仅检查该文件是否存在 | 确认普通 OBS 字段已迁入 `config/obs.json`；密码必须单独走凭据迁移/重新录入；确认 OBS 实际连接正常 | **待迁移**。不要把密码字段当普通配置迁移 |
| `data/music/uid.txt`（本地旧管理员名单） | 旧歌曲模块独立管理员 UID | `main.py`：约 1091-1124 行 `_load_system_admin_uids()` 首次合并；`modules/music/service.py`：35、45-60、85-94 行附近旧文件读取路径；系统注入配置会清空 `admins_file` | 确认所有 UID 已写入 `config/app.json` 的系统级 `admin_uids`，并确认管理页面显示正确；移除首次导入和模块读取代码 | **待迁移** |
| `data/video/uid.txt`（本地旧管理员名单） | 旧视频模块独立管理员 UID | `main.py`：约 1091-1124 行首次合并；`modules/video/service.py`：198-220 行附近 `load_admin_uids()`；模块注入时清空 `admins_file` | 同歌曲名单；确认系统管理员 UID 对歌曲、视频均生效 | **待迁移** |
| 根目录 `uid.txt`（本地旧文件） | 更早独立点播程序管理员名单；当前代码不应再使用根目录默认路径 | `.gitignore` 忽略；本轮检索未发现主程序对根目录 `uid.txt` 的直接读取；需检查部署脚本/外部旧程序 | 确认当前仓库程序和用户仍运行的外部工具均不依赖；确认名单已汇总 | **待核实**。不得与 `data/music/uid.txt`、`data/video/uid.txt` 混为一谈 |
| 根目录 `bili_cookie.txt`（本地敏感旧文件） | 独立机器人时代的 B 站 Cookie | `.gitignore` 忽略；`main.py` 凭据状态 API 只报告文件存在，不自动导入 Cookie | 确认当前登录已通过二维码保存在 `credentials/bilibili.json` 且验证有效；用户确认旧 Cookie 不再需要后本机安全删除 | **待核实 / 敏感**。不读取、不打印、不提交、不自动迁移 |
| `config/.legacy_config_migrated_v1.json`（运行时标记） | 标记旧根目录 config 迁移完成 | `core/config_manager.py` 的 `migrate_legacy_config()` 读写；`.gitignore` 已忽略 | 只有彻底移除旧配置迁移代码后，才考虑停止生成；已有标记可留作审计记录 | **保留至迁移代码退役** |
| `config/.legacy_config_migration_v1.json`（运行时迁移日志/计划） | 中断恢复用迁移 journal，记录迁移前后配置 | `core/config_manager.py` 的 `migrate_legacy_config()` 创建、读取、成功后删除；`.gitignore` 当前未明确忽略该文件 | 迁移功能退役前必须保留中断恢复能力；立即补充忽略规则，防止误提交 | **暂不删除；待补 .gitignore** |
| `data/video/legacy_room_config_migrated_v1.json` | 旧 room_config 迁移完成标记 | `modules/video/service.py` 的 room config migration | 视频旧配置迁移机制退役后可停止生成；本地标记可保留或由维护脚本清理 | **保留至迁移代码退役** |
| `data/video/legacy_obs_config_migrated_v1.json` | 旧 obs_config 迁移完成标记 | `modules/video/service.py` 的 OBS config migration | 同上 | **保留至迁移代码退役** |

## B. 当前配置文件：明确保留

以下文件是当前活动配置，不得作为历史垃圾删除：

| 路径 | 用途 | 状态 |
|---|---|---|
| `config/app.json` | 服务/系统设置、统一管理员 UID | **保留** |
| `config/live.json` | 直播间号、连接/重连设置 | **保留** |
| `config/live2d.json` | Live2D 模型、模型偏好和展示设置 | **保留** |
| `config/voice.json` | TTS 设置 | **保留** |
| `config/obs.json` | OBS 普通连接设置 | **保留** |
| `config/music.json` | 歌曲模块配置 | **保留** |
| `config/video.json` | 视频模块配置 | **保留** |
| `config/logging.json` | 日志级别、保留天数等 | **保留** |
| `data/display_layouts.json` | 当前多套综合展示布局 | **保留** |
| `credentials/bilibili.json`、`credentials/obs.json` | 当前敏感凭据（不应提交） | **保留并保护** |
| `models/**/*.json` | Live2D 模型描述、动作、物理等资源 | **保留** |

## C. 必须同步检查和更新的代码、注释、脚本、文档

### 代码与脚本

- `main.py`：旧根目录配置常量、启动迁移调用、`_load_user_settings()` 中旧文件读取、旧管理员名单导入、旧 OBS 凭据兼容 API、旧布局迁移分支。
- `core/config_manager.py`：旧 `config.json` 的迁移实现、默认值合并、journal/marker 生命周期。删除前需确定是否还支持从旧版本升级。
- `modules/video/service.py`：`room_config.txt` 和 `obs_config.txt` 的迁移函数、启动调用、迁移标记。
- `modules/music/service.py`：独立管理员 UID 文件解析函数和默认路径。
- `modules/video/service.py`：独立管理员 UID 文件解析函数和默认路径。
- `build.bat`：移除复制 `user_settings.json` 的命令；确保只复制当前 `config/`、资源和文档。
- `.gitignore`：增加 `config/.legacy_config_migration_v1.json` 忽略规则；清理重复的旧文件规则时确保不放开本地敏感文件。

### 文档（已发现需要修正的过期描述）

- `README.md`：仍声称房间号和 Live2D 偏好保存在根目录 `user_settings.json`；仍声称重连配置在根目录 `config.json`；多处称模型偏好保存在 `user_settings.json`。应改为当前 `config/live.json`、`config/live2d.json`、`config/voice.json` 等真实路径。
- `README.zh-CN.md`：登录与房间号说明基本指向 `config/live.json`，但“本地数据与安全”段落仍需明确当前配置、凭据、运行数据的职责，并准确说明旧配置迁移只在兼容期存在；管理员 UID 统一存放 `config/app.json`。
- `config/README.md`：说明旧 `config.json`、`user_settings.json` 迁移；迁移机制退役后必须更新，不能保留过时承诺。
- 本文件 `HISTORICAL_FILES_AUDIT.md`：每次清理后更新状态、删除提交、验证结果和仍存引用。

## D. 推荐清理顺序

1. **审计与文档校正**：先维护本台账；修正文档中已与当前实现不符的配置路径；补充 journal 忽略规则。此步骤不删除用户数据。
2. **迁移状态核查**：检查各旧迁移标记和目标配置是否存在、字段是否完整；对管理员 UID、房间号、Live2D/TTS、OBS 参数逐项核对。不能仅凭“标记文件存在”就推断迁移内容正确。
3. **退役旧管理员文件**：确认系统级 UID 完整后，移除旧名单导入与模块级文件读取函数，更新相关测试和 README，再停止依赖旧 UID 文件。
4. **退役视频文本配置迁移**：确认 `config/video.json` 与 `config/obs.json` 完整且运行正常后，移除迁移函数和标记依赖，更新文档/测试。
5. **退役单布局迁移**：确认多布局数据无损后，移除 `data/display_layout.json` 读取兼容分支。
6. **退役根目录 user_settings.json**：确认所有旧字段均已迁入新配置，移除读取逻辑和 build 复制命令；更新 README、注释、测试。
7. **退役根目录 config.json**：最后处理。先确保 OBS 密码已迁入凭据库，移除旧普通配置迁移和旧凭据迁移入口，更新安全说明及测试。该文件可能包含敏感历史内容，用户本机文件由用户自行安全处置。
8. **全仓复查**：搜索每个已退役文件名、旧配置字段、旧迁移函数、注释、Markdown、批处理和测试；确认只剩审计历史说明或明确的安全忽略规则。执行 Python 编译、配置迁移测试、凭据测试、Windows 专项测试和完整 CI。

## E. 每批清理验收标准

- 先确认迁移目标数据存在且值正确，不覆盖用户已经修改的新配置。
- 删除兼容读取代码前，必须同步删除/改写对应迁移测试；新增“旧文件不存在也能正常启动”的回归测试。
- 任何敏感旧文件不在 CI 日志中输出内容；不读取、不提交 Cookie/密码。
- 全仓搜索旧文件名与旧字段，检查 Python、HTML/JS、BAT、YAML、Markdown、测试和工作流。
- 更新所有相关注释、README、`config/README.md`、打包脚本和本台账。
- CI 全部通过后才把状态改为“已清理”；代码删除不等于用户本地旧文件已删除。
- 通过 PR 合并到 `master`；本地目录需要用户自行同步 master。

## F. 清理记录

| 日期 | 批次 | 变更 / 删除 | 验证 | 状态 |
|---|---|---|---|---|
| 2026-11-03 | 初始审计 | 新建本台账；尚未删除任何历史文件 | 已核对 master 中主要引用位置与 README/build/.gitignore | 已记录，待逐项清理 |
