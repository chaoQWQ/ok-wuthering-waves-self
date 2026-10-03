# 地图导航检查记录

检查日期：2026-10-03。

## 检查范围

检查 MapOverlayTask、MapItemOverlay、MinimapDirectionWindow、ChestGuidanceFilter、MapMarksDB、MouseWatcher、ViewInputProbe、AssetsDownloader、NodeIconCache，以及本地 pynput Windows 后端。

## 账号与数据安全

- 在上述导航代码中没有发现游戏内存读写、DLL 注入、游戏客户端 API hooking 或游戏封包操作。位置来源为截图、OCR 和地图图像匹配。
- 导航任务没有发现向游戏发送移动或点击的调用；地图图标交互用于显示说明、选择目标和保存本地领取记录。自动战斗属于独立任务，本次没有完整审查其所有执行分支。
- pynput Windows 后端使用 SetWindowsHookExW 注册全局低级键盘或鼠标钩子。此行为仍需纳入风险判断；仅检查导航源文件中的 API 名称不足以判断依赖行为。
- 小地图窗口通过 Qt 和窗口样式设置显示独立透明窗口。SetWindowLongW 的目标为覆盖层自身的窗口句柄。
- 地图资源包下载源为 GitHub 的 9268/wuwa-map 发布页；图标使用 web-static.kurobbs.com。资源包来源及更新流程需要信任第三方维护者，本次检查未发现发布者签名或固定 SHA256 校验。
- 地图特征通过 np.load 的 allow_pickle=False 加载。
- 静态代码检查无法确认反作弊系统的检测规则，也无法承诺零封号风险。地图数据来自库街区不代表外部导航程序获得官方许可。

## 显示调整

- Qt 方向窗口可用时，由其单独绘制目标方向与目标标记。
- 窗口位置发生变化时才调整 geometry；仅在窗口重新显示时调用 raise_。
- 相同显示数据不重复请求窗口更新。
- 静止状态以及被拒绝的坐标样本保留已显示方向。
- 接近目标时，在 800 地图单位以内进入附近状态，超过 1100 地图单位才退出，减少边界附近的显示切换。数值沿用项目内部地图单位。

## 验证

TestMinimapStability 的四项测试分别在离屏 Qt 和实际 Windows Qt 窗口运行通过。测试使用真实 QWidget、Qt 事件处理和绘制图像；没有使用 mock。覆盖重复显示的窗口句柄和像素一致性、方向变化后的图像更新、隐藏与再次显示、静止和异常坐标保持、距离边界及正北方向角度变化。

MapOverlayTask 导入检查和 git diff --check 通过。

检查时没有运行中的鸣潮或 OK-WW，尚未完成游戏画面中的显示验收。坐标 OCR 的长期错误识别、显示驱动和游戏显示模式仍可能影响结果。
