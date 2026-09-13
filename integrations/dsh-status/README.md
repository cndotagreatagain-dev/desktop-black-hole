# DSH 可选本地状态插件

本机未安装 DSH，尚未完成真实 DSH 端到端验证。插件按 2026-09-12 官方原生接口编写，已提供协议与模拟上下文测试；这不等同于实际 DSH 验收。DSH 处于开发预览期，接口可能变化。

## 安装后接入

无需为黑洞额外安装 Python。先按 [官方安装说明](https://github.com/deepseek-ai/deepseek-harness)安装/启动 DSH。保留本目录在稳定位置。

创建自己的 `black-hole.patch.yml`，把路径换成这台电脑上 `index.mjs` 的绝对路径（Windows 推荐使用 `/`）：

```yaml
- insert:
    - id: black-hole-status
      name: 'D:/Apps/DesktopBlackHole/dsh-status/index.mjs'
```

以这个根级 overlay 启动 DSH：

```powershell
dsh web --patch "D:/Apps/black-hole.patch.yml"
```

若使用 npx 启动，使用 `npx @deepseek-ai/dsh web --patch "D:/Apps/black-hole.patch.yml"`。
不要把插件装进单个 agent 的作用域，否则不代表整机总状态。每个想纳入统计的 DSH 实例都需要加载。
再在黑洞右键“状态来源”勾选 DSH。关闭复选框停止读取；去掉启动命令的 patch 参数并重启 DSH，可卸载插件。

## 行为与范围

订阅 `agent/created`、`agent/status`、`agent/disposed`；每次取 `ctx.agents.list()` 的当前聚合，包含子任务。不通过 Codex 兼容钩子，不解析 DSH 聊天日志。
只向 `%LOCALAPPDATA%/DesktopBlackHole/status/dsh-v1` 写入版本、PID、启动时间、更新时间、agent 数量、running/unknown 数量。
单实例异步原子替换，同一时刻事件合并；十秒健康心跳。卸载删除该实例自身文件；进程崩溃留下的文件会按存活状态和启动时间失效。
黑洞两秒检查、未改变不重读；心跳超过35秒变为状态待确认，不假定已经完成。只支持同一 Windows 用户的原生 DSH 实例，暂不桥接 WSL/远端。

DSH 的 `running` 表示 agent 驱动正在执行（包含等待模型和工具），不等同于 CPU 使用率。空闲不代表所有后台维护都停止，也不代表输入框是否可用。

## 自检

`node --test status.test.mjs`（完整测试文件在源码包中）。安装后还需真实核实：启动为空闲、发送请求为忙、子任务未结束仍为忙、全部结束转空闲、关掉 DSH 后未连接。

依据：[插件教程](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/index.md)、[Agent 事件](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/src/runtime-types.ts)、[Agent 注册表](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/src/index.ts)。
