"""
ComfyUI 自定义节点：角色标签选择器
支持用户上传 JSON 文件，选择角色并输出不同格式的标签
"""

import os
import json
from typing import Dict, List, Tuple
import requests
from io import BytesIO
from PIL import Image
import numpy as np
import torch
import hashlib


class CharacterTagSelector:
    """角色标签选择器节点"""
    
    # 输出类型映射
    OUTPUT_TYPES_MAP = {
        "Danbooru标签": "danbooru_tag",
        "英文自然语言": "natural_en",
        "中文自然语言": "natural_cn",
        "中文名 + 作品名": "cn_name_source",
    }
    
    # 类级别的数据缓存（文件路径 -> 数据）
    _data_cache = {}
    
    # 图片缓存（URL的MD5 -> torch.Tensor）
    _image_cache = {}
    
    def __init__(self):
        pass
    
    @classmethod
    def get_data_dir(cls) -> str:
        """获取data目录的绝对路径"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(current_dir, "web", "data")
        return data_dir
    
    @classmethod
    def get_available_json_files(cls) -> list:
        """扫描data目录，返回所有JSON文件的文件名列表"""
        data_dir = cls.get_data_dir()
        
        # 如果data目录不存在，返回空列表
        if not os.path.exists(data_dir):
            print(f"⚠️ data目录不存在: {data_dir}")
            return ["未找到JSON文件"]
        
        # 扫描所有.json文件
        json_files = []
        try:
            for filename in os.listdir(data_dir):
                if filename.endswith('.json'):
                    json_files.append(filename)
        except Exception as e:
            print(f"❌ 扫描data目录失败: {e}")
            return ["未找到JSON文件"]
        
        # 如果没有找到JSON文件
        if not json_files:
            return ["未找到JSON文件"]
        
        # 排序后返回
        json_files.sort()
        return json_files
    
    @classmethod
    def load_json_file(cls, json_file: str) -> List[Dict]:
        """加载JSON文件并缓存"""
        if not json_file or json_file.strip() == "" or json_file == "未找到JSON文件":
            return []
        
        # 处理文件路径：如果是文件名（不含路径分隔符），则从data目录加载
        if os.path.sep not in json_file and '/' not in json_file and '\\' not in json_file:
            # 这是一个文件名，拼接data目录路径
            full_path = os.path.join(cls.get_data_dir(), json_file)
        else:
            # 这是完整路径，直接使用
            full_path = json_file
        
        # 检查缓存
        if full_path in cls._data_cache:
            return cls._data_cache[full_path]
        
        # 加载文件
        if not os.path.exists(full_path):
            print(f"⚠️ 文件不存在: {full_path}")
            return []
        
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, list):
                print(f"❌ 文件格式错误: 期望数组，得到 {type(data)}")
                return []
            
            # 缓存数据
            cls._data_cache[full_path] = data
            print(f"✅ 已加载: {os.path.basename(full_path)} ({len(data)} 个角色)")
            return data
        except Exception as e:
            print(f"❌ 加载文件失败: {e}")
            return []
    
    @classmethod
    def get_character_list(cls) -> List[str]:
        """
        获取默认JSON文件的角色列表
        返回格式：["中文名 (英文名)", ...]
        """
        # 获取第一个可用的JSON文件
        available_files = cls.get_available_json_files()
        if not available_files or available_files[0] == "未找到JSON文件":
            return ["未加载角色数据"]
        
        # 加载数据
        characters_data = cls.load_json_file(available_files[0])
        if not characters_data:
            return ["未加载角色数据"]
        
        # 生成角色列表
        character_list = []
        for char in characters_data:
            name_cn = char.get('name_cn', '')
            name_en = char.get('name_en', '')
            
            # 格式化显示名称
            if name_cn and name_en:
                display_name = f"{name_cn} ({name_en})"
            elif name_cn:
                display_name = name_cn
            else:
                display_name = name_en if name_en else "未命名角色"
            
            character_list.append(display_name)
        
        print(f"✅ 已加载 {len(character_list)} 个角色")
        return character_list
    
    @classmethod
    def find_character_by_name(cls, character_name: str, json_file: str) -> Dict:
        """
        根据显示名称查找角色数据
        
        Args:
            character_name: 显示名称，格式为 "中文名 (英文名)" 或 "英文名" 或 "中文名"
            json_file: JSON文件名或路径
            
        Returns:
            角色数据字典，未找到返回None
        """
        characters_data = cls.load_json_file(json_file)
        if not characters_data:
            return None
        
        for char in characters_data:
            name_cn = char.get('name_cn', '')
            name_en = char.get('name_en', '')
            
            # 生成显示名称（与get_character_list保持一致）
            if name_cn and name_en:
                display_name = f"{name_cn} ({name_en})"
            elif name_cn:
                display_name = name_cn
            else:
                display_name = name_en if name_en else "未命名角色"
            
            if display_name == character_name:
                return char
        
        return None
    
    @classmethod
    def create_placeholder_image(cls, width: int = 512, height: int = 512) -> torch.Tensor:
        """创建占位图片（纯灰色图片）"""
        # 创建一个灰色图片 (RGB: 128, 128, 128)
        img_array = np.full((height, width, 3), 128, dtype=np.uint8)
        
        # 转换为torch tensor并标准化到[0, 1]
        img_tensor = torch.from_numpy(img_array).float() / 255.0
        
        # 添加batch维度 [1, height, width, channels]
        img_tensor = img_tensor.unsqueeze(0)
        
        return img_tensor
    
    @classmethod
    def download_and_cache_image(cls, icon_url: str) -> torch.Tensor:
        """
        从URL下载图片并转换为ComfyUI所需的tensor格式
        
        Args:
            icon_url: 图片URL
            
        Returns:
            torch.Tensor: 格式为 [1, height, width, 3]，范围 [0, 1]
        """
        # 如果URL为空，返回占位图
        if not icon_url or icon_url.strip() == "":
            print("⚠️ 图片URL为空，使用占位图")
            return cls.create_placeholder_image()
        
        # 生成缓存键
        cache_key = hashlib.md5(icon_url.encode()).hexdigest()
        
        # 检查缓存
        if cache_key in cls._image_cache:
            return cls._image_cache[cache_key]
        
        try:
            # 下载图片
            print(f"📥 正在下载图片: {icon_url[:50]}...")
            response = requests.get(icon_url, timeout=10)
            response.raise_for_status()
            
            # 使用PIL打开图片
            img = Image.open(BytesIO(response.content))
            
            # 转换为RGB（处理RGBA等格式）
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # 转换为numpy数组
            img_array = np.array(img)
            
            # 转换为torch tensor并标准化到[0, 1]
            img_tensor = torch.from_numpy(img_array).float() / 255.0
            
            # 添加batch维度 [1, height, width, channels]
            img_tensor = img_tensor.unsqueeze(0)
            
            # 缓存图片
            cls._image_cache[cache_key] = img_tensor
            print(f"✅ 图片下载成功: {img.size[0]}x{img.size[1]}")

            
            return img_tensor
            
        except requests.exceptions.RequestException as e:
            print(f"❌ 下载图片失败: {e}")
            return cls.create_placeholder_image()
        except Exception as e:
            print(f"❌ 处理图片失败: {e}")
            return cls.create_placeholder_image()
    
    @classmethod
    def INPUT_TYPES(cls):
        """定义节点的输入参数"""
        # 获取可用的JSON文件列表
        available_files = cls.get_available_json_files()
        # 获取角色列表
        character_list = cls.get_character_list()
        
        return {
            "required": {
                "json_file": (available_files, {
                    "default": available_files[0] if available_files else "未找到JSON文件"
                }),
                "character": (character_list, {
                    "default": character_list[0] if character_list else "未加载角色数据"
                }),
                "output_type": (list(cls.OUTPUT_TYPES_MAP.keys()), {
                    "default": "Danbooru标签"
                }),
            },
        }
    
    RETURN_TYPES = ("STRING", "IMAGE",)
    RETURN_NAMES = ("text", "preview_image",)
    FUNCTION = "generate_tag"
    CATEGORY = "🎮 Character Tags"
    
    OUTPUT_NODE = True  # 标记为输出节点
    
    def generate_tag(self, json_file: str, character: str, output_type: str) -> Tuple[str, torch.Tensor]:
        """
        生成角色标签和预览图
        
        Args:
            json_file: JSON文件路径
            character: 角色显示名称
            output_type: 输出类型
        
        Returns:
            (tag_string, preview_image) 元组
        """
        # 创建占位图
        placeholder = self.create_placeholder_image()
        
        # 根据显示名称查找角色数据
        char_data = self.find_character_by_name(character, json_file)
        
        if not char_data:
            return (f"❌ 未找到角色: {character}", placeholder)
        
        name_cn = char_data.get('name_cn', '')
        name_en = char_data.get('name_en', '')
        source_cn = char_data.get('source_cn', '')
        tag = char_data.get('tag', '')
        icon_url = char_data.get('icon_url', '')
        
        # 下载角色预览图
        preview_image = self.download_and_cache_image(icon_url)
        
        output_format = self.OUTPUT_TYPES_MAP.get(output_type, "danbooru_tag")
        
        # 1. Danbooru标签格式 - 完整tag
        if output_format == "danbooru_tag":
            if tag:
                return (tag, preview_image)
            # 如果没有tag，生成一个
            tag_name = name_en.lower().replace(' ', '_').replace('-', '_').replace(':', '').replace('•', '_')
            tag_name = '_'.join(filter(None, tag_name.split('_')))
            source_tag = char_data.get('source', 'unknown')
            return (f"{tag_name}_({source_tag})", preview_image)
        
        # 2. 英文自然语言 - "Character Name from Game Name"
        elif output_format == "natural_en":
            return (f"{name_en} from {source_cn}", preview_image)
        
        # 3. 中文自然语言 - "中文名来自作品名"
        elif output_format == "natural_cn":
            return (f"{name_cn}来自{source_cn}", preview_image)
        
        # 4. 中文名 + 作品名 - "中文名, 作品名"
        elif output_format == "cn_name_source":
            return (f"{name_cn}, {source_cn}", preview_image)
        
        return ("❌ 未知的输出类型", placeholder)
    
    @classmethod
    def IS_CHANGED(cls, json_file, character, output_type):
        """检测参数变化，确保节点更新"""
        # 处理文件路径
        if os.path.sep not in json_file and '/' not in json_file and '\\' not in json_file:
            full_path = os.path.join(cls.get_data_dir(), json_file)
        else:
            full_path = json_file
        
        # 包含文件的修改时间和角色名
        if os.path.exists(full_path):
            mtime = os.path.getmtime(full_path)
            return f"{full_path}_{mtime}_{character}_{output_type}"
        return f"{json_file}_{character}_{output_type}"


# ComfyUI 节点映射
NODE_CLASS_MAPPINGS = {
    "CharacterTagSelector": CharacterTagSelector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CharacterTagSelector": "🎮 Character Tag Selector",
}
