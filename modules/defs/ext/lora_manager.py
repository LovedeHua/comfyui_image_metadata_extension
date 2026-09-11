# lora_manager.py
# ComfyUI-Lora-Manager Extension for comfyui-image-metadata-extension
# https://github.com/willmiao/ComfyUI-Lora-Manager

import re
import os
import sys
import json
import hashlib
from collections import deque
from pathlib import Path

# 注意：本模块末尾不要再定义同名 SAMPLERS，否则会覆盖这里的引用（曾导致追溯
# 采样器时拿到空映射）。用别名绑定，load_extensions 后续对同一 dict 的
# update 依然可见。
from ..samplers import SAMPLERS as SAMPLER_FIELDS
from ...utils.log import print_warning

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


# ============ TriggerWord Toggle (LoraManager) ============
# 触发词切换器的勾选状态保存在 widget 值里（toggle_trigger_words / orinalMessage），
# 这是静态数据，不依赖执行缓存，因此是最可靠的触发词来源。
# 它的输出通常经由字符串拼接节点（JoinStringMulti 等）汇入提示词，而那条拼接链的
# 运行时值经常取不到，导致最终落盘的 Positive prompt 缺触发词。capture 会在写
# pnginfo 前用这里的结果兜底补全。

TRIGGER_TOGGLE_NODE = "TriggerWord Toggle (LoraManager)"


def _parse_trigger_entries(value):
    """把 toggle_trigger_words 的各种形态归一化成 [(词, 是否激活)]。"""
    seen = 0
    while seen < 4 and isinstance(value, dict):
        if "__value__" in value:
            value = value["__value__"]
        elif "value" in value:
            value = value["value"]
        else:
            break
        seen += 1

    if not isinstance(value, (list, tuple)):
        return []

    entries = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("text") or item.get("word") or ""
            active = item.get("active", True)
            if isinstance(active, (list, tuple)):
                active = active[0] if active else True
            entries.append((str(text).strip(), bool(active)))
        elif isinstance(item, str):
            entries.append((item.strip(), True))
    return entries


def _iter_link_targets(node):
    """遍历节点输入里的连线目标 id。"""
    for value in node.get("inputs", {}).values():
        if isinstance(value, list) and value and not isinstance(value[0], dict):
            yield str(value[0])


def _collect_reachable_from_samplers(prompt, field_name):
    """从各采样器的 positive/negative 输入出发，收集全部上游可达节点 id。

    比单纯"输出被谁引用"更精准：能区分触发词究竟汇入了 positive 还是 negative，
    避免把挂在 negative 分支上的切换器误补进 Positive prompt。
    """
    reachable = set()
    queue = deque()

    for node in prompt.values():
        field_map = SAMPLER_FIELDS.get(node.get("class_type"))
        if not field_map:
            continue
        input_key = field_map.get(field_name)
        if not input_key:
            continue
        link = node.get("inputs", {}).get(input_key)
        if isinstance(link, list) and link:
            queue.append(str(link[0]))

    while queue:
        node_id = queue.popleft()
        if node_id in reachable or node_id not in prompt:
            continue
        reachable.add(node_id)
        queue.extend(_iter_link_targets(prompt[node_id]))

    return reachable


def _is_node_consumed(node_id, prompt):
    """兜底判据：该节点的输出是否被图中其它节点引用。"""
    target = str(node_id)
    for node in prompt.values():
        for value in node.get("inputs", {}).values():
            if isinstance(value, list) and value and str(value[0]) == target:
                return True
    return False


_TRIGGER_PARSE_WARNED = False


