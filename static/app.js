const state = {
  series: [],
  activeSeries: null,
  slice: 0,
  echo: 0,
  fitEchoStart: 0,
  fitEchoEnd: 0,
  point: null,
  decay: null,
  image: null,
  imageAbort: null,
  imageObjectUrl: null,
  map: null,
  mapAbort: null,
  mapObjectUrl: null,
  mapInfo: null,
  correctedMap: null,
  correctedMapAbort: null,
  correctedMapObjectUrl: null,
  correctedMapInfo: null,
  fatMap: null,
  fatMapAbort: null,
  fatMapObjectUrl: null,
  fatMapInfo: null,
  decayAbort: null,
  lastWheelAt: 0,
};

const els = {
  sequence: document.querySelector("#sequence"),
  series: document.querySelector("#series"),
  slice: document.querySelector("#slice"),
  echo: document.querySelector("#echo"),
  fitEchoStart: document.querySelector("#fitEchoStart"),
  fitEchoEnd: document.querySelector("#fitEchoEnd"),
  radius: document.querySelector("#radius"),
  autoWindow: document.querySelector("#autoWindow"),
  sliceValue: document.querySelector("#sliceValue"),
  echoValue: document.querySelector("#echoValue"),
  fitRangeValue: document.querySelector("#fitRangeValue"),
  fitWindow: document.querySelector("#fitWindow"),
  scanMeta: document.querySelector("#scanMeta"),
  pointMeta: document.querySelector("#pointMeta"),
  fitValue: document.querySelector("#fitValue"),
  fitMeta: document.querySelector("#fitMeta"),
  imageCanvas: document.querySelector("#imageCanvas"),
  imageWrap: document.querySelector("#imageWrap"),
  mapTitle: document.querySelector("#mapTitle"),
  mapMeta: document.querySelector("#mapMeta"),
  mapCanvas: document.querySelector("#mapCanvas"),
  mapLow: document.querySelector("#mapLow"),
  mapHigh: document.querySelector("#mapHigh"),
  correctedMapMeta: document.querySelector("#correctedMapMeta"),
  correctedMapCanvas: document.querySelector("#correctedMapCanvas"),
  correctedMapLow: document.querySelector("#correctedMapLow"),
  correctedMapHigh: document.querySelector("#correctedMapHigh"),
  fatMapMeta: document.querySelector("#fatMapMeta"),
  fatMapCanvas: document.querySelector("#fatMapCanvas"),
  graphCanvas: document.querySelector("#graphCanvas"),
  signalRows: document.querySelector("#signalRows"),
};

const imageCtx = els.imageCanvas.getContext("2d");
const mapCtx = els.mapCanvas.getContext("2d");
const correctedMapCtx = els.correctedMapCanvas.getContext("2d");
const fatMapCtx = els.fatMapCanvas.getContext("2d");
const graphCtx = els.graphCanvas.getContext("2d");

async function loadSeries() {
  const response = await fetch("/api/series");
  const data = await response.json();
  state.series = data.series;
  renderSeriesOptions();
}

function renderSeriesOptions() {
  const sequence = els.sequence.value;
  const options = state.series.filter((item) => item.sequenceType === sequence);
  els.series.replaceChildren(
    ...options.map((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      return option;
    }),
  );

  if (options.length > 0) {
    setActiveSeries(options[0].id);
  }
}

function setActiveSeries(seriesId) {
  state.activeSeries = state.series.find((item) => item.id === seriesId) || null;
  state.slice = 0;
  state.echo = 0;
  state.fitEchoStart = 0;
  state.fitEchoEnd = Math.max(0, (state.activeSeries?.echoTimes.length || 1) - 1);
  state.point = null;
  state.decay = null;
  syncControls();
  loadImage();
  loadMap();
  loadCorrectedMap();
  loadFatMap();
  renderDecay();
}

