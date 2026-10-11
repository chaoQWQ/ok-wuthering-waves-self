# 崩溃诊断日志

重启 OK-WW 后，新日志会写入 `ok-wuthering-waves/logs/faults/`。`recent-<PID>.json` 每 15 秒更新一次，异常事件生成独立的 `incident-*.json`，包含此前 120 秒的时间线。

## 记录内容

- `input_request`：任务请求的操作、按键、任务和角色。连续重复请求按秒汇总，`count` 表示次数，`epoch/last_epoch` 表示首次和末次时间。
- `input_filtered`：请求被公共间隔限制过滤，`reason=interval`。
- `post_message`：窗口消息编号、目标句柄和提交结果。`submitted` 表示底层提交返回且没有记录提交错误；`submission_error` 表示底层记录提交失败；`filtered` 表示前台保护或者激活保护过滤消息。提交成功无法证明游戏处理了该消息。
- `character_state`：清霄循环开始、结束，重击图标变化，长按开始与结束、持续时间、退出原因，以及共鸣解放分支进入和返回结果。图标字段表示当前识别结果。
- `window_state`：游戏窗口是否存在、当前前台窗口句柄、名称、窗口类型、PID 和所属程序。记录线程每约 0.5 秒检查一次，仅在状态变化时保存事件。

日志读取脚本自身状态和 Windows 窗口信息。高频请求和成功提交按秒汇总，错误与角色状态变化保留独立记录。记录队列上限为 20,000 条，`recent` 文件中的 `evicted_events` 表示受到容量限制而提前移除的记录数量。

## 验证

`ok-wuthering-waves/tests/verify_fault_diagnostics.py` 使用测试创建的真实隐藏窗口验证消息提交与失效句柄错误，并检查异常文件、窗口状态变化和角色名称。持续运行 125 秒时，51,423 次重复请求产生 120 条汇总记录，时间范围验证通过。测试不向游戏发送消息。

运行方式：在 `ok-wuthering-waves` 目录设置 `PYTHONPATH=G:\geme_auto\ok-wuthering-waves`，使用 `..\.venv\Scripts\python.exe tests\verify_fault_diagnostics.py`。默认持续 125 秒，`--duration 2` 用于验证消息结果和文件保存。