def get_active_trigger_words(prompt, branch="positive"):
    """返回图中「已汇入指定分支 + 已勾选」的触发词，按出现顺序去重。

    不做缓存：本函数每次保存只调用一次，全图扫描是 O(N)（几十个节点），
    而基于 id(prompt) 的缓存在对象被回收后会因地址复用而误命中，
    把上一张工作流的触发词写进当前图 —— 不值得为这点开销冒这个险。
    """
    if not isinstance(prompt, dict):
        return []

    toggles = [
        (node_id, node)
        for node_id, node in prompt.items()
        if node.get("class_type") == TRIGGER_TOGGLE_NODE
    ]
    if not toggles:
        return []

    # 主判据：能沿图走到采样器的 positive/negative 输入
    reachable = _collect_reachable_from_samplers(prompt, branch)
    use_reachability = bool(reachable)

    words = []
    seen = set()
    parsed_any = False

    for node_id, node in toggles:
        inputs = node.get("inputs", {})
        entries = _parse_trigger_entries(inputs.get("toggle_trigger_words"))

        if not entries:
            # 前端会把结果镜像到这里（Lora-Manager 源码里就是这个拼写）
            raw = inputs.get("orinalMessage")
            if isinstance(raw, str) and raw.strip():
                entries = [(part.strip(), True) for part in raw.split(",")]

        if entries:
            parsed_any = True

        active = [word for word, on in entries if on and word]
        if not active:
            continue

        # 主判据失效（图里没有采样器）时退化为"输出被引用"
        if use_reachability:
            if str(node_id) not in reachable:
                continue
        elif not _is_node_consumed(node_id, prompt):
            continue

        for word in active:
            key = word.lower()
            if key not in seen:
                seen.add(key)
                words.append(word)

    # 静默失效最难排查：看得到切换器却一个词都解析不出来，多半是上游改了字段名
    global _TRIGGER_PARSE_WARNED
    if not words and not parsed_any and not _TRIGGER_PARSE_WARNED:
        _TRIGGER_PARSE_WARNED = True
        print_warning(
            "Found TriggerWord Toggle (LoraManager) node(s) but could not read any "
            "trigger words from them. The node's widget layout may have changed; "
            "trigger words will be missing from the saved metadata."
        )

    return words


# ============ Prompt (LoraManager) 支持 ============
# 该节点替代 CLIPTextEncode：除了主文本 text，还有若干动态触发词输入槽
# trigger_words1..N（通常连自 TriggerWord Toggle 或 Lora Loader 的 trigger_words
# 输出）。节点内部把它们拼在主文本之前：
#     prompt = ", ".join(trigger_words + [text])
# 下面完全按这个规则还原，因此触发词会一并进入 Positive prompt 元数据。

PROMPT_LM_NODE = "Prompt (LoraManager)"
TRIGGER_SLOT_RE = re.compile(r"^trigger_words(\d+)$")

_PROMPT_SOURCE_CACHE = {}
_PROMPT_SOURCE_CACHE_LIMIT = 64


def _select_input_dict(input_data):
    """把 selector 收到的 input_data 归一化成 {输入名: 值} 字典。"""
    if isinstance(input_data, dict):
        return input_data
    if isinstance(input_data, (tuple, list)):
        for part in input_data:
            if isinstance(part, dict):
                return part
    return {}


def _as_text(value):
    """
    取出 get_input_data 值里的字符串。

    widget 值被包成 [value]，连线值则是上游节点的输出元组 ("text",)，
    前端还可能再套一层 {"__value__": ...}。
    """
    seen = 0
    while seen < 4:
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            if not value:
                return ""
            value = value[0]
            seen += 1
            continue
        if isinstance(value, dict):
            if "__value__" in value:
                value = value["__value__"]
            elif "content" in value:
                value = value["content"]
            else:
                return ""
            seen += 1
            continue
        break

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def get_prompt_lm_text(node_id, obj, prompt, extra_data, outputs, input_data):
    """还原 Prompt (LoraManager) 实际送进 CLIP 的完整文本（触发词在前）。"""
    try:
        data = _select_input_dict(input_data)
        text = _as_text(data.get("text"))

        slots = []
        for key, value in data.items():
            match = TRIGGER_SLOT_RE.match(str(key))
            if not match:
                continue
            trigger_text = _as_text(value)
            if trigger_text:
                slots.append((int(match.group(1)), trigger_text))

        if not slots:
            return text

        # 与 Lora-Manager 的 encode() 保持一致：", ".join(trigger_words + [text])
        slots.sort(key=lambda item: item[0])
        trigger_words = [value for _, value in slots]
        if text:
            return ", ".join(trigger_words + [text])
        return ", ".join(trigger_words)
    except Exception:
        return ""


