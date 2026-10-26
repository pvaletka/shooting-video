"use strict";

// Overlay layout mirrors video/timer_overlay.py (designed for a 1080 px panel).
const LAYOUT = {
  baseWidth: 1080,
  lastShots: 5,
  flashSeconds: 0.12,
  green: "rgb(80,230,110)",
  white: "rgb(255,255,255)",
  yellow: "rgb(255,214,60)",
};
const SEEK_LEAD_SECONDS = 0.3;
const SAVE_DELAY_MS = 500;
const POLL_MS = 1000;
const SOURCE_LABELS = { detector: "детектор", candidate: "кандидат", manual: "вручную" };

const $ = (id) => document.getElementById(id);
const video = $("video");
const canvas = $("overlay");
const ctx = canvas.getContext("2d");

let state = null;       // server state of the current video
let videoId = null;
let saveTimer = null;
let pollTimer = null;
let activeRowId = null;
let resultShownFor = null;

// ---------- helpers ----------

function fmt(seconds) {
  return seconds.toFixed(2);
}

function enabledShots() {
  return state.signals.filter((s) => s.enabled).map((s) => s.t).sort((a, b) => a - b);
}

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || response.statusText);
  }
  return response.json();
}

// ---------- video list & loading ----------

async function refreshVideoList() {
  const videos = await api("/api/videos");
  const select = $("video-select");
  select.length = 1;
  for (const item of videos) {
    select.add(new Option(`${item.name} (${item.id.slice(0, 15)})`, item.id));
  }
  select.value = videoId || "";
}

async function openVideo(id) {
  videoId = id;
  location.hash = id;
  $("video-select").value = id;
  clearTimeout(pollTimer);
  state = await api(`/api/videos/${id}`);
  video.src = `/api/videos/${id}/source`;
  $("workspace").hidden = false;
  onStateChanged();
}

function onStateChanged() {
  const busy = state.status === "analyzing" || state.status === "rendering";
  const labels = {
    analyzing: "Анализ звука…",
    ready: "Готово к разметке",
    rendering: "Рендер видео…",
    done: "Видео готово",
    error: state.error || "Ошибка",
  };
  setStatus(labels[state.status] || state.status, state.status === "error");
  for (const id of ["add-shot", "set-beep", "render"]) $(id).disabled = busy || state.beep === null;

  $("render-progress").hidden = state.status !== "rendering";
  $("render-progress").value = state.progress || 0;
  $("result").hidden = state.status !== "done";
  if (state.status === "done" && resultShownFor !== videoId) {
    // Cache-bust so a re-render replaces the previous result.
    const url = `/api/videos/${videoId}/result?v=${Date.now()}`;
    $("result-video").src = url;
    $("download").href = url;
    resultShownFor = videoId;
  } else if (state.status !== "done" && resultShownFor) {
    $("result-video").removeAttribute("src");
    resultShownFor = null;
  }

  renderTable();
  drawOverlay();
  if (busy) pollTimer = setTimeout(poll, POLL_MS);
}

async function poll() {
  if (!videoId) return;
  const fresh = await api(`/api/videos/${videoId}`);
  // Keep local edits; only take status/progress while a render runs.
  if (state.status === "rendering" && fresh.status !== "analyzing") {
    state = { ...state, status: fresh.status, progress: fresh.progress, error: fresh.error };
  } else {
    state = fresh;
  }
  onStateChanged();
  if (fresh.status !== "analyzing" && fresh.status !== "rendering") refreshVideoList();
}

// ---------- upload ----------

function upload(file) {
  const form = new FormData();
  form.append("file", file);
  const request = new XMLHttpRequest();
  request.open("POST", "/api/videos");
  request.upload.onprogress = (event) => {
    if (event.lengthComputable) setStatus(`Загрузка ${Math.round((100 * event.loaded) / event.total)}%`);
  };
  request.onload = async () => {
    if (request.status !== 200) {
      setStatus(`Ошибка загрузки: ${request.statusText}`, true);
      return;
    }
    const created = JSON.parse(request.responseText);
    await refreshVideoList();
    openVideo(created.id);
  };
  request.onerror = () => setStatus("Ошибка загрузки", true);
  request.send(form);
}

