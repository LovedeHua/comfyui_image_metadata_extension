from .modules.nodes.node import SaveImageWithMetaData, CreateExtraMetaData
from .modules.nodes.preview import ImageHistoryPreview

# (base_name, class_ref, display_name)
node_definitions = [
    ("SaveImageWithMetaData", SaveImageWithMetaData, "保存图像（含元数据）"),
    ("CreateExtraMetaData", CreateExtraMetaData, "创建附加元数据"),
    ("ImageHistoryPreview", ImageHistoryPreview, "历史预览"),
]

NODE_CLASS_MAPPINGS = {
    f"{base_name}": class_ref for base_name, class_ref, _ in node_definitions
}

NODE_DISPLAY_NAME_MAPPINGS = {
    f"{base_name}": f"{display_name}" for base_name, _, display_name in node_definitions
}

# 前端扩展（历史切换控件）所在目录
WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
