# Publication and privacy notes / 发布与隐私检查

The app is a local Windows widget, not an authenticated monitoring server. A program already able to modify the same user's files can affect its status inputs. Do not run the widget or its helpers as administrator just to make integration work.

状态灯是本地小工具，不是隔离同一用户下恶意程序的安全系统。无需为了接入而用管理员身份运行。

## Data and defaults

- Normal first startup enables desktop-background lensing. Pixels stay in the production renderer's memory and are not recorded or uploaded. Disable it through the menu to stop capture. Developer diagnostics are different: explicitly invoked capture tools can save desktop pixels and local machine paths.
- Optional Windows sign-in startup is **off by default**. Only the explicit menu toggle adds/removes this app's value under the current user's Run key. It does not use administrator rights, scheduled tasks, shell scripts or a service, and does not overwrite unrelated startup commands. Windows controls launch timing and can disable startup apps separately. See [Microsoft's Run key documentation](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys).
- DSH writes only version/source, process identifiers and times, and aggregate agent/running/unknown counts. The source toggle does not install DSH or load the plugin.
- Codex hooks receive host-provided input but persist an allowlist of status metadata, which can include task identifiers and transcript references. Recovery reads are bounded and restricted to known local session roots. The installer separately reads and backs up configuration; those backups can contain private configuration.
- The DSH setup dialog only derives paths and renders a snippet. It does not inspect configuration contents, grant plugin trust, launch a process or write a file. Copying happens only after clicking Copy; the resulting path is machine-local information, not something to publish.
- Startup error logs can contain local paths. Source-console file-drop feedback prints accepted paths. Neither should be uploaded without review.

## Release checklist

1. Review `release-files.txt`. The source archive uses this exact allowlist; DSH runtime files use a fixed list. Unexpected matching files are not automatically added. Linked source inputs and Windows reparse points are rejected.
2. Keep `.env`, credentials, personal Codex/DSH configuration, session logs, generated state, backups and raw QA captures outside the publication set. `.gitignore` alone does not enforce this.
3. Check final ZIP names and text contents, including nested `source.zip`; visually review any image actually selected for publication. Automated pattern checks are useful but cannot prove absence of secrets.
4. Resealing verifies the existing file set and hashes first, refusing unexpected additions or modifications. Only the reviewed `docs/TESTING.md` can be copied in as a public QA report. It is still the publisher's responsibility to review that document.
5. Publish the complete folder bundle, dependency notices and checksum. A checksum detects changes; it does not authenticate an unsigned author or replace malware review.

发布前必须检查最终文件清单和内容，不上传个人配置、日志、状态文件、备份和未经检查的桌面截图。不要把私有安全扫描报告原样放进发行包。

## Review limits

A pre-update static review covered production status readers/writers, hook installation, native capture/cursor inputs and release tools. It found no confirmed exploitable vulnerability in those reviewed boundaries. It also identified publication hardening and misleading documentation that this update addresses. This is **not** an exhaustive audit of every shader/test, a dependency CVE scan, binary reverse engineering, or a guarantee of antivirus acceptance.

本次检查未确认已审查边界中存在可利用漏洞，但不等于整个项目、所有依赖和所有运行环境都绝对安全。真实任务编号已改为虚构测试数据；正常的新提交不会删除旧 Git 历史中的内容。这些编号不是密码或访问凭证。
