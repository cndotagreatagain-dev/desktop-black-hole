# 桌面黑洞 · Codex / DSH 总状态灯

[English](README.md) | 简体中文

一个放在 Windows 桌面上的透明黑洞小玩具。齐马蓝伴星既是装饰，也可以成为 Codex 或 DeepSeek Harness（DSH）的本机忙闲指示灯。

![黑洞实时渲染效果](docs/images/black-hole.png)

薄而明亮的吸积盘、纯黑阴影、光子环和引力透镜由 GLSL 实时渲染，使用 Schwarzschild 零测地线计算光路，不是播放视频或拿截图冒充黑洞。支持桌面背景扭曲、鼠标引力效果和前后绕行的蓝色伴星。

## 下载与打开

1. 前往 [Releases 下载页](https://github.com/cndotagreatagain-dev/desktop-black-hole/releases)，下载 **DesktopBlackHole-Windows-x64.zip**。
2. **完整解压**，双击 **DesktopBlackHole.exe**，不需要安装 Python。
3. 首次启动默认英文：右键 → **Language / 语言 → 中文**，选择会保存。

不要只发给别人一个 EXE，配套文件夹都需要保留。GitHub 绿色 Code 菜单里的 Download ZIP 是源码，不是可直接运行的程序。

需要 Windows 10/11 x64 和支持 OpenGL 3.3 的显卡驱动。桌面背景扭曲需要 Windows 10 2004 或更新版本。完整说明见[便携版使用说明](docs/PORTABLE.md)。

## 怎么玩

- 左键拖动位置，滚轮调整大小；文件拖放仅产生视觉反馈，不移动、删除或修改原文件。
- 当前源码新增右键“开机启动（当前用户）”，默认关闭；勾选后在当前用户登录 Windows 时启动，无需管理员权限。取消即可移除，移动目录后重新开关一次更新路径。系统可能延迟启动，任务管理器中的禁用也可能影响运行。现有 v0.1.0 ZIP 尚无此选项。
- 右键切换画质、鼠标引力、背景扭曲、伴星、状态来源和语言。
- 可以始终置顶，也可以保持在其他应用下面。置底不是嵌入壁纸，Win+D 可能一起隐藏黑洞。
- 默认高清画质；负载较高时可改为标准画质或关闭背景扭曲。

## 伴星如何表示状态

| 状态 | 伴星表现 |
| --- | --- |
| 空闲 | 外环慢速公转 |
| 处理中 | 更靠近黑洞、悬浮在吸积盘上方，约五秒一圈，呼吸增亮并带弧形长尾 |
| 未连接 / 状态待确认 | 暗光外环，不伪装成空闲 |

黑洞本身不需要 Codex、DSH、API 密钥或模型调用。两个状态来源可以独立开关；同时启用时，任一已接入实例工作即为忙，全部确认空闲才显示空闲。

### Codex（可选）

便携版右键 → 状态来源 → 安装 Codex 接入。安装器会备份已有配置，合并本程序的钩子，不替你授予信任。随后在 Codex CLI 的 `/hooks` 中检查 black-hole-status 命令，确认信任，再重启 Codex 并发送消息。

只统计加载了这些钩子的本机实例，不涵盖云端、其他电脑或所有未接入会话。它表示总体忙闲，不精确区分长思考、逐字输出或输入框是否可用。

### DSH（可选，默认关闭）

**只勾选 DSH 并不会让插件自动加载，这是“未连接”最常见的原因。**

当前源码版新增右键 → 状态来源 → **DSH 接入向导**：显示本机插件路径、配置文件位置和可复制片段，不读取或自动修改 DSH 配置。已发布的 v0.1.0 没有这个向导，也能按[中文接入说明](integrations/dsh-status/README.md)完成持久配置，无需重装插件。

先备份，再把本插件条目合并到 DSH web profile 的 patch 层。已有插件要保留；profile、home patch 和 `--patch` 三处不要重复添加同一个插件。已完成一次真实 Windows web-profile 连接验证，但不能据此保证所有 DSH 版本和运行模式都兼容。

## 隐私与安全

- 状态灯只在本机读写小状态文件，不调用模型，不消耗 token。约每两秒检查一次，未变化的记录使用缓存。
- DSH 只写进程时间、健康心跳与忙闲计数，不保存问题、回答或工具内容。
- Codex 信使只保存限定的状态字段；必要时，读取端会核实已知来源的有限日志尾部。状态记录仍可能包含任务标识和本地路径引用，不能当作可公开文件。
- **正常首次启动默认开启背景扭曲**：它在内存中读取黑洞后面的桌面区域，不录制或上传；可以在右键菜单关闭。开发用截图工具则会主动保存图片，分享前必须检查。
- 安装器的配置备份、启动错误日志、个人 Codex/DSH 目录都不应上传。更多说明见[发布与隐私检查](docs/PRIVACY.md)。

程序暂未签名，Windows SmartScreen 或杀毒软件可能提示。不要为了运行它关闭防护或添加全盘排除；只从可信来源下载。安全检查和哈希校验都不代表“绝对无风险”或“永不误报”。

## 从源码运行

需要 Python 3.11 或更新版本：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe desktop_black_hole.py
```

使用同一环境的 `pythonw.exe` 启动 `launcher.pyw` 可以不显示控制台。测试及打包步骤见 [English README](README.md#tests-and-packaging)，已验证范围见[测试记录](docs/TESTING.md)。

项目自己的代码采用 [MIT 许可证](LICENSE)，欢迎使用、修改和分享；依赖继续遵循各自[第三方许可证](docs/THIRD_PARTY_NOTICES.md)。这不是 OpenAI 或 DeepSeek 的官方产品。
