# Agent Instructions

## Python

- When running Python commands in this repository, use the local virtual environment if it exists.
- On Windows/PowerShell, prefer `.\.venv\Scripts\python.exe` when present.
- On POSIX shells, prefer `./.venv/bin/python` when present.
- Fall back to `python` only when no local `.venv` interpreter exists.
- Prefer invoking the interpreter directly, for example `.\.venv\Scripts\python.exe -m pytest`, instead of relying on shell activation.

## Git

- Every completed modification/task MUST be committed to the local git repository (`git commit`).
- NEVER push to remote repositories (`git push` is prohibited unless explicitly requested by the user).
- Use clear, conventional commit messages (e.g., `fix(...)`, `feat(...)`, `refactor(...)`).
- 用户要求撤回已经提交的更改时，优先使用 `git revert <commit>` 回退对应提交，并保留其他更改。

## Account Safety & Anti-Ban Policy

- All modifications and features MUST prioritize game account security above all else.
- Strictly PROHIBIT any memory reading/writing, DLL injection, API hooking on the game client or anti-cheat system, packet manipulation, or actions that violate game integrity.
- Automation must remain non-invasive (e.g. computer vision, OCR, template matching, simulated OS-level/window input events).
- Any implementation carrying potential ban risks or anti-cheat triggers MUST be clearly explained to the user and explicitly confirmed by the user before proceeding.

