import { app } from "../../scripts/app.js";

// 存储角色数据
let characterDataMap = {};
let isDataLoaded = false;

// 加载角色数据
async function loadCharacterData() {
    if (isDataLoaded) return;
    
    try {
        // 从data目录加载JSON文件
        const response = await fetch('extensions/comfyui-character-tag-selector/data/genshin_impact_characters-en-cn.json');
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        // 构建角色名称到数据的映射
        data.forEach(char => {
            const name_cn = char.name_cn || '';
            const name_en = char.name_en || '';
            
            // 生成显示名称（与Python端保持一致）
            let displayName;
            if (name_cn && name_en) {
                displayName = `${name_cn} (${name_en})`;
            } else if (name_cn) {
                displayName = name_cn;
            } else {
                displayName = name_en || "未命名角色";
            }
            
            characterDataMap[displayName] = {
                icon_url: char.icon_url || '',
                name_cn: name_cn,
                name_en: name_en
            };
        });
        
        isDataLoaded = true;
        console.log("✅ 角色数据加载成功:", Object.keys(characterDataMap).length, "个角色");
    } catch (error) {
        console.error("❌ 加载角色数据失败:", error);
    }
}

// 注册节点扩展
app.registerExtension({
    name: "kotone.CharacterTagSelector.Preview",
    
    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        // 只处理我们的节点
        if (nodeData.name !== "CharacterTagSelector") {
            return;
        }
        
        // 加载角色数据
        await loadCharacterData();
        
        // 扩展节点创建逻辑
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function() {
            const result = onNodeCreated?.apply(this, arguments);
            
            // 创建预览容器
            const previewContainer = document.createElement("div");
            previewContainer.className = "character-preview-container";
            previewContainer.style.cssText = `
                width: 100%;
                margin: 10px 0;
                text-align: center;
            `;
            
            const img = document.createElement("img");
            img.className = "character-preview-image";
            img.style.cssText = `
                max-width: 100%;
                max-height: 200px;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.3);
                display: none;
            `;
            
            const loadingText = document.createElement("div");
            loadingText.className = "character-preview-text";
            loadingText.textContent = "选择角色查看预览";
            loadingText.style.cssText = `
                color: #999;
                padding: 20px;
                font-size: 14px;
            `;
            
            previewContainer.appendChild(img);
            previewContainer.appendChild(loadingText);
            
            // 将预览容器添加到节点
            this.addDOMWidget("character_preview", "preview", previewContainer);
            
            // 更新图片的函数
            const updateImage = (iconUrl) => {
                if (!iconUrl || iconUrl.trim() === '') {
                    img.style.display = 'none';
                    loadingText.style.display = 'block';
                    loadingText.textContent = '该角色无预览图';
                    return;
                }
                
                loadingText.textContent = '加载中...';
                loadingText.style.display = 'block';
                img.style.display = 'none';
                
                // 加载图片
                const tempImg = new Image();
                tempImg.onload = function() {
                    img.src = iconUrl;
                    img.style.display = 'block';
                    loadingText.style.display = 'none';
                };
                tempImg.onerror = function() {
                    loadingText.textContent = '图片加载失败';
                    img.style.display = 'none';
                    loadingText.style.display = 'block';
                };
                tempImg.src = iconUrl;
            };
            
            // 查找character输入widget
            const characterWidget = this.widgets?.find(w => w.name === "character");
            
            if (characterWidget) {
                // 保存原始回调
                const originalCallback = characterWidget.callback;
                
                // 重写回调函数
                characterWidget.callback = function(value) {
                    // 调用原始回调
                    if (originalCallback) {
                        originalCallback.apply(this, arguments);
                    }
                    
                    // 更新预览图
                    const charData = characterDataMap[value];
                    if (charData && charData.icon_url) {
                        updateImage(charData.icon_url);
                    } else {
                        updateImage('');
                    }
                };
                
                // 初始化显示第一个角色的图片
                setTimeout(() => {
                    if (characterWidget.value) {
                        const charData = characterDataMap[characterWidget.value];
                        if (charData && charData.icon_url) {
                            updateImage(charData.icon_url);
                        }
                    }
                }, 100);
            }
            
            return result;
        };
    }
});
