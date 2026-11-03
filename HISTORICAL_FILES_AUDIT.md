# 历史文件与兼容逻辑清理台账

> 审计基准：统一管理中心合并后的 `master`（`72e98090647caa41dc68fca885793903039bcc5c`）。
> 当前批次：第一批历史配置迁移完成后的代码清理。
>
> **重要边界：** 本 PR 只清理仓库代码、模板和文档，不访问用户电脑，不删除用户本地旧文件，也不能替用户证明旧配置已完整迁入。仓库删除文件不等于本地文件删除。

## 第一批清理结果

| 历史项目 | 代码处理 | 用户本机仍需确认 |
|---|---|---|
| 根目录 `user_settings.json` | 已移除旧文件读取/迁移逻辑；`build.bat` 不再复制 | 房间号、Live2D 当前模型与各模型偏好、idle tips、TTS 设置已分别存在于 `config/live.json`、`config/live2d.json`、`config/voice.json` |
| 根目录 `config.json` | 已移除 `ConfigManager.migrate_legacy_config()` 和启动迁移调用；仓库中的旧文件已删除 | OBS 普通连接参数已在 `config/obs.json`；若旧文件曾保存 OBS 密码，必须在凭据页重新保存到凭据库后再由用户自行处置本机旧文件 |
| `data/display_layout.json` | 已移除旧单布局文件读取分支 | 确认布局已在 `data/display_layouts.json`；若旧文件仍是唯一副本，请先备份/手动迁移，不能删除 |
| `room_config.txt` | 已移除视频模块旧房间配置迁移函数与启动调用 | 确认房间号及视频限制已在 `config/live.json`、`config/video.json` |
| `obs_config.txt` | 已移除视频模块旧 OBS 文本配置迁移函数与启动调用 | 确认 OBS 地址、端口、来源名已在 `config/obs.json`；密码应在凭据页单独保存 |
| `data/music/uid.txt`、`data/video/uid.txt` | 已移除独立 UID 文件读取；歌曲与视频只使用系统注入的管理员 UID | 确认所有管理员 UID 已在系统设置页 `/settings` 保存到 `config/app.json`，并确认两模块权限一致 |
| 根目录 `uid.txt`、`bili_cookie.txt` | 当前主程序不再读取这些旧文件 | 用户确认不再需要后自行安全处置；Cookie 属敏感信息，不要上传或粘贴到聊天 |
| 旧迁移 marker / journal | 运行代码不再生成或读取旧迁移标记；journal 文件名继续被 `.gitignore` 忽略 | 本机残留 marker/journal 可暂时保留，不影响当前配置读取 |

## 当前配置职责

- `config/app.json`：服务设置及系统统一管理员 UID。
- `config/live.json`：直播间号与连接设置。
- `config/live2d.json`：Live2D 模型与展示偏好。
- `config/voice.json`：语音播报设置。
- `config/obs.json`：OBS 普通连接设置；不存密码。
- `config/music.json`、`config/video.json`：各模块非敏感设置。
- `credentials/bilibili.json`、`credentials/obs.json`：敏感凭据，不得提交到仓库。
- `data/display_layouts.json`：当前多套综合展示布局。

仓库内 `config/live.json`、`config/live2d.json`、`config/voice.json` 保持通用默认模板，不包含个人房间号、模型偏好或 TTS 个性配置。

## 清理后的代码约束

- 运行代码不再包含旧配置迁移函数、旧 UID 文件读取函数、旧 OBS 明文密码迁移 API。
- 凭据管理页仍保留 B 站二维码登录入口、凭据状态检查和 OBS 密码安全保存。
- 系统管理员 UID 只从 `config/app.json` 读取；模块配置保存时不得再写入独立的 `admin_uids` / `admins_file` 副本。
- `build.bat` 只复制当前 `config/`、模板、静态资源和模型，不复制旧用户配置或凭据。
- `.gitignore` 对历史本地配置和敏感文件继续保持忽略。

## 验证状态

| 检查项 | 状态 |
|---|---|
| 默认配置模板与 `master` 原始模板对齐 | 已恢复；待最终 compare 复核 |
| 运行代码旧迁移符号与旧文件引用扫描 | 已做首轮扫描；发现的过期注释已修正，需最终复扫 |
| 系统管理员 UID 不再写入模块配置文件 | 已移除歌曲/视频设置保存时的重复写入 |
| 凭据管理页二维码登录入口与 OBS 密码保存入口 | 页面保留接入中心跳转；状态与保存 API 存在 |
| Python 编译、单元测试、Windows 专项测试 | 已通过（第二批最终代码提交 8206b14 的 Linux / Windows CI 均成功） |
| 用户本机旧配置字段完整性 | 只能由用户在本机核对，仓库无法验证 |

## 用户本机确认清单

在确认以下内容前，不要手动删除本机旧文件：

1. 新版程序的直播间号正确。
2. Live2D 当前模型、每个模型的尺寸/位置/弹幕气泡偏好及 idle tips 正确。
3. TTS 开关、音色、语速、音量、队列长度和礼物播报阈值正确。
4. 多套综合展示布局都已出现在 `data/display_layouts.json`，OBS 场景使用正常。
5. OBS 地址、端口、来源名可正常连接；OBS 密码已通过凭据页重新保存。
6. 系统管理员 UID 已在 `/settings` 配置，歌曲和视频管理员权限均正确。

## 清理记录