function syncControls() {
  const s = state.activeSeries;
  if (!s) return;

  els.slice.max = String(Math.max(0, s.sliceLocations.length - 1));
  els.slice.value = String(state.slice);
  els.slice.disabled = s.sliceLocations.length <= 1;
  els.echo.max = String(Math.max(0, s.echoTimes.length - 1));
  els.echo.value = String(state.echo);
  els.fitEchoStart.max = els.echo.max;
  els.fitEchoStart.value = String(state.fitEchoStart);
  els.fitEchoEnd.max = els.echo.max;
  els.fitEchoEnd.value = String(state.fitEchoEnd);

  const loc = s.sliceLocations[state.slice];
  const te = s.echoTimes[state.echo];
  const fitStartTe = s.echoTimes[state.fitEchoStart];
  const fitEndTe = s.echoTimes[state.fitEchoEnd];
  els.sliceValue.textContent =
    s.sliceLocations.length > 1 ? `${state.slice + 1}/${s.sliceLocations.length}` : "1/1";
  els.echoValue.textContent = `${te.toFixed(2)} ms`;
  els.fitRangeValue.textContent = `${formatNumber(fitStartTe)}-${formatNumber(fitEndTe)} ms`;
  const startPercent = echoPercent(state.fitEchoStart, s.echoTimes.length);
  const endPercent = echoPercent(state.fitEchoEnd, s.echoTimes.length);
  els.fitWindow.style.left = `${startPercent}%`;
  els.fitWindow.style.right = `${100 - endPercent}%`;
  const tr = s.repetitionTime == null ? "TR ?" : `TR ${formatNumber(s.repetitionTime)} ms`;
  const flip = s.flipAngle == null || s.sequenceType !== "GRE" ? "" : `, FA ${formatNumber(s.flipAngle)}`;
  els.scanMeta.textContent = `${s.sequenceType}, ${tr}${flip}, ${s.rows}x${s.cols}, slice ${formatNumber(loc)} mm`;
  els.mapTitle.textContent = `${s.fitLabel} Map`;
}

function echoPercent(index, count) {
  if (count <= 1) return 0;
  return (Math.max(0, Math.min(count - 1, index)) / (count - 1)) * 100;
}

function formatNumber(value) {
  if (value == null || Number.isNaN(value)) return "-";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
}

async function loadImage() {
  const s = state.activeSeries;
  if (!s) return;

  if (state.imageAbort) state.imageAbort.abort();
  state.imageAbort = new AbortController();
  const signal = state.imageAbort.signal;
  const autoWindow = els.autoWindow.checked ? "1" : "0";
  const url =
    `/api/image?series=${encodeURIComponent(s.id)}` +
    `&slice=${state.slice}&echo=${state.echo}&autoWindow=${autoWindow}&t=${Date.now()}`;
  let blob;
  try {
    const response = await fetch(url, { signal });
    blob = await response.blob();
  } catch (error) {
    if (error.name === "AbortError") return;
    throw error;
  }
  if (signal.aborted) return;
  if (state.imageObjectUrl) URL.revokeObjectURL(state.imageObjectUrl);
  state.imageObjectUrl = URL.createObjectURL(blob);

  const image = new Image();
  image.onload = () => {
    if (signal.aborted) return;
    state.image = image;
    drawImage();
  };
  image.onerror = () => {
    if (!signal.aborted) state.image = null;
  };
  image.src = state.imageObjectUrl;
}

async function loadMap() {
  const s = state.activeSeries;
  if (!s) return;
  if (state.mapAbort) state.mapAbort.abort();
  state.mapAbort = new AbortController();
  const signal = state.mapAbort.signal;
  const radius = Number(els.radius.value) || 0;
  const url =
    `/api/map?series=${encodeURIComponent(s.id)}` +
    `&slice=${state.slice}&radius=${radius}` +
    `&echoStart=${state.fitEchoStart}&echoEnd=${state.fitEchoEnd}&t=${Date.now()}`;

  state.map = null;
  state.mapInfo = null;
  els.mapMeta.textContent = "Computing";
  els.mapLow.textContent = "-";
  els.mapHigh.textContent = "-";
  drawMap();

  let response;
  let blob;
  try {
    response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`Map request failed: ${response.status}`);
    blob = await response.blob();
  } catch (error) {
    if (error.name === "AbortError") return;
    els.mapMeta.textContent = error.message;
    drawMap();
    return;
  }
  if (signal.aborted) return;

  state.mapInfo = {
    method: response.headers.get("X-Map-Method"),
    low: Number(response.headers.get("X-Map-Low-Ms")),
    high: Number(response.headers.get("X-Map-High-Ms")),
    valid: Number(response.headers.get("X-Map-Valid-Pixels")),
  };
  if (state.mapObjectUrl) URL.revokeObjectURL(state.mapObjectUrl);
  state.mapObjectUrl = URL.createObjectURL(blob);

  const image = new Image();
  image.onload = () => {
    if (signal.aborted) return;
    state.map = image;
    renderMapInfo();
    drawMap();
  };
  image.onerror = () => {
    if (!signal.aborted) els.mapMeta.textContent = "Map image failed to load";
  };
  image.src = state.mapObjectUrl;
}

