"""
ComfyUI 自定义节点：CharacterTagSelector
- 扫描 web/data 下的多个 JSON 文件作为数据源
- generate_tag 会按当前选中的 json_file 查找角色并输出标签 + 预览图
- 后端 INPUT_TYPES 只能初始化 character 列表（动态联动需要你前端 JS 来更新下拉）
- 额外提供一个可选的 HTTP 接口：/character_tag_selector/characters?json_file=xxx.json
  便于前端按文件获取角色列表（你的 JS 如果走静态 JSON 也可以不用这个接口）
"""

import os
import json
import hashlib
from io import BytesIO
from typing import Dict, List, Tuple, Optional

import numpy as np
import requests
import torch
from PIL import Image

# 可选：如果你想让前端通过接口动态拉取角色列表
from aiohttp import web
try:
    from server import PromptServer
except Exception:
    PromptServer = None


class CharacterTagSelector:
    """角色标签选择器节点"""

    OUTPUT_TYPES_MAP = {
        "Danbooru标签": "danbooru_tag",
        "英文自然语言": "natural_en",
        "中文自然语言": "natural_cn",
        "中文名 + 作品名": "cn_name_source",
    }

    # full_path -> (mtime, data)
    _data_cache: Dict[str, Tuple[float, List[Dict]]] = {}

    # url_md5 -> image_tensor
    _image_cache: Dict[str, torch.Tensor] = {}

    @classmethod
    def get_data_dir(cls) -> str:
        """web/data 目录绝对路径"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(current_dir, "web", "data")

    @classmethod
    def get_available_json_files(cls) -> List[str]:
        """扫描 web/data 下的 .json 文件名列表"""
        data_dir = cls.get_data_dir()

        if not os.path.exists(data_dir):
            print(f"⚠️ data目录不存在: {data_dir}")
            return ["未找到JSON文件"]

        json_files: List[str] = []
        try:
            for filename in os.listdir(data_dir):
                if filename.lower().endswith(".json"):
                    json_files.append(filename)
        except Exception as e:
            print(f"❌ 扫描data目录失败: {e}")
            return ["未找到JSON文件"]

        if not json_files:
            return ["未找到JSON文件"]

        json_files.sort()
        return json_files

    @classmethod
    def _resolve_json_path(cls, json_file: str) -> str:
        """
        解析 json_file（文件名或路径）到 data_dir 内的绝对路径。
        为安全起见，只允许访问 data_dir 内的文件。
        """
        if not json_file or str(json_file).strip() == "" or json_file == "未找到JSON文件":
            return ""

        s = str(json_file).strip()

        # 如果是不带分隔符的文件名，则拼接到 data_dir
        if os.path.sep not in s and "/" not in s and "\\" not in s:
            full_path = os.path.abspath(os.path.join(cls.get_data_dir(), s))
        else:
            full_path = os.path.abspath(s)

        data_dir = os.path.abspath(cls.get_data_dir())
        if not (full_path == data_dir or full_path.startswith(data_dir + os.sep)):
            print(f"⚠️ 拒绝访问 data 目录外路径: {full_path}")
            return ""

        return full_path

    @classmethod
    def load_json_file(cls, json_file: str) -> List[Dict]:
        """加载 JSON（带 mtime 缓存自动失效）"""
        full_path = cls._resolve_json_path(json_file)
        if not full_path:
            return []

        if not os.path.exists(full_path):
            print(f"⚠️ 文件不存在: {full_path}")
            return []

        try:
            mtime = os.path.getmtime(full_path)
            cached = cls._data_cache.get(full_path)
            if cached and cached[0] == mtime:
                return cached[1]

            with open(full_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, list):
                print(f"❌ 文件格式错误: 期望数组(list)，得到 {type(data)}")
                return []

            cls._data_cache[full_path] = (mtime, data)
            print(f"✅ 已加载: {os.path.basename(full_path)} ({len(data)} 个角色)")
            return data

        except Exception as e:
            print(f"❌ 加载文件失败: {e}")
            return []

    @classmethod
    def _format_display_name(cls, char: Dict) -> str:
        name_cn = (char.get("name_cn") or "").strip()
        name_en = (char.get("name_en") or "").strip()

        if name_cn and name_en:
            return f"{name_cn} ({name_en})"
        if name_cn:
            return name_cn
        if name_en:
            return name_en
        return "未命名角色"

    @classmethod
    def get_character_list_for_file(cls, json_file: str) -> List[str]:
        """根据指定 json_file 返回角色显示名列表"""
        data = cls.load_json_file(json_file)
        if not data:
            return ["未加载角色数据"]
        return [cls._format_display_name(c) for c in data]

    @classmethod
    def find_character_by_name(cls, character_name: str, json_file: str) -> Optional[Dict]:
        """根据显示名查找角色数据"""
        data = cls.load_json_file(json_file)
        if not data:
            return None

        target = (character_name or "").strip()
        for char in data:
            if cls._format_display_name(char) == target:
                return char
        return None

    @classmethod
    def create_placeholder_image(cls, width: int = 512, height: int = 512) -> torch.Tensor:
        """创建占位图：[1,H,W,3] float32, 0..1"""
        img_array = np.full((height, width, 3), 128, dtype=np.uint8)
        img_tensor = torch.from_numpy(img_array).to(torch.float32) / 255.0
        return img_tensor.unsqueeze(0)

    @classmethod
    def _pil_to_comfy_tensor(cls, img: Image.Image, max_side: int = 512) -> torch.Tensor:
        """PIL -> [1,H,W,3] float32 0..1，顺手限制最大边避免太大"""
        if img.mode != "RGB":
            img = img.convert("RGB")

        if max_side and max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)

        arr = np.asarray(img, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[2] != 3:
            # 兜底：强制转成 3 通道
            arr = np.stack([arr] * 3, axis=-1) if arr.ndim == 2 else arr[:, :, :3]

        tensor = torch.from_numpy(arr).to(torch.float32) / 255.0
        return tensor.unsqueeze(0)

    @classmethod
    def download_and_cache_image(cls, icon_url: str) -> torch.Tensor:
        """下载图片并缓存为 ComfyUI IMAGE tensor"""
        if not icon_url or str(icon_url).strip() == "":
            return cls.create_placeholder_image()

        url = str(icon_url).strip()
        cache_key = hashlib.md5(url.encode("utf-8")).hexdigest()

        if cache_key in cls._image_cache:
            return cls._image_cache[cache_key]

        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content))
            tensor = cls._pil_to_comfy_tensor(img, max_side=512)
            cls._image_cache[cache_key] = tensor
            return tensor

        except Exception as e:
            print(f"❌ 下载/处理图片失败: {e}")
            return cls.create_placeholder_image()

    @classmethod
    def INPUT_TYPES(cls):
        available_files = cls.get_available_json_files()
        default_file = available_files[0] if available_files else "未找到JSON文件"

        # 注意：这里只能初始化一次，动态联动需要前端 JS 去刷新下拉
        character_list = cls.get_character_list_for_file(default_file)

        return {
            "required": {
                "json_file": (available_files, {"default": default_file}),
                "character": (character_list, {"default": character_list[0] if character_list else "未加载角色数据"}),
                "output_type": (list(cls.OUTPUT_TYPES_MAP.keys()), {"default": "Danbooru标签"}),
            }
        }

    RETURN_TYPES = ("STRING", "IMAGE")
    RETURN_NAMES = ("text", "preview_image")
    FUNCTION = "generate_tag"
    CATEGORY = "🎮 Character Tags"
    OUTPUT_NODE = True

    def generate_tag(self, json_file: str, character: str, output_type: str) -> Tuple[str, torch.Tensor]:
        placeholder = self.create_placeholder_image()

        char_data = self.find_character_by_name(character, json_file)
        if not char_data:
            return (f"❌ 未找到角色: {character}", placeholder)

        name_cn = (char_data.get("name_cn") or "").strip()
        name_en = (char_data.get("name_en") or "").strip()
        source_cn = (char_data.get("source_cn") or "").strip()
        source_en = (char_data.get("source_en") or "").strip()
        tag = (char_data.get("tag") or "").strip()
        icon_url = (char_data.get("icon_url") or "").strip()

        preview_image = self.download_and_cache_image(icon_url)
        output_format = self.OUTPUT_TYPES_MAP.get(output_type, "danbooru_tag")

        if output_format == "danbooru_tag":
            if tag:
                return (tag, preview_image)
            # 没有 tag 就用英文名拼一个兜底
            base = (name_en or name_cn or "unknown").lower()
            tag_name = (
                base.replace(" ", "_")
                .replace("-", "_")
                .replace(":", "")
                .replace("•", "_")
            )
            tag_name = "_".join(filter(None, tag_name.split("_")))
            source_tag = (char_data.get("source") or source_en or source_cn or "unknown").lower().replace(" ", "_")
            return (f"{tag_name}_({source_tag})", preview_image)

        if output_format == "natural_en":
            src = source_en or source_cn or "Unknown"
            nm = name_en or name_cn or "Unknown"
            return (f"{nm} from {src}", preview_image)

        if output_format == "natural_cn":
            src = source_cn or source_en or "未知作品"
            nm = name_cn or name_en or "未知角色"
            return (f"{nm}来自{src}", preview_image)

        if output_format == "cn_name_source":
            src = source_cn or source_en or ""
            nm = name_cn or name_en or ""
            return (f"{nm}, {src}".strip().strip(","), preview_image)

        return ("❌ 未知的输出类型", placeholder)

    @classmethod
    def IS_CHANGED(cls, json_file, character, output_type):
        """
        让 ComfyUI 在文件变化/选择变化时刷新。
        """
        full_path = cls._resolve_json_path(json_file)
        if full_path and os.path.exists(full_path):
            mtime = os.path.getmtime(full_path)
            return f"{full_path}:{mtime}:{character}:{output_type}"
        return f"{json_file}:{character}:{output_type}"


# 可选：给前端动态拿角色列表用（你的 JS 若直接 fetch 静态 JSON，可以不用）
if PromptServer is not None:
    @PromptServer.instance.routes.get("/character_tag_selector/characters")
    async def character_tag_selector_characters(request):
        json_file = request.query.get("json_file", "")
        characters = CharacterTagSelector.get_character_list_for_file(json_file)
        return web.json_response({"characters": characters})


NODE_CLASS_MAPPINGS = {
    "CharacterTagSelector": CharacterTagSelector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CharacterTagSelector": "🎮 Character Tag Selector",
}