# 多账户日常崩溃调查（2026-10-11）

两次客户端崩溃均发生在脚本执行期间。现有证据能够确认客户端发生 C++ 异常，尚未确认触发该异常的具体操作或游戏内部函数。

## Windows 故障记录

Application Error 事件 1000 的记录时间分别为 2026-10-10 10:05:50 和 2026-10-11 08:36:28，应用程序均为 `Client-Win64-Shipping.exe`。

两次记录均包含 `KERNELBASE.dll`、异常代码 `0xe06d7363`、模块内位置 `0xc187a`。Windows Error Reporting 事件 1001 的 `Bucket` 均为 `1566691884611491582`，`HashedBucket` 均为 `5f6bd7d4dd2dc95c25be01f7e6c156fe`。

Microsoft 文档将 `0xe06d7363` 定义为 C++ exception handling exception。这个代码没有给出异常对象类型、异常内容或游戏函数调用信息。相同的 Windows 故障分类不足以证明具体触发条件相同。

来源：https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/specific-exceptions

## 此次崩溃前的输入请求

来源：`ok-wuthering-waves/logs/faults/incident-bbfcd00d69024a5cbbb032d22257f96b.json`。所有时间均为 Asia/Shanghai。

| 时间 | 记录 |
| --- | --- |
| 08:36:05.668 至 08:36:08.642 | 重新锁敌期间记录 68 次鼠标中键点击请求 |
| 08:36:19.080 | 战斗循环结束 |
| 08:36:22.685 | ForgeryTask 开始走向奖励位置 |
| 08:36:22.860 | 按下 `d` |
| 08:36:23.263 | 释放 `d` |
| 08:36:23.294 | 按下 `s` |
| 08:36:24.499 | 游戏窗口失去前台焦点 |
| 08:36:28 | Windows 记录客户端崩溃 |
| 08:36:32.818 | 游戏窗口消失 |
| 08:36:36.913 | 窗口连接检查停止任务 |

`FaultRecorder.record_input()` 在 `BaseWWTask.click()` 入口记录请求，时间早于底层 `BaseTask.check_interval()` 的间隔检查。重新锁敌使用 `middle_click(interval=0.2)`，因此 68 次请求不能直接解释为 68 次实际点击。记录器也没有证明游戏接受了输入。

记录中的最后一次输入请求为按下 `s`，后续没有释放 `s` 的请求。这与任务等待游戏画面、随后检测到窗口断开的执行路径一致，无法据此确定游戏内部异常的开始时间。

## 输入层的共同操作

此次使用 `PostMessageInteraction`。安装目录中的 `ok/device/interaction_methods/post_message.py` 显示：

- `send_key_down()` 默认调用 `try_activate()`。
- 鼠标操作的 `update_mouse_pos()` 调用 `try_activate()`。
- `try_activate()` 每次向游戏窗口发送 `WM_ACTIVATE / WA_ACTIVE`；目标为其他窗口时，还向该目标发送相同消息。
- 该操作没有检查游戏是否已经处于前台，也没有检查是否已经向同一窗口发送过激活消息。

脚本每次输入都会附带窗口激活通知。Microsoft 文档说明 `WM_ACTIVATE` 用于通知窗口激活状态；默认窗口处理在收到激活消息时设置键盘焦点。这是已经确认的输入行为差异，但当前记录没有保存底层消息发送结果或游戏处理过程，尚不能认定它导致此次崩溃。

来源：https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-activate

## 画面获取与证据范围

此次 WGC 最后一次重新启动发生在 08:29:16。日志从该时间至 08:36:28 没有记录捕获会话重新启动或销毁。窗口前台状态字段来自 `HwndWindow.is_foreground()`；`visible:False` 本身没有说明最小化状态，也没有说明哪个窗口取得焦点。

2026-10-10 的客户端崩溃发生在领取通行证奖励、检查每周花园附近；2026-10-11 发生在 ForgeryTask 的奖励移动期间。两次故障都使用 WGC 和 PostMessage 输入，但两次记录缺少相同条件下的对照执行结果。

游戏 `Saved/Crashes` 目录没有这两次故障的新 `CrashContext.runtime-xml`。Windows 事件记录提供的 WER 归档目录和 `Report.wer` 读取均被系统权限拒绝，提升执行权限后读取目录仍然失败。客户端日志直接读取未得到可解释的文本，因此没有从中获得游戏函数调用信息。

本次调查核查了任务日志、输入记录、Windows Application/System/WER 事件和当前运行代码，没有执行游戏复现。当前缺少的关键证据是此次 C++ 异常的内容、游戏函数调用信息，或能够区分输入方式与画面获取方式影响的实际对照结果。
