# 坐标字符检测模型

来源：[WutheringWaves-Navigator](https://github.com/guhuo-km/WutheringWaves-Navigator/tree/5715fbaa6af97858ad3fb8bc832594f647efbb5c/models)。

参考 commit：`5715fbaa6af97858ad3fb8bc832594f647efbb5c`。

文件：`coord_ocr.onnx`，SHA-256 为 `4362a8da7082be40b3f1dfce9fd72b0e3c0d1e85308b362ca225c325e543ea51`。

类别顺序：`0 1 2 3 4 5 6 7 8 9 , : -`。输入尺寸为 `[1,3,64,640]`，输出尺寸为 `[1,17,840]`。

模型保持原始内容，使用现有 OpenVINO CPU 推理。`CoordinateOcr.py` 的图像缩放、填充和检测输出处理参考该项目的 `src/ocr_engine.py`，许可全文见同目录的 `LICENSE.txt`。

模型读取和 SHA-256 校验失败时立即报告错误，不下载替代模型。坐标识别仅处理已有游戏截图。
