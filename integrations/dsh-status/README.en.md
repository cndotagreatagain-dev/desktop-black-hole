# Optional local DSH status plugin

English | [简体中文](README.md)

DSH is a developer preview. The plugin uses native agent events. Protocol tests pass, and one real Windows web-profile connection and busy/idle counts were verified on September 13, 2026. This does not establish compatibility with every host version or lifecycle scenario.

## Why the checkbox still says Disconnected

The checkbox only enables the widget's reader. **DSH must load `index.mjs` before status files can be written.** A plain `dsh web` launch does not discover this plugin automatically. Connected users should not add another copy.

Current source builds provide **Status sources → DSH setup guide** with a local path and copyable snippet. The guide never reads or modifies DSH configuration. Published v0.1.0 has no dialog; the following manual setup works with its included plugin.

## Persistent web-profile setup

1. Install and start DSH web once using the [official instructions](https://github.com/deepseek-ai/deepseek-harness). Keep the extracted widget folder at a stable location.
2. Back up `%USERPROFILE%\.dsh\profiles\web\cordis.patch.yml`. If the DSH process uses `DSH_HOME`, use `profiles/web/cordis.patch.yml` beneath that directory. The widget's guide must inherit the same override.
3. Check the profile patch, home patch and launch overlays for an existing `black-hole-status` entry. **Load it in only one layer.**
4. Only if the existing configuration is the empty list `[]`, replace it with the following. Otherwise merge this one item into the existing YAML list, retaining other plugins and settings. Do not prepend the snippet to `[]`. Replace the example with this computer's absolute module path:

```yaml
- insert:
    - id: black-hole-status
      name: 'D:/Apps/DesktopBlackHole/dsh-status/index.mjs'
```

Use forward slashes on Windows. Double an apostrophe inside a YAML single-quoted path; the setup guide handles this escaping. Source users select `integrations/dsh-status/index.mjs`; portable users select `dsh-status/index.mjs` beside the EXE. Update the entry if the folder moves.

5. Save the file. A standard web profile supports live reload; startup-only profiles require a restart. Check DSH's own load errors if it remains disconnected instead of inserting another copy.
6. Enable the widget's DSH source, send a request and verify Busy → Idle. Only loaded native Windows instances belonging to the same user are covered, not WSL, remote or cloud processes.

## Alternatives

An independent overlay containing the same snippet can instead be supplied at launch:

```powershell
dsh web --patch "D:/Apps/black-hole.patch.yml"
```

For npx, use `npx @deepseek-ai/dsh web --patch "D:/Apps/black-hole.patch.yml"`. Do not combine this with another insertion in the profile/home layers.

For multiple profiles, the shared home layer is `$DSH_HOME/cordis.patch.yml`, defaulting to `%USERPROFILE%\.dsh\cordis.patch.yml`. Back it up, preserve existing entries and remove duplicates from other layers first. Load at the host level, not inside one agent's scope. Every monitored host must load the plugin.

## Disable or uninstall

Unchecking DSH stops the widget's reader, not the plugin. Remove only the `black-hole-status` item from its patch, or stop supplying the standalone overlay. **Do not replace a file containing other plugins with `[]`.** Use `[]` only when no entries remain; do not leave an empty/comment-only file. Restart startup-only profiles.

## Data and verification

The plugin subscribes to `agent/created`, `agent/status` and `agent/disposed` and aggregates `ctx.agents.list()`, including child agents. It does not parse chat logs or call a model.

Only version/source, PID, process/update times and agent/running/unknown counts are written under `%LOCALAPPDATA%/DesktopBlackHole/status/dsh-v1`. No prompts, answers, tool contents or keys are persisted. Writes are coalesced and atomically replaced, with a ten-second heartbeat. The widget checks every two seconds and caches unchanged records; stale or invalid records are not confirmed idle.

`running` includes waiting for models/tools, not CPU utilization or input availability. Administrator rights are not needed.

Run `node --test status.test.mjs` from the source distribution. Also verify request start, unfinished child agents, all work finishing, and host shutdown on the actual client.

References: [official CLI/profile semantics](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/reference/README.md), [plugin development](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/index.md), [agent events](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/core/agent/src/runtime-types.ts). Never publish your actual host patch, state files, or setup screenshots containing personal paths.

