const uploadForm = document.getElementById("upload-form");
const statusEl = document.getElementById("status");
const fileInput = document.getElementById("video");
const fileNameEl = document.getElementById("video-file-name");
const submitButton = document.getElementById("submit-button");
const progressShell = document.getElementById("progress-shell");
const progressLabel = document.getElementById("progress-label");
const progressValue = document.getElementById("progress-value");
const progressFill = document.getElementById("progress-fill");
const tableBody = document.querySelector("#table tbody");
const downloadLink = document.getElementById("download");
const videoPlayer = document.getElementById("video-player");
const overlayCanvas = document.getElementById("overlay-canvas");
const videoPlaceholder = document.getElementById("video-placeholder");
const histogramCanvas = document.getElementById("histogram-canvas");
const histogramCaption = document.getElementById("histogram-caption");
const metricCount = document.getElementById("metric-count");
const metricMean = document.getElementById("metric-mean");
const metricMeanPx = document.getElementById("metric-mean-px");
const metricMax = document.getElementById("metric-max");
const metricMaxPx = document.getElementById("metric-max-px");
const metricMin = document.getElementById("metric-min");
const metricMinPx = document.getElementById("metric-min-px");
const metricStd = document.getElementById("metric-std");
const metricStdPx = document.getElementById("metric-std-px");

const FIXED_LINE_RATIO = 0.04;
const FIXED_PLAYBACK_RATE = 0.5;
const MIN_TRACK_SAMPLES = 5;
const MIN_TRACK_TRAVEL_PX = 40;
const MIN_TRACK_BOX_SIZE_PX = 18;
const LABEL_NEAR_LINE_DISTANCE_RATIO = 0.18;
const NOMINAL_TOMATO_DIAMETER_MM = 60;

let pollingHandle = null;
let renderHandle = null;
let frameMap = new Map();
let sortedFrameKeys = [];
let frameTimeMap = new Map();
let trackTimelineMap = new Map();
let validTrackIds = new Set();
let crossedTrackIds = new Set();
let lastCenterByTrack = new Map();
let crossings = [];
let lastProcessedFrame = -1;

function fmt(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return Number(value).toFixed(digits);
}

function updateSelectedFileName() {
  const selectedFile = fileInput.files?.[0];
  fileNameEl.textContent = selectedFile ? selectedFile.name : "Ningún archivo seleccionado";
}

function setProcessingState(isProcessing) {
  submitButton.hidden = isProcessing;
  progressShell.hidden = !isProcessing;
  submitButton.disabled = isProcessing;
}

function updateProgress(progress, label = "Procesando video...") {
  const normalized = Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : 0;
  progressLabel.textContent = label;
  progressValue.textContent = `${Math.round(normalized)}%`;
  progressFill.style.width = `${normalized}%`;
}

function setVideoState(hasVideo) {
  videoPlayer.style.visibility = hasVideo ? "visible" : "hidden";
  videoPlayer.style.pointerEvents = hasVideo ? "auto" : "none";
  overlayCanvas.style.visibility = hasVideo ? "visible" : "hidden";
  videoPlaceholder.style.display = hasVideo ? "none" : "grid";
  videoPlaceholder.style.pointerEvents = hasVideo ? "none" : "auto";
}

function setPlaceholder(title, message) {
  videoPlaceholder.innerHTML = `
    <div>
      <strong>${title}</strong>
      <p>${message}</p>
    </div>
  `;
  setVideoState(false);
}

function clearDashboard() {
  tableBody.innerHTML = "";
  downloadLink.removeAttribute("href");
  metricCount.textContent = "0";
  metricMean.textContent = "-";
  metricMeanPx.textContent = "-";
  metricMax.textContent = "-";
  metricMaxPx.textContent = "-";
  metricMin.textContent = "-";
  metricMinPx.textContent = "-";
  metricStd.textContent = "-";
  metricStdPx.textContent = "-";
  drawHistogram([]);
}

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 0) {
    return (sorted[mid - 1] + sorted[mid]) / 2;
  }
  return sorted[mid];
}

