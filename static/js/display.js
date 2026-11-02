(() => {
  "use strict";

  const canvas = document.getElementById("live2d-canvas");
  const placeholder = document.getElementById("display-placeholder");
  const panel = document.getElementById("display-settings-panel");
  const toggle = document.getElementById("display-settings-toggle");
  const closeButton = document.getElementById("display-settings-close");
  const modelSelect = document.getElementById("model-select");
  const saveStatus = document.getElementById("settings-save-status");
  const saveButton = document.getElementById("settings-save-button");
  const bubble = document.getElementById("danmaku-bubble");
  const bubblePreview = document.getElementById("danmaku-preview");
  const ttsDanmakuEnabled = document.getElementById("tts-danmaku-enabled");
  const ttsGiftEnabled = document.getElementById("tts-gift-enabled");
  const ttsScEnabled = document.getElementById("tts-sc-enabled");
  const ttsVoice = document.getElementById("tts-voice");
  const ttsRate = document.getElementById("tts-rate-slider");
  const ttsVolume = document.getElementById("tts-volume-slider");
  const ttsQueueLimit = document.getElementById("tts-queue-slider");
  const ttsGiftMin = document.getElementById("tts-gift-min");
  const ttsRateValue = document.getElementById("tts-rate-value");
  const ttsVolumeValue = document.getElementById("tts-volume-value");
  const ttsQueueValue = document.getElementById("tts-queue-value");
  const ttsTestButton = document.getElementById("tts-test-button");
  const ttsSaveButton = document.getElementById("tts-save-button");
  const ttsStatus = document.getElementById("tts-status");
  if (!canvas) return;

  const ids = [
    "scale", "x", "y", "bubble-x", "bubble-bottom", "bubble-width",
    "bubble-height", "bubble-items", "bubble-duration", "bubble-font"
  ];
  const controls = Object.fromEntries(ids.map(key => [key, document.getElementById(key + "-slider")]));
  const outputs = Object.fromEntries(ids.map(key => [key, document.getElementById(key + "-value")]));

  const MAX_DPR = 2;
  let gl = null;
  let resizeObserver = null;
  let resizePending = false;
  let statusSent = false;
  let currentModel = "live_uu";
  let isSaving = false;
  let hasUnsavedChanges = false;
  let lastDanmakuId = 0;
  let pollTimer = null;
  const activeItems = new Map();
  let ttsSettings = null;
  let speechQueue = [];
  let speechSequence = 0;
  let speaking = false;
  let audioUnlocked = false;
  let audioContext = null;
  let currentAudioSource = null;
  let audioPlaying = false;
  let currentAnalyser = null;
  let mouthAnimationFrame = 0;


  function setTtsStatus(message, isError = false) {
    if (!ttsStatus) return;
    ttsStatus.textContent = message;
    ttsStatus.classList.toggle("error", isError);
  }

  function updateTtsLabels() {
    if (ttsRateValue) ttsRateValue.textContent = Number(ttsRate.value) + "%";
    if (ttsVolumeValue) ttsVolumeValue.textContent = Number(ttsVolume.value) + "%";
    if (ttsQueueValue) ttsQueueValue.textContent = Number(ttsQueueLimit.value) + " 条";
  }

  async function loadTtsSettings() {
    const response = await fetch("/api/tts/settings", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "无法读取语音设置。");
    const p = data.preferences || {};
    ttsSettings = p;
    if (ttsVoice) {
      ttsVoice.replaceChildren();
      for (const voice of data.voices || []) {
        const option = document.createElement("option");
        option.value = voice;
        option.textContent = voice.replace(/^zh-CN-/, "").replace(/Neural$/, "");
        ttsVoice.appendChild(option);
      }
      ttsVoice.value = p.voice || "zh-CN-XiaoxiaoNeural";
    }
    if (ttsDanmakuEnabled) ttsDanmakuEnabled.checked = p.danmaku_enabled !== false;
    if (ttsGiftEnabled) ttsGiftEnabled.checked = p.gift_enabled !== false;
    if (ttsScEnabled) ttsScEnabled.checked = p.super_chat_enabled !== false;
    if (ttsRate) ttsRate.value = String(p.rate ?? 0);
    if (ttsVolume) ttsVolume.value = String(p.volume ?? 0);
    if (ttsQueueLimit) ttsQueueLimit.value = String(p.max_queue_length ?? 30);
    if (ttsGiftMin) ttsGiftMin.value = String(p.gift_min_amount ?? 0);
    updateTtsLabels();
  }

  function currentTtsPreferences() {
    return {
      danmaku_enabled: Boolean(ttsDanmakuEnabled && ttsDanmakuEnabled.checked),
      gift_enabled: Boolean(ttsGiftEnabled && ttsGiftEnabled.checked),
      super_chat_enabled: Boolean(ttsScEnabled && ttsScEnabled.checked),
      voice: ttsVoice ? ttsVoice.value : "zh-CN-XiaoxiaoNeural",
      rate: Number(ttsRate ? ttsRate.value : 0),
      volume: Number(ttsVolume ? ttsVolume.value : 0),
      max_queue_length: Number(ttsQueueLimit ? ttsQueueLimit.value : 30),
      gift_min_amount: Math.max(0, Number(ttsGiftMin ? ttsGiftMin.value : 0) || 0)
    };
  }

  async function saveTtsSettings() {
    if (ttsSaveButton) ttsSaveButton.disabled = true;
    setTtsStatus("正在保存语音设置…");
    try {
      const response = await fetch("/api/tts/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentTtsPreferences())
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "保存失败。");
      ttsSettings = data.preferences;
      setTtsStatus("语音设置已保存。");
    } catch (error) {
      setTtsStatus("保存失败：" + error.message, true);
    } finally {
      if (ttsSaveButton) ttsSaveButton.disabled = false;
    }
  }

  function numberToChinese(value) {
    const digits = "零一二三四五六七八九";
    const units = ["", "十", "百", "千"];
    const n = Math.max(0, Math.floor(Number(value) || 0));
    if (n < 10) return digits[n];
    if (n < 100) {
      const tens = Math.floor(n / 10), ones = n % 10;
      return (tens === 1 ? "" : digits[tens]) + "十" + (ones ? digits[ones] : "");
    }
    if (n < 10000) {
      let out = "", rest = n, zero = false;
      for (let i = 3; i >= 0; i--) {
        const base = Math.pow(10, i), digit = Math.floor(rest / base);
        rest %= base;
        if (digit) {
          if (zero && out) out += "零";
          out += digits[digit] + units[i];
          zero = false;
        } else if (out && rest > 0) zero = true;
      }
      return out;
    }
    return String(n);
  }

  function speechTextFor(item) {
    const nickname = item.nickname || "观众";
    if (item.type === "danmaku") return nickname + "说：" + item.text;
    if (item.type === "gift") {
      const count = Math.max(1, Number(item.gift_num) || 1);
      const countText = count === 1 ? "一个" : numberToChinese(count) + "个";
      return nickname + "送出了" + countText + (item.gift_name || "礼物") + "。";
    }
    if (item.type === "super_chat") {
      const amount = Math.max(0, Number(item.amount_yuan) || 0);
      if (amount > 0) {
        const amountText = Number.isInteger(amount) ? numberToChinese(amount) : String(amount).replace(".", "点");
        return nickname + "发来价值" + amountText + "元的醒目留言：" + item.text;
      }
      return "醒目留言，" + nickname + "说：" + item.text;
    }
    return "";
  }

  function speechPriority(type) {
    return type === "danmaku" ? 1 : 0;
  }

  function enqueueSpeech(item) {
    if (!ttsSettings || !item || !item.text) return;
    if (item.type === "danmaku" && !ttsSettings.danmaku_enabled) return;
    if (item.type === "gift") {
      if (!ttsSettings.gift_enabled) return;
      if ((Number(item.gift_value_yuan) || 0) < Number(ttsSettings.gift_min_amount || 0)) return;
    }
    if (item.type === "super_chat" && !ttsSettings.super_chat_enabled) return;
    if (!["danmaku", "gift", "super_chat"].includes(item.type)) return;

    const text = speechTextFor(item);
    if (!text) return;
    const limit = Math.max(1, Number(ttsSettings.max_queue_length) || 30);
    if (speechQueue.length >= limit) {
      const oldestOrdinary = speechQueue.findIndex(entry => entry.priority === 1);
      if (oldestOrdinary >= 0) {
        speechQueue.splice(oldestOrdinary, 1);
      } else {
        return;
      }
    }
    speechQueue.push({
      text,
      priority: speechPriority(item.type),
      sequence: ++speechSequence,
      type: item.type
    });
    speechQueue.sort((a, b) => a.priority - b.priority || a.sequence - b.sequence);
    processSpeechQueue();
  }

  function setLive2DMouth(value) {
    try {
      const manager = window.__live2dManager;
      const model = manager && manager.getModel(0);
      if (!model || !model.initialized || typeof model.getLive2DModel !== "function") return;
      const liveModel = model.getLive2DModel();
      if (!liveModel || typeof liveModel.getParamIndex !== "function") return;
      if (liveModel.getParamIndex("PARAM_MOUTH_OPEN_Y") < 0) return;
      model.lipSync = null;
      model.lipSyncValue = Math.max(0, Math.min(1, value));
    } catch (error) {
      console.debug("Live2D mouth sync unavailable:", error);
    }
  }

  function stopMouthSync() {
    if (mouthAnimationFrame) cancelAnimationFrame(mouthAnimationFrame);
    mouthAnimationFrame = 0;
    setLive2DMouth(0);
  }

  function startMouthSync(analyser) {
    const samples = new Uint8Array(analyser.fftSize);
    const tick = () => {
      if (!audioPlaying) {
        setLive2DMouth(0);
        mouthAnimationFrame = 0;
        return;
      }
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (let i = 0; i < samples.length; i++) {
        const normalized = (samples[i] - 128) / 128;
        sum += normalized * normalized;
      }
      const rms = Math.sqrt(sum / samples.length);
      const level = Math.max(0, Math.min(1, (rms - 0.012) * 7.5));
      setLive2DMouth(level);
      mouthAnimationFrame = requestAnimationFrame(tick);
    };
    if (mouthAnimationFrame) cancelAnimationFrame(mouthAnimationFrame);
    tick();
  }

  async function unlockSpeechAudio() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error("当前浏览器不支持 Web Audio，无法进行嘴型同步。");
    if (!audioContext) audioContext = new AudioContextClass();
    if (audioContext.state !== "running") await audioContext.resume();
    audioUnlocked = audioContext.state === "running";
    if (!audioUnlocked) throw new Error("浏览器未允许播放语音，请再次点击测试语音。");
  }

  async function playSpeechText(text) {
    await unlockSpeechAudio();
    const response = await fetch("/api/tts/synthesize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "语音生成失败。");

    const audioResponse = await fetch(data.url, { cache: "no-store" });
    if (!audioResponse.ok) throw new Error("无法读取生成的语音文件。");
    const encodedAudio = await audioResponse.arrayBuffer();
    const decodedAudio = await audioContext.decodeAudioData(encodedAudio);
    const source = audioContext.createBufferSource();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    source.buffer = decodedAudio;
    source.connect(analyser);
    analyser.connect(audioContext.destination);
    currentAudioSource = source;
    currentAnalyser = analyser;
    audioPlaying = true;

    await new Promise((resolve, reject) => {
      let finished = false;
      const finish = error => {
        if (finished) return;
        finished = true;
        audioPlaying = false;
        stopMouthSync();
        currentAudioSource = null;
        currentAnalyser = null;
        try { source.disconnect(); } catch (_) {}
        try { analyser.disconnect(); } catch (_) {}
        if (error) reject(error);
        else resolve();
      };
      source.onended = () => finish();
      try {
        source.start(0);
        startMouthSync(analyser);
      } catch (error) {
        finish(error);
      }
    });
  }

  async function processSpeechQueue() {
    if (speaking || !speechQueue.length) return;
    if (!audioUnlocked) return;
    speaking = true;
    while (speechQueue.length) {
      const next = speechQueue.shift();
      try {
        await playSpeechText(next.text);
      } catch (error) {
        setTtsStatus("语音播放失败：" + error.message, true);
        console.warn("TTS playback failed:", error);
        if (/允许播放|支持 Web Audio/.test(error.message)) {
          speechQueue.unshift(next);
          break;
        }
      }
    }
    speaking = false;
  }

  async function testSpeech() {
    try {
      await unlockSpeechAudio();
      const saveResponse = await fetch("/api/tts/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentTtsPreferences())
      });
      const saved = await saveResponse.json();
      if (!saveResponse.ok || !saved.ok) throw new Error(saved.error || "保存当前语音参数失败。");
      ttsSettings = saved.preferences;
      setTtsStatus("正在生成测试语音…");
      if (enqueueTestSpeech()) setTtsStatus("测试语音已加入播报队列。");
    } catch (error) {
      setTtsStatus("无法启动语音：" + error.message, true);
    }
  }

  function enqueueTestSpeech() {
    const limit = Math.max(1, Number(ttsSettings && ttsSettings.max_queue_length) || 30);
    if (speechQueue.length >= limit) {
      const oldestOrdinary = speechQueue.findIndex(entry => entry.priority === 1);
      if (oldestOrdinary >= 0) speechQueue.splice(oldestOrdinary, 1);
      else {
        setTtsStatus("语音队列已满，且没有可替换的普通弹幕。", true);
        return false;
      }
    }
    speechQueue.push({
      text: "这是一条 Edge TTS 语音测试。嘴型会根据实际语音音量同步变化。",
      priority: 0,
      sequence: ++speechSequence,
      type: "test"
    });
    speechQueue.sort((a, b) => a.priority - b.priority || a.sequence - b.sequence);
    processSpeechQueue();
    return true;
  }

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
    if (bubblePreview) {
      bubblePreview.hidden = !open;
      if (open) renderBubblePreview();
    }
  }

  async function reportState(state, message) {
    try {
      await fetch("/api/live2d/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ state, message }),
        keepalive: true
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
      bubble_duration: Number(controls["bubble-duration"].value),
      bubble_font_size: Number(controls["bubble-font"].value)
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
    outputs["bubble-font"].textContent = controls["bubble-font"].value + "px";
  }

  function applyBubblePreferences() {
    if (!bubble) return;
    const p = currentPreferences();
    const width = Math.min(p.bubble_width, Math.max(180, window.innerWidth - 24));
    const requestedCenter = window.innerWidth * p.bubble_x / 100;
    const left = Math.max(12, Math.min(window.innerWidth - width - 12, requestedCenter - width / 2));
    for (const target of [bubble, bubblePreview]) {
      if (!target) continue;
      target.style.left = left + "px";
      target.style.transform = "none";
      target.style.bottom = p.bubble_bottom + "%";
      target.style.width = width + "px";
      target.style.maxHeight = p.bubble_max_height + "px";
      target.style.fontSize = p.bubble_font_size + "px";
      target.style.lineHeight = Math.round(p.bubble_font_size * 1.1) + "px";
      target.style.setProperty("--bubble-max-items", String(p.bubble_max_items));

      // .danmaku-entry has its own CSS font-size, so set the size directly
      // on every entry instead of relying on inheritance from the container.
      for (const entry of target.querySelectorAll(".danmaku-entry")) {
        entry.style.fontSize = p.bubble_font_size + "px";
        entry.style.lineHeight = Math.round(p.bubble_font_size * 1.1) + "px";
      }
    }
    while (bubble.children.length > p.bubble_max_items) {
      const oldest = bubble.firstElementChild;
      if (!oldest) break;
      const oldId = oldest.dataset.entryId;
      const timer = oldId ? activeItems.get(oldId) : null;
      if (timer) clearTimeout(timer);
      if (oldId) activeItems.delete(oldId);
      oldest.remove();
    }
    if (!bubble.children.length) bubble.hidden = true;
  }

  function renderBubblePreview() {
    if (!bubblePreview || !panel || panel.hidden) return;
    const samples = [
      ["示例观众 A", "主播今天状态不错！"],
      ["示例观众 B", "这个模型效果真好"],
      ["示例观众 C", "弹幕位置和大小可以在这里调整"]
    ];
    const count = Math.min(samples.length, Number(controls["bubble-items"].value) || 1);
    bubblePreview.replaceChildren();
    for (const [name, message] of samples.slice(0, count)) {
      const entry = document.createElement("div");
      entry.className = "danmaku-entry preview-entry";
      const nickname = document.createElement("span");
      nickname.className = "danmaku-nickname";
      nickname.textContent = name;
      const text = document.createElement("span");
      text.className = "danmaku-text";
      text.textContent = message;
      entry.append(nickname, text);
      bubblePreview.appendChild(entry);
    }
    bubblePreview.hidden = false;
    applyBubblePreferences();
  }

  async function saveSettings() {
    if (isSaving || !hasUnsavedChanges) return;
    isSaving = true;
    if (saveButton) saveButton.disabled = true;
    setSaveStatus("正在保存设置，并尝试刷新 OBS…");
    try {
      const response = await fetch("/api/display/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentPreferences())
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "保存失败。");
      hasUnsavedChanges = false;
      const obs = result.obs_refresh || {};
      setSaveStatus("设置已保存。" + (obs.message ? " " + obs.message : ""));
      // A normal browser preview must reload to rebuild the Live2D model using
      // the saved transform. OBS sources are refreshed by the backend request.
      if (!window.obsstudio) {
        window.setTimeout(() => window.location.reload(), 350);
      }
    } catch (error) {
      setSaveStatus("保存失败：" + error.message, true);
    } finally {
      isSaving = false;
      if (saveButton) saveButton.disabled = false;
    }
  }

  function markUnsaved() {
    updateSliderLabels();
    applyBubblePreferences();
    hasUnsavedChanges = true;
    setSaveStatus("有未保存的更改；点击下方“保存设置”后才会保存并尝试刷新 OBS。");
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
    controls["bubble-font"].value = String(p.bubble_font_size ?? 20);
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
    wrapper.style.fontSize = p.bubble_font_size + "px";
    wrapper.style.lineHeight = Math.round(p.bubble_font_size * 1.1) + "px";
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
        enqueueSpeech(item);
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

  if (ttsRate) ttsRate.addEventListener("input", updateTtsLabels);
  if (ttsVolume) ttsVolume.addEventListener("input", updateTtsLabels);
  if (ttsQueueLimit) ttsQueueLimit.addEventListener("input", updateTtsLabels);
  if (ttsSaveButton) ttsSaveButton.addEventListener("click", saveTtsSettings);
  if (ttsTestButton) ttsTestButton.addEventListener("click", testSpeech);

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
    controls["bubble-font"].value = String(p.bubble_font_size ?? 20);
      markUnsaved();
    } catch (error) {
      setSaveStatus(error.message, true);
    }
  }

  if (modelSelect) modelSelect.addEventListener("change", handleModelChange);
  if (saveButton) saveButton.addEventListener("click", saveSettings);
  for (const [key, slider] of Object.entries(controls)) {
    if (!slider) continue;
    slider.addEventListener("input", () => {
      updateSliderLabels();
      applyBubblePreferences();
      if (key.startsWith("bubble-") && panel && !panel.hidden) renderBubblePreview();
    });
    slider.addEventListener("change", markUnsaved);
  }

  start();
  async function initializeDanmakuCursor() {
    try {
      await loadTtsSettings();
    } catch (ttsError) {
      console.warn("Unable to load TTS settings:", ttsError);
      setTtsStatus("读取语音设置失败：" + ttsError.message, true);
    }
    try {
      const response = await fetch("/api/danmaku?latest=1", { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        lastDanmakuId = Number(data.latest_id) || 0;
      }
    } catch (error) {
      console.debug("Unable to initialize danmaku cursor:", error);
    }
    await pollDanmaku();
    pollTimer = window.setInterval(pollDanmaku, 450);
  }
  initializeDanmakuCursor();
  window.addEventListener("resize", applyBubblePreferences, { passive: true });
  window.addEventListener("beforeunload", () => {
    if (resizeObserver) resizeObserver.disconnect();
    if (pollTimer) clearInterval(pollTimer);
    reportState("not_started", "OBS 展示页面已关闭，Live2D 运行时当前未运行。");
  });
})();
