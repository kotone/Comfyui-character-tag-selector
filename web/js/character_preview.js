import { app } from "../../../scripts/app.js";
// ComfyWidgets 这里其实不再需要了（我们用 custom widget 来稳定占高度并绘制）
// import { ComfyWidgets } from "../../../scripts/widgets.js";

// -------------------------
// 角色数据缓存
// -------------------------
let characterDataMap = {};
let isDataLoaded = false;

function addKey(map, key, payload) {
    if (!key) return;
    const k = String(key).trim();
    if (!k) return;
    map[k] = payload;
}

// 加载角色数据
async function loadCharacterData() {
    if (isDataLoaded) return;

    try {
        const dataUrl = new URL("../data/genshin_impact_characters-en-cn.json", import.meta.url);
        const response = await fetch(dataUrl);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        const newMap = {};
        data.forEach((char) => {
            const name_cn = (char.name_cn || "").trim();
            const name_en = (char.name_en || "").trim();

            let displayName = "";
            if (name_cn && name_en) displayName = `${name_cn} (${name_en})`;
            else if (name_cn) displayName = name_cn;
            else displayName = name_en || "未命名角色";

            const payload = {
                icon_url: (char.icon_url || "").trim(),
                name_cn,
                name_en,
                displayName,
            };

            // 关键：同时支持 displayName / 中文名 / 英文名 三种 key
            addKey(newMap, displayName, payload);
            addKey(newMap, name_cn, payload);
            addKey(newMap, name_en, payload);
        });

        characterDataMap = newMap;
        isDataLoaded = true;

        console.log("✅ 角色数据加载成功:", Object.keys(characterDataMap).length, "keys");
    } catch (error) {
        console.error("❌ 加载角色数据失败:", error);
        characterDataMap = {};
        isDataLoaded = false;
    }
}

// -------------------------
// 注册节点扩展
// -------------------------
app.registerExtension({
    name: "kotone.CharacterTagSelector.Preview",

    async beforeRegisterNodeDef(nodeType, nodeData, appInstance) {
        if (nodeData.name !== "CharacterTagSelector") return;

        await loadCharacterData();

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);

            const PREVIEW_H = 220;
            const dirty = () => appInstance.graph.setDirtyCanvas(true, true);

            // 用一个 Image 对象保存当前预览图
            const img = new Image();

            // 如果你的 icon_url 是外站且不需要“读取像素/导出canvas”，不要设置 crossOrigin 更稳
            // 如果你确定资源端带了 Access-Control-Allow-Origin，才打开下面这行
            // img.crossOrigin = "anonymous";

            let statusText = "选择角色查看预览";

            img.onload = () => dirty();
            img.onerror = () => {
                img.src = "";
                statusText = "图片加载失败";
                dirty();
            };

            // 用 custom widget 占位并绘制，避免 onDrawForeground 被裁剪/高度不稳定的问题
            const previewWidget = this.addWidget("custom", "角色预览", "", () => { }, { serialize: false });

            previewWidget.computeSize = () => [this.size[0], PREVIEW_H];

            previewWidget.draw = (ctx, node, width, y) => {
                ctx.save();

                // 背景
                ctx.fillStyle = "rgba(0,0,0,0.06)";
                ctx.fillRect(0, y, width, PREVIEW_H);

                // 只有 naturalWidth > 0 才算真正加载成功
                if (img.src && img.complete && img.naturalWidth > 0) {
                    ctx.drawImage(img, 10, y + 10, width - 20, PREVIEW_H - 20);
                } else {
                    ctx.fillStyle = "#999";
                    ctx.font = "14px Arial";
                    ctx.textAlign = "center";
                    ctx.textBaseline = "middle";
                    ctx.fillText(statusText, width / 2, y + PREVIEW_H / 2);
                }

                ctx.restore();
            };

            const updateImage = (iconUrl) => {
                const url = String(iconUrl ?? "").trim();

                if (!url) {
                    img.src = "";
                    statusText = "该角色无预览图";
                    dirty();
                    return;
                }

                statusText = "加载中...";
                dirty();

                // 如遇到缓存导致不触发 onload，可加时间戳强制刷新
                const bust = (url.includes("?") ? "&" : "?") + "t=" + Date.now();
                img.src = url + bust;
            };

            // 找到节点里的 character widget
            const characterWidget = this.widgets?.find((w) => w.name === "character");
            if (characterWidget) {
                const originalCallback = characterWidget.callback;

                characterWidget.callback = (value) => {
                    originalCallback?.apply(characterWidget, arguments);

                    const key = String(value ?? "").trim();
                    const charData = characterDataMap[key];

                    // 调试：确认是否命中 icon_url
                    console.log("character changed:", {
                        value,
                        key,
                        found: !!charData,
                        icon: charData?.icon_url,
                    });

                    updateImage(charData?.icon_url || "");
                };

                // 初始化：如果节点创建时已经有值，立刻更新一次
                const initKey = String(characterWidget.value ?? "").trim();
                if (initKey) {
                    const initData = characterDataMap[initKey];
                    updateImage(initData?.icon_url || "");
                }
            } else {
                console.warn("⚠️ 未找到名为 character 的 widget，预览不会更新");
            }

            // 防止工作流加载后 size 被覆盖：配置时重新按 widgets 计算尺寸
            const origOnConfigure = this.onConfigure;
            this.onConfigure = function (info) {
                origOnConfigure?.call(this, info);
                this.setSize(this.computeSize());
                dirty();

                // 配置后再根据当前 character 值刷新一次（防止 value 先于 onNodeCreated 回填）
                const cw = this.widgets?.find((w) => w.name === "character");
                const key = String(cw?.value ?? "").trim();
                const data = characterDataMap[key];
                if (key) updateImage(data?.icon_url || "");
            };

            // 初次也让节点尺寸适配 widgets
            this.setSize(this.computeSize());
            dirty();

            return result;
        };
    },
});