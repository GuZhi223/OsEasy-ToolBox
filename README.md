# OsEasy-ToolBox 自动增强版

这是 [ZiHaoSaMa66/OsEasy-ToolBox](https://github.com/ZiHaoSaMa66/OsEasy-ToolBox) 的 Fork。在 `v1.8_Beta2` 的原始 Flet 界面上增量修改，保留原有七个页面和手动功能入口。增强功能位于默认的 `auto-enhanced` 分支，上游提交历史完整保留。

当前版本：自动增强 **0.1.1**。适配用户提供的教室样本，构建路径标签为 `10.9.1.5433`；运行时使用文件指纹识别。此前独立界面仅作为历史原型保留，当前默认入口已切回原工具箱。

## 直接试用

按下方步骤从源码运行，或自行打包后运行 `dist/OsEasyToolBoxEnhanced.exe`。单文件构建面向 64 位 Windows，包含 Python 和 Flet 桌面运行时，可连接 32 位学生端。正常启动通过 Windows UAC 请求管理员权限。

1. 打开原工具箱，进入 **广播命令** 页。
2. **自动填写教师机IP** 默认开启。识别到可靠地址后，直接填入原来的 **输入教师机IP地址** 输入框，并显示来源。
3. 原来的 **由教师机IP生成远程命令** 按钮直接使用这个输入框，无需复制地址。
4. 手动编辑 IP 会暂停自动填写，保留你的输入；点击 **重新自动识别** 或重新开启自动填写可以恢复。
5. **进程管理** 页增加 **一键开启自动模式**，接入现有自动处理引擎。停止时恢复本次自动模式修改的窗口状态。

仅获取 IP 不需要先开启自动处理模式；打开工具箱后就会后台识别。

## 教师机 IP 的依据

- 使用通过样本校验的本机学生端控制连接。读取已建立的 TCP 连接，按样本 `skin/core.conf` 的 `ConnectPort` 识别控制通道；缺少该文件时采用样本默认端口 9003。
- 读取当前广播进程参数中的明确 `teacher_ip` 字段，以及刚收到的有效广播启动参数。
- 单播 `remote` 只列为来源候选，不直接自动填入。组播地址、本机地址、回环地址、无效值和 `0.0.0.0` 都排除。
- 多个可靠地址冲突时提示冲突，不随意填入。连接变化后重新核对，已过期的自动结果不会当作当前地址。

实现仅读取本机进程和连接信息，不扫描局域网。自动识别到地址只更新原输入框，不替你点击生成命令或其他手动操作。

## 自动模式的当前覆盖

自动广播参数和窗口处理、已定位的新快捷键/特定网络/USB 限制请求处理、后台事件记录和窗口恢复，均已接到原工具箱的进程管理页。

黑屏、未知的已有驱动锁定、其余远程控制路径仍未完整覆盖。拒绝一个新限制请求不等于已经解除以前的限制，状态会区分已接入和待适配。

原有手动按钮继续沿用原实现；用户对旧工具箱的实机反馈仍作为原功能基线，新增自动功能和这份重新构建的 EXE 还需要教室实测。原版需要旁置 `ScreenRender_Helper.exe` 的手动替换功能，仍应沿用原工具箱的辅助文件和放置方式。

## 源码结构

- `src/REmainv3.py`：原 Flet 界面；教师 IP 输入框绑定、自动填写状态和自动模式入口。
- `src/remain.py`：原手动功能实现。运行目录初始化延迟到正式启动，管理员启动参数支持带空格的路径。
- `src/Fake_SCR.py`：原辅助源码，未重新实现其协议。
- `oseasy_auto/teacher.py`：本机连接与广播参数中的教师 IPv4 识别。
- `oseasy_auto/toolbox_integration.py`：自动填写策略、后台识别和原界面之间的桥接。
- `oseasy_auto/engine.py`、`assets/observer.js`：复用的自动处理后端。
- `run.py`：默认启动原 Flet 工具箱。

原项目首次启动时对 Autodesk DLL 的无关重命名已移除。交互预览使用临时配置目录，禁用手动操作，不启动教师/学生端样本或自动处理模式。

## 开发和验证

```powershell
git clone --branch auto-enhanced https://github.com/GuZhi223/OsEasy-ToolBox.git
cd OsEasy-ToolBox
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe run.py
```

```powershell
.\.venv\Scripts\python.exe run.py --demo
.\.venv\Scripts\python.exe run.py --probe
.\.venv\Scripts\python.exe run.py --self-test
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\preview_original.py
.\tools\build.ps1
```

`--demo` 打开原 Flet 界面的禁用操作预览，展示示例 IP 自动填入原框；`--probe` 只读识别；`--self-test` 仅连接本程序新建的测试进程。

自动化测试包含原输入框与原生成按钮绑定、手动输入保护、无须启用自动处理即可识别 IP、地址误判/冲突/过期处理，以及自动模式的窗口恢复、请求边界和跨位数连接。原生测试使用自建测试程序和模拟 DLL，不调用教室样本或真实驱动。

本机验证环境为 Windows 11，当前 **45 项测试通过**。普通运行和打包不需要 C 编译器；完整原生测试需要 PATH 中可用的 MinGW GCC，跨位数测试使用 Windows 自带的 .NET Framework x86 编译器，缺少相应编译器时对应测试会跳过。实际课堂中的控制连接和广播格式仍需要实机核验。

## 记录与说明

原手动功能继续使用原配置和备份位置。新增自动模式日志及窗口恢复记录在 `%LOCALAPPDATA%/OsEasyAuto/`，其中教师 IP 变化会记录地址和来源，不记录完整远程命令参数。

参见 [原项目增强版试用说明](docs/原项目增强版试用说明.md)、[自动模式设计](docs/自动模式设计.md) 和 [样本适配范围](docs/样本适配范围.md)。`docs/独立原型历史说明.md` 仅记录之前的独立界面原型。

仓库只公开源码、测试、构建脚本和整理后的文档；教室软件样本、学生端二进制和驱动、配置、原始分析记录、运行日志与本机打包产物均不上传。

## 归属与许可

原工具箱归 [ZiHaoSaMa66/OsEasy-ToolBox](https://github.com/ZiHaoSaMa66/OsEasy-ToolBox) 作者所有。上游在本次 Fork 时未附 LICENSE，本仓库保留其归属，不把上游代码重新标记为 MIT。[上游原始说明](docs/UPSTREAM-README.md) 保留了原作者的项目介绍和使用声明。

我们新增的自动逻辑、测试、脚本和文档采用 MIT 许可，范围见 [LICENSE-ENHANCEMENTS.md](LICENSE-ENHANCEMENTS.md)。本仓库整体包含未明确许可的上游代码，因此不是统一 MIT 授权的软件包；对整体的再分发与商用应先取得上游授权。来源和设计参考见 [UPSTREAM.md](UPSTREAM.md) 与 [NOTICE.md](NOTICE.md)。
