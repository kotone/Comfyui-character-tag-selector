"""
ComfyUI 自定义节点：角色标签选择器
支持用户上传 JSON 文件，选择角色并输出不同格式的标签
"""

import os
import json
from typing import Dict, List, Tuple


class CharacterTagSelector:
    """角色标签选择器节点"""
    
    # 输出类型映射
    OUTPUT_TYPES_MAP = {
        "Danbooru标签 (推荐)": "danbooru_tag",
        "简化标签": "simple_tag", 
        "英文自然语言": "natural_en",
        "中英混合": "natural_mixed",
        "仅中文名": "name_cn_only",
        "仅英文名": "name_en_only",
    }
    
    # 类级别的数据缓存（文件路径 -> 数据）
    _data_cache = {}
    
    def __init__(self):
        pass
    
    @classmethod
    def load_json_file(cls, json_file: str) -> List[Dict]:
        """加载JSON文件并缓存"""
        if not json_file or json_file.strip() == "":
            return []
        
        # 检查缓存
        if json_file in cls._data_cache:
            return cls._data_cache[json_file]
        
        # 加载文件
        if not os.path.exists(json_file):
            print(f"⚠️ 文件不存在: {json_file}")
            return []
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, list):
                print(f"❌ 文件格式错误: 期望数组，得到 {type(data)}")
                return []
            
            # 缓存数据
            cls._data_cache[json_file] = data
            print(f"✅ 已加载: {os.path.basename(json_file)} ({len(data)} 个角色)")
            return data
        except Exception as e:
            print(f"❌ 加载文件失败: {e}")
            return []
    
    @classmethod
    def INPUT_TYPES(cls):
        """定义节点的输入参数"""
        return {
            "required": {
                "json_file": ("STRING", {
                    "default": "",
                    "multiline": False,
                }),
                "character_index": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 9999,
                    "step": 1,
                }),
                "output_type": (list(cls.OUTPUT_TYPES_MAP.keys()), {
                    "default": "Danbooru标签 (推荐)"
                }),
            },
        }
    
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "generate_tag"
    CATEGORY = "🎮 Character Tags"
    
    OUTPUT_NODE = True  # 标记为输出节点
    
    def generate_tag(self, json_file: str, character_index: int, output_type: str) -> Tuple[str]:
        """
        生成角色标签
        
        Args:
            json_file: JSON文件路径
            character_index: 角色索引
            output_type: 输出类型
        
        Returns:
            (tag_string,) 元组
        """
        # 加载数据
        characters_data = self.load_json_file(json_file)
        
        if not characters_data:
            return ("❌ 无法加载角色数据文件",)
        
        # 检查索引是否有效
        if character_index < 0 or character_index >= len(characters_data):
            return (f"❌ 索引超出范围: {character_index} (总数: {len(characters_data)})",)
        
        # 获取角色数据
        char_data = characters_data[character_index]
        
        name_cn = char_data.get('name_cn', '')
        name_en = char_data.get('name_en', '')
        source_cn = char_data.get('source_cn', '')
        tag = char_data.get('tag', '')
        
        output_format = self.OUTPUT_TYPES_MAP.get(output_type, "danbooru_tag")
        
        # 1. Danbooru标签格式 (推荐) - 完整tag
        if output_format == "danbooru_tag":
            if tag:
                return (tag,)
            # 如果没有tag，生成一个
            tag_name = name_en.lower().replace(' ', '_').replace('-', '_').replace(':', '').replace('•', '_')
            tag_name = '_'.join(filter(None, tag_name.split('_')))
            source_tag = char_data.get('source', 'unknown')
            return (f"{tag_name}_({source_tag})",)
        
        # 2. 简化标签 - 只有角色名，适合知名角色
        elif output_format == "simple_tag":
            tag_name = name_en.lower().replace(' ', '_').replace('-', '_').replace(':', '').replace('•', '_')
            tag_name = '_'.join(filter(None, tag_name.split('_')))
            return (tag_name,)
        
        # 3. 英文自然语言 - "Character Name from Game Name"
        elif output_format == "natural_en":
            return (f"{name_en} from {source_cn}",)
        
        # 4. 中英混合 - "中文名 (English Name), Game Name"
        elif output_format == "natural_mixed":
            return (f"{name_cn} ({name_en}), {source_cn}",)
        
        # 5. 仅中文名
        elif output_format == "name_cn_only":
            return (name_cn,)
        
        # 6. 仅英文名
        elif output_format == "name_en_only":
            return (name_en,)
        
        return ("❌ 未知的输出类型",)
    
    @classmethod
    def IS_CHANGED(cls, json_file, character_index, output_type):
        """检测参数变化，确保节点更新"""
        # 包含文件的修改时间，以便文件更新时自动刷新
        if os.path.exists(json_file):
            mtime = os.path.getmtime(json_file)
            return f"{json_file}_{mtime}_{character_index}_{output_type}"
        return f"{json_file}_{character_index}_{output_type}"


# ComfyUI 节点映射
NODE_CLASS_MAPPINGS = {
    "CharacterTagSelector": CharacterTagSelector,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CharacterTagSelector": "🎮 Character Tag Selector",
}