$("file-input").addEventListener("change", (event) => {
  if (event.target.files[0]) upload(event.target.files[0]);
  event.target.value = "";
});

let dragDepth = 0;
document.addEventListener("dragenter", (event) => {
  event.preventDefault();
  dragDepth += 1;
  $("drop-hint").hidden = false;
});
document.addEventListener("dragleave", () => {
  dragDepth -= 1;
  if (dragDepth <= 0) $("drop-hint").hidden = true;
});
document.addEventListener("dragover", (event) => event.preventDefault());
document.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  $("drop-hint").hidden = true;
  const file = event.dataTransfer.files[0];
  if (file) upload(file);
});

$("video-select").addEventListener("change", (event) => {
  if (event.target.value) openVideo(event.target.value);
});

// ---------- table ----------

function renderTable() {
  const tbody = $("rows");
  tbody.textContent = "";
  if (state.beep === null) {
    $("shot-count").textContent = "0";
    $("total").textContent = "—";
    return;
  }

  const beepRow = document.createElement("tr");
  beepRow.className = "beep";
  beepRow.dataset.id = "beep";
  beepRow.innerHTML = `<td></td><td>бип</td><td>0.00</td><td></td><td class="muted">${state.beep.toFixed(3)} с</td><td></td>`;
  beepRow.addEventListener("click", () => seek(state.beep - SEEK_LEAD_SECONDS));
  tbody.append(beepRow);

  let number = 0;
  let previous = null;
  for (const signal of state.signals) {
    const row = document.createElement("tr");
    row.dataset.id = signal.id;
    row.classList.toggle("off", !signal.enabled);

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = signal.enabled;
    checkbox.addEventListener("click", (event) => event.stopPropagation());
    checkbox.addEventListener("change", () => {
      signal.enabled = checkbox.checked;
      changed();
    });

    let numberText = "";
    let splitText = "";
    if (signal.enabled) {
      number += 1;
      numberText = String(number);
      splitText = previous === null ? "" : `+${fmt(signal.t - previous)}`;
      previous = signal.t;
    }

    const cells = [
      checkbox,
      numberText,
      fmt(signal.t - state.beep),
      splitText,
      Object.assign(document.createElement("span"), {
        className: `badge ${signal.source}`,
        textContent: SOURCE_LABELS[signal.source],
      }),
    ];
    for (const content of cells) {
      const cell = document.createElement("td");
      cell.append(content);
      row.append(cell);
    }
    const actions = document.createElement("td");
    if (signal.source === "manual") {
      const remove = Object.assign(document.createElement("button"), {
        className: "delete",
        textContent: "×",
        title: "Удалить",
      });
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        state.signals = state.signals.filter((item) => item !== signal);
        changed();
      });
      actions.append(remove);
    }
    row.append(actions);
    row.addEventListener("click", () => seek(signal.t - SEEK_LEAD_SECONDS));
    tbody.append(row);
  }

  const shots = enabledShots();
  $("shot-count").textContent = String(shots.length);
  $("total").textContent = shots.length ? fmt(shots[shots.length - 1] - state.beep) : "—";
  activeRowId = null;
  updateActiveRow();
}

function updateActiveRow(t = shownTime()) {
  if (!state || state.beep === null) return;
  t += 1e-3;
  let id = null;
  if (t >= state.beep) id = "beep";
  for (const signal of state.signals) {
    if (signal.t <= t) id = signal.id;
  }
  if (id === activeRowId) return;
  activeRowId = id;
  for (const row of $("rows").children) {
    const isActive = row.dataset.id === id;
    row.classList.toggle("active", isActive);
    if (isActive) row.scrollIntoView({ block: "nearest" });
  }
}