function getEstimatedMmPerPx() {
  const diametersPx = crossings
    .map((row) => Number(row.diameter_px))
    .filter((value) => Number.isFinite(value) && value > 0);

  const referencePx = median(diametersPx);
  if (!referencePx || referencePx <= 0) {
    return null;
  }

  return NOMINAL_TOMATO_DIAMETER_MM / referencePx;
}

function getEstimatedDiameterMm(row) {
  const explicitMm = Number(row.diameter_mm);
  if (Number.isFinite(explicitMm) && explicitMm > 0) {
    return explicitMm;
  }

  const mmPerPx = getEstimatedMmPerPx();
  const diameterPx = Number(row.diameter_px);
  if (!mmPerPx || !Number.isFinite(diameterPx)) {
    return null;
  }

  return diameterPx * mmPerPx;
}

function mean(values) {
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function stdDev(values) {
  if (values.length < 2) return 0;
  const avg = mean(values);
  const variance = values.reduce((sum, value) => sum + ((value - avg) ** 2), 0) / values.length;
  return Math.sqrt(variance);
}

function resizeHistogramCanvas() {
  const rect = histogramCanvas.getBoundingClientRect();
  histogramCanvas.width = Math.max(320, Math.floor(rect.width * window.devicePixelRatio));
  histogramCanvas.height = Math.max(240, Math.floor(rect.height * window.devicePixelRatio));
}

function drawHistogram(valuesMm) {
  resizeHistogramCanvas();
  const ctx = histogramCanvas.getContext("2d");
  const width = histogramCanvas.width;
  const height = histogramCanvas.height;
  const dpr = window.devicePixelRatio || 1;
  ctx.clearRect(0, 0, width, height);

  ctx.save();
  ctx.scale(dpr, dpr);

  const logicalWidth = width / dpr;
  const logicalHeight = height / dpr;
  ctx.fillStyle = "rgba(8, 13, 23, 0.94)";
  ctx.fillRect(0, 0, logicalWidth, logicalHeight);

  if (!valuesMm.length) {
    ctx.fillStyle = "#98a7bf";
    ctx.font = '600 14px Sora, sans-serif';
    ctx.fillText("Sin cruces suficientes para construir el histograma.", 20, logicalHeight / 2);
    ctx.restore();
    histogramCaption.textContent = "El histograma se actualiza a medida que los tomates cruzan la línea de conteo.";
    return;
  }

  const padding = { top: 22, right: 18, bottom: 38, left: 42 };
  const chartWidth = logicalWidth - padding.left - padding.right;
  const chartHeight = logicalHeight - padding.top - padding.bottom;
  const binCount = Math.min(8, Math.max(4, Math.round(Math.sqrt(valuesMm.length))));
  const minValue = Math.min(...valuesMm);
  const maxValue = Math.max(...valuesMm);
  const range = Math.max(1, maxValue - minValue);
  const step = range / binCount;
  const bins = Array.from({ length: binCount }, (_, index) => ({
    start: minValue + (index * step),
    end: index === binCount - 1 ? maxValue : minValue + ((index + 1) * step),
    count: 0,
  }));

  for (const value of valuesMm) {
    const ratio = Math.min(binCount - 1, Math.floor(((value - minValue) / range) * binCount));
    bins[Math.max(0, ratio)].count += 1;
  }

  const maxCount = Math.max(...bins.map((bin) => bin.count), 1);

  ctx.strokeStyle = "rgba(152, 167, 191, 0.22)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = padding.top + (chartHeight * i) / 4;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(padding.left + chartWidth, y);
    ctx.stroke();
  }

  const barGap = 10;
  const barWidth = (chartWidth - (barGap * (binCount - 1))) / binCount;
  bins.forEach((bin, index) => {
    const barHeight = (bin.count / maxCount) * chartHeight;
    const x = padding.left + index * (barWidth + barGap);
    const y = padding.top + chartHeight - barHeight;

    const gradient = ctx.createLinearGradient(x, y, x, y + barHeight);
    gradient.addColorStop(0, "#ffb347");
    gradient.addColorStop(1, "#3dc4ff");
    ctx.fillStyle = gradient;
    ctx.fillRect(x, y, barWidth, barHeight);

    ctx.fillStyle = "#f4f7fb";
    ctx.font = '600 11px Sora, sans-serif';
    ctx.fillText(String(bin.count), x + Math.max(2, (barWidth / 2) - 6), Math.max(16, y - 6));

    ctx.fillStyle = "#98a7bf";
    ctx.font = '500 10px Sora, sans-serif';
    ctx.fillText(`${fmt(bin.start, 0)}-${fmt(bin.end, 0)}`, x, logicalHeight - 14);
  });

  ctx.fillStyle = "#98a7bf";
  ctx.font = '600 11px Sora, sans-serif';
  ctx.fillText("Frecuencia", 10, padding.top - 6);
  ctx.fillText("Diámetro estimado (mm)", padding.left, logicalHeight - 6);
  ctx.restore();

  histogramCaption.textContent = `Base de estimación: mediana visual = ${NOMINAL_TOMATO_DIAMETER_MM} mm para el tomate de referencia.`;
}

