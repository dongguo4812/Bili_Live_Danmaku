(() => {
  "use strict";

  const canvas = document.getElementById("live2d-canvas");
  if (!canvas) return;

  // This prepares a correctly sized transparent WebGL surface. It is not
  // itself a MOC2 renderer; the Cubism 2 runtime must be integrated separately.
  const gl = canvas.getContext("webgl", {
    alpha: true,
    antialias: true,
    premultipliedAlpha: true,
    preserveDrawingBuffer: false
  });

  if (!gl) {
    const placeholder = document.getElementById("display-placeholder");
    if (placeholder) placeholder.textContent = "当前浏览器不支持 WebGL";
    return;
  }

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const configuredMaxDpr = 2;
    const dpr = Math.min(window.devicePixelRatio || 1, configuredMaxDpr);
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));

    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }

    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
  }

  const observer = new ResizeObserver(resizeCanvas);
  observer.observe(canvas);
  window.addEventListener("resize", resizeCanvas, { passive: true });
  resizeCanvas();
})();
