# 坐标 OCR 截图测试

测试日期：2026-10-12。

## 截图与人工标注

使用本地保存的 54 张真实游戏截图，全部为 1920×1080。41 张包含坐标，13 张没有显示坐标。标注通过查看原图的放大区域完成，标注期间没有运行识别模型。

截图包含明亮背景、室内环境、草地、水面、正负坐标、一位至四位数字以及负数高度。部分截图来自相近场景，样本数量不能代表全部地图、分辨率和 UI 比例。

人工标注保存在 `tests/data/coordinate_ocr_real_screenshots.json`。截图位于本机 `screenshots` 和 `tests/images`，未将私人截图加入版本控制。运行工具需要这些原始截图；文件缺失会立即报错。`expected: null` 表示截图对应区域没有显示坐标。

## 比较方法

- 当前方案：调用 `PositionDetector.preprocess`、实际 `ONNXPaddleOcr` 模型以及 `PositionDetector.postprocess`。截取 1080p 下的 `(0, 1050, 240, 1080)`，保持生产配置的 RGB 输入与两倍 LANCZOS 放大。
- Navigator 相同区域：使用同一个截图区域，调用参考项目的 `OCRWorker._run_onnx_inference` 和 `_parse_path_b_spacing_dominant`。
- Navigator 默认区域：使用参考项目自动计算的左下角四分之一宽度、三十二分之一高度，即本组截图中的 `(0, 1047, 480, 1080)`，调用相同的实际模型与解析方法。

参考项目：[WutheringWaves-Navigator](https://github.com/guhuo-km/WutheringWaves-Navigator)。测试的 commit 为 `5715fbaa6af97858ad3fb8bc832594f647efbb5c`，使用仓库公开的 `coord_ocr.onnx`。模型 SHA-256 为 `4362a8da7082be40b3f1dfce9fd72b0e3c0d1e85308b362ca225c325e543ea51`。

当前模型通过 OpenVINO 在 CPU 上运行；Navigator 使用 ONNX Runtime CPUExecutionProvider。没有启动参考项目的主程序，也没有启动截图线程或向游戏发送输入。

每个方案预热三次，每张截图重复五次。重复识别的坐标必须一致，否则工具立即报错。计时包含图像预处理、模型推理和坐标解析，不包含模型初始化、截图读取、游戏截屏和覆盖层绘制。

## 识别结果

- 当前方案：36/41 正确，87.8%；遗漏 4 张，返回错误坐标 1 张；耗时中位数 36.5 ms，P95 为 46.1 ms。
- Navigator 默认区域：35/41 正确，85.4%；遗漏 5 张，返回错误坐标 1 张；耗时中位数 20.2 ms，P95 为 22.2 ms。
- Navigator 相同区域：20/41 正确，48.8%；遗漏 5 张，返回错误坐标 16 张；耗时中位数 20.1 ms，P95 为 22.2 ms。

三个方案对 13 张无坐标截图均没有返回坐标。完整识别输出、耗时和解析过程保存在本机 `G:/geme_auto/cache/coordinate-benchmark/final-results.json`。两次完整比较的正确、错误与遗漏数量一致；这里使用最后一次比较的耗时。

当前方案的错误坐标来自 `after-right-recovery.png`：画面为 `-483,168,-15`，识别为 `483,168,-15`，平均识别置信度约 0.968。其余遗漏包括逗号和负号未被识别的情况。

Navigator 相同区域会把截取区域内残留的日期数字加入高度，例如将 `2317,3074,-79` 识别为 `2317,3074,-79202`。默认区域对旧截图 `mini_map.png` 返回 `2126,72025,-4`，原始坐标为 `-1175,2126,7`。该截图日期为 2025 年，参考解析器的日期处理依赖运行时年份。

## 图像处理实验

同一批截图、同一实际当前模型，每张重复三次：

- 保留原始颜色：36/41 正确，错误坐标 1 张，遗漏 4 张。
- 转为灰度再复制为三个通道：34/41 正确，错误坐标 4 张，遗漏 3 张。
- 灰度 CLAHE，`clipLimit=2.0`、`tileGridSize=(8,8)`：34/41 正确，错误坐标 2 张，遗漏 5 张。
- 当前模型使用 Navigator 默认区域：32/41 正确，错误坐标 7 张，遗漏 2 张；耗时中位数 47.1 ms。

全部实验对 13 张无坐标截图均没有返回坐标。实验属于同一个本地截图集合上的比较，没有独立验证集。

## 接入判断

保留当前生产定位代码。截图测试没有支持直接替换识别模型、统一增强对比度或者扩大固定区域。

专用字符检测模型的 CPU 耗时具有参考价值。进一步接入需要同时处理字符边界、坐标与日期的间隔、缺失逗号和符号可靠性，并用独立的连续游戏画面验证坐标变化。平均文本置信度无法单独发现本组截图中的负号错误。

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
.\.venv\Scripts\python.exe ok-wuthering-waves/tools/benchmark_coordinate_ocr.py --manifest ok-wuthering-waves/tests/data/coordinate_ocr_real_screenshots.json --reference G:/geme_auto/cache/navigator-benchmark --output cache/coordinate-benchmark/final-results.json --variants current navigator_same_roi navigator_default_roi --repeats 5
```

运行图像处理实验：

```powershell
.\.venv\Scripts\python.exe ok-wuthering-waves/tools/benchmark_coordinate_ocr.py --manifest ok-wuthering-waves/tests/data/coordinate_ocr_real_screenshots.json --reference G:/geme_auto/cache/navigator-benchmark --output cache/coordinate-benchmark/preprocessing-results.json --variants current current_grayscale current_clahe current_wide_roi --repeats 3
```

输出文件包含模型 hash、参考 commit、依赖版本、截图 hash、每张截图的识别结果以及每次耗时。