async function loadCorrectedMap() {
  const s = state.activeSeries;
  if (!s) return;
  if (state.correctedMapAbort) state.correctedMapAbort.abort();
  state.correctedMap = null;
  state.correctedMapInfo = null;
  els.correctedMapLow.textContent = "-";
  els.correctedMapHigh.textContent = "-";

  if (s.sequenceType !== "GRE") {
    els.correctedMapMeta.textContent = "GRE only";
    drawCorrectedMap();
    return;
  }

  state.correctedMapAbort = new AbortController();
  const signal = state.correctedMapAbort.signal;
  const radius = Number(els.radius.value) || 0;
  const url =
    `/api/fat-corrected-map?series=${encodeURIComponent(s.id)}` +
    `&slice=${state.slice}&radius=${radius}` +
    `&echoStart=${state.fitEchoStart}&echoEnd=${state.fitEchoEnd}&t=${Date.now()}`;

  els.correctedMapMeta.textContent = "Computing";
  drawCorrectedMap();

  let response;
  let blob;
  try {
    response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`Fat-corrected map request failed: ${response.status}`);
    blob = await response.blob();
  } catch (error) {
    if (error.name === "AbortError") return;
    els.correctedMapMeta.textContent = error.message;
    drawCorrectedMap();
    return;
  }
  if (signal.aborted) return;

  state.correctedMapInfo = {
    method: response.headers.get("X-Corrected-Map-Method"),
    low: Number(response.headers.get("X-Corrected-Map-Low-Ms")),
    high: Number(response.headers.get("X-Corrected-Map-High-Ms")),
    valid: Number(response.headers.get("X-Corrected-Map-Valid-Pixels")),
  };
  if (state.correctedMapObjectUrl) URL.revokeObjectURL(state.correctedMapObjectUrl);
  state.correctedMapObjectUrl = URL.createObjectURL(blob);

  const image = new Image();
  image.onload = () => {
    if (signal.aborted) return;
    state.correctedMap = image;
    renderCorrectedMapInfo();
    drawCorrectedMap();
  };
  image.onerror = () => {
    if (!signal.aborted) els.correctedMapMeta.textContent = "Fat-corrected map image failed to load";
  };
  image.src = state.correctedMapObjectUrl;
}

async function loadFatMap() {
  const s = state.activeSeries;
  if (!s) return;
  if (state.fatMapAbort) state.fatMapAbort.abort();
  state.fatMap = null;
  state.fatMapInfo = null;

  if (s.sequenceType !== "GRE") {
    els.fatMapMeta.textContent = "GRE only";
    drawFatMap();
    return;
  }

  state.fatMapAbort = new AbortController();
  const signal = state.fatMapAbort.signal;
  const radius = Number(els.radius.value) || 0;
  const url =
    `/api/fat-map?series=${encodeURIComponent(s.id)}` +
    `&slice=${state.slice}&radius=${radius}` +
    `&echoStart=${state.fitEchoStart}&echoEnd=${state.fitEchoEnd}&t=${Date.now()}`;

  els.fatMapMeta.textContent = "Computing";
  drawFatMap();

  let response;
  let blob;
  try {
    response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`Water-fat map request failed: ${response.status}`);
    blob = await response.blob();
  } catch (error) {
    if (error.name === "AbortError") return;
    els.fatMapMeta.textContent = error.message;
    drawFatMap();
    return;
  }
  if (signal.aborted) return;

  state.fatMapInfo = {
    method: response.headers.get("X-Fat-Method"),
    valid: Number(response.headers.get("X-Fat-Valid-Pixels")),
    displayMax: Number(response.headers.get("X-Fat-Display-Max")),
  };
  if (state.fatMapObjectUrl) URL.revokeObjectURL(state.fatMapObjectUrl);
  state.fatMapObjectUrl = URL.createObjectURL(blob);

  const image = new Image();
  image.onload = () => {
    if (signal.aborted) return;
    state.fatMap = image;
    renderFatMapInfo();
    drawFatMap();
  };
  image.onerror = () => {
    if (!signal.aborted) els.fatMapMeta.textContent = "Water-fat map image failed to load";
  };
  image.src = state.fatMapObjectUrl;
}

