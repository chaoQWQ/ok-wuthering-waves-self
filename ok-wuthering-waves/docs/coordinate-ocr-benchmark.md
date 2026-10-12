# 坐标 OCR 截图测试

测试日期：2026-10-12。

## 截图与人工标注

使用本地保存的 54 张真实游戏截图，全部为 1920×1080。41 张包含坐标，13 张没有显示坐标。标注通过查看原图的放大区域完成，标注期间没有运行识别模型。

截图包含明亮背景、室内环境、草地、水面、正负坐标、一位至四位数字以及负数高度。部分截图来自相近场景，样本数量不能代表全部地图、分辨率和 UI 比例。

人工标注保存在 `tests/data/coordinate_ocr_real_screenshots.json`。截图位于本机 `screenshots` 和 `tests/images`，未将私人截图加入版本控制。运行工具需要这些原始截图；文件缺失会立即报错。`expected: null` 表示截图对应区域没有显示坐标。

## 比较方法

- 通用 OCR 基线：调用 `BaselinePositionDetector` 的图像与文本处理、实际 `ONNXPaddleOcr` 模型。基线对应 commit `cb463715`，截取 1080p 下的 `(0, 1050, 240, 1080)`，使用 RGB 输入与两倍 LANCZOS 放大。
- 生产坐标 OCR：直接调用地图任务使用的 `PositionDetector.detect_position`。使用专用字符检测模型、OpenVINO CPU 推理与字符边界解析。1080p 下截取 `(0, 1047, 480, 1080)`。
- Navigator 相同区域：使用同一个截图区域，调用参考项目的 `OCRWorker._run_onnx_inference` 和 `_parse_path_b_spacing_dominant`。
- Navigator 默认区域：使用参考项目自动计算的左下角四分之一宽度、三十二分之一高度，即本组截图中的 `(0, 1047, 480, 1080)`，调用相同的实际模型与解析方法。

参考项目：[WutheringWaves-Navigator](https://github.com/guhuo-km/WutheringWaves-Navigator)。测试的 commit 为 `5715fbaa6af97858ad3fb8bc832594f647efbb5c`，使用仓库公开的 `coord_ocr.onnx`。模型 SHA-256 为 `4362a8da7082be40b3f1dfce9fd72b0e3c0d1e85308b362ca225c325e543ea51`。

基线与生产模型均通过 OpenVINO 在 CPU 上运行；Navigator 使用 ONNX Runtime CPUExecutionProvider。生产模型使用两个推理线程。没有启动参考项目的主程序，也没有启动截图线程或向游戏发送输入。

每个方案预热三次，每张截图重复五次。重复识别的坐标必须一致，否则工具立即报错。计时包含图像预处理、模型推理和坐标解析，不包含模型初始化、截图读取、游戏截屏和覆盖层绘制。

## 识别结果

- 通用 OCR 基线：36/41 正确，87.8%；遗漏 4 张，返回错误坐标 1 张；耗时中位数 37.3 ms，P95 为 46.5 ms。
- 生产坐标 OCR：41/41 正确，100%；没有遗漏或错误坐标；耗时中位数 12.3 ms，P95 为 15.4 ms。
- Navigator 默认区域：35/41 正确，85.4%；遗漏 5 张，返回错误坐标 1 张；耗时中位数 20.7 ms，P95 为 23.1 ms。

三个方案对 13 张无坐标截图均没有返回坐标。完整识别输出、耗时和字符检测结果保存在本机 `G:/geme_auto/cache/coordinate-benchmark/production-final.json`。生产坐标 OCR 相比基线，耗时中位数减少约 67%。

生产坐标 OCR 正确识别 `after-right-recovery.png` 的 `-483,168,-15`、三个草地场景中的 `-6785,-983,-224`，以及 `9,-85,16`。这五张截图在基线中分别出现负号遗漏或未返回坐标。

Navigator 相同区域会把截取区域内残留的日期数字加入高度，例如将 `2317,3074,-79` 识别为 `2317,3074,-79202`。默认区域对旧截图 `mini_map.png` 返回 `2126,72025,-4`，原始坐标为 `-1175,2126,7`。该截图日期为 2025 年，参考解析器的日期处理依赖运行时年份。

## 生产识别处理

- 保持原始截图颜色，按照模型输入尺寸等比例缩放并填充。
- 保留每个数字、逗号与负号的检测位置和置信度。
- 使用字符间隔截取坐标部分，日期判断不依赖当前年份。
- 逗号遗漏时，通过相邻字符的间隔恢复分隔；符号不完整、分量数量不符合三个时返回空结果。
- 合并同一位置重复检测的分隔符。
- 地图任务和手动路线录制共享同一个模型，初始化与推理使用线程锁；不复用上一张截图的坐标。
- 模型随项目提供，读取时校验 SHA-256，读取或校验失败时立即报告错误。

原有任务开关、地图匹配、坐标变化过滤、资源点与路线绘制、游戏输入保持原样。

## 额外截图与入口测试

另选仓库已有的 16 张真实游戏截图，在模型与解析方法确定后执行测试。人工标注保存在 `tests/data/coordinate_ocr_additional_screenshots.json`。包含 6 张坐标画面、10 张无坐标画面，覆盖 720p、900p、1080p、1440p 以及部分裁切截图；坐标包含五位数。

生产入口全部通过：6/6 坐标正确，10/10 无坐标画面返回空结果。

`tests/TestCoordinateOcr.py` 的 20 项测试全部通过，包括上述截图、PIL 输入、显式识别区域、四个线程同时处理 48 张真实截图、空画面状态清理以及缺失模型错误。

主要的 54 张截图参与了解析方法的调整，100% 正确率仅代表该截图集合。额外截图数量也有限。

本次没有测试实时移动、地图切换、跳跃、传送和覆盖层稳定性。测试仅使用离线截图和本地模型，不访问游戏内存，不修改游戏文件，不注入 DLL，不处理游戏通信数据。离线测试结果不代表游戏账号的零封禁风险保证。

## 重复运行

从 `G:/geme_auto` 执行，安装测试所需的 ONNX Runtime：

```powershell
.\.venv\Scripts\python.exe -m pip install onnxruntime==1.31.0
git clone https://github.com/guhuo-km/WutheringWaves-Navigator.git cache/navigator-benchmark
git -C cache/navigator-benchmark checkout --detach 5715fbaa6af97858ad3fb8bc832594f647efbb5c
```

参考项目已存在时无需再次执行 clone。生成与标注清单一致的检查图：

```powershell
.\.venv\Scripts\python.exe ok-wuthering-waves/tools/benchmark_coordinate_ocr.py --sheet --manifest ok-wuthering-waves/tests/data/coordinate_ocr_real_screenshots.json --output cache/coordinate-benchmark/review
```

运行模型比较：

```powershell
.\.venv\Scripts\python.exe ok-wuthering-waves/tools/benchmark_coordinate_ocr.py --manifest ok-wuthering-waves/tests/data/coordinate_ocr_real_screenshots.json --reference G:/geme_auto/cache/navigator-benchmark --output cache/coordinate-benchmark/production-final.json --variants baseline production navigator_default_roi --repeats 5
```

运行生产入口测试，从项目目录执行：

```powershell
Set-Location G:/geme_auto/ok-wuthering-waves
..\.venv\Scripts\python.exe -m pytest tests/TestCoordinateOcr.py -q -p no:cacheprovider
```

输出文件包含模型 hash、参考 commit、依赖版本、截图 hash、每张截图的识别结果以及每次耗时。
