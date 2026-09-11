import glob
import json
import os
import posixpath
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import piexif
import piexif.helper
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from enum import Enum

import folder_paths

from .. import hook
from ..capture import Capture
from ..trace import Trace
from ..utils.log import print_warning


class OutputFormat(str, Enum):
    PNG = "png"
    PNG_JSON = "png_with_json"
    JPG = "jpg"
    JPG_JSON = "jpg_with_json"
    WEBP = "webp"
    WEBP_JSON = "webp_with_json"


class QualityOption(str, Enum):
    MAX = "max"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class MetadataScope(str, Enum):
    FULL = "full"
    DEFAULT = "default"
    PARAMETERS_ONLY = "parameters_only"
    WORKFLOW_ONLY = "workflow_only"
    NONE = "none"


# refer. https://github.com/comfyanonymous/ComfyUI/blob/38b7ac6e269e6ecc5bdd6fefdfb2fb1185b09c9d/nodes.py#L1411
class SaveImageWithMetaData:
    OUTPUT_FORMATS = [e for e in OutputFormat]
    QUALITY_OPTIONS = [e for e in QualityOption]
    METADATA_OPTIONS = [e for e in MetadataScope]
    NEEDS_METADATA_KEYS = {"seed", "width", "height", "pprompt", "nprompt", "model"}
    EXIF_FORMATS = ("jpg", "webp")

    def __init__(self):
        self.output_dir = folder_paths.get_output_directory()
        self.type = "output"
        self.prefix_append = ""
        self.compress_level = 4

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "要保存的图像。"}),
                "filename_prefix": ("STRING", {"default": "ComfyUI", "tooltip": "保存文件名的前缀。可使用格式化占位符，如 %date:yyyy-MM-dd% 或 %seed%，也可以组合使用，例如 %date:hhmmss%_%seed%。"}),
                "subdirectory_name": ("STRING", {
                    "default": "",
                    "tooltip": (
                        "自定义子目录。留空则保存到默认输出目录。"
                        "可使用格式化占位符，如 %date:yyyy-MM-dd%。"
                    ),
                }),
                "output_format": (s.OUTPUT_FORMATS, {
                    "tooltip": "图像的保存格式。"
                }),
            },
            "optional": {
                "extra_metadata": ("EXTRA_METADATA", {
                    "tooltip": "写入图像的附加键值对元数据。"
                }),
                "quality": (s.QUALITY_OPTIONS, {
                    "tooltip": "图像质量："
                            "\n'max' / 'lossless WebP' - 100"
                            "\n'high' - 80"
                            "\n'medium' - 60"
                            "\n'low' - 30"
                            "\n\n质量越低、文件越小。PNG 格式忽略此设置。"
                }),
                "metadata_scope": (s.METADATA_OPTIONS, {
                    "tooltip": "选择要写入的元数据："
                            "\n'full' - 默认元数据 + 附加元数据，"
                            "\n'default' - 与原生 SaveImage 节点相同，"
                            "\n'parameters_only' - 仅 A1111 风格参数，"
                            "\n'workflow_only' - 仅工作流数据，"
                            "\n'none' - 不保存。"
                }),
                "include_batch_num": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "文件名中包含批次序号。"
                }),
                "prefer_nearest": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "为 true 时优先从拓扑距离最近的节点取值。"
                }),
            },
            "hidden": {
                "prompt": "PROMPT",
                "extra_pnginfo": "EXTRA_PNGINFO"
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "save_images"
    OUTPUT_NODE = True
    DESCRIPTION = "将输入图像连同元数据一起保存到 ComfyUI 输出目录。"
    CATEGORY = "SaveImage"
    SEARCH_ALIASES = ["保存图像", "元数据", "save image", "metadata", "civitai"]

    pattern_format = re.compile(r"(%[^%]+%)") # Pattern to match mask values in the filename

    def parse_output_format(self, output_format: str):
        fmt = OutputFormat(output_format)
        save_workflow_json = fmt.name.endswith("JSON")
        base_format = fmt.replace("_with_json", "")
        return base_format, save_workflow_json

    def get_quality_value(self, quality: str) -> int:
        return {
            QualityOption.MAX: 100,
            QualityOption.HIGH: 80,
            QualityOption.MEDIUM: 60,
            QualityOption.LOW: 30
        }.get(quality, 100)

    @staticmethod
    def sanitize_subdirectory(name: str) -> str:
        """
        Keeps a user supplied subdirectory inside the output directory.

        Strips drive letters, leading separators and any "." / ".." segment so a
        value like "../../foo" or "C:\\foo" cannot write outside of the output
        directory. Returns a relative path, or "" when nothing is left.
        """
        cleaned = name.replace("\\", "/")
        cleaned = os.path.splitdrive(cleaned)[1]
        # Normalize first so segments such as "a/../../b" collapse to "../b"
        # instead of silently landing one level below the intended folder.
        normalized = posixpath.normpath(cleaned)
        parts = [p for p in normalized.split("/") if p and p != "."]
        while parts and parts[0] == "..":
            parts.pop(0)
        return "/".join(parts)

    def find_next_available_filename(self, folder: str, name: str, ext: str):
        """
        Finds the next available filename by checking existing files in the directory.
        """
        # Escape the name: it may come from a prompt placeholder and contain glob
        # metacharacters ("[", "]", "*"), which would silently match other files
        # and hand back a name that is already taken.
        pattern = f"{glob.escape(name)}_*.{ext}"
        existing = {f.stem for f in Path(folder).glob(pattern)}
        i = 1
        while f"{name}_{i:05d}" in existing:
            i += 1
        return i

    def build_exif_bytes(self, pnginfo_dict, extra_metadata):
        """
        Build the EXIF payload for jpg/webp output.

        The A1111 style parameters keep going into UserComment, while the custom
        pairs are stored in ImageDescription as UTF-8 JSON. JPEG/WebP have no
        free-form text chunk like PNG's tEXt block, and ImageDescription is the
        most widely read free-text tag (exiftool, file managers, PIL). Keeping
        them apart also leaves the parameters string parseable by Civitai.
        """
        exif_ifd = {
            piexif.ExifIFD.UserComment: piexif.helper.UserComment.dump(
                Capture.gen_parameters_str(pnginfo_dict), encoding="unicode"
            )
        }

        zeroth = {}
        if extra_metadata:
            zeroth[piexif.ImageIFD.ImageDescription] = json.dumps(
                extra_metadata, ensure_ascii=False
            ).encode("utf-8")

        return piexif.dump({"0th": zeroth, "Exif": exif_ifd})

    def insert_exif(self, path, pnginfo_dict, extra_metadata):
        """
        Write EXIF into an already saved file.

        Failures only warn: losing metadata must not lose the image itself.
        """
        try:
            piexif.insert(self.build_exif_bytes(pnginfo_dict, extra_metadata), path)
        except Exception as e:
            print_warning(f"Could not write EXIF metadata into '{path}': {e}")

    @staticmethod
    def write_workflow_json(folder, image_filename, workflow):
        """Write <image stem>.json beside the image it belongs to."""
        json_path = os.path.join(folder, os.path.splitext(image_filename)[0] + ".json")
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(workflow, f)
        except Exception as e:
            print_warning(f"Failed to write workflow JSON '{json_path}': {e}")

    @classmethod
    def parse_filename_placeholders(cls, filename: str) -> list[str]:
        """Extracts placeholder segments like %seed%, %pprompt:32%, etc."""
        return re.findall(cls.pattern_format, filename) if "%" in filename else []

    def needs_pnginfo_in_filename(self, segments: list[str]) -> bool:
        for segment in segments:
            parts = segment.strip("%").split(":")
            if parts[0] in self.NEEDS_METADATA_KEYS:
                return True
        return False

    def save_images(self, images, filename_prefix="ComfyUI", subdirectory_name="", prompt=None,
                    extra_pnginfo=None, extra_metadata=None, output_format="png",
                    quality="max", metadata_scope="full",
                    include_batch_num=True, prefer_nearest=True, pnginfo_dict=None):

        extra_metadata = extra_metadata or {}
        base_format, save_workflow_json = self.parse_output_format(output_format)
        pnginfo = PngInfo()

        # Parse filename
        filename_prefix = filename_prefix.strip()
        segments = self.parse_filename_placeholders(filename_prefix)

        if metadata_scope in [MetadataScope.FULL, MetadataScope.PARAMETERS_ONLY] or self.needs_pnginfo_in_filename(segments):
            pnginfo_dict = pnginfo_dict or self.gen_pnginfo(prompt, prefer_nearest)

        filename_prefix = self.format_filename(filename_prefix, pnginfo_dict or {}, segments) + self.prefix_append
        subdirectory_name = self.format_filename(subdirectory_name, pnginfo_dict or {})


        image_shape = images[0].shape
        full_output_folder, filename, counter, subfolder, filename_prefix = folder_paths.get_save_image_path(
            filename_prefix, self.output_dir, image_shape[1], image_shape[0]
        )

        # Handle subdirectory naming and creation.
        # Placeholders were already resolved above, so this step only sanitizes
        # the path (re-running format_filename here could receive a None dict and
        # would also re-expand values that legitimately contain "%").
        raw_subdirectory = subdirectory_name.strip()
        subdirectory_name = self.sanitize_subdirectory(raw_subdirectory)
        if subdirectory_name != raw_subdirectory.replace("\\", "/"):
            print_warning(
                f"Subdirectory name was adjusted to stay inside the output "
                f"directory: '{raw_subdirectory}' -> '{subdirectory_name}'"
            )

        if subdirectory_name:
            full_output_folder = os.path.join(self.output_dir, *subdirectory_name.split("/"))
            filename = filename_prefix

        os.makedirs(full_output_folder, exist_ok=True)

        # Resolve the workflow once; it is written next to every image below.
        workflow = None
        if save_workflow_json:
            workflow = (extra_pnginfo or {}).get("workflow")
            if workflow is None:
                print_warning("Workflow data is unavailable, no JSON sidecar file will be written.")

        results = list()
        images_length = len(images)

        # Process each image
        for batch_number, image in enumerate(images):
            i = 255. * image.cpu().numpy()
            img = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))

            # Prepare metadata
            metadata = self.prepare_pnginfo(pnginfo, pnginfo_dict, batch_number, images_length, prompt, extra_pnginfo, metadata_scope)
            for key, value in extra_metadata.items():
                metadata.add_text(key, value)

            # Handle filename collision and batch number inclusion
            file = f"{filename}_{batch_number:05d}.{base_format}" if include_batch_num else f"{filename}.{base_format}"
            path = os.path.join(full_output_folder, file)

            # Check for filename collision (using next available name)
            if os.path.exists(path):
                count = self.find_next_available_filename(full_output_folder, filename, base_format)
                file = f"{filename}_{count:05d}.{base_format}"
                path = os.path.join(full_output_folder, file)

            quality_value = self.get_quality_value(quality)

            # Save image based on format
            if base_format == "webp":
                img.save(path, "WEBP", lossless=(quality_value == 100), quality=quality_value)
            elif base_format == "png":
                img.save(path, pnginfo=metadata, compress_level=self.compress_level)
            else:
                img.save(path, optimize=True, quality=quality_value)

            # Insert EXIF for jpg/webp formats
            if base_format in self.EXIF_FORMATS:
                self.insert_exif(path, pnginfo_dict, extra_metadata)

            # Write the workflow sidecar for this image, not just for the last one
            if workflow is not None:
                self.write_workflow_json(full_output_folder, file, workflow)

            results.append({"filename": file, "subfolder": full_output_folder, "type": self.type})

        return {"ui": {"images": results}}

    def prepare_pnginfo(self, metadata, pnginfo_dict, batch_number, total_images, prompt, extra_pnginfo, metadata_scope):
        """
        Return final PNG metadata with batch information, parameters, and optional prompt details.
        """
        if metadata_scope == MetadataScope.NONE:
            # Return an empty container rather than None: the caller still writes
            # the user supplied extra_metadata on top of it, and PngInfo is safe
            # to pass to PIL even when it holds nothing.
            return PngInfo()

        if pnginfo_dict:
            pnginfo_copy = pnginfo_dict.copy()

            if total_images > 1:
                pnginfo_copy["Batch index"] = batch_number
                pnginfo_copy["Batch size"] = total_images

            if metadata_scope in [MetadataScope.FULL, MetadataScope.PARAMETERS_ONLY]:
                parameters = Capture.gen_parameters_str(pnginfo_copy)
                if parameters and "Steps" in parameters:
                    metadata.add_text("parameters", parameters)
                    if metadata_scope == MetadataScope.PARAMETERS_ONLY:
                        return metadata

        if prompt is not None and metadata_scope != MetadataScope.WORKFLOW_ONLY:
            metadata.add_text("prompt", json.dumps(prompt))

        if extra_pnginfo is not None:
            for x in extra_pnginfo:
                metadata.add_text(x, json.dumps(extra_pnginfo[x]))

        return metadata

    @classmethod
    def gen_pnginfo(s, prompt, prefer_nearest):
        inputs = Capture.get_inputs()
        trace_tree_from_this_node = Trace.trace(hook.current_save_image_node_id, prompt)
        inputs_before_this_node = Trace.filter_inputs_by_trace_tree(inputs, trace_tree_from_this_node, prefer_nearest)

        sampler_node_id = Trace.find_sampler_node_id(trace_tree_from_this_node)
        if sampler_node_id:
            trace_tree_from_sampler_node = Trace.trace(sampler_node_id, prompt)
            inputs_before_sampler_node = Trace.filter_inputs_by_trace_tree(inputs, trace_tree_from_sampler_node, prefer_nearest)
        else:
            inputs_before_sampler_node = {}

        return Capture.gen_pnginfo_dict(inputs_before_sampler_node, inputs_before_this_node, prompt)

    @classmethod
    def format_filename(cls, filename, pnginfo_dict, segments=None):
        """
        Replaces placeholders in the filename with actual values like date, seed, prompt, etc.
        """
        if "%" not in filename:
            return filename

        segments = segments or re.findall(cls.pattern_format, filename)
        now = datetime.now()
        date_table = {
            "yyyy": f"{now.year}",
            "MM": f"{now.month:02d}",
            "dd": f"{now.day:02d}",
            "hh": f"{now.hour:02d}",
            "mm": f"{now.minute:02d}",
            "ss": f"{now.second:02d}",
        }

        for segment in segments:
            parts = segment.strip("%").split(":")
            key = parts[0]

            if key == "seed":
                seed = pnginfo_dict.get("Seed")
                if seed is None:
                    print_warning("Seed not found in pnginfo_dict!")
                filename = filename.replace(segment, str(seed or ""))

            elif key in {"width", "height"}:
                size = pnginfo_dict.get("Size", "x").split("x")
                if "Size" not in pnginfo_dict:
                    print_warning("Size not found in pnginfo_dict!")
                value = size[0] if key == "width" else size[1]
                filename = filename.replace(segment, value)

            elif key in {"pprompt", "nprompt"}:
                prompt_key = "Positive prompt" if key == "pprompt" else "Negative prompt"
                prompt = pnginfo_dict.get(prompt_key, "")
                if not prompt:
                    print_warning(f"{prompt_key} not found in pnginfo_dict!")
                prompt = prompt.replace("\n", " ")
                length = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                filename = filename.replace(segment, prompt[:length].strip() if length else prompt.strip())

            elif key == "model":
                model = pnginfo_dict.get("Model", "")
                if not model:
                    print_warning("Model not found in pnginfo_dict!")
                model = os.path.splitext(os.path.basename(model))[0]
                length = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                filename = filename.replace(segment, model[:length] if length else model)

            elif key == "date":
                date_format = parts[1] if len(parts) > 1 else "yyyyMMddhhmmss"
                for k, v in date_table.items():
                    date_format = date_format.replace(k, v)
                filename = filename.replace(segment, date_format)

        return filename


class CreateExtraMetaData:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "optional": {
                "extra_metadata": ("EXTRA_METADATA", {"forceInput": True}),
                **{
                    f"{type}{i}": ("STRING", {
                        "default": "",
                        "multiline": False,
                        "tooltip": f"第 {i} 组的{'键名' if type == 'key' else '值'}",
                    })
                    for i in range(1, 5)
                    for type in ["key", "value"]
                },
            }
        }

    RETURN_TYPES = ("EXTRA_METADATA",)
    FUNCTION = "create_extra_metadata"
    DESCRIPTION = "通过键值对创建自定义附加元数据。允许空值，但键和值必须成对出现。"
    CATEGORY = "SaveImage"

    def create_extra_metadata(self, extra_metadata=None, **keys_values):
        if extra_metadata is None:
            extra_metadata = {}

        for i in range(1, 5):
            key = keys_values.get(f"key{i}", "").strip()
            value = keys_values.get(f"value{i}", "").strip()

            if key:
                extra_metadata[key] = value
            elif value:
                raise ValueError(f"Value provided for 'value{i}' without corresponding 'key{i}'.")

        return (extra_metadata,)
