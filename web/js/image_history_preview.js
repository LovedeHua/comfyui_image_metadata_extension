import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// 与后端 MAX_HISTORY 保持一致
const MAX_HISTORY = 5;

function viewUrl(entry) {
    const params = new URLSearchParams({
        filename: entry.filename,
        subfolder: entry.subfolder || "",
        type: entry.type || "temp",
    });
    return api.apiURL("/view?" + params.toString());
}

// 把 node.imgs 换成历史里第 index 张，交给 ComfyUI 默认的节点内图片渲染
function showIndex(node, index) {
    const history = node.history || [];
    if (!history.length) return;
    index = Math.max(0, Math.min(index, history.length - 1));
    node.histIndex = index;

    const entry = history[index];
    const img = new Image();
    img.onload = () => {
        node.setSizeForImage?.();
        app.graph.setDirtyCanvas(true, false);
    };
    // 服务端重启会清空 temp，旧引用失效时至少给出可见反馈
    img.onerror = () => {
        if (node.histCounter) {
            node.histCounter.textContent = "缓存已失效";
            node.histCounter.title = "ComfyUI 已重启，重新运行一次即可刷新历史";
        }
    };
    img.src = viewUrl(entry);
    node.imgs = [img];

    if (node.histCounter) {
        node.histCounter.textContent = `${index + 1}/${history.length}`;
        node.histCounter.title = entry.filename || "";
    }
}

app.registerExtension({
    name: "ImageMetadataExt.ImageHistoryPreview",

    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "ImageHistoryPreview") return;

        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated?.apply(this, arguments);

            this.history = [];
            this.histIndex = -1;

            const element = document.createElement("div");
            element.style.cssText =
                "display:flex;align-items:center;gap:4px;padding:2px 6px;width:100%;";

            const prevBtn = document.createElement("button");
            prevBtn.textContent = "◀";
            prevBtn.title = "上一张";
            prevBtn.style.cssText = "cursor:pointer;";

            const counter = document.createElement("span");
            counter.textContent = "0/0";
            counter.style.cssText =
                "flex:1;text-align:center;font-size:10px;opacity:0.75;cursor:pointer;user-select:none;";
            counter.title = "点击回到最新一张";

            const nextBtn = document.createElement("button");
            nextBtn.textContent = "▶";
            nextBtn.title = "下一张";
            nextBtn.style.cssText = "cursor:pointer;";

            element.append(prevBtn, counter, nextBtn);
            const widget = this.addDOMWidget("history_nav", "history_nav", element);
            widget.serialize = false;
            // 控件条很矮，别按 DOM 默认给节点撑一大块空
            widget.computeSize = () => [0, 28];

            prevBtn.addEventListener("click", () =>
                showIndex(this, (this.histIndex ?? -1) - 1)
            );
            nextBtn.addEventListener("click", () =>
                showIndex(this, (this.histIndex ?? -1) + 1)
            );
            counter.addEventListener("click", () =>
                showIndex(this, (this.history?.length || 1) - 1)
            );
            this.histCounter = counter;

            return r;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const r = onExecuted?.apply(this, arguments);
            // 兼容不同版本：有的传 output 本体，有的包一层
            const history = message?.history ?? message?.output?.history;
            if (Array.isArray(history) && history.length) {
                this.history = history.slice(-MAX_HISTORY);
                // 每次执行完都跳到最新
                showIndex(this, this.history.length - 1);
            }
            return r;
        };

        // 历史随 workflow 序列化，页面刷新后还能继续翻
        const onSerialize = nodeType.prototype.onSerialize;
        nodeType.prototype.onSerialize = function (o) {
            const r = onSerialize?.apply(this, arguments);
            if (o.properties) {
                o.properties.history = (this.history || []).slice(-MAX_HISTORY);
            }
            return r;
        };

        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const r = onConfigure?.apply(this, arguments);
            const history = info?.properties?.history;
            if (Array.isArray(history) && history.length) {
                this.history = history.slice(-MAX_HISTORY);
                showIndex(this, this.history.length - 1);
            }
            return r;
        };
    },
});
