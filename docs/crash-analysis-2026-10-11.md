# 多账户日常崩溃调查（2026-10-11）

## 1. 崩溃事件与转储文件概览

三次客户端崩溃均发生在 `MultiAccountDailyTask` 执行期间，Windows Error Reporting 在 `C:\Users\zc\AppData\Local\CrashDumps\` 目录生成了对应的进程转储文件：

| 序号 | Application Error 时间 | 转储文件 | 崩溃进程 PID | 异常线程 TID | 故障报告文件 |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-10-10 10:05:50 | `Client-Win64-Shipping.exe.1416.dmp` | `1416` | `15128` | `G:\geme_auto\ok-wuthering-waves\logs\ok-script.log.2026-10-10` |
| 2 | 2026-10-11 08:36:28 | `Client-Win64-Shipping.exe.13360.dmp` | `13360` | `22484` | `G:\geme_auto\ok-wuthering-waves\logs\faults\incident-bbfcd00d69024a5cbbb032d22257f96b.json` |
| 3 | 2026-10-11 09:29:11 | `Client-Win64-Shipping.exe.400.dmp` | `400` | `10780` | `G:\geme_auto_stable\ok-wuthering-waves\logs\faults\incident-e2cb8b19bd3a4e16b984dc0044ed1535.json` |

三次 Windows 事件 1000 记录的模块均为 `KERNELBASE.dll`（偏移 `0xc187a`，即 `RaiseException`），异常代码均为 `0xe06d7363`，Windows Error Reporting 事件 1001 的 `Bucket` 均为 `1566691884611491582`（`HashedBucket` 为 `5f6bd7d4dd2dc95c25be01f7e6c156fe`）。

## 2. 崩溃转储（`.dmp`）异常结构与线程分析

解析三个 `.dmp` 文件的 `ExceptionStream`、`ThreadListStream` 与 `ModuleListStream` 得到以下完全一致的结构特征：

### 2.1 异常线程位置
三个转储中触发未处理异常的线程均为进程按创建顺序排列的**第 10 个线程（索引 `[09]`）**，紧邻索引 `[10]` 的 `ACE-Base64.dll+0x18a3f48` 工作线程：
- 索引 `[00]`：`Client-Win64-Shipping.exe` 主线程
- 索引 `[01]` ~ `[03]`：`ntdll.dll+0x558d0`（`TppWorkerThread`）
- 索引 `[09]`：异常崩溃线程（`Rip = KERNELBASE.dll+0xc187a`）
- 索引 `[10]`：`ACE-Base64.dll+0x18a3f48`

### 2.2 `MINIDUMP_EXCEPTION` 参数与寄存器状态
三次崩溃的 `ExceptionRecord` 与线程 `[09]` 非易失性寄存器对比如下：

| 字段 | `1416.dmp` | `13360.dmp` | `400.dmp` | 结构规律 |
| --- | --- | --- | --- | --- |
| `ExceptionCode` | `0xe06d7363` | `0xe06d7363` | `0xe06d7363` | MSVC C++ 异常 |
| `NumberParameters` | `4` | `4` | `4` | x64 `_CxxThrowException` 标准四参数 |
| `ExceptionInformation[0]` | `0x01994000` | `0x01994000` | `0x01994000` | `EH_PURE_MAGIC_NUMBER1` |
| `ExceptionInformation[1]` | `0x0b49ee40` | `0x0b2fedc0` | `0x0b40ee40` | `pExceptionObject`（恒等于 `RSP + 0x190`） |
| `ExceptionInformation[2]` | `0x0d147ce0` | `0x0d4b7ce0` | `0x0d207ce0` | `pThrowInfo`（恒等于 `R15 + 0x7636d`，页内偏移恒为 `0x7ce0`） |
| `ExceptionInformation[3]` | `0x0` | `0x0` | `0x0` | `ThrowImageBase == NULL` |
| `RSP` | `0x0b49ecb0` | `0x0b2fec30` | `0x0b40ecb0` | 线程调用帧指针 |
| `RBP` | `0x4` | `0x4` | `0x4` | 恒为 `0x4` |
| `RSI` | `0x0b49ecd0` | `0x0b2fec50` | `0x0b40ecd0` | 恒等于 `RSP + 0x20` |
| `RDI` | `0x0b49e510` | `0x0b2fe490` | `0x0b40e510` | 恒等于 `RSP - 0x7a0` |
| `R12` | `0x0b49ef00` | `0x0b2fee80` | `0x0b40ef00` | 恒等于 `RSP + 0x250` |
| `R13` | `0x04010820` | `0x14010eb6` | `0x05011160` | 次低字节恒为 `0x01` |
| `R14` | `0x48050df5` | `0x1a050f54` | `0xffffffffb805116a` | 次低字节恒为 `0x05` |
| `R15` | `0x0d0d1973` | `0x0d441973` | `0x0d191973` | 动态映射模块内代码地址（页内偏移恒为 `0x1973`） |

### 2.3 异常未捕获导致进程退出的机制
1. 线程 `[09]` 运行在动态分配于低地址空间（基址约为 `0x0d050000` ~ `0x0d430000`，邻近 `ACE-Base64.dll` 所在的 `0x0dc30000`）的模块中，该动态模块未注册到 `PEB->Ldr` 模块列表（不在 `ModuleListStream` 的 205 个模块内）。
2. 当该模块在偏移 `0x1973`（`R15`）附近通过 `_CxxThrowException` 抛出 `_ThrowInfo` 位于偏移 `0x7ce0`（`ExceptionInformation[2]`）的 C++ 异常时，CRT 调用 `RtlPcToFileHeader` 查找所属模块基址。
3. 由于该动态内存区域未在 `PEB->Ldr` 注册，`RtlPcToFileHeader` 返回 `NULL`，导致 `ExceptionInformation[3]`（`ThrowImageBase`）被设为 `0x0`。
4. Windows x64 异常分发器在解析 `_ThrowInfo` 中的相对虚拟地址（RVA）时依赖有效的 `ThrowImageBase`，当 `ThrowImageBase == 0x0` 时无法匹配 `catch` 块，直接触发未处理异常并终止客户端进程。

## 3. 崩溃前窗口焦点变化与后台输入时间线

对比三次崩溃前后的 `ok-script.log` 与故障记录，三次崩溃全部发生在**游戏窗口刚刚从前台切换到后台（`visible:False`）后的 2.6 ~ 4.7 秒内**，且期间脚本仍在通过 `PostMessageInteraction` 高频发送 `WM_ACTIVATE(WA_ACTIVE)` 与输入消息：

### 3.1 第一次崩溃（2026-10-10 10:05:50）
- `10:05:45.321`：游戏窗口失去前台焦点（`visible:False, exists:1`）。
- `10:05:46.995` ~ `10:05:48.200`：`DailyTask` 与 `GardenTask` 在后台状态下发送 `send_key('esc')`、`send_key('f2')`，每次调用均附带 `PostMessage(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)`。
- `10:05:50`：游戏在窗口失去焦点约 4.7 秒后触发线程 `[09]` 异常崩溃。

### 3.2 第二次崩溃（2026-10-11 08:36:28）
- `08:36:22.860` ~ `08:36:23.294`：`ForgeryTask` 执行 `do_walk_to_box`，依次发送 `send_key_down('d')`、`send_key_up('d')`、`send_key_down('s')`。
- `08:36:24.499`：游戏窗口失去前台焦点（`visible:False, exists:1`），此时 `s` 键仍处于按下状态。
- `08:36:28`：游戏在窗口失去焦点约 3.5 秒后触发线程 `[09]` 异常崩溃。

### 3.3 第三次崩溃（2026-10-11 09:29:11）
- `09:29:07.568`：游戏窗口失去前台焦点（`visible:False, exists:1`）。
- `09:29:07.706` ~ `09:29:10.230`：`TacetTask` 中的 `Suisui` 在后台状态下连续发送 6 次鼠标左键点击、1 次 `send_key('r')`、3 次 `send_key('e')`，每次操作均通过 `PostMessageInteraction.try_activate()` 发送 `WM_ACTIVATE(WA_ACTIVE)`。在此 2.52 秒内 WGC 正常返回了多帧画面。
- `09:29:10.230` ~ `09:29:11`：游戏在窗口失去焦点约 3.4 秒后触发线程 `[09]` 异常崩溃。

### 3.4 前台运行对照
在 `2026-10-11 09:44:37` 重新启动游戏并执行 `MultiAccountDailyTask` 后，游戏窗口自 `09:47:33` 起保持在前台（`visible:True`），连续执行 `TacetTask` 与 `NightmareNestTask` 超过 15 分钟未发生异常。

## 4. 崩溃后多账户任务未能自动重启恢复的原因

1. 在 `src/task/BaseWWTask.py` 的 `_raise_if_game_window_disconnected()` 中，当检测到游戏窗口句柄失效（`hwnd_window.exists == False`）时，直接抛出了 `TaskDisabledException`。
2. 在 `src/task/DailyTask.py` 与 `src/task/MultiAccountDailyTask.py` 的 `_execute_daily_for_account()` 中，`TaskDisabledException` 被视为用户主动停止任务（`not self.enabled`）而直接向上传递，跳过了 `MultiAccountDailyTask` 外层捕获普通异常并调用 `start_device()` 重新启动客户端的恢复分支。