function renderMapInfo() {
  const info = state.mapInfo;
  if (!info) return;
  if (Number.isFinite(info.low) && Number.isFinite(info.high)) {
    els.mapLow.textContent = `${formatNumber(info.low)} ms`;
    els.mapHigh.textContent = `${formatNumber(info.high)} ms`;
  }
  els.mapMeta.textContent = `${info.method || "fit"}, ${formatNumber(info.valid)} valid pixels`;
}

function renderCorrectedMapInfo() {
  const info = state.correctedMapInfo;
  if (!info) return;
  if (Number.isFinite(info.low) && Number.isFinite(info.high)) {
    els.correctedMapLow.textContent = `${formatNumber(info.low)} ms`;
    els.correctedMapHigh.textContent = `${formatNumber(info.high)} ms`;
  }
  els.correctedMapMeta.textContent = `${info.method || "fit"}, ${formatNumber(info.valid)} valid pixels`;
}

function renderFatMapInfo() {
  const info = state.fatMapInfo;
  if (!info) return;
  const scale =
    Number.isFinite(info.displayMax) && info.displayMax > 0
      ? `, display 0-${formatNumber(info.displayMax * 100)}%`
      : "";
  els.fatMapMeta.textContent = `${info.method || "fit"}, ${formatNumber(info.valid)} valid pixels, apparent signal scale${scale}`;
}

function resizeCanvasToDisplaySize(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(rect.width * dpr));
  const height = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
}

function drawImage() {
  resizeCanvasToDisplaySize(els.imageCanvas);
  const canvas = els.imageCanvas;
  imageCtx.clearRect(0, 0, canvas.width, canvas.height);

  if (!state.image) return;
  const size = Math.min(canvas.width, canvas.height);
  const x0 = Math.floor((canvas.width - size) / 2);
  const y0 = Math.floor((canvas.height - size) / 2);
  imageCtx.imageSmoothingEnabled = false;
  imageCtx.drawImage(state.image, x0, y0, size, size);

  if (state.point) {
    const px = x0 + ((state.point.x + 0.5) / state.activeSeries.cols) * size;
    const py = y0 + ((state.point.y + 0.5) / state.activeSeries.rows) * size;
    imageCtx.save();
    imageCtx.strokeStyle = "#ff6b55";
    imageCtx.lineWidth = Math.max(1, window.devicePixelRatio || 1);
    imageCtx.beginPath();
    imageCtx.moveTo(px - 11, py);
    imageCtx.lineTo(px + 11, py);
    imageCtx.moveTo(px, py - 11);
    imageCtx.lineTo(px, py + 11);
    imageCtx.stroke();
    const radius = Number(els.radius.value) || 0;
    if (radius > 0) {
      imageCtx.beginPath();
      imageCtx.arc(px, py, (radius / state.activeSeries.cols) * size, 0, Math.PI * 2);
      imageCtx.stroke();
    }
    imageCtx.restore();
  }
}

function drawMap() {
  resizeCanvasToDisplaySize(els.mapCanvas);
  const canvas = els.mapCanvas;
  mapCtx.clearRect(0, 0, canvas.width, canvas.height);
  mapCtx.fillStyle = "#000000";
  mapCtx.fillRect(0, 0, canvas.width, canvas.height);

  if (!state.map) {
    mapCtx.fillStyle = "#dce7ea";
    mapCtx.font = `${13 * (window.devicePixelRatio || 1)}px sans-serif`;
    mapCtx.fillText("Computing map", 18 * (window.devicePixelRatio || 1), 30 * (window.devicePixelRatio || 1));
    return;
  }

  const size = Math.min(canvas.width, canvas.height);
  const x0 = Math.floor((canvas.width - size) / 2);
  const y0 = Math.floor((canvas.height - size) / 2);
  mapCtx.imageSmoothingEnabled = false;
  mapCtx.drawImage(state.map, x0, y0, size, size);

  if (!state.point || !state.activeSeries) return;
  const px = x0 + ((state.point.x + 0.5) / state.activeSeries.cols) * size;
  const py = y0 + ((state.point.y + 0.5) / state.activeSeries.rows) * size;
  mapCtx.save();
  mapCtx.strokeStyle = "#ffffff";
  mapCtx.lineWidth = Math.max(1, window.devicePixelRatio || 1);
  mapCtx.beginPath();
  mapCtx.moveTo(px - 10, py);
  mapCtx.lineTo(px + 10, py);
  mapCtx.moveTo(px, py - 10);
  mapCtx.lineTo(px, py + 10);
  mapCtx.stroke();
  mapCtx.restore();
}

