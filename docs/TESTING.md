# Validation notes / 测试记录

## September 14, 2026 update

This update adds a read-only DSH setup dialog, English/Chinese onboarding, an explicit release-input allowlist and optional per-user Windows startup (off by default). Approved shader/companion visuals and the DSH wire protocol are unchanged.

- Targeted DSH setup, release-input and language tests: 27 passed, 1 skipped. The skipped test requires Windows permission to create a real symbolic link; simulated reparse-point rejection passed.
- DSH plugin contract: 1 passed, including initial snapshot, nested busy, completion, disposal and metadata-only output.
- English and Chinese setup dialogs were opened and captured locally; text and buttons were checked. These captures include machine paths and are not publication assets.
- Full Python regression: **338 passed, 1 skipped** in 74.95 seconds. The only skip requires real symbolic-link creation permission; mocked Windows reparse-point rejection passed.
- New portable EXE launched from outside the project with PATH limited to Windows directories: exit 0, three 600×400 frames, no reported errors, GL error 0, console handle 0. Transparent/frameless/file-drop flags remained enabled (Intel Arc A770, OpenGL 3.3, driver 32.0.101.8629).
- The actual frozen EXE opened its English menu and DSH setup dialog. The startup menu, DSH guide action, packaged plugin file and enabled Copy button were verified; the computed startup command uses the windowed executable. No real startup entry was written and no reboot was performed.
- First-party compiled module filenames checked in the EXE use relative module names, not the build machine's absolute paths. This is a targeted privacy check, not a complete binary audit.
- Startup tests use a fake registry: no real login-startup entry is enabled by the test suite. An actual reboot/logon test is not performed.

## Previous portable release: v0.1.0 (September 13, 2026)

- Python regression suite: 308 passed (82.15 seconds); DSH simulated-host contract: 1 passed; additional packaging/language checks: 7 passed.
- Portable EXE launched outside the project with Python removed from PATH; three 600×400 frames, no reported errors, GL error 0.
- Intel Arc A770, OpenGL 3.3, driver 32.0.101.8629. Transparent/frameless/file-drop flags checked; console handle 0.
- English/Chinese menus captured and persistence tested. Defender scan found no threats; the unsigned app has no universal antivirus guarantee.

v0.1.0 ZIP SHA-256: `317c7f1b0aab41667a574f714c17603873f0df22b752737391c4615e46cceb82`.

## Scope and limits

On September 13, a real Windows DSH web-profile connection produced valid busy/idle metadata after persistent plugin loading. This is one integration observation, not full lifecycle/version certification. Protocol tests use a simulated DSH context; neither proves every real host scenario.

No clean-machine test on a physical PC without Python, all-GPU/remote-desktop validation, or actual reboot test has been performed. A clean-PATH launch is useful but not a substitute. Static security review covered runtime and publication boundaries, not every dependency or binary; see [privacy notes](PRIVACY.md).

本次新增接入向导、双语说明、发布白名单和默认关闭的开机启动。真实 DSH 已有一次接通记录，但不保证所有版本、模式和异常场景。开机启动测试不修改真实注册表，不代表已做重启登录验收。

Reproduce source tests with `python -m pytest tests -q`; plugin checks with `node --test integrations/dsh-status/status.test.mjs`. Use an isolated build environment with `requirements-build.txt` and `python tools/build_release.py --output dist/new-release`.