def _prompt_signature(prompt, field_name):
    """基于图内容生成缓存键。

    不能用 id(prompt)：对象被回收后内存地址会被新对象复用，配合 len() 一起
    仍会大量误命中（同节点数的工作流很常见），把上一张图的追溯结果用在这一张上。
    """
    parts = []
    for node_id, node in prompt.items():
        class_type = node.get("class_type", "")
        target = ""
        field_map = SAMPLER_FIELDS.get(class_type)
        if field_map and field_map.get(field_name):
            link = node.get("inputs", {}).get(field_map[field_name])
            if isinstance(link, list) and link:
                target = str(link[0])
        parts.append((str(node_id), class_type, target))
    parts.sort()
    return (field_name, tuple(parts))


def _resolve_prompt_sources(prompt, field_name):
    """
    找出所有连到采样器 positive/negative 上的 Prompt (LoraManager) 节点。

    核心 metadata 只认 CLIPTextEncode，这里按同样的思路追溯一遍，只是把
    终点的节点类型换成 Lora-Manager 的 prompt 节点。
    """
    if not isinstance(prompt, dict):
        return set()

    cache_key = _prompt_signature(prompt, field_name)
    cached = _PROMPT_SOURCE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    found = set()
    for node in prompt.values():
        field_map = SAMPLER_FIELDS.get(node.get("class_type"))
        if not field_map:
            continue

        input_key = field_map.get(field_name)
        if not input_key:
            continue

        link = node.get("inputs", {}).get(input_key)
        if not isinstance(link, list) or not link:
            continue

        queue = deque([link[0]])
        visited = set()
        while queue:
            node_id = queue.popleft()
            node_id = str(node_id) if isinstance(node_id, int) else node_id
            if node_id in visited or node_id not in prompt:
                continue
            visited.add(node_id)

            target = prompt[node_id]
            if target.get("class_type") == PROMPT_LM_NODE:
                # 触发词是从它的输入槽读的，不必再往上游走
                found.add(node_id)
                continue

            for value in target.get("inputs", {}).values():
                if isinstance(value, list) and value:
                    queue.append(value[0])

    if len(_PROMPT_SOURCE_CACHE) > _PROMPT_SOURCE_CACHE_LIMIT:
        _PROMPT_SOURCE_CACHE.clear()
    _PROMPT_SOURCE_CACHE[cache_key] = found
    return found


def is_positive_prompt_lm(node_id, obj, prompt, extra_data, outputs, input_data_all):
    return node_id in _resolve_prompt_sources(prompt, "positive")


def is_negative_prompt_lm(node_id, obj, prompt, extra_data, outputs, input_data_all):
    return node_id in _resolve_prompt_sources(prompt, "negative")


# ============ 构建 CAPTURE_FIELD_LIST ============
CAPTURE_FIELD_LIST = {}

# 如果 MetaField 可用，也添加 MetaField 键名
if MetaField is not None:
    try:
        lora_fields = {
            "LORA_MODEL_NAME": get_lora_model_name_stack,
            "LORA_MODEL_HASH": get_lora_model_hash_stack,
            "LORA_STRENGTH_MODEL": get_lora_strength_model_stack,
            "LORA_STRENGTH_CLIP": get_lora_strength_clip_stack,
        }
        lora_node_map = {
            MetaField[name]: selector
            for name, selector in lora_fields.items()
            if hasattr(MetaField, name)
        }
        if lora_node_map:
            CAPTURE_FIELD_LIST["Lora Loader (LoraManager)"] = {
                meta: {"selector": selector} for meta, selector in lora_node_map.items()
            }

        # 提示词来源：text + trigger_words 槽，一并带上触发词
        prompt_node_map = {}
        if hasattr(MetaField, "POSITIVE_PROMPT"):
            prompt_node_map[MetaField.POSITIVE_PROMPT] = {
                "selector": get_prompt_lm_text,
                "validate": is_positive_prompt_lm,
            }
        if hasattr(MetaField, "NEGATIVE_PROMPT"):
            prompt_node_map[MetaField.NEGATIVE_PROMPT] = {
                "selector": get_prompt_lm_text,
                "validate": is_negative_prompt_lm,
            }
        if prompt_node_map:
            CAPTURE_FIELD_LIST[PROMPT_LM_NODE] = prompt_node_map
    except Exception:
        pass

# 自动初始化
try:
    init_lora_manager()
except Exception:
    pass