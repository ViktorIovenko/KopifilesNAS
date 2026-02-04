const STATUS_POLL_MS = 3000;

function setText(id, value) {
  const el = document.getElementById(id);
  if (!el) {
    return;
  }
  el.textContent = value ?? "-";
}

function setStatus(present) {
  const el = document.getElementById("flash-status");
  if (!el) {
    return;
  }
  el.classList.toggle("stopped", !present);
  el.textContent = present ? "Флэшка обнаружена" : "Флэшка не обнаружена";
}

function setCamera(make, model, serial) {
  const container = document.getElementById("flash-camera");
  if (!container) {
    return;
  }
  const parts = [];
  if (make) {
    parts.push(make);
  }
  if (model) {
    parts.push(model);
  }
  if (serial) {
    parts.push(`(S/N ${serial})`);
  }
  container.textContent = parts.length ? `Камера: ${parts.join(" ")}` : "";
}

function setCopyStatus(state) {
  const el = document.getElementById("copy-status");
  if (!el) {
    return;
  }
  const running = Boolean(state && state.running);
  const stopRequested = Boolean(state && state.stop_requested);
  el.classList.toggle("stopped", !running);
  el.textContent = running
    ? `Идет процесс копирования${stopRequested ? " (остановка запрошена)" : ""}`
    : "Завершено";
  setText("last-started", formatDateTime(state?.last_started));
  setText("last-finished", formatDateTime(state?.last_finished));
  setText("last-processed", state?.last_result?.processed);
  setText("last-copied", state?.last_result?.copied);
  setText("last-skipped", state?.last_result?.skipped);
  setText("last-errors", state?.last_result?.errors);
  setText("progress-copied", state?.current_copied ?? 0);
  setText("progress-total", state?.current_total ?? "-");
  setText("copy-duration", formatDuration(state?.last_started, state?.last_finished));

  const btnStart = document.getElementById("btn-start");
  const btnStop = document.getElementById("btn-stop");
  if (btnStart) {
    btnStart.disabled = running;
  }
  if (btnStop) {
    btnStop.disabled = !running;
  }
}

function formatDateTime(value) {
  if (!value) {
    return "-";
  }
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ru-RU", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(dt);
}

function formatDuration(startValue, endValue) {
  if (!startValue || !endValue) {
    return "-";
  }
  const start = new Date(startValue);
  const end = new Date(endValue);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return "-";
  }
  let seconds = Math.max(0, Math.floor((end - start) / 1000));
  const hours = Math.floor(seconds / 3600);
  seconds -= hours * 3600;
  const minutes = Math.floor(seconds / 60);
  seconds -= minutes * 60;

  const parts = [];
  if (hours) {
    parts.push(`${hours}ч`);
  }
  if (minutes || hours) {
    parts.push(`${minutes}м`);
  }
  parts.push(`${seconds}с`);
  return parts.join(" ");
}

function setToggleState(id, labelId, value) {
  const el = document.getElementById(id);
  const label = document.getElementById(labelId);
  if (el) {
    el.checked = Boolean(value);
  }
  if (label) {
    label.textContent = value ? "Включен" : "Выключен";
  }
}

function setVisible(id, visible) {
  const el = document.getElementById(id);
  if (!el) {
    return;
  }
  el.style.display = visible ? "" : "none";
}

async function fetchStatus() {
  try {
    const response = await fetch("/status", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    const flash = data.flash || {};

    setStatus(Boolean(flash.present));
    setText("flash-kind", flash.kind);
    setText("auto-dest", data.auto_dest);
    if (data.source_path) {
      setText("watch-path", data.source_path);
    }
    setText("flash-total", flash.total_files);
    setText("flash-images", flash.image_files);
    setText("flash-videos", flash.video_files);
    setCamera(flash.camera_make, flash.camera_model, flash.camera_serial);
    setCopyStatus(data.copy_state);
    setToggleState("manual-toggle", "manual-toggle-label", data.manual_mode);
    setToggleState("archive-toggle", "archive-toggle-label", data.use_archive);
    setVisible("manual-controls", Boolean(data.manual_mode));
    setVisible("archive-controls", Boolean(data.use_archive));
    setVisible("manual-paths", Boolean(data.manual_mode));
  } catch (err) {
    // Ignore network errors to avoid UI flicker.
  }
}

function renderEvents(items) {
  const container = document.getElementById("events-list");
  if (!container) {
    return;
  }
  if (!items || !items.length) {
    if (!container.querySelector(".events-empty")) {
      container.innerHTML = "<div class=\"meta events-empty\">Пока нет операций.</div>";
    }
    return;
  }

  const prevList = container.querySelector(".events");
  const list = document.createElement("ul");
  list.className = "events";
  items.forEach((item) => {
    const li = document.createElement("li");
    const ts = document.createElement("span");
    ts.className = "event-ts";
    ts.textContent = formatDateTime(item.ts);
    li.appendChild(ts);

    const kind = document.createElement("span");
    kind.className = "event-kind";
    kind.textContent = item.kind || "-";
    li.appendChild(kind);

    const src = document.createElement("span");
    src.className = "event-src";
    src.textContent = item.src || "-";
    li.appendChild(src);

    if (item.dest) {
      const arrow = document.createElement("span");
      arrow.className = "event-arrow";
      arrow.textContent = "→";
      li.appendChild(arrow);

      const dest = document.createElement("span");
      dest.className = "event-dest";
      dest.textContent = item.dest;
      li.appendChild(dest);
    }

    if (item.error) {
      const err = document.createElement("span");
      err.className = "event-error";
      err.textContent = `(${item.error})`;
      li.appendChild(err);
    }

    list.appendChild(li);
  });

  const prevScroll = prevList ? prevList.scrollTop : 0;
  container.innerHTML = "";
  container.appendChild(list);
  list.scrollTop = prevScroll;
}

async function fetchEvents() {
  try {
    const response = await fetch("/events", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    const items = data.events || [];
    const signature = JSON.stringify(items);
    if (fetchEvents.lastSignature === signature) {
      return;
    }
    fetchEvents.lastSignature = signature;
    renderEvents(items);
  } catch (err) {
    // Ignore network errors.
  }
}

document.addEventListener("DOMContentLoaded", () => {
  if (sessionStorage.getItem("scrollY")) {
    const y = Number(sessionStorage.getItem("scrollY"));
    if (!Number.isNaN(y)) {
      window.scrollTo(0, y);
    }
    sessionStorage.removeItem("scrollY");
  }

  fetchStatus();
  setInterval(fetchStatus, STATUS_POLL_MS);
  fetchEvents();
  setInterval(fetchEvents, STATUS_POLL_MS);

  const formatsForm = document.getElementById("formats-form");
  if (formatsForm) {
    formatsForm.addEventListener("submit", (event) => {
      event.preventDefault();
    });
    formatsForm.addEventListener("change", (event) => {
      if (event.target && event.target.name === "formats") {
        const data = new FormData(formatsForm);
        fetch(formatsForm.action, {
          method: "POST",
          body: data,
        }).catch(() => {});
      }
    });
  }

  const refreshBtn = document.getElementById("btn-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      fetchStatus();
      fetchEvents();
    });
  }

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      sessionStorage.setItem("scrollY", String(window.scrollY));
    });
  });

  document.querySelectorAll(".toggle input[type=\"checkbox\"]").forEach((toggle) => {
    toggle.addEventListener("change", (event) => {
      const form = event.target.closest("form");
      if (!form) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const data = new FormData(form);
      fetch(form.action, { method: "POST", body: data })
        .then(() => fetchStatus())
        .catch(() => {});
    });
  });
});