function drawCorrectedMap() {
  resizeCanvasToDisplaySize(els.correctedMapCanvas);
  const canvas = els.correctedMapCanvas;
  correctedMapCtx.clearRect(0, 0, canvas.width, canvas.height);
  correctedMapCtx.fillStyle = "#000000";
  correctedMapCtx.fillRect(0, 0, canvas.width, canvas.height);

  if (!state.correctedMap) {
    correctedMapCtx.fillStyle = "#dce7ea";
    correctedMapCtx.font = `${13 * (window.devicePixelRatio || 1)}px sans-serif`;
    const message = state.activeSeries?.sequenceType === "GRE" ? "Computing map" : "GRE only";
    correctedMapCtx.fillText(message, 18 * (window.devicePixelRatio || 1), 30 * (window.devicePixelRatio || 1));
    return;
  }

  const size = Math.min(canvas.width, canvas.height);
  const x0 = Math.floor((canvas.width - size) / 2);
  const y0 = Math.floor((canvas.height - size) / 2);
  correctedMapCtx.imageSmoothingEnabled = false;
  correctedMapCtx.drawImage(state.correctedMap, x0, y0, size, size);

  if (!state.point || !state.activeSeries) return;
  const px = x0 + ((state.point.x + 0.5) / state.activeSeries.cols) * size;
  const py = y0 + ((state.point.y + 0.5) / state.activeSeries.rows) * size;
  correctedMapCtx.save();
  correctedMapCtx.strokeStyle = "#ffffff";
  correctedMapCtx.lineWidth = Math.max(1, window.devicePixelRatio || 1);
  correctedMapCtx.beginPath();
  correctedMapCtx.moveTo(px - 10, py);
  correctedMapCtx.lineTo(px + 10, py);
  correctedMapCtx.moveTo(px, py - 10);
  correctedMapCtx.lineTo(px, py + 10);
  correctedMapCtx.stroke();
  correctedMapCtx.restore();
}

function drawFatMap() {
  resizeCanvasToDisplaySize(els.fatMapCanvas);
  const canvas = els.fatMapCanvas;
  fatMapCtx.clearRect(0, 0, canvas.width, canvas.height);
  fatMapCtx.fillStyle = "#000000";
  fatMapCtx.fillRect(0, 0, canvas.width, canvas.height);

  if (!state.fatMap) {
    fatMapCtx.fillStyle = "#dce7ea";
    fatMapCtx.font = `${13 * (window.devicePixelRatio || 1)}px sans-serif`;
    const message = state.activeSeries?.sequenceType === "GRE" ? "Computing map" : "GRE only";
    fatMapCtx.fillText(message, 18 * (window.devicePixelRatio || 1), 30 * (window.devicePixelRatio || 1));
    return;
  }

  const size = Math.min(canvas.width, canvas.height);
  const x0 = Math.floor((canvas.width - size) / 2);
  const y0 = Math.floor((canvas.height - size) / 2);
  fatMapCtx.imageSmoothingEnabled = false;
  fatMapCtx.drawImage(state.fatMap, x0, y0, size, size);

  if (!state.point || !state.activeSeries) return;
  const px = x0 + ((state.point.x + 0.5) / state.activeSeries.cols) * size;
  const py = y0 + ((state.point.y + 0.5) / state.activeSeries.rows) * size;
  fatMapCtx.save();
  fatMapCtx.strokeStyle = "#ffffff";
  fatMapCtx.lineWidth = Math.max(1, window.devicePixelRatio || 1);
  fatMapCtx.beginPath();
  fatMapCtx.moveTo(px - 10, py);
  fatMapCtx.lineTo(px + 10, py);
  fatMapCtx.moveTo(px, py - 10);
  fatMapCtx.lineTo(px, py + 10);
  fatMapCtx.stroke();
  fatMapCtx.restore();
}

function canvasPointToPixel(event) {
  const s = state.activeSeries;
  if (!s) return null;
  const rect = els.imageCanvas.getBoundingClientRect();
  const cx = event.clientX - rect.left;
  const cy = event.clientY - rect.top;
  const size = Math.min(rect.width, rect.height);
  const x0 = (rect.width - size) / 2;
  const y0 = (rect.height - size) / 2;
  const nx = (cx - x0) / size;
  const ny = (cy - y0) / size;
  if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return null;
  return {
    x: Math.max(0, Math.min(s.cols - 1, Math.floor(nx * s.cols))),
    y: Math.max(0, Math.min(s.rows - 1, Math.floor(ny * s.rows))),
  };
}

