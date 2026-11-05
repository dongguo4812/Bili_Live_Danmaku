(() => {
  "use strict";

  const canvas = document.getElementById("live2d-canvas");
  const live2dViewport = document.getElementById("live2d-viewport");
  const moc3Frame = document.getElementById("moc3-runtime-frame");
  const placeholder = document.getElementById("display-placeholder");
  const panel = document.getElementById("display-settings-panel");
  const toggle = document.getElementById("display-settings-toggle");
  const closeButton = document.getElementById("display-settings-close");
  const modelSelect = document.getElementById("model-select");
  const modelProfileField = document.getElementById("model-profile-field");
  const modelProfileSelect = document.getElementById("model-profile-select");
  const motionSelect = document.getElementById("model-motion-select");
  const motionPlayButton = document.getElementById("model-motion-play");
  const motionShortcutsContainer = document.getElementById("model-motion-shortcuts");
  let motionShortcutBindings = {};
  const expressionSelect = document.getElementById("model-expression-select");
  const expressionApplyButton = document.getElementById("model-expression-apply");
  const expressionResetButton = document.getElementById("model-expression-reset");
  const modelActionStatusElement = document.getElementById("model-action-status");
  const modelCapabilitiesElement = document.getElementById("model-capabilities");
  let lastMoc3MouthSyncStatus = null;
  const mouthSyncStatusElement = document.getElementById("moc3-mouth-sync-status");
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
  const idleTipsEnabled = document.getElementById("idle-tips-enabled");
  const idleTipsInterval = document.getElementById("idle-tips-interval");
  const idleTipsMessages = document.getElementById("idle-tips-messages");
  const idleTipsSaveButton = document.getElementById("idle-tips-save-button");
  const idleTipsStatus = document.getElementById("idle-tips-status");
  const idleTimeFields = {
    early_morning: document.getElementById("idle-time-early-morning"),
    morning: document.getElementById("idle-time-morning"),
    lunch: document.getElementById("idle-time-lunch"),
    after_lunch: document.getElementById("idle-time-after-lunch"),
    afternoon: document.getElementById("idle-time-afternoon"),
    evening: document.getElementById("idle-time-evening"),
    night: document.getElementById("idle-time-night"),
    late_night: document.getElementById("idle-time-late-night"),
    midnight: document.getElementById("idle-time-midnight")
  };
  if (!canvas) return;

  const displayMode = document.documentElement.dataset.displayMode || "all";
  const live2dEnabledAtLoad = document.documentElement.dataset.live2dEnabled !== "0";
  const live2dRuntimeVersion = document.documentElement.dataset.live2dVersion || "moc2";
  const showLive2D = live2dEnabledAtLoad && (displayMode === "all" || displayMode === "live2d");
  const showDanmaku = displayMode === "all" || displayMode === "live2d" || displayMode === "danmaku";
  const showTts = displayMode === "all" || displayMode === "tts";

  const ids = [
    "scale", "x", "y", "crop-top", "crop-right", "crop-bottom", "crop-left",
    "bubble-x", "bubble-bottom", "bubble-width",
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
  let currentModelProfile = "";
  let selectedModelConfig = null;
  let loadedModelId = null;
  let loadedModelProfile = "";
  let live2dTransformBase = null;
  let modelSwitchPromise = Promise.resolve();
  let modelSwitchToken = 0;
  let isSaving = false;
  let hasUnsavedChanges = false;
  let lastDanmakuId = 0;
  let pollTimer = null;
  const activeItems = new Map();
  let ttsSettings = null;
  let speechQueue = [];
  let currentSpeech = null;
  let speechSequence = 0;
  let speaking = false;
  let audioUnlocked = false;
  let audioContext = null;
  let connectionTimer = null;
  let lastConnectionState = null;
  let live2dModuleActive = false;
  let voiceModuleActive = false;
  let live2dModuleState = "stopped";
  let audioResumeInProgress = false;
  let currentAudioSource = null;
  let audioPlaying = false;
  let currentAnalyser = null;
  let mouthAnimationFrame = 0;
  let idleTipSettings = null;
  let idleConnected = false;
  let idleTimer = null;
  let idleTipElement = null;
  let lastIdleTipText = "";


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
    const response = await fetch("/api/voice/tts/settings", { cache: "no-store" });
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
      const response = await fetch("/api/voice/tts/settings", {
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

  function publishSpeechState() {
    if (window.parent === window) return;
    try {
      window.parent.postMessage({
        type: "combined-tts-state",
        current: currentSpeech ? currentSpeech.text : "",
        queue: speechQueue.map(entry => entry.text)
      }, window.location.origin);
    } catch (error) {
      
    }
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
    if (!voiceModuleActive || !ttsSettings || !item || !item.text) return;
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
    publishSpeechState();
    processSpeechQueue();
  }

  function setLive2DMouth(value) {
    try {
      if (live2dRuntimeVersion === "moc3") {
        const runtimeWindow = moc3Frame && moc3Frame.contentWindow;
        if (runtimeWindow && typeof runtimeWindow.live2dSetMouthOpen === "function") {
          runtimeWindow.live2dSetMouthOpen(Math.max(0, Math.min(1, value)));
        }
        return;
      }
      const manager = window.__live2dManager;
      const model = manager && manager.getModel(0);
      if (!model || !model.initialized || typeof model.getLive2DModel !== "function") return;
      const liveModel = model.getLive2DModel();
      if (!liveModel || typeof liveModel.getParamIndex !== "function") return;
      if (liveModel.getParamIndex("PARAM_MOUTH_OPEN_Y") < 0) return;
      model.lipSync = null;
      model.lipSyncValue = Math.max(0, Math.min(1, value));
    } catch (error) {
      
    }
  }

  function stopMouthSync() {
    if (mouthAnimationFrame) cancelAnimationFrame(mouthAnimationFrame);
    mouthAnimationFrame = 0;
    setLive2DMouth(0);
  }

  function startMouthSync(analyser) {
    const floatSamples = new Float32Array(analyser.fftSize);
    const byteSamples = new Uint8Array(analyser.fftSize);
    let smoothedLevel = 0;
    const tick = () => {
      if (!audioPlaying) {
        smoothedLevel = 0;
        setLive2DMouth(0);
        mouthAnimationFrame = 0;
        return;
      }

      let sum = 0;
      if (typeof analyser.getFloatTimeDomainData === "function") {
        analyser.getFloatTimeDomainData(floatSamples);
        for (let i = 0; i < floatSamples.length; i++) {
          sum += floatSamples[i] * floatSamples[i];
        }
      } else {
        analyser.getByteTimeDomainData(byteSamples);
        for (let i = 0; i < byteSamples.length; i++) {
          const sample = (byteSamples[i] - 128) / 128;
          sum += sample * sample;
        }
      }

      // Use the actual decoded playback signal, not the requested TTS volume setting.
      const sampleCount = floatSamples.length;
      const rms = Math.sqrt(sum / sampleCount);
      const targetLevel = Math.max(0, Math.min(1, (rms - 0.008) * 11.5));
      // Fast attack and slower release make speech articulation visible without jitter.
      const smoothing = targetLevel > smoothedLevel ? 0.55 : 0.2;
      smoothedLevel += (targetLevel - smoothedLevel) * smoothing;
      if (smoothedLevel < 0.005) smoothedLevel = 0;

      setLive2DMouth(smoothedLevel);
      mouthAnimationFrame = requestAnimationFrame(tick);
    };
    if (mouthAnimationFrame) cancelAnimationFrame(mouthAnimationFrame);
    tick();
  }

  async function unlockSpeechAudio(options = {}) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error("当前浏览器不支持 Web Audio，无法进行嘴型同步。");
    if (!audioContext) {
      audioContext = new AudioContextClass();
      audioContext.onstatechange = () => {
        audioUnlocked = audioContext.state === "running";
        if (audioUnlocked) processSpeechQueue();
      };
    }
    if (audioContext.state !== "running") {
      try {
        await audioContext.resume();
      } catch (error) {
        audioUnlocked = false;
        if (!options.silent) throw error;
        return false;
      }
    }
    audioUnlocked = audioContext.state === "running";
    if (!audioUnlocked && !options.silent) {
      throw new Error("OBS 浏览器尚未允许自动播放；请检查 OBS 浏览器源的自动播放策略。");
    }
    return audioUnlocked;
  }

  async function initializeAutomaticAudio() {
    try {
      const ready = await unlockSpeechAudio({ silent: true });
      if (ready) {
        setTtsStatus("Web Audio 已自动启动，等待直播间连接。");
        processSpeechQueue();
      } else {
        setTtsStatus("已尝试自动启动 Web Audio；正在等待 OBS 浏览器允许播放。");
      }
    } catch (error) {
      console.warn("Automatic Web Audio initialization failed:", error);
      setTtsStatus("Web Audio 自动启动失败：" + error.message, true);
    }
  }


  function setIdleTipsStatus(message, isError = false) {
    if (!idleTipsStatus) return;
    idleTipsStatus.textContent = message;
    idleTipsStatus.classList.toggle("error", isError);
  }

  function linesToMessages(value) {
    return String(value || "").split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  }

  function messagesToLines(value) {
    return Array.isArray(value) ? value.join("\n") : "";
  }

  async function loadIdleTipSettings() {
    const response = await fetch("/api/danmaku/idle-tips/settings", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "无法读取空闲提示设置。");
    const p = data.preferences || {};
    idleTipSettings = p;
    if (idleTipsEnabled) idleTipsEnabled.checked = p.enabled !== false;
    if (idleTipsInterval) idleTipsInterval.value = String(p.interval_seconds ?? 60);
    if (idleTipsMessages) idleTipsMessages.value = messagesToLines(p.messages);
    for (const [key, field] of Object.entries(idleTimeFields)) {
      if (field) field.value = messagesToLines((p.time_messages || {})[key]);
    }
    syncIdleTipTimer(true);
  }

  function currentIdleTipPreferences() {
    const time_messages = {};
    for (const [key, field] of Object.entries(idleTimeFields)) {
      time_messages[key] = linesToMessages(field ? field.value : "");
    }
    return {
      enabled: Boolean(idleTipsEnabled && idleTipsEnabled.checked),
      interval_seconds: Number(idleTipsInterval ? idleTipsInterval.value : 60),
      messages: linesToMessages(idleTipsMessages ? idleTipsMessages.value : ""),
      time_messages
    };
  }

  async function saveIdleTipSettings() {
    if (idleTipsSaveButton) idleTipsSaveButton.disabled = true;
    setIdleTipsStatus("正在保存空闲提示设置…");
    try {
      const response = await fetch("/api/danmaku/idle-tips/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentIdleTipPreferences())
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "保存空闲提示设置失败。");
      idleTipSettings = data.preferences;
      if (idleTipsInterval) idleTipsInterval.value = String(idleTipSettings.interval_seconds);
      if (idleTipsMessages) idleTipsMessages.value = messagesToLines(idleTipSettings.messages);
      for (const [key, field] of Object.entries(idleTimeFields)) {
        if (field) field.value = messagesToLines(idleTipSettings.time_messages[key]);
      }
      syncIdleTipTimer(true);
      setIdleTipsStatus("空闲提示设置已保存，并已立即生效。");
    } catch (error) {
      setIdleTipsStatus("保存失败：" + error.message, true);
    } finally {
      if (idleTipsSaveButton) idleTipsSaveButton.disabled = false;
    }
  }

  function removeIdleTip() {
    if (idleTipElement) {
      idleTipElement.remove();
      idleTipElement = null;
    }
    if (bubble && !bubble.children.length) bubble.hidden = true;
  }

  function currentTimeMessageKey() {
    const hour = new Date().getHours();
    if (hour >= 5 && hour < 9) return "early_morning";
    if (hour >= 9 && hour < 12) return "morning";
    if (hour >= 12 && hour < 13) return "lunch";
    if (hour >= 13 && hour < 14) return "after_lunch";
    if (hour >= 14 && hour < 18) return "afternoon";
    if (hour >= 18 && hour < 19) return "evening";
    if (hour >= 19 && hour < 21) return "night";
    if (hour >= 21 && hour < 23) return "late_night";
    return "midnight";
  }

  function pickIdleTip() {
    if (!idleTipSettings) return "";
    const ordinary = Array.isArray(idleTipSettings.messages) ? idleTipSettings.messages : [];
    const timeMessages = idleTipSettings.time_messages || {};
    const timed = Array.isArray(timeMessages[currentTimeMessageKey()])
      ? timeMessages[currentTimeMessageKey()] : [];
    const pool = [...ordinary, ...timed].filter(value => typeof value === "string" && value.trim());
    if (!pool.length) return "";
    const candidates = pool.length > 1 && lastIdleTipText
      ? pool.filter(value => value !== lastIdleTipText) : pool;
    const selected = candidates[Math.floor(Math.random() * candidates.length)] || pool[0];
    lastIdleTipText = selected;
    return selected;
  }

  function showIdleTip() {
    if (!bubble || !idleConnected || !idleTipSettings || idleTipSettings.enabled === false) return;
    const message = pickIdleTip();
    if (!message) return;
    removeIdleTip();
    const wrapper = document.createElement("div");
    wrapper.className = "danmaku-entry idle-tip-entry";
    const text = document.createElement("span");
    text.className = "danmaku-text";
    text.textContent = message;
    wrapper.appendChild(text);
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
      if (oldest === idleTipElement) idleTipElement = null;
      oldest.remove();
    }
    idleTipElement = wrapper;
    window.setTimeout(() => {
      if (idleTipElement === wrapper) idleTipElement = null;
      wrapper.remove();
      if (!bubble.children.length) bubble.hidden = true;
    }, p.bubble_duration * 1000);
  }

  function syncIdleTipTimer(reset = false) {
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
    if (!live2dModuleActive || !idleConnected || !idleTipSettings || idleTipSettings.enabled === false) {
      removeIdleTip();
      return;
    }
    if (!reset) return;
    const interval = Math.max(10, Number(idleTipSettings.interval_seconds) || 60) * 1000;
    idleTimer = window.setTimeout(() => {
      idleTimer = null;
      if (!live2dModuleActive || !idleConnected || !idleTipSettings || idleTipSettings.enabled === false) return;
      showIdleTip();
      syncIdleTipTimer(true);
    }, interval);
  }

  function setIdleConnectionState(connected) {
    const next = Boolean(connected);
    if (next === idleConnected) return;
    idleConnected = next;
    syncIdleTipTimer(true);
  }

  function onIncomingLiveEvent() {
    removeIdleTip();
    syncIdleTipTimer(true);
  }

  function applyModuleStates(modules) {
    const liveState = modules && modules.live2d ? modules.live2d.state : "stopped";
    const voiceState = modules && modules.voice ? modules.voice.state : "stopped";
    live2dModuleState = liveState || "stopped";
    // 弹幕展示不依赖 Live2D 总开关；关闭模型后，弹幕框和空闲提示仍可工作。
    const nextLive2D = showDanmaku || (showLive2D && live2dModuleState === "running");
    const nextVoice = showTts && voiceState === "running";

    if (live2dModuleActive !== nextLive2D) {
      live2dModuleActive = nextLive2D;
      if (!live2dModuleActive) {
        removeIdleTip();
        if (bubble) bubble.hidden = true;
        if (bubblePreview) bubblePreview.hidden = true;
      }
      syncIdleTipTimer(true);
    }
    if (canvas) canvas.style.visibility = showLive2D && live2dModuleState !== "stopped" ? "visible" : "hidden";

    if (voiceModuleActive && !nextVoice) {
      speechQueue.length = 0;
      currentSpeech = null;
      publishSpeechState();
      if (currentAudioSource) {
        try { currentAudioSource.stop(); } catch (_) {}
      }
      audioPlaying = false;
      stopMouthSync();
    }
    voiceModuleActive = nextVoice;
    if (voiceModuleActive && audioUnlocked) processSpeechQueue();
  }

  async function checkLiveConnectionForAudio() {
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      const connected = Boolean(data.bilibili && data.bilibili.connected);
      if (data.live2d && Boolean(data.live2d.enabled) !== live2dEnabledAtLoad) {
        window.location.reload();
        return;
      }
      applyModuleStates(data.modules || {});
      if (showDanmaku) setIdleConnectionState(connected);
      if (showTts && connected !== lastConnectionState) {
        lastConnectionState = connected;
        if (connected) {
          setTtsStatus("直播间已连接，正在自动启动语音播报。");
          await unlockSpeechAudio({ silent: true });
          processSpeechQueue();
        } else if (lastConnectionState === false) {
          setTtsStatus("等待直播间连接；语音将在连接后自动启动。");
        }
      } else if (showTts && connected && !audioUnlocked) {
        await unlockSpeechAudio({ silent: true });
        processSpeechQueue();
      }
    } catch (error) {
      
    }
  }

  async function playSpeechText(text) {
    await unlockSpeechAudio();
    const response = await fetch("/api/voice/tts/synthesize", {
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
    analyser.smoothingTimeConstant = 0.72;
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
    if (!voiceModuleActive || speaking || !speechQueue.length) return;
    speaking = true;
    publishSpeechState();
    try {
      if (!audioUnlocked) {
        const ready = await unlockSpeechAudio({ silent: true });
        if (!ready) return;
      }
      while (speechQueue.length) {
        const next = speechQueue.shift();
        currentSpeech = next;
        publishSpeechState();
        try {
          await playSpeechText(next.text);
          currentSpeech = null;
          publishSpeechState();
        } catch (error) {
          currentSpeech = null;
          publishSpeechState();
          setTtsStatus("语音播放失败：" + error.message, true);
          console.warn("TTS playback failed:", error);
          if (/允许播放|支持 Web Audio|自动播放|suspended/i.test(error.message)) {
            speechQueue.unshift(next);
            publishSpeechState();
            audioUnlocked = false;
            break;
          }
        }
      }
    } catch (error) {
      audioUnlocked = false;
      console.warn("Unable to start speech queue:", error);
    } finally {
      speaking = false;
      currentSpeech = null;
      publishSpeechState();
    }
  }

  async function testSpeech() {
    try {
      await unlockSpeechAudio();
      const saveResponse = await fetch("/api/voice/tts/settings", {
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
    publishSpeechState();
    processSpeechQueue();
    return true;
  }

  function showMessage(message) {
    if (placeholder) {
      placeholder.textContent = message;
      placeholder.style.display = message ? "block" : "none";
    }
  }

  function modelConfigUrl(modelId, profile = currentModelProfile) {
    const encodedId = encodeURIComponent(modelId);
    if (live2dRuntimeVersion !== "moc2") {
      return "/models/" + live2dRuntimeVersion + "/" + encodedId + "/model.json";
    }
    const base = "/models/" + encodedId + "/model.json";
    return profile ? base + "?profile=" + encodeURIComponent(profile) : base;
  }

  function populateModelProfiles(profiles, selectedProfile) {
    if (!modelProfileSelect || !modelProfileField) return;
    modelProfileSelect.replaceChildren();
    const entries = Array.isArray(profiles) ? profiles : [];
    for (const profile of entries) {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = profile.name && profile.name !== profile.id
        ? profile.name + "（" + profile.id + "）"
        : profile.id;
      modelProfileSelect.appendChild(option);
    }
    currentModelProfile = entries.some(item => item.id === selectedProfile)
      ? selectedProfile
      : (entries[0] ? entries[0].id : "");
    modelProfileSelect.value = currentModelProfile;
    modelProfileField.hidden = live2dRuntimeVersion !== "moc2" || entries.length <= 1;
  }

  async function loadMoc3Model(modelId, token = null) {
    if (!moc3Frame) throw new Error("MOC3 展示框架不存在。");
    if (!modelId) throw new Error("尚未选择有效的 MOC3 模型。");
    const isStale = () => token !== null && token !== modelSwitchToken;
    setMouthSyncStatus("正在检测当前模型的 TTS 嘴型同步参数…", "checking");
    showMessage("正在加载 Cubism 5 MOC3 模型：" + modelId + "…");
    await reportState("loading", "正在加载 Cubism 5 R5 模型：" + modelId);
    const loaded = await new Promise((resolve, reject) => {
      let frameLoaded = false;
      let modelLoaded = false;
      const expectedUrl = modelConfigUrl(modelId);
      const cleanup = () => {
        window.clearTimeout(timeout);
        moc3Frame.removeEventListener("load", onLoad);
        window.removeEventListener("message", onMessage);
      };
      const finish = () => {
        if (isStale()) {
          cleanup();
          resolve(false);
          return;
        }
        if (frameLoaded && modelLoaded) {
          cleanup();
          resolve(true);
        }
      };
      const timeout = window.setTimeout(() => {
        cleanup();
        reject(new Error("MOC3 模型加载超时；请检查模型资源和浏览器控制台。"));
      }, 45000);
      const onLoad = () => {
        if (isStale()) {
          cleanup();
          resolve(false);
          return;
        }
        const runtimeWindow = moc3Frame.contentWindow;
        if (!runtimeWindow || typeof runtimeWindow.live2dLoadModel !== "function" ||
            typeof runtimeWindow.live2dSetMouthOpen !== "function") {
          cleanup();
          reject(new Error("Cubism 5 R5 运行时未能完成初始化。"));
          return;
        }
        frameLoaded = true;
        finish();
      };
      const onMessage = event => {
        if (isStale()) {
          cleanup();
          resolve(false);
          return;
        }
        if (event.origin !== window.location.origin ||
            event.source !== moc3Frame.contentWindow ||
            !event.data || event.data.modelUrl !== expectedUrl) return;
        if (event.data.type === "moc3-model-error") {
          cleanup();
          const resource = event.data.resource ? "（资源：" + event.data.resource + "）" : "";
          reject(new Error((event.data.message || "MOC3 模型资源加载失败") + resource));
          return;
        }
        if (event.data.type !== "moc3-model-loaded") return;
        modelLoaded = true;
        finish();
      };
      moc3Frame.addEventListener("load", onLoad, { once: true });
      window.addEventListener("message", onMessage);
      moc3Frame.src = "/live2d/moc3/runtime/" + encodeURIComponent(modelId);
    });
    if (!loaded || isStale()) return false;
    loadedModelId = modelId;
    applyLive2DTransform();
    showMessage("");
    if (placeholder) placeholder.style.display = "none";
    await reportState("loaded", modelId + " 已由 Cubism 5 R5 Framework 完成模型和纹理加载。");
    return true;
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

  function setModelActionStatus(message, isError = false) {
    if (!modelActionStatusElement) return;
    modelActionStatusElement.textContent = message;
    modelActionStatusElement.classList.toggle("error", isError);
  }

  function setMouthSyncStatus(message, state = "checking") {
    if (!mouthSyncStatusElement) return;
    mouthSyncStatusElement.textContent = message;
    mouthSyncStatusElement.dataset.state = state;
    mouthSyncStatusElement.classList.toggle("error", state === "unsupported");
  }

  window.addEventListener("message", event => {
    if (live2dRuntimeVersion !== "moc3" || !moc3Frame ||
        event.origin !== window.location.origin || event.source !== moc3Frame.contentWindow ||
        !event.data || event.data.type !== "moc3-mouth-sync-status") return;
    const currentModelId = modelSelect && modelSelect.value;
    if (!currentModelId || event.data.modelUrl !== modelConfigUrl(currentModelId)) return;
    lastMoc3MouthSyncStatus = event.data;
    const capabilityRows = modelCapabilitiesElement ? Array.from(modelCapabilitiesElement.querySelectorAll(".model-capability-row")) : [];
    const mouthCapability = capabilityRows.find(item => item.firstElementChild && item.firstElementChild.textContent === "TTS 嘴型同步");
    if (mouthCapability && mouthCapability.lastElementChild) {
      const invalidIds = event.data.invalidDeclaredIds || [];
      mouthCapability.lastElementChild.textContent = event.data.supported
        ? "支持；检测到参数 " + (event.data.parameterIds || []).join("、") + (invalidIds.length ? "；无效声明：" + invalidIds.join("、") : "")
        : "不支持；" + (event.data.reason || "没有可用嘴型参数") + (invalidIds.length ? "；无效声明：" + invalidIds.join("、") : "");
      mouthCapability.lastElementChild.dataset.state = event.data.supported ? "ok" : "warn";
    }
    if (event.data.supported) {
      const ids = Array.isArray(event.data.parameterIds) ? event.data.parameterIds : [];
      const source = event.data.source === "model3-lipsync"
        ? "模型配置 LipSync 参数组"
        : "兼容嘴型参数检测";
      let message = "支持 TTS 嘴型同步（" + source + "：" + ids.join("、") + "）。";
      const invalid = Array.isArray(event.data.invalidDeclaredIds) ? event.data.invalidDeclaredIds : [];
      if (invalid.length) message += " 以下配置参数未通过验证，已跳过：" + invalid.join("、") + "。";
      setMouthSyncStatus(message, "supported");
    } else {
      const declared = Array.isArray(event.data.declaredIds) ? event.data.declaredIds : [];
      const invalid = Array.isArray(event.data.invalidDeclaredIds) ? event.data.invalidDeclaredIds : [];
      let detail = event.data.reason || "未找到可用的嘴型参数。";
      if (declared.length) detail = "模型声明了 LipSync 参数 " + declared.join("、") + "，但这些参数未能通过实际参数验证；" + detail;
      else detail = "模型没有声明 LipSync 参数组；" + detail;
      if (invalid.length) detail += " 无效参数：" + invalid.join("、") + "。";
      setMouthSyncStatus("当前模型无法进行 TTS 嘴型同步。" + detail, "unsupported");
    }
  });

  function updateModelActionAvailability() {
    const sameModel = Boolean(loadedModelId && modelSelect && modelSelect.value === loadedModelId);
    const ready = sameModel && statusSent;
    if (motionSelect) motionSelect.disabled = !ready || motionSelect.options.length <= 1;
    if (motionPlayButton) motionPlayButton.disabled = !ready || !motionSelect || !motionSelect.value;
    if (expressionSelect) expressionSelect.disabled = !ready || expressionSelect.options.length <= 1;
    if (expressionApplyButton) expressionApplyButton.disabled = !ready || !expressionSelect || !expressionSelect.value;
    const expressionEntries = live2dRuntimeVersion === "moc3"
      ? selectedModelConfig && selectedModelConfig.FileReferences && selectedModelConfig.FileReferences.Expressions
      : selectedModelConfig && selectedModelConfig.expressions;
    if (expressionResetButton) expressionResetButton.disabled = !ready || !Array.isArray(expressionEntries) || !expressionEntries.length;
  }

  function populateModelActions(config, modelId) {
    selectedModelConfig = config && typeof config === "object" ? config : {};
    const moc3References = live2dRuntimeVersion === "moc3" &&
      selectedModelConfig.FileReferences && typeof selectedModelConfig.FileReferences === "object"
      ? selectedModelConfig.FileReferences : null;
    const groups = moc3References
      ? (moc3References.Motions && typeof moc3References.Motions === "object" ? moc3References.Motions : {})
      : (selectedModelConfig.motions && typeof selectedModelConfig.motions === "object" ? selectedModelConfig.motions : {});
    if (motionSelect) {
      motionSelect.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      const entries = [];
      for (const [group, motions] of Object.entries(groups)) {
        if (!Array.isArray(motions)) continue;
        motions.forEach((motion, index) => {
          const file = motion && (motion.file || motion.File);
          if (typeof file !== "string" || !file) return;
          entries.push({ group, index, file });
        });
      }
      placeholder.textContent = entries.length ? "请选择动作" : "当前模型没有配置动作";
      motionSelect.appendChild(placeholder);
      for (const entry of entries) {
        const option = document.createElement("option");
        option.value = entry.group + "::" + entry.index;
        option.textContent = (entry.group || "默认动作组") + " / " + entry.file.split("/").pop();
        motionSelect.appendChild(option);
      }
    }
    if (expressionSelect) {
      expressionSelect.replaceChildren();
      const placeholder = document.createElement("option");
      placeholder.value = "";
      const rawExpressions = moc3References ? moc3References.Expressions : selectedModelConfig.expressions;
      const expressions = Array.isArray(rawExpressions)
        ? rawExpressions.map(item => ({
            name: item && (item.name || item.Name),
            file: item && (item.file || item.File)
          })).filter(item => typeof item.file === "string" && item.file)
        : [];
      placeholder.textContent = expressions.length ? "请选择表情" : "当前模型没有配置独立表情";
      expressionSelect.appendChild(placeholder);
      for (const expression of expressions) {
        const option = document.createElement("option");
        option.value = expression.name || expression.file;
        option.textContent = expression.name || expression.file.split("/").pop();
        expressionSelect.appendChild(option);
      }
    }
    if (modelActionStatusElement) {
      const motionCount = motionSelect ? Math.max(0, motionSelect.options.length - 1) : 0;
      const expressionCount = expressionSelect ? Math.max(0, expressionSelect.options.length - 1) : 0;
      modelActionStatusElement.textContent = modelId + "：读取到 " + motionCount + " 个动作、" + expressionCount + " 个表情。";
    }
    renderMotionShortcutEditor();
    updateModelActionAvailability();
  }

  function normalizeMotionShortcut(key) {
    const value = String(key || "").toUpperCase();
    return /^[A-Z0-9]$/.test(value) || /^F(?:[1-9]|1[0-2])$/.test(value) ? value : "";
  }

  function renderMotionShortcutEditor() {
    if (!motionShortcutsContainer || !motionSelect) return;
    motionShortcutsContainer.replaceChildren();
    const heading = document.createElement("strong");
    heading.className = "settings-subtitle";
    heading.textContent = "动作快捷键";
    motionShortcutsContainer.appendChild(heading);
    const hint = document.createElement("p");
    hint.className = "settings-help";
    hint.textContent = "点击输入框后按下单个数字、字母或 F1-F12 绑定；同一模型配置内不能重复，完成后点击下方“保存设置”。";
    motionShortcutsContainer.appendChild(hint);
    for (const option of Array.from(motionSelect.options).slice(1)) {
      const action = option.value;
      if (!action) continue;
      const row = document.createElement("div");
      row.className = "model-motion-shortcut-row";
      const label = document.createElement("span");
      label.textContent = option.textContent;
      const input = document.createElement("input");
      input.type = "text";
      input.readOnly = true;
      input.placeholder = "点击后按键";
      input.value = motionShortcutBindings[action] || "";
      input.setAttribute("aria-label", option.textContent + "快捷键");
      input.addEventListener("keydown", event => {
        event.preventDefault();
        event.stopPropagation();
        if (event.key === "Escape" || event.key === "Backspace" || event.key === "Delete") {
          delete motionShortcutBindings[action];
          input.value = "";
          markUnsaved();
          return;
        }
        if (event.ctrlKey || event.altKey || event.metaKey) return;
        const key = normalizeMotionShortcut(event.key);
        if (!key) return;
        const duplicate = Object.entries(motionShortcutBindings).find(([otherAction, otherKey]) => otherAction !== action && otherKey === key);
        if (duplicate) {
          setModelActionStatus("快捷键 " + key + " 已绑定到其他动作，请先清除原绑定。", true);
          return;
        }
        motionShortcutBindings[action] = key;
        input.value = key;
        markUnsaved();
      });
      const clear = document.createElement("button");
      clear.type = "button";
      clear.className = "secondary-button";
      clear.textContent = "清除";
      clear.addEventListener("click", () => {
        delete motionShortcutBindings[action];
        input.value = "";
        markUnsaved();
      });
      row.append(label, input, clear);
      motionShortcutsContainer.appendChild(row);
    }
  }

  function isShortcutTypingTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(target.closest("input, textarea, select, [contenteditable='true'], [role='textbox']"));
  }

  document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.repeat || event.ctrlKey || event.altKey || event.metaKey ||
        isShortcutTypingTarget(event.target)) return;
    const key = normalizeMotionShortcut(event.key);
    if (!key) return;
    const match = Object.entries(motionShortcutBindings).find(([, bound]) => bound === key);
    if (!match) return;
    const action = match[0];
    const option = motionSelect && Array.from(motionSelect.options).find(item => item.value === action);
    if (!option || !loadedModelId || !modelSelect || modelSelect.value !== loadedModelId || !statusSent) return;
    motionSelect.value = action;
    updateModelActionAvailability();
    event.preventDefault();
    playSelectedModelMotion();
  });

  function renderModelCapabilityRows(rows) {
    if (!modelCapabilitiesElement) return;
    modelCapabilitiesElement.replaceChildren();
    for (const row of rows) {
      const item = document.createElement("div");
      item.className = "model-capability-row";
      const label = document.createElement("strong");
      label.textContent = row.label;
      const value = document.createElement("span");
      value.textContent = row.value;
      value.dataset.state = row.state || "info";
      item.append(label, value);
      modelCapabilitiesElement.appendChild(item);
    }
  }

  async function diagnoseModelCapabilities(config, modelId, token = null) {
    if (!modelCapabilitiesElement) return;
    const isStale = () => token !== null && token !== modelSwitchToken;
    const moc3 = live2dRuntimeVersion === "moc3";
    const refs = moc3 && config.FileReferences && typeof config.FileReferences === "object"
      ? config.FileReferences : null;
    const motions = refs ? (refs.Motions || {}) : (config.motions || {});
    const expressions = refs ? (refs.Expressions || []) : (config.expressions || []);
    const hitAreas = refs ? (config.HitAreas || []) : (config.hit_areas || config.hitAreas || []);
    const baseUrl = modelConfigUrl(modelId);
    const assetUrl = file => new URL(String(file || ""), new URL(baseUrl, window.location.href)).href;
    const checkAsset = async file => {
      if (typeof file !== "string" || !file) return false;
      try {
        const response = await fetch(assetUrl(file), { method: "HEAD", cache: "no-store" });
        return response.ok;
      } catch (_) { return false; }
    };
    const motionEntries = [];
    for (const [group, items] of Object.entries(motions || {})) {
      if (!Array.isArray(items)) continue;
      items.forEach((item, index) => {
        const file = item && (item.File || item.file);
        if (file) motionEntries.push({ group, index, file });
      });
    }
    const validMotions = await Promise.all(motionEntries.map(async item => ({ ...item, valid: await checkAsset(item.file) })));
    const idle = validMotions.filter(item => item.group.toLowerCase() === "idle");
    const expressionEntries = Array.isArray(expressions) ? expressions.map(item => item && (item.File || item.file)).filter(Boolean) : [];
    const validExpressions = await Promise.all(expressionEntries.map(checkAsset));
    const modelFiles = refs
      ? [refs.Moc, ...(Array.isArray(refs.Textures) ? refs.Textures : []), refs.Pose, refs.UserData].filter(Boolean)
      : [config.model, ...(Array.isArray(config.textures) ? config.textures : []), config.pose].filter(Boolean);
    const modelFileChecks = await Promise.all(modelFiles.map(async file => ({ file, valid: await checkAsset(file) })));
    const physicsFile = refs ? refs.Physics : config.physics;
    let physicsState = "未配置";
    if (physicsFile) {
      try {
        const response = await fetch(assetUrl(physicsFile), { cache: "no-store" });
        if (!response.ok) throw new Error("HTTP " + response.status);
        const data = await response.json();
        const valid = moc3
          ? Array.isArray(data.PhysicsSettings) && data.PhysicsSettings.length > 0
          : Boolean(data && typeof data === "object" && Object.keys(data).length > 0);
        physicsState = valid ? "有效配置" : "文件存在，但没有有效物理设置";
      } catch (error) {
        physicsState = "资源无效：" + error.message;
      }
    }
    const touchNames = Array.isArray(hitAreas) ? hitAreas.map(item => item && (item.Name || item.name)).filter(name => typeof name === "string" && name) : [];
    const mappedTouchNames = touchNames.map(name => {
      const group = name.startsWith("Touch") ? "Tap" + name : name === "Body" ? "TapBody" : "";
      const exists = validMotions.some(item => item.group === group && item.valid);
      return { name, group, exists };
    });
    const validTouchCount = mappedTouchNames.filter(item => item.exists || item.name === "Head" || (item.name === "Body" && validMotions.some(motion => motion.group === "TapBody" && motion.valid))).length;
    const invalidTouchMappings = mappedTouchNames.filter(item => item.name.startsWith("Touch") && !item.exists);
    const invalidAssets = [
      ...modelFileChecks.filter(item => !item.valid).map(item => item.file),
      ...validMotions.filter(item => !item.valid).map(item => item.file),
      ...expressionEntries.filter((file, index) => !validExpressions[index]),
      ...(physicsFile && physicsState !== "有效配置" ? [physicsFile + "（" + physicsState + "）"] : []),
      ...invalidTouchMappings.map(item => "HitArea " + item.name + " → Motion Group " + item.group)
    ];
    const rows = [
      { label: "Idle 动作", value: idle.length ? (idle.some(item => item.valid) ? "存在且资源可读取" : "已声明，但动作文件无效") : "未配置", state: idle.length && idle.some(item => item.valid) ? "ok" : "warn" },
      { label: "可播放动作", value: motionEntries.length ? validMotions.filter(item => item.valid).length + " / " + motionEntries.length + " 个动作资源有效" : "未配置", state: validMotions.some(item => item.valid) ? "ok" : "warn" },
      { label: "模型主体 / 贴图", value: modelFileChecks.filter(item => item.valid).length + " / " + modelFileChecks.length + " 个资源可读取", state: modelFileChecks.length && modelFileChecks.every(item => item.valid) ? "ok" : "warn" },
      { label: "表情", value: expressionEntries.length ? validExpressions.filter(Boolean).length + " / " + expressionEntries.length + " 个表情资源有效" : "未配置", state: expressionEntries.length && validExpressions.every(Boolean) ? "ok" : "warn" },
      { label: "物理配置", value: physicsState, state: physicsState === "有效配置" ? "ok" : physicsFile ? "warn" : "info" },
      { label: "触摸区域", value: touchNames.length ? touchNames.map(item => item).join("、") + "；可识别 " + validTouchCount + " / " + touchNames.length + (invalidTouchMappings.length ? "；未映射：" + invalidTouchMappings.map(item => item.name + "→" + item.group).join("、") : "") : "未配置", state: touchNames.length && validTouchCount === touchNames.length ? "ok" : "warn" },
      { label: "TTS 嘴型同步", value: moc3 ? "等待 Cubism 运行时参数检测" : "等待 MOC2 模型参数检测", state: "checking" },
      { label: "无效资源", value: invalidAssets.length ? [...new Set(invalidAssets)].join("、") : "未发现已声明资源缺失", state: invalidAssets.length ? "warn" : "ok" }
    ];
    if (isStale()) return;
    renderModelCapabilityRows(rows);
    if (moc3 && lastMoc3MouthSyncStatus && lastMoc3MouthSyncStatus.modelUrl === modelConfigUrl(modelId)) {
      const mouth = rows.find(row => row.label === "TTS 嘴型同步");
      if (mouth) {
        const invalidIds = lastMoc3MouthSyncStatus.invalidDeclaredIds || [];
        mouth.value = lastMoc3MouthSyncStatus.supported
          ? "支持；检测到参数 " + (lastMoc3MouthSyncStatus.parameterIds || []).join("、") + (invalidIds.length ? "；无效声明：" + invalidIds.join("、") : "")
          : "不支持；" + (lastMoc3MouthSyncStatus.reason || "没有可用嘴型参数") + (invalidIds.length ? "；无效声明：" + invalidIds.join("、") : "");
        mouth.state = lastMoc3MouthSyncStatus.supported ? "ok" : "warn";
        renderModelCapabilityRows(rows);
      }
    }
    if (!moc3) {
      const model = window.__live2dManager && window.__live2dManager.getModel(0);
      const liveModel = model && model.live2DModel;
      const candidates = Array.isArray(config.lip_sync) ? config.lip_sync : ["PARAM_MOUTH_OPEN_Y"];
      const parameter = liveModel && typeof liveModel.getParamIndex === "function"
        ? candidates.find(id => liveModel.getParamIndex(id) >= 0) : null;
      const mouth = rows.find(row => row.label === "TTS 嘴型同步");
      if (mouth) {
        mouth.value = parameter ? "支持；检测到参数 " + parameter : "未检测到可用嘴型参数";
        mouth.state = parameter ? "ok" : "warn";
        renderModelCapabilityRows(rows);
      }
    }
    return { motionEntries, validMotions, expressionEntries, validExpressions, touchNames, mappedTouchNames, invalidAssets };
  }

  async function loadModelActionConfig(modelId, token = null) {
    const url = modelConfigUrl(modelId);
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error("无法读取模型动作配置（HTTP " + response.status + "）。");
    const config = await response.json();
    if (token !== null && token !== modelSwitchToken) return false;
    populateModelActions(config, modelId);
    await diagnoseModelCapabilities(config, modelId, token);
    return token === null || token === modelSwitchToken;
  }

  async function playSelectedModelMotion() {
    if (!loadedModelId || !modelSelect || modelSelect.value !== loadedModelId) {
      setModelActionStatus("模型正在加载，完成后即可操作动作和表情。", true);
      return;
    }
    const value = motionSelect && motionSelect.value;
    if (!value) return;
    const separator = value.lastIndexOf("::");
    const group = value.slice(0, separator);
    const index = Number(value.slice(separator + 2));
    try {
      if (live2dRuntimeVersion === "moc3") {
        const runtimeWindow = moc3Frame && moc3Frame.contentWindow;
        if (!runtimeWindow || typeof runtimeWindow.live2dPlayMotion !== "function") {
          setModelActionStatus("MOC3 动作接口尚未就绪。", true);
          return;
        }
        const result = await runtimeWindow.live2dPlayMotion(group, index);
        if (result === false || result === -1) throw new Error("运行时未能启动该动作，请检查动作资源和运行时缓存。");
      } else {
        const model = window.__live2dManager && window.__live2dManager.getModel(0);
        if (!model || !model.initialized || typeof model.startMotion !== "function") {
          setModelActionStatus("Live2D 模型尚未完成初始化。", true);
          return;
        }
        // Cubism 2 priority 4 is FORCE.
        model.startMotion(group, index, 4);
      }
      setModelActionStatus("已播放动作：" + group + " / " + motionSelect.selectedOptions[0].textContent);
    } catch (error) {
      setModelActionStatus("动作播放失败：" + error.message, true);
    }
  }

  function applySelectedModelExpression() {
    if (!loadedModelId || !modelSelect || modelSelect.value !== loadedModelId) {
      setModelActionStatus("模型正在加载，完成后即可操作动作和表情。", true);
      return;
    }
    const name = expressionSelect && expressionSelect.value;
    if (!name) return;
    try {
      if (live2dRuntimeVersion === "moc3") {
        const runtimeWindow = moc3Frame && moc3Frame.contentWindow;
        if (!runtimeWindow || typeof runtimeWindow.live2dSetExpression !== "function") {
          setModelActionStatus("MOC3 表情接口尚未就绪。", true);
          return;
        }
        if (!runtimeWindow.live2dSetExpression(name)) throw new Error("当前模型未找到该表情。");
      } else {
        const model = window.__live2dManager && window.__live2dManager.getModel(0);
        if (!model || !model.initialized || typeof model.setExpression !== "function") {
          setModelActionStatus("Live2D 模型尚未完成初始化。", true);
          return;
        }
        if (!model.expressions || !model.expressions[name]) {
          setModelActionStatus("表情资源尚未加载完成，请稍后再试。", true);
          return;
        }
        model.setExpression(name);
      }
      setModelActionStatus("已切换表情：" + name);
    } catch (error) {
      setModelActionStatus("表情切换失败：" + error.message, true);
    }
  }

  function resetSelectedModelExpression() {
    if (!loadedModelId || !modelSelect || modelSelect.value !== loadedModelId) return;
    if (live2dRuntimeVersion === "moc3") {
      const runtimeWindow = moc3Frame && moc3Frame.contentWindow;
      if (!runtimeWindow || typeof runtimeWindow.live2dResetExpression !== "function" ||
          !runtimeWindow.live2dResetExpression()) {
        setModelActionStatus("当前 MOC3 模型不支持恢复默认表情。", true);
        return;
      }
      setModelActionStatus("已恢复模型默认表情。");
      return;
    }
    const model = window.__live2dManager && window.__live2dManager.getModel(0);
    if (!model || !model.expressionManager || typeof model.expressionManager.stopAllMotions !== "function") {
      setModelActionStatus("当前模型不支持恢复默认表情。", true);
      return;
    }
    model.expressionManager.stopAllMotions();
    setModelActionStatus("已恢复模型默认表情。");
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
      profile: currentModelProfile,
      motion_shortcuts: motionShortcutBindings,
      scale: Number(controls.scale.value) / 100,
      x: Number(controls.x.value) / 100,
      y: Number(controls.y.value) / 100,
      crop_top: Number(controls["crop-top"].value),
      crop_right: Number(controls["crop-right"].value),
      crop_bottom: Number(controls["crop-bottom"].value),
      crop_left: Number(controls["crop-left"].value),
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
    for (const key of ["crop-top", "crop-right", "crop-bottom", "crop-left"]) {
      outputs[key].textContent = controls[key].value + "%";
    }
    outputs["bubble-x"].textContent = controls["bubble-x"].value + "%";
    outputs["bubble-bottom"].textContent = controls["bubble-bottom"].value + "%";
    outputs["bubble-width"].textContent = controls["bubble-width"].value + "px";
    outputs["bubble-height"].textContent = controls["bubble-height"].value + "px";
    outputs["bubble-items"].textContent = controls["bubble-items"].value + " 条";
    outputs["bubble-duration"].textContent = controls["bubble-duration"].value + " 秒";
    outputs["bubble-font"].textContent = controls["bubble-font"].value + "px";
  }

  function applyLive2DCrop() {
    if (!canvas) return;
    const p = currentPreferences();
    const inset = [p.crop_top, p.crop_right, p.crop_bottom, p.crop_left]
      .map(value => Math.max(0, Math.min(45, Number(value) || 0)) + "%")
      .join(" ");
    const clip = "inset(" + inset + ")";
    const target = live2dViewport || canvas;
    target.style.clipPath = clip;
    target.style.webkitClipPath = clip;
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
    let settingsSaved = false;
    if (saveButton) saveButton.disabled = true;
    setSaveStatus("正在保存设置…");
    try {
      // 等待当前模型切换完成，避免保存与模型替换并发执行。
      await modelSwitchPromise.catch(() => {});
      const preferences = currentPreferences();
      const response = await fetch("/api/live2d/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...preferences, version: live2dRuntimeVersion, refresh_obs: false })
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "保存失败。");
      settingsSaved = true;
      hasUnsavedChanges = false;
      const obs = result.obs_refresh || {};
      setSaveStatus("设置已保存，正在刷新展示页面。" + (obs.message ? " " + obs.message : ""));
      // MOC3 展示页包含独立 runtime iframe。使用带缓存隔离参数的顶层导航，
      // 确保保存后重新创建整个展示页及 runtime，而不只是保留旧 iframe。
      window.setTimeout(() => {
        if (live2dRuntimeVersion === "moc3") {
          const refreshUrl = new URL(window.location.href);
          refreshUrl.searchParams.set("_live2d_refresh", String(Date.now()));
          window.location.replace(refreshUrl.toString());
          return;
        }
        window.location.reload();
      }, 350);

      // 保存只负责持久化；不要重置当前模型的原始矩阵基准。
    } catch (error) {
      setSaveStatus((settingsSaved ? "设置已保存，但模型刷新失败：" : "保存失败：") + error.message, true);
    } finally {
      isSaving = false;
      if (saveButton) saveButton.disabled = false;
    }
  }

  function markUnsaved() {
    updateSliderLabels();
    applyBubblePreferences();
    applyLive2DCrop();
    applyLive2DTransform();
    hasUnsavedChanges = true;
    setSaveStatus("有未保存的更改；点击下方“保存设置”后才会保存并尝试刷新 OBS。");
  }

  async function loadDisplaySettings() {
    const response = await fetch("/api/live2d/settings?version=" + encodeURIComponent(live2dRuntimeVersion), { cache: "no-store" });
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
    motionShortcutBindings = p.motion_shortcuts && typeof p.motion_shortcuts === "object" ? { ...p.motion_shortcuts } : {};
    populateModelProfiles(data.profiles, p.profile);
    controls.scale.value = String(Math.round((Number(p.scale) || 1) * 100));
    controls.x.value = String(Math.round((Number(p.x) || 0) * 100));
    controls.y.value = String(Math.round((Number(p.y) || 0) * 100));
    controls["crop-top"].value = String(p.crop_top ?? 0);
    controls["crop-right"].value = String(p.crop_right ?? 0);
    controls["crop-bottom"].value = String(p.crop_bottom ?? 0);
    controls["crop-left"].value = String(p.crop_left ?? 0);
    controls["bubble-x"].value = String(p.bubble_x ?? 50);
    controls["bubble-bottom"].value = String(p.bubble_bottom ?? 45);
    controls["bubble-width"].value = String(p.bubble_width ?? 420);
    controls["bubble-height"].value = String(p.bubble_max_height ?? 320);
    controls["bubble-items"].value = String(p.bubble_max_items ?? 5);
    controls["bubble-duration"].value = String(p.bubble_duration ?? 10);
    controls["bubble-font"].value = String(p.bubble_font_size ?? 20);
    updateSliderLabels();
    applyBubblePreferences();
    applyLive2DCrop();
    applyLive2DTransform();
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

  let pollInProgress = false;
  let danmakuCursorInitialized = false;
  let cursorInitializationInProgress = false;

  async function pollDanmaku() {
    if (!danmakuCursorInitialized || pollInProgress) return;
    pollInProgress = true;
    try {
      const response = await fetch("/api/danmaku?after=" + lastDanmakuId, { cache: "no-store" });
      if (!response.ok) return;
      const data = await response.json();
      const incomingItems = data.items || [];
      if (incomingItems.length && showDanmaku) onIncomingLiveEvent();
      for (const item of incomingItems) {
        lastDanmakuId = Math.max(lastDanmakuId, Number(item.id) || 0);
        // 弹幕显示与 TTS 完全独立；语音处理异常不得中断后续弹幕显示。
        try {
          if (showDanmaku && live2dModuleActive) addDanmaku(item);
        } catch (displayError) {
          console.warn("Unable to display danmaku:", displayError);
        }
        try {
          if (showTts && voiceModuleActive) enqueueSpeech(item);
        } catch (speechError) {
          console.warn("Unable to enqueue danmaku speech:", speechError);
        }
      }
    } catch (error) {
      
    } finally {
      pollInProgress = false;
    }
  }

  async function initializeDanmakuCursor() {
    if (danmakuCursorInitialized || cursorInitializationInProgress) return;
    cursorInitializationInProgress = true;
    try {
      const response = await fetch("/api/danmaku?latest=1", { cache: "no-store" });
      if (!response.ok) throw new Error("无法初始化弹幕游标（HTTP " + response.status + "）。");
      const data = await response.json();
      lastDanmakuId = Number(data.latest_id) || 0;
      danmakuCursorInitialized = true;
      await pollDanmaku();
    } catch (error) {
      console.debug("Unable to initialize danmaku cursor; will retry:", error);
    } finally {
      cursorInitializationInProgress = false;
    }
  }

  function refreshDanmakuPolling() {
    if (!danmakuCursorInitialized) {
      initializeDanmakuCursor();
      return;
    }
    pollDanmaku();
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

  function transformSnapshot(preferences = currentPreferences()) {
    return {
      scale: Number(preferences.scale) || 1,
      x: Number(preferences.x) || 0,
      y: Number(preferences.y) || 0
    };
  }

  function captureLive2DTransformBase() {
    const manager = window.__live2dManager;
    const model = manager && typeof manager.getModel === "function" ? manager.getModel(0) : null;
    const matrix = model && typeof model.getModelMatrix === "function" ? model.getModelMatrix() : null;
    if (!matrix || !ArrayBuffer.isView(matrix.tr) || matrix.tr.length < 16 ||
        typeof matrix.getScaleX !== "function" || typeof matrix.getScaleY !== "function" ||
        typeof matrix.scale !== "function" || typeof matrix.centerX !== "function" ||
        typeof matrix.centerY !== "function") {
      live2dTransformBase = null;
      console.warn("Live2D 实时变换基准尚不可用：模型矩阵接口不完整。");
      return false;
    }

    const width = Number(matrix.width);
    const height = Number(matrix.height);
    const scaleX = Number(matrix.getScaleX());
    const scaleY = Number(matrix.getScaleY());
    if (![width, height, scaleX, scaleY, Number(matrix.tr[12]), Number(matrix.tr[13])]
      .every(Number.isFinite) || width <= 0 || height <= 0) {
      live2dTransformBase = null;
      console.warn("Live2D 实时变换基准尚不可用：模型矩阵数据无效。");
      return false;
    }

    // /models/<id>/model.json 已经把已保存的 scale/x/y 写入 layout，
    // Cubism 2 加载时也已经应用过一次。这里必须同时记录这组基准设置，
    // 后续实时预览只应用“当前值相对已保存值的差量”，不能重复应用完整设置。
    live2dTransformBase = {
      matrix,
      scaleX,
      scaleY,
      centerX: Number(matrix.tr[12]) + width * scaleX / 2,
      centerY: Number(matrix.tr[13]) + height * scaleY / 2,
      preferences: transformSnapshot()
    };
    return true;
  }

  function applyLive2DTransform(preferences = currentPreferences()) {
    const current = transformSnapshot(preferences);
    if (live2dRuntimeVersion === "moc3" && moc3Frame) {
      if (!Number.isFinite(current.scale) || current.scale <= 0) return false;
      const runtimeWindow = moc3Frame.contentWindow;
      if (!runtimeWindow || typeof runtimeWindow.live2dSetModelTransform !== "function") return false;
      return runtimeWindow.live2dSetModelTransform(current.scale, current.x, current.y) !== false;
    }
    const base = live2dTransformBase;
    if (!base || !base.matrix) return false;
    if (!Number.isFinite(current.scale) || current.scale <= 0) return false;

    // 模型矩阵已经包含后端应用的已保存设置，因此只叠加相对差量。
    // 每次都从加载完成时的矩阵计算，避免拖动滑块时累计误差。
    const saved = base.preferences || { scale: 1, x: 0, y: 0 };
    const scaleRatio = current.scale / saved.scale;
    if (!Number.isFinite(scaleRatio) || scaleRatio <= 0) return false;
    base.matrix.scale(base.scaleX * scaleRatio, base.scaleY * scaleRatio);
    base.matrix.centerX(base.centerX + current.x - saved.x);
    base.matrix.centerY(base.centerY + current.y - saved.y);
    return true;
  }

  function replaceLive2DModel(modelUrl) {
    const manager = window.__live2dManager;
    if (!manager || typeof manager.changeModel !== "function" ||
        typeof manager.releaseModel !== "function" || !gl || gl.isContextLost()) {
      throw new Error("Live2D 模型管理器或 WebGL 上下文尚未就绪。");
    }

    // 复用当前 Canvas 和 WebGL 上下文，只替换 Cubism 2 模型实例。
    // 不再次调用 loadlive2d，避免重复注册鼠标/触摸事件和启动第二套渲染循环。
    manager.reloadFlg = true;
    manager.count += 1;
    manager.changeModel(gl, modelUrl);
  }

  async function switchModelInPlace(modelId, token, modelPreferences) {
    const previousModelId = loadedModelId;
    const previousModelProfile = loadedModelProfile;
    let modelReplaced = false;
    statusSent = false;
    if (live2dRuntimeVersion === "moc3") setMouthSyncStatus("正在检测当前模型的 TTS 嘴型同步参数…", "checking");
    updateModelActionAvailability();
    showMessage("正在切换 Live2D 模型：" + modelId + "…");
    await reportState("loading", "正在局部切换 Live2D 模型：" + modelId);

    try {
      const modelUrl = modelConfigUrl(modelId);
      const modelConfig = await verifyAssets(modelUrl);
      if (token !== modelSwitchToken) return false;
      if (!window.__live2dManager || !gl) {
        throw new Error("Live2D 运行时尚未完成初始化。");
      }

      populateModelActions(modelConfig, modelId);
      replaceLive2DModel(modelUrl);
      modelReplaced = true;
      await waitForRenderedPixels();

      loadedModelId = modelId;
      loadedModelProfile = currentModelProfile;
      captureLive2DTransformBase();
      applyLive2DTransform(currentPreferences());
      statusSent = true;
      updateModelActionAvailability();
      if (placeholder) placeholder.style.display = "none";
      showMessage("");
      await reportState("loaded", modelId + " 已在当前页面内完成模型切换。");
      if (token === modelSwitchToken) {
        setModelActionStatus(modelId + " 已加载，可以直接使用动作和表情。");
      }
      return true;
    } catch (error) {
      let restored = false;
      if (modelReplaced && previousModelId) {
        try {
          const previousUrl = modelConfigUrl(previousModelId, previousModelProfile);
          await verifyAssets(previousUrl);
          replaceLive2DModel(previousUrl);
          await waitForRenderedPixels();
          loadedModelId = previousModelId;
          loadedModelProfile = previousModelProfile;
          statusSent = true;
          restored = true;
          await reportState("loaded", "新模型加载失败，已恢复原模型：" + previousModelId);
        } catch (restoreError) {
          console.error("Unable to restore previous Live2D model:", restoreError);
        }
      } else if (!modelReplaced) {
        statusSent = Boolean(loadedModelId);
      }

      updateModelActionAvailability();
      showMessage(restored ? "新模型加载失败，已恢复原模型。" : "Live2D 模型切换失败：" + error.message);
      if (!restored) await reportState("error", error.message);
      if (token === modelSwitchToken) {
        setSaveStatus("模型切换失败：" + error.message, true);
        setModelActionStatus(restored ? "已恢复原模型：" + previousModelId : "模型切换失败：" + error.message, true);
      }
      throw error;
    }
  }

  function queueModelSwitch(modelId, token, modelPreferences) {
    const next = modelSwitchPromise.catch(() => {}).then(() => switchModelInPlace(modelId, token, modelPreferences));
    modelSwitchPromise = next;
    return next;
  }

  async function start() {
    if (live2dRuntimeVersion === "moc3") {
      try {
        const modelId = await loadDisplaySettings();
        const token = ++modelSwitchToken;
        modelSwitchPromise = loadMoc3Model(modelId, token);
        const loaded = await modelSwitchPromise;
        if (loaded) {
          const actionsLoaded = await loadModelActionConfig(modelId, token);
          if (actionsLoaded && token === modelSwitchToken) {
            statusSent = true;
            updateModelActionAvailability();
          }
        }
      } catch (error) {
        console.error("MOC3 initialization failed:", error);
        showMessage("MOC3 加载失败：" + error.message);
        await reportState("error", error.message);
        setSaveStatus(error.message, true);
      }
      return;
    }
    try {
      showMessage("正在读取展示设置…");
      const modelId = await loadDisplaySettings();
      const modelUrl = modelConfigUrl(modelId);
      showMessage("正在检查 " + modelId + " 模型资源…");
      await reportState("loading", "正在检查模型配置和资源文件：" + modelId);
      const modelConfig = await verifyAssets(modelUrl);
      populateModelActions(modelConfig, modelId);
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
      loadedModelId = modelId;
      loadedModelProfile = currentModelProfile;
      captureLive2DTransformBase();
      applyLive2DTransform(currentPreferences());
      updateModelActionAvailability();
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
  if (idleTipsSaveButton) idleTipsSaveButton.addEventListener("click", saveIdleTipSettings);

  if (toggle) toggle.addEventListener("click", () => setPanelOpen(panel.hidden));
  if (closeButton) closeButton.addEventListener("click", () => setPanelOpen(false));

  async function handleModelChange() {
    const selectedModelId = modelSelect.value;
    const token = ++modelSwitchToken;
    try {
      const query = "/api/live2d/settings?version=" + encodeURIComponent(live2dRuntimeVersion)
        + "&model=" + encodeURIComponent(selectedModelId)
        + (modelProfileSelect && modelProfileSelect.value ? "&profile=" + encodeURIComponent(modelProfileSelect.value) : "");
      const response = await fetch(query, { cache: "no-store" });
      if (!response.ok) throw new Error("无法读取该模型的独立设置。");
      const data = await response.json();
      if (token !== modelSwitchToken) return;
      const p = data.preferences || {};
      motionShortcutBindings = p.motion_shortcuts && typeof p.motion_shortcuts === "object" ? { ...p.motion_shortcuts } : {};
      populateModelProfiles(data.profiles, p.profile);
      controls.scale.value = String(Math.round((Number(p.scale) || 1) * 100));
      controls.x.value = String(Math.round((Number(p.x) || 0) * 100));
      controls.y.value = String(Math.round((Number(p.y) || 0) * 100));
      controls["crop-top"].value = String(p.crop_top ?? 0);
      controls["crop-right"].value = String(p.crop_right ?? 0);
      controls["crop-bottom"].value = String(p.crop_bottom ?? 0);
      controls["crop-left"].value = String(p.crop_left ?? 0);
      controls["bubble-x"].value = String(p.bubble_x ?? 50);
      controls["bubble-bottom"].value = String(p.bubble_bottom ?? 45);
      controls["bubble-width"].value = String(p.bubble_width ?? 420);
      controls["bubble-height"].value = String(p.bubble_max_height ?? 320);
      controls["bubble-items"].value = String(p.bubble_max_items ?? 5);
      controls["bubble-duration"].value = String(p.bubble_duration ?? 10);
      controls["bubble-font"].value = String(p.bubble_font_size ?? 20);
      updateSliderLabels();
      applyLive2DCrop();
      markUnsaved();

      // 立即切换当前模型；model ID 仍只会在点击保存时写入用户设置。
      if (live2dRuntimeVersion === "moc3") {
        modelSwitchPromise = loadMoc3Model(selectedModelId, token);
        const loaded = await modelSwitchPromise;
        if (loaded && token === modelSwitchToken) {
          const actionsLoaded = await loadModelActionConfig(selectedModelId, token);
          if (actionsLoaded && token === modelSwitchToken) {
            statusSent = true;
            updateModelActionAvailability();
          }
        }
      } else {
        await queueModelSwitch(selectedModelId, token, p);
      }
    } catch (error) {
      if (token !== modelSwitchToken) return;
      setModelActionStatus("模型动作/表情配置读取失败：" + error.message, true);
      setSaveStatus(error.message, true);
    }
  }

  if (modelSelect) modelSelect.addEventListener("change", handleModelChange);
  if (modelProfileSelect) modelProfileSelect.addEventListener("change", handleModelChange);
  if (motionPlayButton) motionPlayButton.addEventListener("click", playSelectedModelMotion);
  if (expressionApplyButton) expressionApplyButton.addEventListener("click", applySelectedModelExpression);
  if (expressionResetButton) expressionResetButton.addEventListener("click", resetSelectedModelExpression);
  if (motionSelect) motionSelect.addEventListener("change", updateModelActionAvailability);
  if (expressionSelect) expressionSelect.addEventListener("change", updateModelActionAvailability);
  if (saveButton) saveButton.addEventListener("click", saveSettings);
  window.addEventListener("resize", () => {
    if (live2dRuntimeVersion === "moc3") applyLive2DTransform();
  }, { passive: true });
  for (const [key, slider] of Object.entries(controls)) {
    if (!slider) continue;
    slider.addEventListener("input", () => {
      updateSliderLabels();
      applyBubblePreferences();
      applyLive2DCrop();
      if (key === "scale" || key === "x" || key === "y") applyLive2DTransform();
      if (key.startsWith("bubble-") && panel && !panel.hidden) renderBubblePreview();
    });
    slider.addEventListener("change", markUnsaved);
  }

  // OBS 浏览器源启动时自动创建并尝试恢复 Web Audio；无需点击测试语音。
  if (showTts) initializeAutomaticAudio();
  checkLiveConnectionForAudio();
  connectionTimer = window.setInterval(checkLiveConnectionForAudio, 3000);

  if (showLive2D) {
    start();
  } else {
    if (!live2dEnabledAtLoad && placeholder && (displayMode === "all" || displayMode === "live2d")) {
      placeholder.textContent = "Live2D 运行时已在接入中心停用。";
      placeholder.style.display = "block";
    }
    if (showDanmaku) {
      loadDisplaySettings().catch(displayError => {
        console.warn("Unable to load danmaku display preferences:", displayError);
      });
    }
  }

  // TTS 设置独立加载；无论接口慢、失败还是语音不可用，都不阻塞弹幕显示。
  if (showDanmaku) {
    loadIdleTipSettings().catch(idleError => {
      console.warn("Unable to load idle tip settings:", idleError);
    });
  }

  if (showTts) {
    loadTtsSettings().catch(ttsError => {
      console.warn("Unable to load TTS settings:", ttsError);
      setTtsStatus("读取语音设置失败：" + ttsError.message, true);
    });
  }

  // 弹幕游标和轮询独立启动，不依赖 TTS 初始化或设置面板状态。
  if (showDanmaku || showTts) {
    initializeDanmakuCursor();
    pollTimer = window.setInterval(refreshDanmakuPolling, 450);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshDanmakuPolling();
    });
    window.addEventListener("focus", refreshDanmakuPolling);
  }

  window.addEventListener("resize", applyBubblePreferences, { passive: true });
  window.addEventListener("beforeunload", () => {
    if (idleTimer) clearTimeout(idleTimer);
    if (resizeObserver) resizeObserver.disconnect();
    if (pollTimer) clearInterval(pollTimer);
    if (connectionTimer) clearInterval(connectionTimer);
    if (showLive2D) reportState("not_started", "OBS 展示页面已关闭，Live2D 运行时当前未运行。");
  });
})();
