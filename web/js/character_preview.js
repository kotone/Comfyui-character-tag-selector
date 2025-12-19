import { app } from "../../../scripts/app.js";

// -------------------------
// 角色数据缓存：按 json_file 缓存
// -------------------------
let currentFile = "";
let characterDataMap = {}; // 当前文件对应的 map（key -> payload）
let characterDisplayList = []; // 当前文件对应的下拉列表（displayName 数组）

const fileCache = new Map(); // fileName -> { map, list }

function addKey(map, key, payload) {
    if (!key) return;
    const k = String(key).trim();
    if (!k) return;
    map[k] = payload;
}

function normalizeJsonFile(value) {
    // 保险：只取 basename，避免传入路径导致 URL 不对
    let s = String(value ?? "").trim();
    if (!s) return "";
    s = s.replace(/\\/g, "/");
    s = s.split("/").pop();       // basename
    if (s.includes("..")) return "";
    return s;
}

async function loadCharacterDataForFile(jsonFileValue) {
    const fileName = normalizeJsonFile(jsonFileValue);
    if (!fileName) {
        currentFile = "";
        characterDataMap = {};
        characterDisplayList = ["未加载角色数据"];
        return;
    }

    // 命中缓存
    if (fileCache.has(fileName)) {
        const cached = fileCache.get(fileName);
        currentFile = fileName;
        characterDataMap = cached.map;
        characterDisplayList = cached.list;
        return;
    }

    try {
        // 注意：这里假设你的 json 都在 web/data/ 下
        const dataUrl = new URL(`../data/${fileName}`, import.meta.url);
        const response = await fetch(dataUrl);

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        if (!Array.isArray(data)) {
            throw new Error("JSON 格式错误：期望数组");
        }

        const newMap = {};
        const newList = [];

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

            // 下拉列表只放 displayName（一角色一项）
            newList.push(displayName);

            // map 同时支持 displayName / 中文名 / 英文名 命中
            addKey(newMap, displayName, payload);
            addKey(newMap, name_cn, payload);
            addKey(newMap, name_en, payload);
        });

        // 去重一下（以防 JSON 内 displayName 重复）
        const uniqList = Array.from(new Set(newList));
        if (uniqList.length === 0) uniqList.push("未加载角色数据");

        fileCache.set(fileName, { map: newMap, list: uniqList });

        currentFile = fileName;
        characterDataMap = newMap;
        characterDisplayList = uniqList;

        console.log("✅ 角色数据加载成功:", fileName, uniqList.length, "characters");
    } catch (error) {
        console.error("❌ 加载角色数据失败:", fileName, error);
        currentFile = fileName;
        characterDataMap = {};
        characterDisplayList = ["未加载角色数据"];
    }
}

function setComboValues(widget, values) {
    widget.options = widget.options || {};
    widget.options.values = Array.isArray(values) && values.length ? values : ["未加载角色数据"];
}

