# lora_manager.py
# ComfyUI-Lora-Manager Extension for comfyui-image-metadata-extension
# https://github.com/willmiao/ComfyUI-Lora-Manager

import re
import os
import sys
import json
import hashlib
from pathlib import Path

# ============ 配置 ComfyUI 路径 ============
def get_comfyui_paths():
    """获取 ComfyUI 的各种路径"""
    paths = {
        'comfyui_root': None,
        'lora_folders': [],
    }
    
    # 方法 1: 从当前文件位置推断
    try:
        current_file = os.path.abspath(__file__)
        parts = current_file.split(os.sep)
        for i in range(len(parts) - 1, -1, -1):
            if 'custom_nodes' in parts[i]:
                paths['comfyui_root'] = os.sep.join(parts[:i])
                break
    except Exception:
        pass
    
    # 方法 2: 从环境变量获取
    if not paths['comfyui_root']:
        paths['comfyui_root'] = os.environ.get('COMFYUI_ROOT')
    
    # 方法 3: 常见路径
    if not paths['comfyui_root']:
        common_roots = [
            os.path.expanduser("~/ComfyUI"),
            os.path.expanduser("~/comfyui"),
            "/opt/ComfyUI",
            "/app/ComfyUI",
            ".",
        ]
        for root in common_roots:
            if os.path.exists(os.path.join(root, "main.py")) or \
               os.path.exists(os.path.join(root, "ComfyUI", "main.py")):
                paths['comfyui_root'] = root
                break
    
    if paths['comfyui_root']:
        standard_lora = os.path.join(paths['comfyui_root'], "models", "loras")
        if os.path.exists(standard_lora):
            paths['lora_folders'].append(standard_lora)
        
        extra_paths_file = os.path.join(paths['comfyui_root'], "extra_model_paths.yaml")
        if os.path.exists(extra_paths_file):
            try:
                import yaml
                with open(extra_paths_file, 'r') as f:
                    extra_config = yaml.safe_load(f)
                    if extra_config and 'loras' in extra_config:
                        for path in extra_config['loras']:
                            if os.path.exists(path) and path not in paths['lora_folders']:
                                paths['lora_folders'].append(path)
            except Exception:
                pass
    
    return paths

COMFYUI_PATHS = get_comfyui_paths()

# ============ 导入 ComfyUI 模块 ============
try:
    import folder_paths
    FOLDER_PATHS_AVAILABLE = True
    try:
        fp_loras = folder_paths.get_folder_paths("loras")
        if fp_loras:
            for p in fp_loras:
                if p not in COMFYUI_PATHS['lora_folders'] and os.path.exists(p):
                    COMFYUI_PATHS['lora_folders'].append(p)
    except Exception:
        pass
except Exception:
    FOLDER_PATHS_AVAILABLE = False
    folder_paths = None

# ============ 尝试导入 MetaField ============
try:
    from ..meta import MetaField
except Exception:
    MetaField = None

# ============ Civitai AutoV2 哈希计算 ============
def calc_autov2_hash(file_path):
    """计算 Civitai AutoV2 哈希值（SHA256 整个文件，取前 10 位）"""
    try:
        if not file_path or not os.path.exists(file_path) or not os.path.isfile(file_path):
            return None
        
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        
        return sha256_hash.hexdigest()[:10]
    except Exception:
        return None


def calc_lora_hash(file_path):
    """计算 LoRA 文件哈希（Civitai AutoV2 兼容版本）"""
    return calc_autov2_hash(file_path)


# ============ LoraManager 连接 ============
LORA_MANAGER_AVAILABLE = False
ServiceRegistry = None

def init_lora_manager():
    """初始化 ComfyUI-Lora-Manager 的连接"""
    global LORA_MANAGER_AVAILABLE, ServiceRegistry
    
    if LORA_MANAGER_AVAILABLE and ServiceRegistry is not None:
        return True
    
    try:
        lora_manager_paths = []
        if COMFYUI_PATHS['comfyui_root']:
            lora_manager_paths.extend([
                os.path.join(COMFYUI_PATHS['comfyui_root'], 'custom_nodes', 'ComfyUI-Lora-Manager', 'py', 'services'),
                os.path.join(COMFYUI_PATHS['comfyui_root'], 'custom_nodes', 'comfyui-lora-manager', 'py', 'services'),
                os.path.join(COMFYUI_PATHS['comfyui_root'], 'custom_nodes', 'ComfyUI-Lora-Manager', 'py'),
                os.path.join(COMFYUI_PATHS['comfyui_root'], 'custom_nodes', 'comfyui-lora-manager', 'py'),
            ])
        
        try:
            from services.service_registry import ServiceRegistry as SR
            ServiceRegistry = SR
            LORA_MANAGER_AVAILABLE = True
            return True
        except ImportError:
            pass
        
        for path in lora_manager_paths:
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path) and abs_path not in sys.path:
                sys.path.insert(0, abs_path)
                try:
                    if 'services' in abs_path:
                        from service_registry import ServiceRegistry as SR
                    else:
                        from services.service_registry import ServiceRegistry as SR
                    ServiceRegistry = SR
                    LORA_MANAGER_AVAILABLE = True
                    return True
                except ImportError:
                    continue
        
        return False
    except Exception:
        return False


