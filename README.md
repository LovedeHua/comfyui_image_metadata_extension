# ComfyUI Image Metadata Extension（ComfyUI 图像元数据扩展）

![node-preview](assets/preview.PNG)

[ComfyUI](https://github.com/comfyanonymous/ComfyUI) 自定义节点，为保存的图像写入附加元数据，保证与 [Civitai](https://civitai.com/) 网站的兼容性。

*本项目 fork 自 [nkchocoai/ComfyUI-SaveImageWithMetaData](https://github.com/nkchocoai/ComfyUI-SaveImageWithMetaData)，并在此基础上做了大量修改。*

## 包含的节点

| 节点 | 功能 |
| --- | --- |
| **保存图像（含元数据）** `SaveImageWithMetaData` | 替代原生 SaveImage。保存图像的同时写入提示词、种子、模型、LoRA 等元数据，兼容 Civitai / A1111 格式 |
| **创建附加元数据** `CreateExtraMetaData` | 创建最多 4 组键值对，作为附加元数据喂给保存节点 |
| **历史预览** `ImageHistoryPreview` | 带缓存的预览节点：保留最近 5 张图，可在节点上前后翻看（详见[下文](#历史预览节点)） |

**与上游的主要差异：**
- 精简了节点，去掉了普通使用中用不到的字段。
- 自动包含 LoRA 权重信息（模型名、哈希、强度）。
- `subdirectory_name` 支持自定义子目录名或占位符，例如 `%date:yyyy-MM%` 会创建形如 `2024-10` 的目录，按出图时间归档。
- `output_format` 定义保存格式：
  - `png`、`jpg`、`webp` —— 按指定格式保存。
  - `png_with_json`、`jpg_with_json`、`webp_with_json` —— 按指定格式保存，并额外写一份同名 JSON 工作流文件（每张图一份）。
- `quality` 质量等级：
  - **`max` / `lossless WebP`（无损）** – 100%
  - **`high`** – 80%
  - **`medium`** – 60%
  - **`low`** – 30%

  *（质量越低、文件越小。PNG 格式忽略此设置。）*
- `metadata_scope` 控制元数据范围：
  - **`full`** – 默认元数据 + 附加元数据。
  - **`default`** – 与原生 SaveImage 节点相同。
  - **`parameters_only`** – 仅 A1111 风格参数。
  - **`workflow_only`** – 仅工作流数据。
  - **`none`** – 不保存元数据。

## 安装

### 推荐安装

通过 [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager) 安装：

```
comfyui_image_metadata_extension
```

### 手动安装

1. 进入 ComfyUI 目录下的 `custom_nodes` 目录。
2. 克隆本仓库：

  ```bash
   git clone https://github.com/LovedeHua/comfyui_image_metadata_extension.git
  ```

## 使用

基本用法见（[workflow.json](assets/workflow.json)）：

![workflow-preview](assets/Capture1.PNG)

LoRA 字符串会自动追加到提示词区域，Civitai 网站据此识别你使用的权重；其他元数据也会一并写入。

![website-preview](assets/Capture2.PNG)

## 历史预览节点

`ImageHistoryPreview` 可串联在任意图像输出上（图像原样透传，不影响下游保存）：

- 每次执行把新图存入缓存，节点上通过 **◀ / 2/5 / ▶** 控件前后翻看最近 5 张，点击中间的计数直接回到最新一张。
- 缓存按节点独立计数：工作流里放多个历史预览节点，各自维护各自的 5 张。
- 图像缓存在 ComfyUI 的临时目录中：**运行期间自动清理不再被引用的旧文件**（磁盘占用恒定），正常关闭 ComfyUI 时随临时目录一并清空；重启后缓存失效，重新运行一次即可。
- 不占用显存：缓存的是磁盘文件引用，不是张量。

## 格式化选项

`filename_prefix` 和 `subdirectory_name` 支持以下占位符：

| 占位符          | 替换为                    |
| --------------- | ------------------------- |
| %seed%          | 种子值                    |
| %width%         | 图像宽度                  |
| %height%        | 图像高度                  |
| %pprompt%       | 正向提示词                |
| %pprompt:[n]%   | 正向提示词的前 n 个字符   |
| %nprompt%       | 负向提示词                |
| %nprompt:[n]%   | 负向提示词的前 n 个字符   |
| %model%         | 模型（checkpoint）名称    |
| %model:[n]%     | 模型名称的前 n 个字符     |
| %date%          | 生成时间（yyyyMMddhhmmss）|
| %date:[format]% | 生成时间，按指定格式      |

`%date:[format]%` 中 `[format]` 可用的标识符：

| 标识符 | 含义 |
| ------ | ---- |
| yyyy   | 年   |
| MM     | 月   |
| dd     | 日   |
| hh     | 时   |
| mm     | 分   |
| ss     | 秒   |

## 更新说明

### 2026-09（本仓库维护版）

- **健壮性修复**（全量代码审计后）：
  - 模型哈希缓存键改用归一化绝对路径，修复不同模型目录下同名文件互相串哈希的问题；导入时自动清理旧格式的缓存条目。
  - `subdirectory_name` 增加路径穿越防护（`../../`、盘符等会被拦截并告警）；修复二次格式化可能传入空字典导致崩溃的问题。
  - JSON 副文件名改用换扩展名的方式（旧逻辑会把文件名里的 "png"/"jpg" 字样也替换掉）；工作流数据缺失时不再报错。
  - `metadata_scope=none` 且挂载了附加元数据时不再崩溃。
  - 遇到未注册的第三方节点时跳过并告警，不再导致整次保存失败。
  - 去噪强度（Denoising strength）不再被无条件覆盖为真实值。
  - 节点拓扑缓存改为有上限的 LRU（256 条），长驻进程内存不再无限增长。
  - 修复 `easyuse` 扩展里 6 处未转义正则导致的 SyntaxWarning（Python 3.12+）。
- **jpg/webp 元数据增强**：附加元数据以 UTF-8 JSON 写入 EXIF 的 `ImageDescription` 字段（实测兼容 JPEG 与 WebP，中文可正确往返），A1111 参数照旧写 `UserComment`，互不干扰；EXIF 写入失败只告警，不影响图像落盘。
- **逐图 JSON**：批量出图时每张图都写一份同名 JSON 工作流文件。
- **Lora-Manager 支持**：兼容 [ComfyUI-Lora-Manager](https://github.com/wuchengyangcn/ComfyUI-Lora-Manager) 的触发词切换器——已勾选且汇入采样器正向分支的触发词会自动补全到元数据的正向提示词里（带词边界匹配，不会误判 LoRA 文件名中包含的子串）；同时注册了 `Prompt (LoraManager)` 节点作为提示词来源。
- **历史预览节点**：新增带缓存的可翻看预览节点（见上文）。

## 支持的节点与扩展

- **ComfyUI 核心节点**：
  - [modules/defs/samplers.py](modules/defs/samplers.py)
  - [modules/defs/captures.py](modules/defs/captures.py)

- **第三方节点**：
  - [modules/defs/ext/](modules/defs/ext/)（含 easyuse、efficiency、rgthree、Lora-Manager 等 20 余个扩展）

> [!TIP]  
> 如果使用 `full` 元数据范围时遇到报错，通常是因为你的第三方节点还不被支持。可以改用 ComfyUI 核心的对应节点，或者在 [ext](modules/defs/ext/) 目录里自己写一个声明式映射扩展。
