"""ImageHistoryPreview: 带历史缓存的预览节点。

后端只负责两件事：
1. 把进来的每张图落到 temp 目录（与 PreviewImage 同类，不出图之间不清理，
   重启才清空），拿到 filename/subfolder/type 引用；
2. 按节点 id 维护最近 5 张的引用列表，随 ui 输出给前端。

切换浏览完全在前端做（web/js/image_history_preview.js）：后端每次执行
只追加历史，不承担"当前看到第几张"的状态。
"""

import os

import numpy as np
from PIL import Image

import folder_paths

MAX_HISTORY = 5
PREFIX = "HistoryPreview/HistoryPreview"


def _tensor_to_pil(image_tensor) -> Image.Image:
    arr = (255.0 * image_tensor.cpu().numpy()).clip(0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _cleanup_orphans() -> None:
    """删除 HistoryPreview 目录里不再被任何节点历史引用的 PNG。

    历史引用只保留每个节点最近 MAX_HISTORY 张，被挤出去的旧文件若不回收，
    temp 目录会随出图量无限增长。引用集合取自全部节点（目录是共享的），
    删除失败（文件正被浏览器预览等）就留给下一次清理，绝不影响出图。
    """
    referenced = set()
    for entries in ImageHistoryPreview._history.values():
        for entry in entries:
            referenced.add(entry.get("filename"))

    folder = os.path.join(folder_paths.get_temp_directory(), "HistoryPreview")
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if not name.endswith(".png") or name in referenced:
            continue
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


class ImageHistoryPreview:
    # unique_id -> [{"filename", "subfolder", "type"}, ...]，旧的在前面
    _history = {}

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    OUTPUT_NODE = True
    FUNCTION = "preview"
    CATEGORY = "SaveImage"
    DESCRIPTION = (
        "预览图像，不保存到输出目录。保留本节点最近见过的 5 张图，"
        "可以在界面上前后翻看。图像缓存在临时目录中，重启 ComfyUI 后失效。"
    )
    SEARCH_ALIASES = [
        "历史预览",
        "图片缓存",
        "history preview",
        "image history",
        "preview",
        "show image",
        "image viewer",
    ]

    def preview(self, images, unique_id=None):
        key = str(unique_id) if unique_id else "default"
        history = list(ImageHistoryPreview._history.get(key) or [])

        if images is not None and len(images) > 0:
            output_dir = folder_paths.get_temp_directory()
            first = images[0]
            full_output_folder, filename, counter, subfolder, _ = (
                folder_paths.get_save_image_path(
                    PREFIX, output_dir, first.shape[1], first.shape[0]
                )
            )

            for batch_number, image in enumerate(images):
                file = f"{filename}_{counter + batch_number:05}_.png"
                path = os.path.join(full_output_folder, file)
                try:
                    _tensor_to_pil(image).save(path, compress_level=1)
                except Exception:
                    # 保存失败只影响历史，不该中断工作流
                    continue
                history.append(
                    {"filename": file, "subfolder": subfolder, "type": "temp"}
                )

        history = history[-MAX_HISTORY:]
        ImageHistoryPreview._history[key] = history
        _cleanup_orphans()

        return {"ui": {"history": history}, "result": (images,)}