async function loadDecay() {
  const s = state.activeSeries;
  if (!s || !state.point) return;
  if (state.decayAbort) state.decayAbort.abort();
  state.decayAbort = new AbortController();
  const radius = Number(els.radius.value) || 0;
  const url =
    `/api/decay?series=${encodeURIComponent(s.id)}` +
    `&slice=${state.slice}&x=${state.point.x}&y=${state.point.y}&radius=${radius}` +
    `&echoStart=${state.fitEchoStart}&echoEnd=${state.fitEchoEnd}`;
  let response;
  try {
    response = await fetch(url, { signal: state.decayAbort.signal });
  } catch (error) {
    if (error.name === "AbortError") return;
    throw error;
  }
  state.decay = await response.json();
  renderDecay();
  drawImage();
}

function renderDecay() {
  const s = state.activeSeries;
  if (!s) return;

  if (!state.point) {
    els.pointMeta.textContent = "x -, y -";
    els.fitValue.textContent = "-";
    els.fitMeta.textContent = "No point selected";
    els.signalRows.replaceChildren();
    drawGraph(null);
    return;
  }

  els.pointMeta.textContent = `x ${state.point.x}, y ${state.point.y}`;
  const fit = state.decay?.fit;
  if (!fit) {
    els.fitValue.textContent = "-";
    els.fitMeta.textContent = "Loading";
    drawGraph(null);
    return;
  }

  if (fit.valueMs == null) {
    els.fitValue.textContent = "-";
    els.fitMeta.textContent = fit.error || "Fit unavailable";
  } else {
    const corrected = state.decay.correctedFit;
    const correctedValue =
      s.sequenceType === "GRE" && corrected?.valueMs != null
        ? `, corrected ${formatNumber(corrected.valueMs)} ms`
        : "";
    els.fitValue.textContent = `${s.fitLabel} ${formatNumber(fit.valueMs)} ms${correctedValue}`;
    const offset = fit.offset == null ? "" : `, C ${formatNumber(fit.offset)}`;
    const warning = fit.warnings?.length ? ` | ${fit.warnings.join(" ")}` : "";
    const usedCount = fit.used?.filter(Boolean).length || 0;
    const correctedMeta =
      s.sequenceType === "GRE" && corrected?.valueMs != null
        ? ` | corrected R2 ${formatNumber(corrected.r2)}, apparent fat ${formatNumber((corrected.fatFraction ?? 0) * 100)}%`
        : "";
    els.fitMeta.textContent = `${fit.method}, ${usedCount} echoes, S0 ${formatNumber(fit.s0)}${offset}, R2 ${formatNumber(fit.r2)}${correctedMeta}${warning}`;
  }

  const rows = state.decay.echoTimes.map((te, idx) => {
    const row = document.createElement("tr");
    const isUsed = Boolean(fit.used?.[idx]);
    const fitSignal = isUsed ? fit.fitSignals?.[idx] : null;
    row.className = isUsed ? "" : "excluded";
    row.innerHTML = `<td>${formatNumber(te)}</td><td>${formatNumber(state.decay.signals[idx])}</td><td>${fitSignal == null ? "-" : formatNumber(fitSignal)}</td>`;
    return row;
  });
  els.signalRows.replaceChildren(...rows);
  drawGraph(state.decay);
}