| 日期 | 批次 | 变更 | 验证 |
|---|---|---|---|
| 2026-11-03 | 第一批 | 退役根配置、视频文本配置、单布局及独立 UID 迁移/兼容读取；删除仓库中的根目录旧配置文件；更新打包脚本、模板与文档 | 模板个人化内容已撤回；代码/测试审查和 CI 仍在进行 |


## 第二批：运行数据与项目结构整理

| 检查项 | 审查结果 | 处理 |
|---|---|---|
| 旧配置迁移标记 / journal | 当前运行代码不再读写旧迁移标记或 journal；`.gitignore` 仍忽略两种历史 marker 文件名，防止用户本机残留文件被提交 | 保留忽略规则，不在启动时自动删除本机残留标记；不将旧 marker 当作当前运行状态 |
| 日志文件生命周期 | 日志按日期写入 `logs/YYYY-MM-DD.log`；启动及日志设置变更时按保留天数清理过期日期文件；保留天数为 0 表示无限期保留；非日期命名的 .log 不会被清理 | 移除无效的 `daily_rotation` 配置项；保留 `retention_days` 与 UI 使用的 `retention_options`；补充保留策略测试 |
| API 兼容路由 | `/api/display/settings` 仍作为 Live2D 设置兼容地址；`/music/display`、`/music/widget`、`/video/display` 等旧展示入口仍可能被 OBS 浏览器源或外部配置使用 | 暂不删除。README、页面入口和静态契约测试仍覆盖这些地址；删除前需单独确认用户 OBS 源已迁移 |
| 页面模板 | 所有当前 `templates/*.html` 均由 Flask 页面路由渲染 | 未发现可安全删除的孤立模板 |
| 静态资源 | `style.css`、`display.js`、`live2d.min.js`、`display_media.css`、`display_media.js` 均被页面引用 | 未发现可安全删除的孤立静态资源 |
| 视频粉丝牌配置字段 | 配置模板/模块使用 `require_fans_medal`，设置 API 曾误用单数 `require_fan_medal` 持久化，造成页面与运行时配置脱节 | 已统一持久化字段为 `require_fans_medal`；保留前端请求/响应字段 `require_fan_medal` 以维持页面契约；默认值统一为模板中的 `false` |
| 无效默认配置 | `live.connect_on_start`、Live2D 的固定目录/配置路径及画布尺寸字段未被当前代码使用；日志 `daily_rotation` 也没有对应开关逻辑 | 从默认模板和运行时默认值中移除无效字段；保留实际读取的 `format`、`default_model` 等字段 |
| 其他无效默认配置 | OBS `password_file` 仅被传入一个不使用该参数的函数；语音模板中的 `enabled`、`rate`、`volume` 不参与当前默认值解析 | 已移除这些模板字段与 OBS 伪路径参数；OBS 密码始终由凭据库读取，语音速率/音量由用户偏好设置保存 |

### 第二批验证状态

- [x] 最新代码提交 `8206b14` 的 Python 编译与全部离线测试通过。
- [x] 最新代码提交 `8206b14` 的 Windows DPAPI / 凭据专项测试通过。
- [x] 配置模板全部可解析，且无效字段已从默认模板移除。
- [x] 页面与静态资源引用清单复核完成。
- [x] 旧 OBS 展示地址对应的路由别名均保留；用户机器上实际浏览器源是否仍在使用旧地址，需由用户本机确认。
- [x] 本次 GitHub 操作未删除用户本地历史 marker、日志与运行数据。

## 第三轮：配置读写闭环与运行时边界复核

| 检查项 | 审查结果 | 处理 |
|---|---|---|
| 普通配置敏感字段拦截 | 驼峰字段会先规范化再检测；此前 `sessData`、`acTimeValue`、`buvid3/4` 等 Cookie 字段未全部覆盖 | 已扩充敏感字段规则，并新增大小写/驼峰回归用例 |
| 直播间号保存 | 保存接口先单独写入 `config/live.json`，随后统一设置保存函数又写一次；注释仍描述已退役的旧用户设置 | 已去掉重复写入，统一由当前配置保存函数写入 `config/live.json` |
| 凭据状态看门狗 | 单次凭据验证失败（包括网络波动）不会主动停止现有 WebSocket；原日志却声称已停止连接 | 已修正日志表述，避免把一次验证失败误报成断开动作；连接状态仍由 WebSocket 回调管理 |
| 模块生命周期 | 已复核首连自动启动、手动关闭优先级、断线暂停、重连恢复、关闭时资源释放路径；未发现本轮可直接确认的状态机缺陷 | 保留现有状态机，已有异步生命周期回归测试 |
| 凭据存储路径 | Windows DPAPI / 非 Windows 本地受限存储、路径名称校验、状态接口不回显明文均已复核 | 保持现有实现；不把非 Windows Base64 本地格式描述为加密 |
| 二维码登录流程 | QR ticket URL 限定 HTTPS 且限制在 B 站/游戏官方域名；取得 Cookie 后再调用 nav 验证，成功后才保存 | 保留；不更改用户已验收的登录流程 |
| TTS 临时音频 | 文件名为随机 UUID，下载路由限制 32 位小写十六进制 MP3 名称，缓存清理仅清理超过一小时的 MP3 | 保留当前清理策略 |

### 第三轮验证状态

- [x] 普通配置敏感字段检测增加驼峰 Cookie 字段覆盖。
- [x] 直播间号重复写入已消除。
- [x] 凭据看门狗日志与实际行为一致。
- [ ] 最新第三轮代码提交的 Linux / Windows CI 均通过。