# Live2D 静态预览图

综合展示布局编辑页使用这里的 PNG 作为 Live2D 模块预览，不会在编辑页启动 Live2D 模型或音频。

文件按当前模型 ID 命名：

- `chiaki_kitty.png` 对应 `models/chiaki_kitty/`
- `live_uu.png` 对应 `models/live_uu/`

将 PNG 放在本目录，文件名必须与 `/api/live2d/settings` 返回的 `model` 字段一致，例如：

```text
static/live2d_preview/<model_id>.png
```

建议使用实际 Live2D 展示页中模型正常加载后的画面导出 PNG，保留透明背景并尽量去除多余空白。编辑页会按当前模型 ID 自动加载图片，并随模块尺寸和位置缩放；切换模型后会加载另一张图。图片不存在或加载失败时会显示提示文字，不影响布局编辑和 OBS 实际展示。

预览图是静态图，不反映模型动作、表情或实时状态。
