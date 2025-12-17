# ComfyUI 角色标签选择器节点

## 功能简介

这是一个 ComfyUI 自定义节点，用于从游戏角色数据库中选择角色并生成各种格式的标签输出。

支持任意游戏角色数据，只需提供符合格式的 JSON 文件即可。

## 安装方法

### 手动安装

1. 将整个 `comfyui-character-tag-selector` 文件夹复制到 ComfyUI 的自定义节点目录：
   ```
   ComfyUI/custom_nodes/
   ```

2. 确保目录结构如下：
   ```
   ComfyUI/
   └── custom_nodes/
       └── comfyui-character-tag-selector/
           ├── __init__.py
           ├── character_tag_selector.py
           └── README.md
   ```

3. 重启 ComfyUI

## 使用方法

### 1. 准备数据文件

将符合格式的 JSON 文件（参见下方"数据格式"章节）放入节点目录的 `data` 文件夹中：

```
ComfyUI/
└── custom_nodes/
    └── comfyui-character-tag-selector/
        └── data/
            └── your_game_characters.json
```

节点会自动扫描 `data` 目录下的所有 JSON 文件。

### 2. 使用节点

在 ComfyUI 中添加"Character Tag Selector"节点：
1. 从 `json_file` 下拉列表选择数据文件
2. 从 `character` 下拉列表选择角色（支持输入中英文搜索）
3. 选择输出格式
4. 自动显示角色预览图

### 3. 连接节点

将输出连接到其他节点（如文本框、提示词节点等）

## 输出示例

假设 JSON 中索引 0 的角色数据为：
```json
{
  "name_cn": "雷电将军",
  "name_en": "Raiden Shogun",
  "tag": "raiden_shogun_(genshin_impact)",
  "source": "genshin_impact",
  "source_cn": "原神"
}
```

不同输出格式的结果：

| 输出类型 | 结果 |
|---------|------|
| Danbooru标签 | `raiden_shogun_(genshin_impact)` |
| 英文自然语言 | `Raiden Shogun from 原神` |
| 中文自然语言 | `雷电将军来自原神` |
| 中文名 + 作品名 | `雷电将军, 原神` |

**额外输出**：节点会自动获取并显示角色的预览图（从 `icon_url` 字段加载），方便选择时预览。

## 节点参数说明

| 参数 | 类型 | 范围 | 说明 |
|------|------|------|------|
| json_file | 下拉选择 | - | 从 data 目录中选择 JSON 文件 |
| character | 下拉选择 | - | 选择角色（支持中英文搜索） |
| output_type | 下拉选择 | 4种格式 | 选择输出标签的格式 |

## 节点输出说明

节点提供两个输出：

| 输出 | 类型 | 说明 |
|------|------|------|
| text | STRING | 根据选择的格式生成的角色标签文本 |
| preview_image | IMAGE | 角色预览图（从icon_url自动下载） |

## 数据格式

JSON 数据文件必须是一个数组，每个元素包含以下字段：

```json
[
  {
    "name_cn": "角色中文名",
    "name_en": "Character English Name",
    "tag": "character_tag_(game_name)",
    "source": "game_identifier",
    "source_cn": "游戏中文名",
    "icon_url": "https://example.com/icon.png",
    "header_img_url": "https://example.com/header.png"
  },
  ...
]
```

**必需字段**:
- `name_cn`: 角色中文名
- `name_en`: 角色英文名
- `source_cn`: 游戏/作品中文名

**可选字段**:
- `tag`: 预设的 Danbooru 格式标签
- `source`: 游戏/作品英文标识符
- `icon_url`: 角色头像URL
- `header_img_url`: 角色大图URL


## 开发者信息

- **版本**: 1.0.0
- **作者**: kotone
- **许可**: MIT