def get_lora_hash_from_manager(lora_name):
    """从 ComfyUI-Lora-Manager 的缓存中获取 LoRA 哈希值"""
    global ServiceRegistry, LORA_MANAGER_AVAILABLE
    
    if not LORA_MANAGER_AVAILABLE or ServiceRegistry is None:
        return None
    
    try:
        scanner = ServiceRegistry.get_service_sync("lora_scanner")
        if scanner is None:
            return None
        
        base_name = lora_name.replace('.safetensors', '').replace('.pt', '').replace('.ckpt', '')
        
        if hasattr(scanner, 'get_hash_by_filename'):
            hash_value = scanner.get_hash_by_filename(base_name)
            if hash_value:
                return hash_value
        
        if hasattr(scanner, 'lora_cache'):
            cache = scanner.lora_cache
            
            if base_name in cache:
                lora_data = cache[base_name]
                if isinstance(lora_data, dict) and 'hash' in lora_data:
                    return lora_data['hash']
            
            for ext in ['.safetensors', '.pt', '.ckpt']:
                key = base_name + ext
                if key in cache:
                    lora_data = cache[key]
                    if isinstance(lora_data, dict) and 'hash' in lora_data:
                        return lora_data['hash']
            
            for key, lora_data in cache.items():
                key_basename = os.path.basename(key)
                key_name = os.path.splitext(key_basename)[0]
                if key_name == base_name:
                    if isinstance(lora_data, dict) and 'hash' in lora_data:
                        return lora_data['hash']
        
        return None
    except Exception:
        return None


def find_lora_file(lora_name):
    """查找 LoRA 文件的完整路径"""
    if not lora_name.endswith(('.safetensors', '.pt', '.ckpt')):
        lora_name_with_ext = lora_name + ".safetensors"
    else:
        lora_name_with_ext = lora_name
        lora_name = lora_name.replace('.safetensors', '').replace('.pt', '').replace('.ckpt', '')
    
    if FOLDER_PATHS_AVAILABLE and folder_paths is not None:
        try:
            full_path = folder_paths.get_full_path("loras", lora_name_with_ext)
            if full_path and os.path.isfile(full_path):
                return full_path
        except Exception:
            pass
    
    for folder in COMFYUI_PATHS['lora_folders']:
        if not folder or not os.path.isdir(folder):
            continue
        
        full_path = os.path.join(folder, lora_name_with_ext)
        if os.path.isfile(full_path):
            return full_path
        
        try:
            for root, dirs, files in os.walk(folder):
                if lora_name_with_ext in files:
                    return os.path.join(root, lora_name_with_ext)
                for file in files:
                    if file.lower() == lora_name_with_ext.lower():
                        return os.path.join(root, file)
        except Exception:
            pass
    
    return None


def get_lora_hash(lora_name):
    """获取 LoRA 哈希值（Civitai AutoV2 兼容）"""
    if not lora_name:
        return None
    
    lora_name = str(lora_name).strip()
    if not lora_name:
        return None
    
    init_lora_manager()
    
    hash_value = get_lora_hash_from_manager(lora_name)
    if hash_value:
        return hash_value[:10] if len(hash_value) >= 10 else hash_value
    
    file_path = find_lora_file(lora_name)
    if file_path:
        return calc_autov2_hash(file_path)
    
    return None