// -------------------------
// 注册节点扩展
// -------------------------
app.registerExtension({
    name: "kotone.CharacterTagSelector.Preview+DynamicList",

    async beforeRegisterNodeDef(nodeType, nodeData, appInstance) {
        if (nodeData.name !== "CharacterTagSelector") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onNodeCreated?.apply(this, arguments);

            const PREVIEW_H = 220;
            const dirty = () => appInstance.graph.setDirtyCanvas(true, true);

            // 预览 Image
            const img = new Image();
            let statusText = "选择角色查看预览";

            img.onload = () => dirty();
            img.onerror = () => {
                img.src = "";
                statusText = "图片加载失败";
                dirty();
            };

            // 预览 widget
            const previewWidget = this.addWidget("custom", "角色预览", "", () => { }, { serialize: false });
            previewWidget.computeSize = () => [this.size[0], PREVIEW_H];

            // 等比包含（不裁切，不形变）
            function drawImageContain(ctx, img, x, y, w, h) {
                const iw = img.naturalWidth || img.width;
                const ih = img.naturalHeight || img.height;
                if (!iw || !ih) return;

                // 等比缩放：把整张图“塞进”容器，不裁切、不形变
                const scale = Math.min(w / iw, h / ih);

                // 如果你不希望小图被放大（避免糊），用这一行替换上面那行：
                // const scale = Math.min(w / iw, h / ih, 1);

                const dw = iw * scale;
                const dh = ih * scale;
                const dx = x + (w - dw) / 2;
                const dy = y + (h - dh) / 2;

                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = "high";
                ctx.drawImage(img, dx, dy, dw, dh);
            }

            previewWidget.draw = (ctx, node, width, y) => {
                ctx.save();

                // 背景
                ctx.fillStyle = "rgba(0,0,0,0.06)";
                ctx.fillRect(0, y, width, PREVIEW_H);

                const pad = 10;
                const boxX = pad;
                const boxY = y + pad;
                const boxW = width - pad * 2;
                const boxH = PREVIEW_H - pad * 2;

                if (img.src && img.complete && img.naturalWidth > 0) {
                    // 裁剪到预览区域，防止绘制溢出
                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(boxX, boxY, boxW, boxH);
                    ctx.clip();

                    drawImageContain(ctx, img, boxX, boxY, boxW, boxH);

                    ctx.restore();
                } else {
                    ctx.fillStyle = "#999";
                    ctx.font = "14px Arial";
                    ctx.textAlign = "center";
                    ctx.textBaseline = "middle";
                    ctx.fillText(statusText, width / 2, y + PREVIEW_H / 2);
                }

                ctx.restore();
            };
            // previewWidget.draw = (ctx, node, width, y) => {
            //     ctx.save();
            //     ctx.fillStyle = "rgba(0,0,0,0.06)";
            //     ctx.fillRect(0, y, width, PREVIEW_H);

            //     if (img.src && img.complete && img.naturalWidth > 0) {
            //         ctx.drawImage(img, 10, y + 10, width - 20, PREVIEW_H - 20);
            //     } else {
            //         ctx.fillStyle = "#999";
            //         ctx.font = "14px Arial";
            //         ctx.textAlign = "center";
            //         ctx.textBaseline = "middle";
            //         ctx.fillText(statusText, width / 2, y + PREVIEW_H / 2);
            //     }
            //     ctx.restore();
            // };
            function buildPreviewSrc(iconUrl) {
                const url = String(iconUrl ?? "").trim();
                if (!url) return "";
                const u = new URL("/character_tag_selector/icon", window.location.origin);
                u.searchParams.set("url", url);
                return u.toString();
            }

            const updateImage = (iconUrl) => {
                const url = buildPreviewSrc(iconUrl) || String(iconUrl ?? "").trim();
                if (!url) {
                    img.src = "";
                    statusText = "该角色无预览图";
                    dirty();
                    return;
                }

                statusText = "加载中...";
                dirty();

                // const bust = (url.includes("?") ? "&" : "?") + "t=" + Date.now();
                img.src = url;
            };

            // 找 widgets
            const jsonWidget = this.widgets?.find((w) => w.name === "json_file");
            const characterWidget = this.widgets?.find((w) => w.name === "character");

            if (!characterWidget) {
                console.warn("⚠️ 未找到名为 character 的 widget，预览不会更新");
                return result;
            }

            // 角色变化：更新预览
            const originalCharacterCb = characterWidget.callback;
            characterWidget.callback = (value) => {
                originalCharacterCb?.apply(characterWidget, arguments);

                const key = String(value ?? "").trim();
                const charData = characterDataMap[key];

                console.log("character changed:", {
                    file: currentFile,
                    value,
                    key,
                    found: !!charData,
                    icon: charData?.icon_url,
                });

                updateImage(charData?.icon_url || "");
            };

            // json_file 变化：刷新下拉列表 + 刷新预览
            if (jsonWidget) {
                const originalJsonCb = jsonWidget.callback;

                jsonWidget.callback = (value) => {
                    originalJsonCb?.apply(jsonWidget, arguments);

                    // 异步刷新（不阻塞 UI）
                    Promise.resolve()
                        .then(async () => {
                            await loadCharacterDataForFile(jsonWidget.value);

                            // 更新 character 下拉列表
                            setComboValues(characterWidget, characterDisplayList);

                            // 如果当前选择不在新列表里，切到第一个
                            if (!characterDisplayList.includes(characterWidget.value)) {
                                characterWidget.value = characterDisplayList[0];
                            }

                            // 刷新预览
                            const key = String(characterWidget.value ?? "").trim();
                            const data = characterDataMap[key];
                            updateImage(data?.icon_url || "");

                            this.setSize(this.computeSize());
                            dirty();
                        })
                        .catch((e) => console.error("[CharacterTagSelector] json change refresh failed:", e));
                };
            }

            // 初始化：按当前 json_file 加载一次，并同步下拉 + 预览
            const init = async () => {
                await loadCharacterDataForFile(jsonWidget?.value);

                setComboValues(characterWidget, characterDisplayList);

                if (!characterDisplayList.includes(characterWidget.value)) {
                    characterWidget.value = characterDisplayList[0];
                }

                const key = String(characterWidget.value ?? "").trim();
                const data = characterDataMap[key];
                updateImage(data?.icon_url || "");

                this.setSize(this.computeSize());
                dirty();
            };
            init().catch(console.error);

            // 工作流加载后：再同步一次（避免 value 回填顺序导致不刷新）
            const origOnConfigure = this.onConfigure;
            this.onConfigure = function (info) {
                origOnConfigure?.call(this, info);

                Promise.resolve()
                    .then(async () => {
                        await loadCharacterDataForFile(jsonWidget?.value);

                        setComboValues(characterWidget, characterDisplayList);

                        if (!characterDisplayList.includes(characterWidget.value)) {
                            characterWidget.value = characterDisplayList[0];
                        }

                        const key = String(characterWidget.value ?? "").trim();
                        const data = characterDataMap[key];
                        updateImage(data?.icon_url || "");

                        this.setSize(this.computeSize());
                        dirty();
                    })
                    .catch(console.error);
            };

            // 初次尺寸适配
            this.setSize(this.computeSize());
            dirty();

            return result;
        };
    },
});