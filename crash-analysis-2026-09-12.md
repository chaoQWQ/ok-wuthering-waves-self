# 多账户日常闪退分析（2026-09-12）

## 直接证据

- 游戏崩溃报告：`F:\Wuthering Waves\Wuthering Waves Game\Client\Saved\Crashes\UE4CC-Windows-397CDAD149FA14450227E28E894E9111_0000\CrashContext.runtime-xml`。
- 报告生成时间为本地 17:35:06，崩溃线程为 `GameThread`，错误为 `EXCEPTION_ACCESS_VIOLATION reading address 0x0000000000000010`。
- 托管调用链：`TimerSystemInstance.Tick → RoleTeamComponent.SpawnGoBattleMaterial → CharRenderingComponent.AddMaterialControllerDataGroup → BuiltinUtils.CreateUObjectByNativePointer_Internal`。
- 调用链指向游戏角色出战材质处理期间的对象指针访问异常。具体哪个对象失效、是否由某次自动输入触发，现有日志无法证明。
- 报告 `MemoryStats.bIsOOM=0`，多项内存使用统计均为零，不能据此判断内存耗尽。

## 与脚本时间线对照

来源：`ok-wuthering-waves/logs/ok-script.log`。

- 17:33:30：梦魇巢穴任务点击传送；17:33:54 进入战斗。
- 17:34:49：Hiyuki 释放 R2 后尝试切换 Lucilla，识别不到队伍。
- 17:34:51：尝试直接切换，结束本次战斗循环。
- 17:35:06：游戏生成崩溃报告。
- 17:35:07：WGC 报告连续 10 秒无帧，尝试重启捕获。
- 17:35:49：拾取逻辑报 `No box found for category pick_up_f_hcenter_vcenter`，发生在游戏崩溃之后，不能作为本次客户端闪退原因。
- 17:36:06：游戏窗口断开。
- 17:36:40 起：向失效窗口发送消息失败。
- 17:36:47：多账户任务报告断开并抛出 TaskDisabledException，但执行器随后再次选中任务，仍有无效窗口输入报错；说明停止与重入保护仍需检查。

## 结论与范围

可定位的直接故障是游戏主线程在角色出战材质处理路径中非法读取地址。闪退发生在账号内的梦魇巢穴战斗阶段，而不是日志所显示的账号切换阶段。角色切换操作与崩溃接近，但不足以认定自动切人是根因。

脚本后续的无帧、特征框异常、无效窗口句柄及任务重新执行属于另行处理的异常恢复问题。此次仅分析日志，没有修改运行逻辑、游戏文件或读取游戏进程内存，也未运行游戏复现。