// ---------- editing ----------

function changed() {
  if (state.status === "done") state.status = "ready";
  onStateChanged();
  clearTimeout(saveTimer);
  $("save-state").textContent = "сохраняю…";
  saveTimer = setTimeout(save, SAVE_DELAY_MS);
}

async function save() {
  try {
    const saved = await api(`/api/videos/${videoId}/signals`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beep: state.beep, signals: state.signals }),
    });
    state.status = saved.status;
    $("save-state").textContent = "сохранено";
  } catch (error) {
    $("save-state").textContent = `не сохранено: ${error.message}`;
  }
}

function addShotHere() {
  const t = Number(video.currentTime.toFixed(6));
  const id = `m${Date.now().toString(36)}`;
  state.signals.push({ id, t, source: "manual", enabled: true });
  state.signals.sort((a, b) => a.t - b.t);
  changed();
}

function setBeepHere() {
  state.beep = Number(video.currentTime.toFixed(6));
  changed();
}

$("add-shot").addEventListener("click", addShotHere);
$("set-beep").addEventListener("click", setBeepHere);

$("render").addEventListener("click", async () => {
  clearTimeout(saveTimer);
  await save();
  state = await api(`/api/videos/${videoId}/render`, { method: "POST" });
  onStateChanged();
});

// ---------- playback ----------

function seek(t) {
  video.pause();
  video.currentTime = Math.max(0, Math.min(t, video.duration || t));
}

function step(frames) {
  const fps = (state && state.fps) || 30;
  seek(video.currentTime + frames / fps);
}

// Timeline slider: pause while dragging, resume afterwards if it was playing.
const scrub = $("scrub");
let scrubbing = false;
let resumeAfterScrub = false;
scrub.addEventListener("pointerdown", () => {
  scrubbing = true;
  resumeAfterScrub = !video.paused;
  video.pause();
});
scrub.addEventListener("input", () => {
  video.currentTime = Number(scrub.value);
});
function endScrub() {
  if (!scrubbing) return;
  scrubbing = false;
  if (resumeAfterScrub) video.play();
  resumeAfterScrub = false;
}
// A plain click fires no "change", so finish on pointer release too.
scrub.addEventListener("change", endScrub);
scrub.addEventListener("pointerup", endScrub);
scrub.addEventListener("pointercancel", endScrub);
video.addEventListener("loadedmetadata", () => {
  scrub.max = String(video.duration);
});

$("play").addEventListener("click", () => (video.paused ? video.play() : video.pause()));
video.addEventListener("play", () => ($("play").textContent = "❚❚"));
video.addEventListener("pause", () => ($("play").textContent = "▶︎"));
for (const button of document.querySelectorAll("[data-step]")) {
  button.addEventListener("click", () => step(Number(button.dataset.step)));
}
$("rate").addEventListener("change", (event) => (video.playbackRate = Number(event.target.value)));

document.addEventListener("keydown", (event) => {
  // Only text fields keep their own keys; the slider and checkboxes use ours.
  const typing = event.target.matches("select, textarea, input:not([type=range]):not([type=checkbox])");
  if (!state || typing || event.metaKey || event.ctrlKey) return;
  switch (event.key) {
    case " ":
      video.paused ? video.play() : video.pause();
      break;
    case "ArrowLeft":
      event.shiftKey ? seek(video.currentTime - 0.5) : step(-1);
      break;
    case "ArrowRight":
      event.shiftKey ? seek(video.currentTime + 0.5) : step(1);
      break;
    case "a":
    case "A":
      if (!$("add-shot").disabled) addShotHere();
      break;
    case "b":
    case "B":
      if (!$("set-beep").disabled) setBeepHere();
      break;
    default:
      return;
  }
  event.preventDefault();
});

