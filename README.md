## 项目介绍
这是一个基于PySide6的项目，用于实现从表格中获取图片和提示词，然后调用Klingai的API，来实现图片生成，保存到本地。

## 项目架构
- 前端：PySide6
- 后端：Klingai API

## 代码结构
- 前端：`main.py`
- 后端：`api.py`
- 配置文件：`config.json`

## 代码规范
- PySide6使用QWidgets来实现用户界面
- 字符串使用单引号
- 变量名使用snake_case命名法
- 函数名使用snake_case命名法
- 类名使用PascalCase命名法
- 常量名使用ALL_CAPS命名法

## 注释
- 所有代码都必须有注释，注释要中文
- 注释要详细，包括函数、类、变量等

## 日志
- 使用loguru库来记录日志
- 所有操作都要必须记录到日志文件中
- 日志文件要中文
## Omni Image 接口选项
- 在 `config.json` 设置 `"api_mode": "omni_image"` 可切换到 `POST /v1/images/omni-image`。
- `request_templates.omni_image` 已内置：`model_name`、`prompt`、`element_list`、`image_list`、`resolution`、`n`、`aspect_ratio`。
- 默认模型可由 `model_names.omni_image` 指定（当前为 `kling-image-o1`）。
- 运行时通过 `run_task({...})` 传入同名字段可覆盖配置值。