def get_lora_info_from_input(node_id, obj, prompt, extra_data, outputs, input_data):
    """从 Lora Loader (LoraManager) 节点的输入中提取 LoRA 信息，只返回 active=True 的 LoRA"""
    try:
        if isinstance(input_data, tuple):
            if len(input_data) > 0 and isinstance(input_data[0], dict):
                data = input_data[0]
            else:
                return []
        elif isinstance(input_data, dict):
            data = input_data
        else:
            return []
        
        text_input = data.get("text", "")
        if not text_input:
            return []
        
        if isinstance(text_input, (list, tuple)):
            text_input = text_input[0] if text_input else ""
        
        if not isinstance(text_input, str):
            text_input = str(text_input)
        
        # 提取实际的 LoRA 数组
        loras_raw = data.get("loras", [])
        actual_loras = []
        if isinstance(loras_raw, (list, tuple)) and len(loras_raw) > 0:
            first_item = loras_raw[0]
            if isinstance(first_item, dict) and '__value__' in first_item:
                actual_loras = first_item['__value__']
            elif isinstance(first_item, (list, tuple)):
                actual_loras = first_item
        
        # 解析 text 中的 LoRA，并检查每个的 active 状态
        lora_pattern = r'<lora:([^:]+)(?::([^>]+))?>'
        matches = re.findall(lora_pattern, text_input)
        
        lora_info = []
        for match in matches:
            lora_name = match[0].strip()
            strength_str = match[1].strip() if len(match) > 1 and match[1] else "1.0"
            
            if not lora_name:
                continue
            
            # 在 actual_loras 中查找对应的 LoRA 配置
            lora_config = None
            for config in actual_loras:
                if isinstance(config, dict) and config.get('name') == lora_name:
                    lora_config = config
                    break
            
            # 检查 active 状态
            if lora_config:
                is_active = lora_config.get('active', True)
                if isinstance(is_active, (list, tuple)):
                    is_active = is_active[0] if is_active else True
                if not bool(is_active):
                    continue
            
            # 解析强度
            if ":" in strength_str:
                parts = strength_str.split(":")
                model_strength = float(parts[0]) if parts[0] else 1.0
                clip_strength = float(parts[1]) if len(parts) > 1 and parts[1] else model_strength
            else:
                model_strength = float(strength_str) if strength_str else 1.0
                clip_strength = model_strength
            
            lora_info.append({
                "name": lora_name,
                "strength_model": model_strength,
                "strength_clip": clip_strength
            })
        
        return lora_info
    except Exception:
        return []


def get_lora_model_name_stack(node_id, obj, prompt, extra_data, outputs, input_data):
    lora_info = get_lora_info_from_input(node_id, obj, prompt, extra_data, outputs, input_data)
    return [info["name"] for info in lora_info if info["name"]]


def get_lora_strength_model_stack(node_id, obj, prompt, extra_data, outputs, input_data):
    lora_info = get_lora_info_from_input(node_id, obj, prompt, extra_data, outputs, input_data)
    return [info["strength_model"] for info in lora_info]


def get_lora_strength_clip_stack(node_id, obj, prompt, extra_data, outputs, input_data):
    lora_info = get_lora_info_from_input(node_id, obj, prompt, extra_data, outputs, input_data)
    return [info["strength_clip"] for info in lora_info]


def get_lora_model_hash_stack(node_id, obj, prompt, extra_data, outputs, input_data):
    lora_info = get_lora_info_from_input(node_id, obj, prompt, extra_data, outputs, input_data)
    hashes = []
    for info in lora_info:
        if info["name"]:
            hash_value = get_lora_hash(info["name"])
            hashes.append(hash_value if hash_value else "")
    return hashes


# ============ 构建 CAPTURE_FIELD_LIST ============
CAPTURE_FIELD_LIST = {
    "Lora Loader (LoraManager)": {
        "lora_model_name": {
            "selector": get_lora_model_name_stack,
        },
        "lora_model_hash": {
            "selector": get_lora_model_hash_stack,
        },
        "lora_strength_model": {
            "selector": get_lora_strength_model_stack,
        },
        "lora_strength_clip": {
            "selector": get_lora_strength_clip_stack,
        },
    }
}

# 如果 MetaField 可用，也添加 MetaField 键名
if MetaField is not None:
    try:
        if hasattr(MetaField, 'LORA_MODEL_NAME'):
            CAPTURE_FIELD_LIST["Lora Loader (LoraManager)"][MetaField.LORA_MODEL_NAME] = {
                "selector": get_lora_model_name_stack,
            }
        if hasattr(MetaField, 'LORA_MODEL_HASH'):
            CAPTURE_FIELD_LIST["Lora Loader (LoraManager)"][MetaField.LORA_MODEL_HASH] = {
                "selector": get_lora_model_hash_stack,
            }
        if hasattr(MetaField, 'LORA_STRENGTH_MODEL'):
            CAPTURE_FIELD_LIST["Lora Loader (LoraManager)"][MetaField.LORA_STRENGTH_MODEL] = {
                "selector": get_lora_strength_model_stack,
            }
        if hasattr(MetaField, 'LORA_STRENGTH_CLIP'):
            CAPTURE_FIELD_LIST["Lora Loader (LoraManager)"][MetaField.LORA_STRENGTH_CLIP] = {
                "selector": get_lora_strength_clip_stack,
            }
    except Exception:
        pass

# 向后兼容
SAMPLERS = {}

# 自动初始化
try:
    init_lora_manager()
except Exception:
    pass
