# Macros

For structural codebase exploration, use the installed `codebase-memory` skill.

This project is a native Windows application. Keep keyboard/mouse capture opt-in and playback interruptible. Never commit users' recorded macros or local application data.

The user has authorized publishing this project and subsequent program updates to GitHub. For an update, increment `app_config.VERSION`, run the unit tests and Windows EXE self-test, then commit and push to `main`. The release workflow builds and publishes the matching GitHub release. Verify the release assets and hashes before reporting completion. Do not commit or publish if validation fails.

Preferred complete update command: `.venv\Scripts\python.exe tools\release.py NEXT_VERSION` (it handles version bump, tests, EXE build/verification, commit, push, publication wait, and asset verification).

Development: `.venv\Scripts\python.exe main.py`
Tests: `.venv\Scripts\python.exe -m pytest -q`
Build: `.venv\Scripts\python.exe tools\build.py`
Packaged Windows verification: `dist\Macros.exe --self-test artifacts`

Icons are vendored official Lucide SVGs with their license in `assets/`.
