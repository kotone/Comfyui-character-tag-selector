import { app } from "../../../scripts/app.js";
import { ComfyWidgets } from "../../../scripts/widgets.js";

// 存储角色数据
let characterDataMap = {};
let isDataLoaded = false;

// 加载角色数据
async function loadCharacterData() {
    if (isDataLoaded) return;

    try {
        // 从data目录加载JSON文件
        const dataUrl = new URL('../data/genshin_impact_characters-en-cn.json', import.meta.url);
        const response = await fetch(dataUrl);

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
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);

            // 创建预览图片 widget
            const imageWidget = ComfyWidgets["STRING"](this, "preview_image_widget", ["STRING", { multiline: false }], app);
            imageWidget.widget.type = "preview_image";
            imageWidget.widget.name = "character_preview";

            // 创建img元素
            const img = document.createElement("img");
            img.crossOrigin = "anonymous"; // Ensure CORS compliance
            img.onload = () => {
                // When this specific image finishes processing (even from cache), force a redraw
                app.graph.setDirtyCanvas(true, true);
            };
            Object.assign(img.style, {
                width: "100%",
                maxHeight: "200px",
                objectFit: "contain",
                borderRadius: "8px",
                marginTop: "10px",
                display: "none"
            });

            //创建提示文本
            const textDiv = document.createElement("div");
            textDiv.textContent = "选择角色查看预览";
            Object.assign(textDiv.style, {
                color: "#999",
                padding: "20px",
                textAlign: "center",
                fontSize: "14px"
            });

            // 隐藏原始widget的输入框
            if (imageWidget.widget.inputEl) {
                imageWidget.widget.inputEl.style.display = "none";
            }

            // 将img添加到widget后面
            imageWidget.widget.computedHeight = 220;
            imageWidget.widget.draw = function (ctx, node, width, y) {
                // 不绘制默认的widget
            };

            // 添加自定义绘制
            const onDrawForeground = this.onDrawForeground;
            this.onDrawForeground = function (ctx) {
                const r = onDrawForeground?.apply?.(this, arguments);

                // 在节点底部绘制提示或图片
                if (img.src && img.complete) {
                    const y = this.size[1] - 220;
                    // Debug logging (limited to once per second to avoid spam)
                    if (!this._lastLog || Date.now() - this._lastLog > 1000) {
                        console.log("Drawing image:", { src: img.src, w: this.size[0], y: y });
                        this._lastLog = Date.now();
                    }
                    try {
                        ctx.drawImage(img, 10, y, this.size[0] - 20, 200);
                    } catch (e) {
                        console.error("Draw image error:", e);
                    }
                } else {
                    // Debug logging for missing image state
                    if (!this._lastLog || Date.now() - this._lastLog > 1000) {
                        console.log("Not drawing image:", { src: img.src, complete: img.complete });
                        this._lastLog = Date.now();
                    }
                    ctx.fillStyle = "#999";
                    ctx.font = "14px Arial";
                    ctx.textAlign = "center";
                    const y = this.size[1] - 110;
                    ctx.fillText(textDiv.textContent, this.size[0] / 2, y);
                }

                return r;
            };

            // 更新图片的函数
            const updateImage = (iconUrl) => {
                console.log("updateImage called with:", iconUrl);
                if (!iconUrl || iconUrl.trim() === '') {
                    img.src = "";
                    textDiv.textContent = '该角色无预览图';
                    this.setDirtyCanvas(true, true);
                    return;
                }

                textDiv.textContent = '加载中...';
                this.setDirtyCanvas(true, true);

                // 加载图片
                const tempImg = new Image();
                tempImg.crossOrigin = "anonymous";
                tempImg.onload = () => {
                    img.src = iconUrl;
                    // We don't strictly need setDirtyCanvas here because img.onload (above) will handle it,
                    // but keeping it doesn't hurt for immediate feedback to remove "Loading..." text implies
                    // we might want to trigger once here too to clear text?
                    // actually onDraw checks img.complete.
                    // If we just set src, img.complete might be false.
                    // Let's rely on img.onload to trigger the final draw.
                    // But we might want to clear the 'Loading...' text?
                    // The onDraw logic draws text if !img.src or !img.complete.
                    // So we must wait for img.onload to draw the image.
                };
                tempImg.onerror = () => {
                    img.src = "";
                    textDiv.textContent = '图片加载失败';
                    this.setDirtyCanvas(true, true);
                };
                tempImg.src = iconUrl;
            };

            // 查找character输入widget
            const characterWidget = this.widgets?.find(w => w.name === "character");

            if (characterWidget) {
                // 保存原始回调
                const originalCallback = characterWidget.callback;

                // 重写回调函数
                characterWidget.callback = function (value) {
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

            // 调整节点大小以容纳预览图
            this.setSize([this.size[0], this.size[1] + 220]);

            return result;
        };
    }
});