function drawGraph(decay) {
  resizeCanvasToDisplaySize(els.graphCanvas);
  const canvas = els.graphCanvas;
  graphCtx.clearRect(0, 0, canvas.width, canvas.height);
  graphCtx.fillStyle = "#ffffff";
  graphCtx.fillRect(0, 0, canvas.width, canvas.height);

  const pad = {
    left: 56 * (window.devicePixelRatio || 1),
    right: 20 * (window.devicePixelRatio || 1),
    top: 22 * (window.devicePixelRatio || 1),
    bottom: 42 * (window.devicePixelRatio || 1),
  };
  const plotW = canvas.width - pad.left - pad.right;
  const plotH = canvas.height - pad.top - pad.bottom;
  graphCtx.strokeStyle = "#d7dee2";
  graphCtx.lineWidth = 1;
  graphCtx.strokeRect(pad.left, pad.top, plotW, plotH);

  if (!decay) {
    graphCtx.fillStyle = "#627178";
    graphCtx.font = `${13 * (window.devicePixelRatio || 1)}px sans-serif`;
    graphCtx.fillText("Select a point on the image", pad.left + 14, pad.top + 28);
    return;
  }

  const xs = decay.echoTimes;
  const ys = decay.signals.filter((value) => Number.isFinite(value));
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = 0;
  const showCorrectedFit = decay.correctedFit?.valueMs != null;
  const correctedFitYs = showCorrectedFit
    ? (decay.correctedFit.rawFitSignals || []).filter((value) => Number.isFinite(value))
    : [];
  const yMax = Math.max(...ys, ...(decay.fit.fitSignals || []), ...correctedFitYs, 1);
  const sx = (x) => pad.left + ((x - xMin) / Math.max(1e-9, xMax - xMin)) * plotW;
  const sy = (y) => pad.top + plotH - ((y - yMin) / Math.max(1e-9, yMax - yMin)) * plotH;

  drawAxisLabels(xMin, xMax, yMax, pad, plotW, plotH);

  drawGraphFitLine(decay.echoTimes, decay.fit.fitSignals, decay.fit.used, sx, sy, "#c64b3c", []);
  if (showCorrectedFit) {
    drawGraphFitLine(
      decay.echoTimes,
      decay.correctedFit.rawFitSignals,
      decay.correctedFit.used,
      sx,
      sy,
      "#efc946",
      [6 * (window.devicePixelRatio || 1), 4 * (window.devicePixelRatio || 1)],
    );
  }

  decay.echoTimes.forEach((x, i) => {
    const y = decay.signals[i];
    if (!Number.isFinite(y)) return;
    graphCtx.beginPath();
    graphCtx.arc(sx(x), sy(y), (decay.fit.used?.[i] ? 4 : 3) * (window.devicePixelRatio || 1), 0, Math.PI * 2);
    graphCtx.fillStyle = decay.fit.used?.[i] ? "#007c89" : "#9ba7ac";
    graphCtx.fill();
  });

  const legendItems = [
    { label: "Measured", color: "#007c89", kind: "point" },
    { label: "Fit", color: "#c64b3c", kind: "line" },
  ];
  if (showCorrectedFit) {
    legendItems.push({ label: "Fat-corrected", color: "#efc946", kind: "dash" });
  }
  drawGraphLegend(pad, legendItems);
}

function drawGraphFitLine(xs, ys, used, sx, sy, color, dash) {
  if (!ys?.length) return;
  graphCtx.save();
  graphCtx.beginPath();
  graphCtx.setLineDash(dash);
  let onSegment = false;
  xs.forEach((x, i) => {
    const y = ys[i];
    if (!used?.[i] || !Number.isFinite(y)) {
      onSegment = false;
      return;
    }
    if (!onSegment) {
      graphCtx.moveTo(sx(x), sy(y));
      onSegment = true;
    } else {
      graphCtx.lineTo(sx(x), sy(y));
    }
  });
  graphCtx.strokeStyle = color;
  graphCtx.lineWidth = 2 * (window.devicePixelRatio || 1);
  graphCtx.stroke();
  graphCtx.restore();
}

function drawGraphLegend(pad, items) {
  const dpr = window.devicePixelRatio || 1;
  let x = pad.left + 10 * dpr;
  const y = pad.top + 16 * dpr;
  graphCtx.save();
  graphCtx.font = `${11 * dpr}px sans-serif`;
  graphCtx.textAlign = "left";
  graphCtx.textBaseline = "middle";
  items.forEach((item) => {
    graphCtx.strokeStyle = item.color;
    graphCtx.fillStyle = item.color;
    graphCtx.lineWidth = 2 * dpr;
    graphCtx.setLineDash(item.kind === "dash" ? [6 * dpr, 4 * dpr] : []);
    if (item.kind === "point") {
      graphCtx.beginPath();
      graphCtx.arc(x + 6 * dpr, y, 3.5 * dpr, 0, Math.PI * 2);
      graphCtx.fill();
    } else {
      graphCtx.beginPath();
      graphCtx.moveTo(x, y);
      graphCtx.lineTo(x + 16 * dpr, y);
      graphCtx.stroke();
    }
    graphCtx.setLineDash([]);
    graphCtx.fillStyle = "#29363b";
    graphCtx.fillText(item.label, x + 22 * dpr, y);
    x += (item.label.length * 7 + 46) * dpr;
  });
  graphCtx.restore();
}

