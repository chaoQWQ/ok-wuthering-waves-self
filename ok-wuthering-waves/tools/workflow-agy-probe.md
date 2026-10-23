用户授权：就调用我本地的agy。

请仅查看 G:/geme_auto/ok-wuthering-waves/tests/images/quest_yangyang_interaction.png 这张真实游戏截图。
读取图像中左侧任务目标和交互对象。不要修改文件，不要执行游戏操作，不要搜索网络。
仅返回 JSON 对象，字段为 objective、interaction、visible_texts、image_read。
objective 和 interaction 使用图片中实际识别到的中文；visible_texts 为识别到的文字数组。
image_read 只有在已经实际查看图片后才能为 true；无法读取时为 false 并在 visible_texts 说明原因。
