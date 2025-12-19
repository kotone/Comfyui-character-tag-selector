"""
ComfyUI 自定义节点：CharacterTagSelector
- 扫描 web/data 下的多个 JSON 文件作为数据源
- generate_tag 会按当前选中的 json_file 查找角色并输出标签 + 预览图
"""

from collections import OrderedDict
import threading
import tempfile
import shutil
import os
import json
import hashlib
from io import BytesIO
from typing import Dict, List, Tuple, Optional
import folder_paths

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
    # _image_cache: Dict[str, torch.Tensor] = {}

    # ===== 图片缓存策略 =====
    # 内存：LRU tensor 缓存（强限制，避免 OOM）
    _tensor_lru: "OrderedDict[str, torch.Tensor]" = OrderedDict()
    _tensor_lru_bytes: int = 0
    _MAX_MEM_CACHE_ITEMS: int = 64           # 最多缓存 64 张 tensor
    _MAX_MEM_CACHE_BYTES: int = 256 * 1024 * 1024  # 或最多 256MB（按需调小/调大）

    # 硬盘：持久化缓存（webp + sha256 校验）
    _MAX_DOWNLOAD_BYTES: int = 10 * 1024 * 1024  # 单张最多下载 10MB，避免超大文件
    _USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    _request_headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }

    # 并发下载保护（避免同一 URL 多次下载）
    _url_locks: Dict[str, threading.Lock] = {}
    _url_locks_guard = threading.Lock()

     # signature -> all_character_choices
    _all_character_choices_cache: Tuple[str, List[str]] = ("", [])

    @classmethod
    def get_all_character_choices(cls) -> List[str]:
        """
        返回 web/data 下所有 JSON 的角色 displayName 并集（去重）。
        用文件 mtime 做简单缓存，避免每次 INPUT_TYPES 都全量解析。
        """
        files = cls.get_available_json_files()
        # 构造签名：文件名 + mtime，任何文件更新都会导致签名变化
        sig_parts: List[str] = []
        for f in files:
            full = cls._resolve_json_path(f)
            if full and os.path.exists(full):
                sig_parts.append(f"{os.path.basename(full)}:{os.path.getmtime(full)}")
        signature = "|".join(sig_parts)

        if cls._all_character_choices_cache[0] == signature:
            return cls._all_character_choices_cache[1]

        names = set()
        for f in files:
            if not f or f == "未找到JSON文件":
                continue
            lst = cls.get_character_list_for_file(f)
            for n in lst:
                if n and n != "未加载角色数据":
                    names.add(n)

        all_choices = sorted(names)
        if not all_choices:
            all_choices = ["未加载角色数据"]

        cls._all_character_choices_cache = (signature, all_choices)
        return all_choices

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
    def _get_disk_cache_dir(cls) -> str:
        """
        图片硬盘缓存目录
        """
        # current_dir = os.path.dirname(os.path.abspath(__file__))
        d = os.path.join(folder_paths.get_temp_directory(), "character_tag_selector")
        os.makedirs(d, exist_ok=True)
        return d

    @classmethod
    def _url_to_cache_key(cls, url: str) -> str:
        return hashlib.md5(url.encode("utf-8")).hexdigest()

    @classmethod
    def _cache_paths_for_url(cls, url: str) -> Tuple[str, str]:
        """
        返回 (webp_path, sha256_path)
        """
        key = cls._url_to_cache_key(url)
        base = os.path.join(cls._get_disk_cache_dir(), key)
        return base + ".webp", base + ".sha256"

    @classmethod
    def _sha256_bytes(cls, b: bytes) -> str:
        h = hashlib.sha256()
        h.update(b)
        return h.hexdigest()

    @classmethod
    def _sha256_file(cls, path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @classmethod
    def _get_url_lock(cls, cache_key: str) -> threading.Lock:
        with cls._url_locks_guard:
            lk = cls._url_locks.get(cache_key)
            if lk is None:
                lk = threading.Lock()
                cls._url_locks[cache_key] = lk
            return lk

    @classmethod
    def _estimate_tensor_bytes(cls, t: torch.Tensor) -> int:
        # float32: 4 bytes/elem；但别假设 dtype，直接用 element_size 更稳
        return int(t.numel() * t.element_size())

    @classmethod
    def _lru_put_tensor(cls, key: str, tensor: torch.Tensor) -> None:
        """
        写入内存 LRU，并执行驱逐，严格限制 items + bytes。
        """
        if key in cls._tensor_lru:
            old = cls._tensor_lru.pop(key)
            cls._tensor_lru_bytes -= cls._estimate_tensor_bytes(old)

        cls._tensor_lru[key] = tensor
        cls._tensor_lru.move_to_end(key, last=True)
        cls._tensor_lru_bytes += cls._estimate_tensor_bytes(tensor)

        # 驱逐：先按 items，再按 bytes（两者都满足）
        while len(cls._tensor_lru) > cls._MAX_MEM_CACHE_ITEMS or cls._tensor_lru_bytes > cls._MAX_MEM_CACHE_BYTES:
            k, v = cls._tensor_lru.popitem(last=False)
            cls._tensor_lru_bytes -= cls._estimate_tensor_bytes(v)

    @classmethod
    def _lru_get_tensor(cls, key: str) -> Optional[torch.Tensor]:
        t = cls._tensor_lru.get(key)
        if t is None:
            return None
        cls._tensor_lru.move_to_end(key, last=True)
        return t

    @classmethod
    def _load_tensor_from_disk_webp(cls, webp_path: str) -> Optional[torch.Tensor]:
        if not os.path.exists(webp_path):
            return None
        try:
            img = Image.open(webp_path)
            # 确保真正解码，避免只读到 header
            img.load()
            return cls._pil_to_comfy_tensor(img, max_side=512)
        except Exception as e:
            print(f"❌ 硬盘缓存图片损坏/无法解码，删除并回退: {e}")
            try:
                os.remove(webp_path)
            except Exception:
                pass
            return None

    @classmethod
    def _write_file_atomic(cls, final_path: str, data: bytes) -> None:
        """
        原子写入：先写 tmp，再 replace。
        Windows/Linux 都相对安全。
        """
        d = os.path.dirname(final_path)
        os.makedirs(d, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=d)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp_path, final_path)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
    
    @classmethod
    def download_and_cache_image(cls, icon_url: str) -> torch.Tensor:
        """
        1) 内存 LRU（限制 items/bytes）
        2) 硬盘 webp 持久化 + sha256 校验
        3) 下载限制最大字节 + stream + UA headers
        """
        if not icon_url or str(icon_url).strip() == "":
            return cls.create_placeholder_image()

        url = str(icon_url).strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            print(f"⚠️ 非 http/https 的 icon_url，拒绝: {url}")
            return cls.create_placeholder_image()

        cache_key = cls._url_to_cache_key(url)

        # 先查内存 LRU
        t = cls._lru_get_tensor(cache_key)
        if t is not None:
            return t

        webp_path, sha_path = cls._cache_paths_for_url(url)

        # 并发保护：同一 URL 同时只允许一个线程/协程下载/落盘
        lock = cls._get_url_lock(cache_key)
        with lock:
            # 进锁后再查一次内存（可能其它线程已填充）
            t = cls._lru_get_tensor(cache_key)
            if t is not None:
                return t

            # 查硬盘缓存 + 校验 sha256
            if os.path.exists(webp_path) and os.path.exists(sha_path):
                try:
                    with open(sha_path, "r", encoding="utf-8") as f:
                        expected = f.read().strip()
                    actual = cls._sha256_file(webp_path)
                    if expected and expected == actual:
                        t = cls._load_tensor_from_disk_webp(webp_path)
                        if t is not None:
                            cls._lru_put_tensor(cache_key, t)
                            return t
                    else:
                        print("⚠️ 硬盘缓存 sha256 不匹配，删除后重新下载")
                        try:
                            os.remove(webp_path)
                        except Exception:
                            pass
                        try:
                            os.remove(sha_path)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"⚠️ 校验硬盘缓存失败，删除后重新下载: {e}")
                    try:
                        os.remove(webp_path)
                    except Exception:
                        pass
                    try:
                        os.remove(sha_path)
                    except Exception:
                        pass

            # 下载（stream + 限制大小）
            try:
                resp = requests.get(
                    url,
                    headers=cls._request_headers,
                    stream=True,
                    timeout=(5, 15),  # 连接超时/读取超时
                    allow_redirects=True,
                )
                resp.raise_for_status()

                # 如果 server 给了 Content-Length，先做一次硬限制
                cl = resp.headers.get("Content-Length", "")
                if cl.isdigit():
                    if int(cl) > cls._MAX_DOWNLOAD_BYTES:
                        raise ValueError(f"Content-Length={cl} 超过上限 {cls._MAX_DOWNLOAD_BYTES} bytes")

                content_type = (resp.headers.get("Content-Type") or "").lower()
                if content_type and ("image" not in content_type):
                    # 有些站不标准，这里只做轻提示，不强杀也行；你想更严可以直接 raise
                    print(f"⚠️ Content-Type 看起来不是图片: {content_type}")

                buf = BytesIO()
                downloaded = 0
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > cls._MAX_DOWNLOAD_BYTES:
                        raise ValueError(f"下载大小超过上限 {cls._MAX_DOWNLOAD_BYTES} bytes")
                    buf.write(chunk)

                raw = buf.getvalue()
                if not raw:
                    raise ValueError("下载内容为空")

                # 解码图片（防止坏数据）
                img = Image.open(BytesIO(raw))
                img.load()  # 强制解码

                # 转成 webp 持久化（如果环境 Pillow 没编 WebP，这里会报错）
                # 先限制尺寸再存盘，避免超大图占用空间/解码慢
                if img.mode != "RGB":
                    img = img.convert("RGB")
                if max(img.size) > 1024:
                    img.thumbnail((1024, 1024), Image.LANCZOS)

                out_buf = BytesIO()
                try:
                    img.save(out_buf, format="WEBP", quality=90, method=6)
                    webp_bytes = out_buf.getvalue()
                    sha256 = cls._sha256_bytes(webp_bytes)

                    cls._write_file_atomic(webp_path, webp_bytes)
                    cls._write_file_atomic(sha_path, (sha256 + "\n").encode("utf-8"))
                except Exception as e:
                    # WebP 不可用时：回退为“只走内存 tensor”，不持久化（或你也可改成 PNG 持久化）
                    print(f"⚠️ 保存 WEBP 失败（可能 Pillow 未启用 WebP），将只走内存缓存: {e}")

                # 最终转 tensor（按你原逻辑限制 512）
                tensor = cls._pil_to_comfy_tensor(img, max_side=512)
                cls._lru_put_tensor(cache_key, tensor)
                return tensor

            except Exception as e:
                print(f"❌ 下载/处理图片失败: {e}")
                return cls.create_placeholder_image()
    
    # @classmethod
    # def download_and_cache_image(cls, icon_url: str) -> torch.Tensor:
    #     """下载图片并缓存为 ComfyUI IMAGE tensor"""
    #     if not icon_url or str(icon_url).strip() == "":
    #         return cls.create_placeholder_image()

    #     url = str(icon_url).strip()
    #     cache_key = hashlib.md5(url.encode("utf-8")).hexdigest()

    #     if cache_key in cls._image_cache:
    #         return cls._image_cache[cache_key]

    #     try:
    #         resp = requests.get(url, timeout=10)
    #         resp.raise_for_status()
    #         img = Image.open(BytesIO(resp.content))
    #         tensor = cls._pil_to_comfy_tensor(img, max_side=512)
    #         cls._image_cache[cache_key] = tensor
    #         return tensor

    #     except Exception as e:
    #         print(f"❌ 下载/处理图片失败: {e}")
    #         return cls.create_placeholder_image()

    @classmethod
    def INPUT_TYPES(cls):
        available_files = cls.get_available_json_files()
        default_file = available_files[0] if available_files else "未找到JSON文件"

        # 注意：这里只能初始化一次，动态联动需要前端 JS 去刷新下拉
        # character_list = cls.get_character_list_for_file(default_file)

        # return {
        #     "required": {
        #         "json_file": (available_files, {"default": default_file}),
        #         "character": (character_list, {"default": character_list[0] if character_list else "未加载角色数据"}),
        #         "output_type": (list(cls.OUTPUT_TYPES_MAP.keys()), {"default": "Danbooru标签"}),
        #     }
        # }

        
        # 后端给“全集”用于校验通过；前端再按 json_file 动态过滤显示
        all_character_choices = cls.get_all_character_choices()

        # 默认值仍尽量用默认文件的第一个角色（更符合直觉）
        default_file_characters = cls.get_character_list_for_file(default_file)
        character_default = (
            default_file_characters[0]
            if default_file_characters and default_file_characters[0] != "未加载角色数据"
            else all_character_choices[0]
        )

        return {
            "required": {
                "json_file": (available_files, {"default": default_file}),
                "character": (all_character_choices, {"default": character_default}),
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