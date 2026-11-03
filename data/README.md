# 运行数据目录

此目录保存运行时数据，例如歌曲/视频队列状态、缓存和综合展示页布局。不要在这里保存 Cookie、密码、Token 或 API Key。

运行数据通常应加入 .gitignore，避免把本机状态提交到仓库。


## 管理员 UID 文件迁移

歌曲和视频模块分别读取独立管理员列表：

- 将旧歌曲项目的 `uid.txt` 复制到 `data/music/uid.txt`。
- 将旧视频项目的 `uid.txt` 复制到 `data/video/uid.txt`。

这两个文件属于本机运行数据，不提交到 Git。也可以直接在各自的 `config/music.json`、`config/video.json` 的 `admin_uids` 数组中填写 UID。
