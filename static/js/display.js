(() => {
  "use strict";

  const canvas = document.getElementById("live2d-canvas");
  const placeholder = document.getElementById("display-placeholder");
  if (!canvas) return;

  const MODEL_URL = "/models/live_uu/model.json";
  const MAX_DPR = 2;
  let gl = null;
  let resizeObserver = null;
  let resizePending = false;
  let statusSent = false;

  function showMessage(message) {
    if (placeholder) placeholder.textContent = message;
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
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    if (gl) {
      gl.viewport(0, 0, canvas.width, canvas.height);
    }
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

  async function verifyAssets() {
    const response = await fetch(MODEL_URL, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("无法读取模型配置：" + MODEL_URL + "（HTTP " + response.status + "）");
    }
    const config = await response.json();
    if (!config.model || !Array.isArray(config.textures) || config.textures.length === 0) {
      throw new Error("model.json 缺少 model 或 textures 配置。");
    }

    const base = MODEL_URL.slice(0, MODEL_URL.lastIndexOf("/") + 1);
    const files = collectModelFiles(config);
    const checks = await Promise.all(files.map(async relative => {
      const url = new URL(relative, window.location.origin + base);
      const result = await fetch(url.href, { method: "HEAD", cache: "no-store" });
      if (!result.ok) throw new Error("模型资源缺失：" + relative + "（HTTP " + result.status + "）");
    }));
    return { config, fileCount: checks.length };
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

        // The bundled loader starts its render loop before returning. Reading
        // pixels in this animation frame checks that the model produced output.
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
      showMessage("正在检查 MOC2 模型资源…");
      await reportState("loading", "正在检查模型配置和资源文件。");
      await verifyAssets();

      if (typeof window.loadlive2d !== "function" ||
          typeof window.Live2DModelWebGL === "undefined" ||
          typeof window.Live2D === "undefined") {
        throw new Error("live2d.min.js 未提供预期的 Cubism 2 运行时接口（loadlive2d / Live2DModelWebGL）。");
      }

      // Set the drawing buffer before the runtime creates its WebGL context.
      setCanvasResolution();
      showMessage("模型资源检查通过，正在加载 live_uu…");

      // This legacy runtime owns WebGL context creation and the render loop.
      window.loadlive2d(canvas.id, MODEL_URL);

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
      await reportState("loaded", "live_uu 已由 Cubism 2 WebGL 运行时加载并检测到实际绘制内容。");
    } catch (error) {
      console.error("Live2D initialization failed:", error);
      showMessage("Live2D 加载失败：" + error.message);
      await reportState("error", error.message);
    }
  }

  start();

  window.addEventListener("beforeunload", () => {
    if (resizeObserver) resizeObserver.disconnect();
    if (!statusSent) reportState("error", "OBS 展示页面在模型初始化完成前关闭。");
  });
})();
