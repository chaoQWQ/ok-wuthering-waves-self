# 地图视图与角色位置

大地图使用独立的视图中心和比例绘制标点。静止状态下，程序会对连续匹配结果计算中位数，并保持已经确认的投影状态；持续一致的新结果才会更新视图。检查范围包含画面中心和画面边缘的投影变化。

拖动、滚轮缩放及松手后的短暂动画期间，匹配结果及时更新。程序使用已有鼠标监听器的事件，不增加监听器。地图编号或游戏画面尺寸改变后，重新确认视图状态。

状态面板的「玩家坐标」来自最近一次大世界 OCR 识别。「视图中心」单独显示当前大地图中心。拖动大地图不会改变角色坐标或角色到目标的距离。查看与角色所在地图不同的地图时，角色坐标和目标距离显示「未知」。

回到大世界后，角色位置继续更新，程序清除大地图的视图缓存。图像匹配不会写入角色位置的 OCR 历史。

当前环境验证命令：

```powershell
..\.venv\Scripts\python.exe -X utf8 -m unittest tests.TestBigMapViewStability tests.TestTaskConfigRefresh tests.TestMinimapStability
..\.venv\Scripts\python.exe -X utf8 -m tests.verify_bigmap_snapshot "游戏大地图截图的绝对路径"
```

`TestBigMapViewStability` 的日志回放读取当前工程保存的真实匹配日志和地图坐标资源。截图验证使用下载的 SIFTGZ 特征数据进行真实匹配。
