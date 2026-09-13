# Validation notes

Local Windows validation on September 13, 2026:

- Python regression suite: 308 passed (82.15 seconds).
- DSH plugin protocol test: 1 passed using a simulated host.
- Additional packaging / language checks: 7 passed.
- Actual portable EXE started outside the project with Python removed from PATH; three 600×400 frames captured without reported errors.
- Intel Arc A770, OpenGL 3.3, driver 32.0.101.8629: GL error 0.
- Transparent, frameless and file-drop flags verified; console window handle 0.
- English and Chinese menus were opened and captured; default language and persistence covered by tests.
- Microsoft Defender scan of the ZIP found no threats. The program is unsigned; this does not guarantee all scanners or SmartScreen will accept it.

Portable ZIP SHA-256:

`317c7f1b0aab41667a574f714c17603873f0df22b752737391c4615e46cceb82`

Limits: no clean-machine test on a physical PC without Python installed, no validation of all GPUs / remote-desktop environments, and no end-to-end test with an installed DSH client. Local clean-PATH testing is not a substitute for those checks.

To reproduce source tests, install requirements-dev.txt and run `python -m pytest tests -q`; use `node --test integrations/dsh-status/status.test.mjs` for the plugin contract.
