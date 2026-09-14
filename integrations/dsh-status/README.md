# DSH 可选本地状态插件

[English](README.en.md) | 简体中文

DSH 处于开发预览期。插件按官方原生 agent 事件接口实现；协议测试通过，2026-09-13 在 Windows 上验证过一次 web profile 加载及真实忙闲计数。未覆盖所有版本、子任务和异常退出组合。

## 为什么勾选后仍然“未连接”？

黑洞的复选框只控制读取小状态文件。**DSH 必须先加载 `index.mjs`，才会有人写这些文件。** 普通启动 `dsh web` 不会自动发现黑洞目录中的插件。

已经连通的用户不用重复安装。新版源码中的右键“状态来源 → DSH 接入向导”能显示当前电脑的路径和可复制片段；旧版 v0.1.0 按下文手工配置即可。向导不读取或改写你的配置。

## 推荐：web profile 持久配置

1. 按 [DSH 官方说明](https://github.com/deepseek-ai/deepseek-harness)安装并启动一次 DSH web，保留黑洞解压目录在稳定位置。
2. 先备份 `%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml`。若 DSH 使用 `DSH_HOME`，改为该目录下的 `profiles/web/cordis.patch.yml`；黑洞向导也需要相同环境变量。
3. 检查 profile、home patch 和启动命令是否已经加载 `black-hole-status`。**不要重复添加。**
4. 原文件有效内容只有 `[]` 时，才用下面整段替换。已经有其他条目时，将本条目合并进现有 YAML 列表，保留其他插件和设置，不把 `[]` 留在列表前面。示例路径必须换成本机 `index.mjs` 的绝对路径：

```yaml
- insert:
    - id: black-hole-status
      name: 'D:/Apps/DesktopBlackHole/dsh-status/index.mjs'
```

Windows 路径建议使用正斜杠。单引号中的路径若包含 `'`，在 YAML 中写成 `''`；向导会处理这个转义。源码用户指向仓库内 `integrations/dsh-status/index.mjs`，便携版用户指向解压目录内 `dsh-status/index.mjs`。移动文件夹后需要更新此路径。

官方标准 web profile 使用 live patch，可在保存后重新加载；配置为 startup 的 profile 则需要重启。若未连接，查看 DSH 自己的加载错误，不要盲目再插入一遍。

5. 在黑洞右键“状态来源”勾选 DSH，发送请求，观察“处理中 → 空闲”。只查看已加载插件、同一 Windows 用户下的原生 DSH 实例；不覆盖 WSL、远程主机或云端任务。

## 替代方式与其他 profile

也可以把同样的内容另存为独立 overlay，然后用：

```powershell
dsh web --patch "D:/Apps/black-hole.patch.yml"
```

使用 npx 时为 `npx @deepseek-ai/dsh web --patch "D:/Apps/black-hole.patch.yml"`。**持久 profile、home patch 和启动 overlay 三处只选一处加载本插件。**

如需多个 profile 共用，可改用 `$DSH_HOME/cordis.patch.yml`（默认 `%USERPROFILE%\.dsh\cordis.patch.yml`）的 home patch，并从其他层移除重复条目。先备份，保留已有配置。不要将插件放进单个 agent 的局部作用域，否则不能表示该宿主的总状态。每个纳入统计的宿主实例都必须加载。

## 停用和卸载

黑洞取消勾选 DSH 只停止读取，不卸载插件。卸载时，只删除 patch 里的 `black-hole-status` 插件项，或停用独立 overlay 参数；**不要把包含其他插件的文件整个改成 `[]`**。只有删除后列表完全为空时才保留 `[]`，不能留下空文件或纯注释。live profile 可重新加载，其他 profile 重启生效。

## 行为与隐私

订阅 `agent/created`、`agent/status`、`agent/disposed`，读取 `ctx.agents.list()` 的聚合（包括子任务）。不解析聊天日志，不调用模型。

只向 `%LOCALAPPDATA%/DesktopBlackHole/status/dsh-v1` 写版本/来源、PID、启动及更新时间、agent/running/unknown 计数，不写问题、回答、工具内容或密钥。事件合并、原子替换、十秒心跳；黑洞每两秒检查，未变化使用缓存。失效/过期记录不被当作空闲。

`running` 表示 agent 正在执行（包括等待模型和工具），不是 CPU 使用率，也不表示输入框能否输入。无需管理员权限。

## 自检与依据

`node --test status.test.mjs`（测试文件在源码包）。实际验收还需验证：发送请求变忙、子任务未结束仍忙、全部结束空闲、关闭 DSH 后断开。一次已验证连接不替代完整兼容性测试。

官方依据：[CLI profile 与 patch 层](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md)、[插件教程](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/index.md)、[Agent 事件](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/src/runtime-types.ts)。不要把你的实际 patch 配置、状态文件或包含私人路径的向导截图提交到公开仓库。