function drawAxisLabels(xMin, xMax, yMax, pad, plotW, plotH) {
  const dpr = window.devicePixelRatio || 1;
  graphCtx.fillStyle = "#627178";
  graphCtx.font = `${11 * dpr}px sans-serif`;
  graphCtx.textAlign = "center";
  graphCtx.fillText(`${formatNumber(xMin)} ms`, pad.left, pad.top + plotH + 24 * dpr);
  graphCtx.fillText(`${formatNumber(xMax)} ms`, pad.left + plotW, pad.top + plotH + 24 * dpr);
  graphCtx.fillText("TE", pad.left + plotW / 2, pad.top + plotH + 38 * dpr);
  graphCtx.textAlign = "right";
  graphCtx.fillText("0", pad.left - 8 * dpr, pad.top + plotH + 4 * dpr);
  graphCtx.fillText(formatNumber(yMax), pad.left - 8 * dpr, pad.top + 4 * dpr);
  graphCtx.save();
  graphCtx.translate(16 * dpr, pad.top + plotH / 2);
  graphCtx.rotate(-Math.PI / 2);
  graphCtx.textAlign = "center";
  graphCtx.fillText("Signal", 0, 0);
  graphCtx.restore();
}

function bumpControl(kind, delta) {
  const s = state.activeSeries;
  if (!s) return;
  if (kind === "slice") {
    const max = s.sliceLocations.length - 1;
    state.slice = Math.max(0, Math.min(max, state.slice + delta));
  } else {
    const max = s.echoTimes.length - 1;
    state.echo = Math.max(0, Math.min(max, state.echo + delta));
  }
  syncControls();
  loadImage();
  if (kind === "slice") {
    loadMap();
    loadCorrectedMap();
    loadFatMap();
  }
  if (state.point) {
    loadDecay();
  }
}

function setFitRange(kind, rawValue) {
  const echoCount = state.activeSeries?.echoTimes.length || 0;
  if (echoCount < 2) return;

  const value = Math.max(0, Math.min(echoCount - 1, Number(rawValue)));
  if (kind === "start") {
    state.fitEchoStart = Math.min(value, state.fitEchoEnd - 1);
  } else {
    state.fitEchoEnd = Math.max(value, state.fitEchoStart + 1);
  }
  syncControls();
  loadMap();
  loadCorrectedMap();
  loadFatMap();
  if (state.point) {
    loadDecay();
  }
}

els.sequence.addEventListener("change", renderSeriesOptions);
els.series.addEventListener("change", () => setActiveSeries(els.series.value));
els.slice.addEventListener("input", () => {
  state.slice = Number(els.slice.value);
  syncControls();
  loadImage();
  loadMap();
  loadCorrectedMap();
  loadFatMap();
  if (state.point) {
    loadDecay();
  }
});
els.echo.addEventListener("input", () => {
  state.echo = Number(els.echo.value);
  syncControls();
  loadImage();
});
els.fitEchoStart.addEventListener("input", () => setFitRange("start", els.fitEchoStart.value));
els.fitEchoEnd.addEventListener("input", () => setFitRange("end", els.fitEchoEnd.value));
els.radius.addEventListener("change", () => {
  loadMap();
  loadCorrectedMap();
  loadFatMap();
  if (state.point) {
    loadDecay();
  }
  drawImage();
});
els.autoWindow.addEventListener("change", loadImage);
els.imageCanvas.addEventListener("click", (event) => {
  const point = canvasPointToPixel(event);
  if (!point) return;
  state.point = point;
  drawImage();
  drawMap();
  drawCorrectedMap();
  drawFatMap();
  loadDecay();
});
els.imageWrap.addEventListener(
  "wheel",
  (event) => {
    const s = state.activeSeries;
    if (!s) return;
    event.preventDefault();
    const now = performance.now();
    if (now - state.lastWheelAt < 45) return;
    state.lastWheelAt = now;
    const direction = event.deltaY > 0 ? 1 : -1;
    bumpControl(s.sliceLocations.length > 1 ? "slice" : "echo", direction);
  },
  { passive: false },
);
window.addEventListener("keydown", (event) => {
  if (event.target.matches("input, select")) return;
  if (event.key === "ArrowUp") bumpControl("slice", -1);
  if (event.key === "ArrowDown") bumpControl("slice", 1);
  if (event.key === "ArrowLeft") bumpControl("echo", -1);
  if (event.key === "ArrowRight") bumpControl("echo", 1);
});
window.addEventListener("resize", () => {
  drawImage();
  drawMap();
  drawCorrectedMap();
  drawFatMap();
  renderDecay();
});

loadSeries().catch((error) => {
  els.scanMeta.textContent = error.message;
});