function updateMetrics() {
  const diametersPx = crossings
    .map((row) => Number(row.diameter_px))
    .filter((value) => Number.isFinite(value) && value > 0);
  const diametersMm = crossings
    .map((row) => getEstimatedDiameterMm(row))
    .filter((value) => Number.isFinite(value) && value > 0);

  metricCount.textContent = String(crossings.length);

  if (!diametersPx.length || !diametersMm.length) {
    metricMean.textContent = "-";
    metricMeanPx.textContent = "-";
    metricMax.textContent = "-";
    metricMaxPx.textContent = "-";
    metricMin.textContent = "-";
    metricMinPx.textContent = "-";
    metricStd.textContent = "-";
    metricStdPx.textContent = "-";
    drawHistogram([]);
    return;
  }

  metricMean.textContent = `${fmt(mean(diametersMm), 1)} mm`;
  metricMeanPx.textContent = `${fmt(mean(diametersPx), 1)} px`;
  metricMax.textContent = `${fmt(Math.max(...diametersMm), 1)} mm`;
  metricMaxPx.textContent = `${fmt(Math.max(...diametersPx), 1)} px`;
  metricMin.textContent = `${fmt(Math.min(...diametersMm), 1)} mm`;
  metricMinPx.textContent = `${fmt(Math.min(...diametersPx), 1)} px`;
  metricStd.textContent = `${fmt(stdDev(diametersMm), 1)} mm`;
  metricStdPx.textContent = `${fmt(stdDev(diametersPx), 1)} px`;
  drawHistogram(diametersMm);
}