// ---------- overlay ----------

function drawOverlay(t = shownTime()) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.round(rect.width * dpr);
  const height = Math.round(rect.height * dpr);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  ctx.clearRect(0, 0, width, height);
  $("time").textContent = t.toFixed(3);
  if (!state || state.beep === null || !video.videoWidth) return;

  // Layout units -> canvas pixels: same scale the renderer uses on the source.
  const scale = (Math.min(video.videoWidth, video.videoHeight) / LAYOUT.baseWidth) * (width / video.videoWidth);
  const px = (value) => value * scale;
  const beep = state.beep;
  const shots = enabledShots();
  const seen = shots.filter((shot) => shot <= t);
  const finished = shots.length > 0 && t >= shots[shots.length - 1];
  const total = shots.length ? shots[shots.length - 1] - beep : 0;
  const timer = t < beep ? 0 : finished ? total : t - beep;

  ctx.fillStyle = "rgba(0,0,0,0.47)";
  const boxHeight = 186 + 52 * Math.min(seen.length, LAYOUT.lastShots) + (seen.length ? 10 : 0);
  ctx.fillRect(px(24), px(24), px(470), px(boxHeight));

  ctx.textBaseline = "top";
  ctx.lineJoin = "round";
  ctx.lineWidth = px(6);
  ctx.strokeStyle = "black";
  const text = (value, x, y, size, color) => {
    ctx.font = `bold ${px(size)}px Arial, Helvetica, sans-serif`;
    ctx.strokeText(value, px(x), px(y));
    ctx.fillStyle = color;
    ctx.fillText(value, px(x), px(y));
  };

  text(fmt(timer).padStart(5, " "), 44, 30, 104, finished ? LAYOUT.yellow : LAYOUT.white);
  text(`Shots: ${seen.length}`, 48, 140, 40, LAYOUT.white);
  let y = 200;
  for (let index = Math.max(0, seen.length - LAYOUT.lastShots); index < seen.length; index += 1) {
    const shot = seen[index];
    let line = `${index + 1}. ${fmt(shot - beep).padStart(5, " ")}`;
    if (index) line += `  +${fmt(shot - seen[index - 1])}`;
    text(line, 48, y, 40, t - shot < LAYOUT.flashSeconds ? LAYOUT.yellow : LAYOUT.white);
    y += 52;
  }

  if (seen.length && t - seen[seen.length - 1] < LAYOUT.flashSeconds) {
    ctx.strokeStyle = LAYOUT.yellow;
    ctx.lineWidth = px(8);
    ctx.strokeRect(px(8), px(8), width - px(16), height - px(16));
  }
}

// Media time of the frame actually on screen. While playing, currentTime runs
// ahead of the presented frame; the renderer draws frame k at k / fps, so
// using the frame's own timestamp keeps preview and output identical.
let presentedTime = null;

function shownTime() {
  return presentedTime ?? video.currentTime;
}

function onFrame() {
  if (!scrubbing) scrub.value = String(shownTime());
  drawOverlay();
  updateActiveRow();
}

if ("requestVideoFrameCallback" in HTMLVideoElement.prototype) {
  const loop = (_now, metadata) => {
    presentedTime = metadata.mediaTime;
    onFrame();
    video.requestVideoFrameCallback(loop);
  };
  video.requestVideoFrameCallback(loop);
} else {
  const loop = () => {
    onFrame();
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}
// Seeks while paused don't always produce a frame callback.
video.addEventListener("seeking", () => (presentedTime = null));
video.addEventListener("emptied", () => (presentedTime = null));
video.addEventListener("seeked", onFrame);
video.addEventListener("loadedmetadata", onFrame);
window.addEventListener("resize", onFrame);

// ---------- start ----------

refreshVideoList().then(() => {
  const id = location.hash.slice(1);
  if (id) openVideo(id).catch(() => setStatus("Видео не найдено", true));
});
