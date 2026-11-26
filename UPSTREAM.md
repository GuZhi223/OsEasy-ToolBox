# Upstream

本地 `src/` 基于 [ZiHaoSaMa66/OsEasy-ToolBox](https://github.com/ZiHaoSaMa66/OsEasy-ToolBox/tree/v1.8_Beta2) 的 `v1.8_Beta2`，对应提交 `2a1c9b1a64d93f19b71b8ddaa28eb7688b2f9ce2`。本 Fork 保留上游历史，增强版位于 `auto-enhanced` 分支；`main`、`dev` 和原有标签沿用上游。

增量接入：原教师 IP 输入框自动填写、手动覆盖保护、后台识别、原进程管理页的自动模式入口和关闭时清理。原手动功能继续沿用上游实现。

启动调整：将配置目录初始化移到正式启动时；移除对 Autodesk DLL 的无关重命名；修复管理员启动参数引用；增加隔离的禁用操作预览。页面内容增加垂直滚动，以容纳新增状态。

新增自动后端位于 `oseasy_auto/`，原 Flet 界面仍是当前默认入口。

上游在本次 Fork 时没有附 LICENSE。本仓库不为上游代码重新授权；新增代码、测试、脚本和文档采用 MIT 许可，具体范围见 [LICENSE-ENHANCEMENTS.md](LICENSE-ENHANCEMENTS.md)。
