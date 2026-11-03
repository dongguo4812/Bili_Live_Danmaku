# Live2D 静态预览图

综合展示布局编辑页使用这里的 PNG 作为 Live2D 模块预览，不会在编辑页启动 Live2D 模型或音频。

文件按当前模型 ID 命名：

- `chiaki_kitty.png` 对应 `models/chiaki_kitty/`
- `live_uu.png` 对应 `models/live_uu/`

将 PNG 放在本目录，文件名必须与 `/api/live2d/settings` 返回的 `model` 字段一致，例如：

```text
static/live2d_preview/<model_id>.png
```

在独立 Live2D 展示页（例如 `/live2d/moc2/display`）等待模型正常加载后，打开“设置”，可先选择需要的动作或表情，再点击“生成并保存静态预览图”。程序会直接读取当前页面正在运行的 WebGL 画布，不会额外启动第二个 Live2D 实例；导出时自动裁掉透明空白并保留透明背景，然后由本地服务保存到本目录，文件名自动使用当前模型 ID。

预览图是静态图，不反映模型动作、表情或实时状态。
