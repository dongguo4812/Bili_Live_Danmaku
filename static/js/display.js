(() => {
  "use strict";

  const canvas = document.getElementById("live2d-canvas");
  const placeholder = document.getElementById("display-placeholder");
  const panel = document.getElementById("display-settings-panel");
  const toggle = document.getElementById("display-settings-toggle");
  const closeButton = document.getElementById("display-settings-close");
  const modelSelect = document.getElementById("model-select");
  const saveStatus = document.getElementById("settings-save-status");
  const bubble = document.getElementById("danmaku-bubble");
  if (!canvas) return;

  const ids = [
    "scale", "x", "y", "bubble-x", "bubble-bottom", "bubble-width",
    "bubble-height", "bubble-items", "bubble-duration"
  ];
  const controls = Object.fromEntries(ids.map(key => [key, document.getElementById(key + "-slider")]));
  const outputs = Object.fromEntries(ids.map(key => [key, document.getElementById(key + "-value")]));

  const MAX_DPR = 2;
  let gl = null;
  let resizeObserver = null;
  let resizePending = false;
  let statusSent = false;
  let currentModel = "live_uu";
  let saveTimer = null;
  let isSaving = false;
  let reloadAfterSave = false;
  let lastDanmakuId = 0;
  let pollTimer = null;
  const activeItems = new Map();

  function showMessage(message) {
    if (placeholder) placeholder.textContent = message;
  }

  function setSaveStatus(message, isError = false) {
    if (!saveStatus) return;
    saveStatus.textContent = message;
    saveStatus.classList.toggle("error", isError);
  }

  function setPanelOpen(open) {
    if (!panel || !toggle) return;
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  }

  async function reportState(state, message) {
    try {
      await fetch("/api/live2d/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state, message })
      });
    } catch (error) {
      console.warn("Unable to report Live2D state:", error);
    }
  }

  function setCanvasResolution() {
    const widthCss = canvas.clientWidth;
    const heightCss = canvas.clientHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    const width = Math.max(1, Math.round(widthCss * dpr));
    const height = Math.max(1, Math.round(heightCss * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    if (gl) gl.viewport(0, 0, canvas.width, canvas.height);
  }

  function collectModelFiles(config) {
    const files = [];
    for (const key of ["model", "physics", "pose"]) {
      if (typeof config[key] === "string" && config[key]) files.push(config[key]);
    }
    if (Array.isArray(config.textures)) {
      files.push(...config.textures.filter(value => typeof value === "string"));
    }
    if (Array.isArray(config.expressions)) {
      for (const item of config.expressions) {
        if (item && typeof item.file === "string") files.push(item.file);
      }
    }
    if (config.motions && typeof config.motions === "object") {
      for (const group of Object.values(config.motions)) {
        if (!Array.isArray(group)) continue;
        for (const item of group) {
          if (item && typeof item.file === "string") files.push(item.file);
        }
      }
    }
    return [...new Set(files)];
  }

  async function verifyAssets(modelUrl) {
    const response = await fetch(modelUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("无法读取模型配置：" + modelUrl + "（HTTP " + response.status + "）");
    }
    const config = await response.json();
    if (!config.model || !Array.isArray(config.textures) || config.textures.length === 0) {
      throw new Error("模型配置缺少 model 或 textures 配置。");
    }
    const base = modelUrl.slice(0, modelUrl.lastIndexOf("/") + 1);
    const files = collectModelFiles(config);
    await Promise.all(files.map(async relative => {
      const url = new URL(relative, window.location.origin + base);
      const result = await fetch(url.href, { method: "HEAD", cache: "no-store" });
      if (!result.ok) throw new Error("模型资源缺失：" + relative + "（HTTP " + result.status + "）");
    }));
    return config;
  }

  function currentPreferences() {
    return {
      model: modelSelect.value,
      scale: Number(controls.scale.value) / 100,
      x: Number(controls.x.value) / 100,
      y: Number(controls.y.value) / 100,
      bubble_x: Number(controls["bubble-x"].value),
      bubble_bottom: Number(controls["bubble-bottom"].value),
      bubble_width: Number(controls["bubble-width"].value),
      bubble_max_height: Number(controls["bubble-height"].value),
      bubble_max_items: Number(controls["bubble-items"].value),
      bubble_duration: Number(controls["bubble-duration"].value)
    };
  }

  function updateSliderLabels() {
    outputs.scale.textContent = controls.scale.value + "%";
    outputs.x.textContent = (Number(controls.x.value) / 100).toFixed(2);
    outputs.y.textContent = (Number(controls.y.value) / 100).toFixed(2);
    outputs["bubble-x"].textContent = controls["bubble-x"].value + "%";
    outputs["bubble-bottom"].textContent = controls["bubble-bottom"].value + "%";
    outputs["bubble-width"].textContent = controls["bubble-width"].value + "px";
    outputs["bubble-height"].textContent = controls["bubble-height"].value + "px";
    outputs["bubble-items"].textContent = controls["bubble-items"].value + " 条";
    outputs["bubble-duration"].textContent = controls["bubble-duration"].value + " 秒";
  }

  function applyBubblePreferences() {
    if (!bubble) return;
    const p = currentPreferences();
    const width = Math.min(p.bubble_width, Math.max(180, window.innerWidth - 24));
    const requestedCenter = window.innerWidth * p.bubble_x / 100;
    const left = Math.max(12, Math.min(window.innerWidth - width - 12, requestedCenter - width / 2));
    bubble.style.left = left + "px";
    bubble.style.transform = "none";
    bubble.style.bottom = p.bubble_bottom + "%";
    bubble.style.width = width + "px";
    bubble.style.maxHeight = p.bubble_max_height + "px";
    bubble.style.setProperty("--bubble-max-items", String(p.bubble_max_items));
  }

  async function saveSettings() {
    if (isSaving) return;
    isSaving = true;
    setSaveStatus("正在保存设置…");
    try {
      const response = await fetch("/api/display/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentPreferences())
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "保存失败。");
      setSaveStatus("已保存到本地。");
      if (reloadAfterSave) {
        window.location.reload();
        return;
      }
    } catch (error) {
      setSaveStatus("保存失败：" + error.message, true);
    } finally {
      isSaving = false;
      reloadAfterSave = false;
    }
  }

  function scheduleSave(reload = false) {
    updateSliderLabels();
    applyBubblePreferences();
    reloadAfterSave = reloadAfterSave || reload;
    window.clearTimeout(saveTimer);
    setSaveStatus("有未保存的更改…");
    saveTimer = window.setTimeout(saveSettings, 450);
  }

  async function loadDisplaySettings() {
    const response = await fetch("/api/display/settings", { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取展示设置（HTTP " + response.status + "）。");
    const data = await response.json();
    if (!data.ok || !Array.isArray(data.models) || data.models.length === 0) {
      throw new Error("models 目录下没有找到包含有效模型配置的模型。");
    }
    modelSelect.replaceChildren();
    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.name === model.id ? model.name : model.name + "（" + model.id + "）";
      modelSelect.appendChild(option);
    }
    currentModel = data.model;
    modelSelect.value = data.model;
    if (!modelSelect.value) {
      modelSelect.value = data.models[0].id;
      currentModel = modelSelect.value;
    }
    const p = data.preferences || {};
    controls.scale.value = String(Math.round((Number(p.scale) || 1) * 100));
    controls.x.value = String(Math.round((Number(p.x) || 0) * 100));
    controls.y.value = String(Math.round((Number(p.y) || 0) * 100));
    controls["bubble-x"].value = String(p.bubble_x ?? 50);
    controls["bubble-bottom"].value = String(p.bubble_bottom ?? 45);
    controls["bubble-width"].value = String(p.bubble_width ?? 420);
    controls["bubble-height"].value = String(p.bubble_max_height ?? 320);
    controls["bubble-items"].value = String(p.bubble_max_items ?? 5);
    controls["bubble-duration"].value = String(p.bubble_duration ?? 10);
    updateSliderLabels();
    applyBubblePreferences();
    return currentModel;
  }

  function addDanmaku(item) {
    if (!bubble || !item || !item.text) return;
    const wrapper = document.createElement("div");
    wrapper.className = "danmaku-entry" + (item.type === "gift" ? " gift-entry" : "");
    const nickname = document.createElement("span");
    nickname.className = "danmaku-nickname";
    nickname.textContent = item.nickname || "观众";
    const message = document.createElement("span");
    message.className = "danmaku-text";
    message.textContent = item.text;
    wrapper.append(nickname, message);
    bubble.appendChild(wrapper);
    bubble.hidden = false;

    const p = currentPreferences();
    while (bubble.children.length > p.bubble_max_items) {
      const oldest = bubble.firstElementChild;
      if (!oldest) break;
      const oldId = oldest.dataset.entryId;
      if (oldId) {
        const timer = activeItems.get(oldId);
        if (timer) clearTimeout(timer);
        activeItems.delete(oldId);
      }
      oldest.remove();
    }

    const entryId = String(item.id || (Date.now() + Math.random()));
    wrapper.dataset.entryId = entryId;
    const timer = window.setTimeout(() => {
      wrapper.remove();
      activeItems.delete(entryId);
      if (!bubble.children.length) bubble.hidden = true;
    }, p.bubble_duration * 1000);
    activeItems.set(entryId, timer);
  }

  async function pollDanmaku() {
    try {
      const response = await fetch("/api/danmaku?after=" + lastDanmakuId, { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      for (const item of data.items || []) {
        lastDanmakuId = Math.max(lastDanmakuId, Number(item.id) || 0);
        addDanmaku(item);
      }
    } catch (error) {
      console.debug("Danmaku polling temporarily unavailable:", error);
    }
  }

  function waitForRenderedPixels() {
    return new Promise((resolve, reject) => {
      const startedAt = performance.now();
      let frames = 0;
      function inspect() {
        frames += 1;
        if (!gl || gl.isContextLost()) {
          reject(new Error("WebGL 上下文丢失。"));
          return;
        }
        if (frames >= 3) {
          try {
            const pixels = new Uint8Array(canvas.width * canvas.height * 4);
            gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            let visibleSamples = 0;
            for (let i = 3; i < pixels.length; i += 64) {
              if (pixels[i] > 8 && ++visibleSamples >= 8) break;
            }
            if (visibleSamples >= 8) {
              resolve();
              return;
            }
          } catch (error) {
            reject(new Error("无法验证 Live2D 渲染结果：" + error.message));
            return;
          }
        }
        if (performance.now() - startedAt > 15000) {
          reject(new Error("运行库已启动，但 15 秒内没有检测到模型绘制像素。请检查浏览器控制台和模型资源。"));
          return;
        }
        requestAnimationFrame(inspect);
      }
      requestAnimationFrame(inspect);
    });
  }

  async function start() {
    try {
      showMessage("正在读取展示设置…");
      const modelId = await loadDisplaySettings();
      const modelUrl = "/models/" + encodeURIComponent(modelId) + "/model.json";
      showMessage("正在检查 " + modelId + " 模型资源…");
      await reportState("loading", "正在检查模型配置和资源文件：" + modelId);
      await verifyAssets(modelUrl);
      if (typeof window.loadlive2d !== "function" ||
          typeof window.Live2DModelWebGL === "undefined" ||
          typeof window.Live2D === "undefined") {
        throw new Error("live2d.min.js 未提供预期的 Cubism 2 运行时接口。");
      }
      setCanvasResolution();
      showMessage("模型资源检查通过，正在加载 " + modelId + "…");
      window.loadlive2d(canvas.id, modelUrl);
      gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
      if (!gl) throw new Error("当前浏览器无法创建 WebGL 上下文。");
      resizeObserver = new ResizeObserver(() => {
        if (resizePending) return;
        resizePending = true;
        requestAnimationFrame(() => {
          resizePending = false;
          setCanvasResolution();
        });
      });
      resizeObserver.observe(canvas);
      window.addEventListener("resize", setCanvasResolution, { passive: true });
      await waitForRenderedPixels();
      statusSent = true;
      showMessage("");
      if (placeholder) placeholder.style.display = "none";
      await reportState("loaded", modelId + " 已由 Cubism 2 WebGL 运行时加载并检测到实际绘制内容。");
    } catch (error) {
      console.error("Live2D initialization failed:", error);
      showMessage("Live2D 加载失败：" + error.message);
      await reportState("error", error.message);
      setSaveStatus(error.message, true);
    }
  }

  if (toggle) toggle.addEventListener("click", () => setPanelOpen(panel.hidden));
  if (closeButton) closeButton.addEventListener("click", () => setPanelOpen(false));

  async function handleModelChange() {
    try {
      const response = await fetch("/api/display/settings?model=" + encodeURIComponent(modelSelect.value), { cache: "no-store" });
      if (!response.ok) throw new Error("无法读取该模型的独立设置。");
      const data = await response.json();
      const p = data.preferences || {};
      controls.scale.value = String(Math.round((Number(p.scale) || 1) * 100));
      controls.x.value = String(Math.round((Number(p.x) || 0) * 100));
      controls.y.value = String(Math.round((Number(p.y) || 0) * 100));
      controls["bubble-x"].value = String(p.bubble_x ?? 50);
      controls["bubble-bottom"].value = String(p.bubble_bottom ?? 45);
      controls["bubble-width"].value = String(p.bubble_width ?? 420);
      controls["bubble-height"].value = String(p.bubble_max_height ?? 320);
      controls["bubble-items"].value = String(p.bubble_max_items ?? 5);
      controls["bubble-duration"].value = String(p.bubble_duration ?? 10);
      scheduleSave(true);
    } catch (error) {
      setSaveStatus(error.message, true);
    }
  }

  if (modelSelect) modelSelect.addEventListener("change", handleModelChange);
  for (const [key, slider] of Object.entries(controls)) {
    if (!slider) continue;
    slider.addEventListener("input", () => {
      updateSliderLabels();
      applyBubblePreferences();
    });
    slider.addEventListener("change", () => {
      scheduleSave(["scale", "x", "y"].includes(key));
    });
  }

  start();
  pollTimer = window.setInterval(pollDanmaku, 450);
  window.addEventListener("resize", applyBubblePreferences, { passive: true });
  window.addEventListener("beforeunload", () => {
    if (resizeObserver) resizeObserver.disconnect();
    if (pollTimer) clearInterval(pollTimer);
    if (!statusSent) reportState("error", "OBS 展示页面在模型初始化完成前关闭。");
  });
})();