function updateCrossingTable() {
  tableBody.innerHTML = "";
  const rows = [...crossings].sort((a, b) => Number(b.crossing_time_sec) - Number(a.crossing_time_sec));
  for (const row of rows) {
    const estimatedDiameterMm = getEstimatedDiameterMm(row);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.track_id ?? "-"}</td>
      <td>${fmt(row.crossing_time_sec, 2)}</td>
      <td>${fmt(row.diameter_px, 2)}</td>
      <td>${fmt(estimatedDiameterMm, 2)}</td>
    `;
    tableBody.appendChild(tr);
  }

  updateMetrics();
}

function buildFrameMap(items) {
  const ordered = [...items].sort((a, b) => {
    const timeDiff = Number(a.timestamp_sec) - Number(b.timestamp_sec);
    if (Math.abs(timeDiff) > 1e-9) return timeDiff;
    const frameDiff = Number(a.frame_idx) - Number(b.frame_idx);
    if (frameDiff !== 0) return frameDiff;
    return Number(a.id ?? 0) - Number(b.id ?? 0);
  });

  const map = new Map();
  const times = new Map();
  const rawTracks = new Map();

  for (const item of ordered) {
    if (item.track_id !== null && item.track_id !== undefined) {
      const trackId = Number(item.track_id);
      if (!rawTracks.has(trackId)) {
        rawTracks.set(trackId, []);
      }
      rawTracks.get(trackId).push(item);
    }
  }

  const allowedTrackIds = new Set();
  for (const [trackId, samples] of rawTracks.entries()) {
    if (samples.length < MIN_TRACK_SAMPLES) {
      continue;
    }

    let minCenterX = Number.POSITIVE_INFINITY;
    let maxCenterX = Number.NEGATIVE_INFINITY;
    let maxBoxSize = 0;

    for (const sample of samples) {
      const centerX = (Number(sample.x1) + Number(sample.x2)) / 2;
      const boxWidth = Number(sample.x2) - Number(sample.x1);
      const boxHeight = Number(sample.y2) - Number(sample.y1);
      minCenterX = Math.min(minCenterX, centerX);
      maxCenterX = Math.max(maxCenterX, centerX);
      maxBoxSize = Math.max(maxBoxSize, boxWidth, boxHeight);
    }

    const horizontalTravel = Math.abs(maxCenterX - minCenterX);
    if (horizontalTravel < MIN_TRACK_TRAVEL_PX) {
      continue;
    }
    if (maxBoxSize < MIN_TRACK_BOX_SIZE_PX) {
      continue;
    }

    allowedTrackIds.add(trackId);
  }

  for (const item of ordered) {
    if (item.track_id !== null && item.track_id !== undefined) {
      const trackId = Number(item.track_id);
      if (!allowedTrackIds.has(trackId)) {
        continue;
      }
    }

    const key = Number(item.frame_idx);
    if (!map.has(key)) {
      map.set(key, []);
    }
    map.get(key).push(item);

    if (!times.has(key)) {
      times.set(key, Number(item.timestamp_sec));
    }
  }

  const tracks = new Map();
  for (const trackId of allowedTrackIds) {
    tracks.set(trackId, rawTracks.get(trackId) || []);
  }

  frameMap = map;
  sortedFrameKeys = [...frameMap.keys()].sort((a, b) => a - b);
  frameTimeMap = times;
  trackTimelineMap = tracks;
  validTrackIds = allowedTrackIds;
}

function resizeOverlay() {
  const rect = videoPlayer.getBoundingClientRect();
  overlayCanvas.width = Math.max(1, Math.floor(rect.width));
  overlayCanvas.height = Math.max(1, Math.floor(rect.height));
}

function getClosestFrameKeyForTime(currentTime) {
  if (!sortedFrameKeys.length || !Number.isFinite(currentTime)) return null;
  let closest = sortedFrameKeys[0];
  let minDist = Math.abs((frameTimeMap.get(closest) ?? 0) - currentTime);

  for (const key of sortedFrameKeys) {
    const timeSec = frameTimeMap.get(key) ?? 0;
    const dist = Math.abs(timeSec - currentTime);
    if (dist < minDist) {
      closest = key;
      minDist = dist;
    }
  }

  return minDist <= 0.25 ? closest : null;
}

function detectCrossingsUpTo(timeTarget) {
  const lineX = FIXED_LINE_RATIO * (videoPlayer.videoWidth || 1);

  for (const frameKey of sortedFrameKeys) {
    const frameTime = frameTimeMap.get(frameKey) ?? 0;
    if (frameTime > timeTarget) {
      break;
    }

    if (frameKey <= lastProcessedFrame) {
      continue;
    }

    const detections = frameMap.get(frameKey) || [];
    for (const det of detections) {
      if (det.track_id === null || det.track_id === undefined) {
        continue;
      }

      const trackId = Number(det.track_id);
      if (!validTrackIds.has(trackId)) {
        continue;
      }
      const centerX = (Number(det.x1) + Number(det.x2)) / 2;
      const prevCenter = lastCenterByTrack.get(trackId);

      if (prevCenter !== undefined && !crossedTrackIds.has(trackId)) {
        const crossed = (prevCenter - lineX) * (centerX - lineX) <= 0 && prevCenter !== centerX;
        if (crossed) {
          crossedTrackIds.add(trackId);
          crossings.push({
            ...det,
            crossing_frame_idx: frameKey,
            crossing_time_sec: Number(det.timestamp_sec),
          });
        }
      }

      lastCenterByTrack.set(trackId, centerX);
    }

    lastProcessedFrame = frameKey;
  }

  crossings.sort((a, b) => a.crossing_time_sec - b.crossing_time_sec);
}

function resetCrossingState() {
  crossedTrackIds = new Set();
  lastCenterByTrack = new Map();
  crossings = [];
  lastProcessedFrame = -1;
  updateCrossingTable();
}

function lerp(start, end, alpha) {
  return start + (end - start) * alpha;
}

function getInterpolatedDetections(currentTime) {
  const detections = [];

  for (const [trackId, samples] of trackTimelineMap.entries()) {
    let previous = null;
    let next = null;

    for (const sample of samples) {
      const sampleTime = Number(sample.timestamp_sec);
      if (sampleTime <= currentTime) {
        previous = sample;
        continue;
      }
      next = sample;
      break;
    }

    if (!previous && !next) {
      continue;
    }

    let chosen = null;
    if (previous && next) {
      const prevTime = Number(previous.timestamp_sec);
      const nextTime = Number(next.timestamp_sec);
      const gap = nextTime - prevTime;
      if (gap > 0 && gap <= 0.25) {
        const alpha = (currentTime - prevTime) / gap;
        chosen = {
          ...previous,
          track_id: trackId,
          x1: lerp(Number(previous.x1), Number(next.x1), alpha),
          y1: lerp(Number(previous.y1), Number(next.y1), alpha),
          x2: lerp(Number(previous.x2), Number(next.x2), alpha),
          y2: lerp(Number(previous.y2), Number(next.y2), alpha),
          diameter_px: lerp(Number(previous.diameter_px), Number(next.diameter_px), alpha),
          diameter_mm:
            previous.diameter_mm !== null && next.diameter_mm !== null
              ? lerp(Number(previous.diameter_mm), Number(next.diameter_mm), alpha)
              : previous.diameter_mm,
          confidence: Math.max(Number(previous.confidence ?? 0), Number(next.confidence ?? 0)),
          timestamp_sec: currentTime,
        };
      }
    }

    if (!chosen && previous && Math.abs(currentTime - Number(previous.timestamp_sec)) <= 0.18) {
      chosen = previous;
    }

    if (!chosen && next && Math.abs(Number(next.timestamp_sec) - currentTime) <= 0.18) {
      chosen = next;
    }

    if (chosen) {
      detections.push(chosen);
    }
  }

  if (detections.length) {
    return detections;
  }

  const fallbackFrameKey = getClosestFrameKeyForTime(currentTime);
  return fallbackFrameKey === null ? [] : frameMap.get(fallbackFrameKey) || [];
}

function drawOverlayAtCurrentTime() {
  const ctx = overlayCanvas.getContext("2d");
  const canvasWidth = overlayCanvas.width;
  const canvasHeight = overlayCanvas.height;
  ctx.clearRect(0, 0, canvasWidth, canvasHeight);

  if (!videoPlayer.videoWidth || !videoPlayer.videoHeight) {
    return;
  }

  const currentTime = Number(videoPlayer.currentTime || 0);
  detectCrossingsUpTo(currentTime);
  updateCrossingTable();

  const detections = getInterpolatedDetections(currentTime);
  const scaleX = canvasWidth / videoPlayer.videoWidth;
  const scaleY = canvasHeight / videoPlayer.videoHeight;
  const lineXCanvas = FIXED_LINE_RATIO * canvasWidth;
  const labelNearLineDistance = LABEL_NEAR_LINE_DISTANCE_RATIO * canvasWidth;

  ctx.strokeStyle = "#4fd1ff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(lineXCanvas, 0);
  ctx.lineTo(lineXCanvas, canvasHeight);
  ctx.stroke();

  ctx.fillStyle = "#4fd1ff";
  ctx.font = "bold 14px sans-serif";
  ctx.fillText("Línea de conteo fija", Math.min(lineXCanvas + 8, canvasWidth - 150), 18);

  for (const det of detections) {
    const isCounted = det.track_id !== null && det.track_id !== undefined && crossedTrackIds.has(Number(det.track_id));
    const x = Number(det.x1) * scaleX;
    const y = Number(det.y1) * scaleY;
    const w = (Number(det.x2) - Number(det.x1)) * scaleX;
    const h = (Number(det.y2) - Number(det.y1)) * scaleY;
    const centerXCanvas = x + (w / 2);

    ctx.strokeStyle = isCounted ? "#ff5c5c" : "#ffb347";
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);

    const showLabel = isCounted || Math.abs(centerXCanvas - lineXCanvas) <= labelNearLineDistance || w >= 42 || h >= 42;
    if (showLabel) {
      const label = `T${det.track_id ?? "?"} | ${fmt(det.diameter_px, 1)} px`;
      const labelY = Math.max(14, y - 6);
      ctx.fillStyle = isCounted ? "rgba(140, 16, 16, 0.85)" : "rgba(15, 15, 15, 0.75)";
      ctx.fillRect(x, labelY - 12, Math.max(140, label.length * 6.2), 16);
      ctx.fillStyle = "#fff";
      ctx.font = "12px sans-serif";
      ctx.fillText(label, x + 4, labelY);
    }
  }

}

function renderLoop() {
  drawOverlayAtCurrentTime();
  if (!videoPlayer.paused && !videoPlayer.ended) {
    renderHandle = requestAnimationFrame(renderLoop);
  } else {
    renderHandle = null;
  }
}

function startRenderLoop() {
  if (renderHandle !== null) return;
  renderHandle = requestAnimationFrame(renderLoop);
}

function stopRenderLoop() {
  if (renderHandle !== null) {
    cancelAnimationFrame(renderHandle);
    renderHandle = null;
  }
}

async function fetchResults(jobId) {
  const response = await fetch(`/api/jobs/${jobId}/measurements?limit=50000`);
  if (!response.ok) {
    throw new Error("No se pudieron recuperar resultados");
  }

  const measurements = await response.json();
  const items = measurements.items || [];
  buildFrameMap(items);
  resetCrossingState();

  downloadLink.href = `/api/jobs/${jobId}/csv`;
  setProcessingState(false);
  updateProgress(100, "Procesamiento completo");
  setVideoState(true);
  videoPlayer.src = `/api/jobs/${jobId}/video`;
  videoPlayer.load();
  videoPlayer.playbackRate = FIXED_PLAYBACK_RATE;

  if (!items.length) {
    console.debug("Procesamiento completado. No hubo detecciones, pero el video procesado ya está disponible.");
  } else {
    console.debug("Listo. Reproducí el video para medir cruces en la línea fija.");
  }
}

async function bootstrapDefaultVideo() {
  clearDashboard();
  setPlaceholder("No se ha procesado nada", "Procesá un video para mostrar overlays, conteo y detecciones sincronizadas.");
  console.debug("Cargando video por defecto...");

  const response = await fetch("/api/jobs/default?force=true", {
    method: "POST",
  });

  if (!response.ok) {
    const err = await response.json();
    console.debug(`No se pudo cargar el video por defecto: ${err.detail || "error"}`);
    return;
  }

  const payload = await response.json();
  const jobId = payload.job_id;

  pollingHandle = setInterval(() => {
    pollJob(jobId).catch((err) => {
      console.debug(`Error consultando estado: ${err.message}`);
      clearInterval(pollingHandle);
      pollingHandle = null;
    });
  }, 2000);

  pollJob(jobId).catch((err) => {
    console.debug(`Error consultando estado: ${err.message}`);
  });
}

async function pollJob(jobId) {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) {
    throw new Error("No se pudo consultar el job");
  }

  const job = await response.json();
  const progress = job.progress !== null && job.progress !== undefined ? Number(job.progress) : 0;

  if (job.status === "uploaded" || job.status === "processing") {
    setProcessingState(true);
    updateProgress(progress, "Procesando video...");
  }

  if (job.status === "completed") {
    clearInterval(pollingHandle);
    pollingHandle = null;
    await fetchResults(jobId);
  }

  if (job.status === "failed") {
    clearInterval(pollingHandle);
    pollingHandle = null;
    setProcessingState(false);
    console.debug(`Estado: failed | Error: ${job.error || "sin detalle"}`);
  }
}

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  console.debug("Subiendo y creando job...");
  setProcessingState(true);
  updateProgress(0, "Subiendo video...");
  clearDashboard();
  frameMap = new Map();
  sortedFrameKeys = [];
  resetCrossingState();
  stopRenderLoop();
  videoPlayer.removeAttribute("src");
  videoPlayer.load();
  setPlaceholder("Procesando video", "Cuando termine el job se mostrará el video procesado con overlay y conteo.");

  if (pollingHandle) {
    clearInterval(pollingHandle);
    pollingHandle = null;
  }

  if (!fileInput.files?.[0]) {
    setProcessingState(false);
    console.debug("Seleccioná un video primero");
    return;
  }

  const formData = new FormData();
  formData.append("video", fileInput.files[0]);

  const response = await fetch("/api/jobs", {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const err = await response.json();
    setProcessingState(false);
    console.debug(`Error: ${err.detail || "no se pudo crear el job"}`);
    return;
  }

  const payload = await response.json();
  const jobId = payload.job_id;

  console.debug("Procesamiento iniciado.");
  updateProgress(0, "Procesando video...");
  pollingHandle = setInterval(() => {
    pollJob(jobId).catch((err) => {
      setProcessingState(false);
      console.debug(`Error consultando estado: ${err.message}`);
      clearInterval(pollingHandle);
      pollingHandle = null;
    });
  }, 2000);

  pollJob(jobId).catch((err) => {
    console.debug(`Error consultando estado: ${err.message}`);
  });
});

fileInput.addEventListener("change", () => {
  updateSelectedFileName();
});

videoPlayer.addEventListener("loadedmetadata", () => {
  resizeOverlay();
  videoPlayer.playbackRate = FIXED_PLAYBACK_RATE;
  setVideoState(true);
  drawOverlayAtCurrentTime();
});

videoPlayer.addEventListener("error", () => {
  setPlaceholder("No se pudo cargar el video", "El procesamiento terminó, pero el navegador no pudo abrir el archivo generado.");
  console.debug("Error cargando el video procesado.");
});

videoPlayer.addEventListener("ratechange", () => {
  if (Math.abs(videoPlayer.playbackRate - FIXED_PLAYBACK_RATE) > 0.001) {
    videoPlayer.playbackRate = FIXED_PLAYBACK_RATE;
  }
});

videoPlayer.addEventListener("play", () => {
  startRenderLoop();
});

videoPlayer.addEventListener("pause", () => {
  stopRenderLoop();
  drawOverlayAtCurrentTime();
});

videoPlayer.addEventListener("seeked", () => {
  resetCrossingState();
  drawOverlayAtCurrentTime();
});

window.addEventListener("resize", () => {
  resizeOverlay();
  resizeHistogramCanvas();
  drawOverlayAtCurrentTime();
});

clearDashboard();
setVideoState(false);
setPlaceholder("No se ha procesado nada", "Procesá un video para mostrar overlays, conteo y detecciones sincronizadas.");
updateSelectedFileName();
setProcessingState(false);
updateProgress(0);
