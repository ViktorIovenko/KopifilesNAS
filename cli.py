from __future__ import annotations

import os
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from flask import Flask, redirect, render_template, url_for
try:
    import tkinter as tk
    from tkinter import filedialog
    TK_AVAILABLE = True
except Exception:
    tk = None
    filedialog = None
    TK_AVAILABLE = False

from bot import load_env_file, send_telegram_message

from copy_process import CopyProcess, DEFAULT_PHOTO_EXT, DEFAULT_VIDEO_EXT
from PIL import ExifTags, Image

app = Flask(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
WATCH_PATH = r"\\Vittorio\Dev1Partition1"
DRONE_DEST = r"\\Vittorio\DRONE"
POCKET_DEST = r"\\Vittorio\POCKET"
FOTO_DEST = r"\\Vittorio\FOTO"
EVENTS_PATH = os.path.join(BASE_DIR, "copy_events.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")
manual_mode: bool = False
manual_src_override: Optional[str] = None
manual_dst_override: Optional[str] = None
manual_formats_override: Optional[List[str]] = None

_events_lock = threading.Lock()
_events: List[dict] = []
load_env_file()

@dataclass
class CopyConfigView:
    src: Optional[str]
    dst: Optional[str]
    archive: Optional[str]

@dataclass
class FileCounts:
    total: Optional[int]
    candidates: Optional[int]

@dataclass
class CopyStateView:
    running: bool
    stop_requested: bool
    last_started: Optional[str]
    last_finished: Optional[str]
    last_error: Optional[str]
    last_result: Optional[dict]
    last_mode: Optional[str]
    current_total: Optional[int]
    current_copied: Optional[int]

@dataclass
class FlashInfo:
    present: bool
    kind: Optional[str]
    total_files: Optional[int]
    image_files: Optional[int]
    video_files: Optional[int]
    camera_make: Optional[str]
    camera_model: Optional[str]
    camera_serial: Optional[str]
    ext_counts: Optional[Dict[str, int]]

PHOTO_EXTENSIONS = {ext.lower() for ext in DEFAULT_PHOTO_EXT}
VIDEO_EXTENSIONS = {ext.lower() for ext in DEFAULT_VIDEO_EXT}
EXIF_TAGS = {v: k for k, v in ExifTags.TAGS.items()}

def _count_source_files(src_dir: Optional[str], allowed_formats: List[str]) -> FileCounts:
    if not src_dir or not os.path.isdir(src_dir):
        return FileCounts(total=None, candidates=None)

    total = 0
    candidates = 0
    allowed = {ext.lower() for ext in allowed_formats}
    for root, _, files in os.walk(src_dir):
        for fname in files:
            total += 1
            ext = os.path.splitext(fname)[1].lower()
            if ext in allowed or ext == ".srt":
                candidates += 1

    return FileCounts(total=total, candidates=candidates)

def _classify_flash(
    image_files: int,
    video_files: int,
    has_dji_prefix: bool,
    has_lrf: bool,
    has_srt: bool,
    camera_detected: bool,
) -> str:
    if has_lrf:
        return "Pocket"
    if has_dji_prefix and has_srt:
        return "Drone"
    if camera_detected:
        return "Foto"
    if image_files > 0 and video_files == 0:
        return "Foto"
    if video_files > 0 and image_files == 0:
        return "Drone"
    if image_files > 0 and video_files > 0:
        return "Foto"
    return "Foto"

def _read_exif_info(image_path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
            if not exif:
                return None, None, None
            make = exif.get(EXIF_TAGS.get("Make"))
            model = exif.get(EXIF_TAGS.get("Model"))
            serial = (
                exif.get(EXIF_TAGS.get("BodySerialNumber"))
                or exif.get(EXIF_TAGS.get("SerialNumber"))
            )
            make = str(make).strip() if make else None
            model = str(model).strip() if model else None
            serial = str(serial).strip() if serial else None

            def clean(value: Optional[str]) -> Optional[str]:
                if not value:
                    return None
                cleaned = "".join(ch for ch in value if ch.isprintable())
                cleaned = cleaned.replace("\uFFFD", "").strip()
                return cleaned or None

            make = clean(make)
            model = clean(model)
            serial = clean(serial)
            return make or None, model or None, serial or None
    except Exception:
        return None, None, None

def _inspect_flash(path: str) -> FlashInfo:
    if not os.path.isdir(path):
        return FlashInfo(
            present=False,
            kind=None,
            total_files=None,
            image_files=None,
            video_files=None,
            camera_make=None,
            camera_model=None,
            camera_serial=None,
            ext_counts=None,
        )

    total = 0
    images = 0
    videos = 0
    has_lrf = False
    has_srt = False
    has_dji_prefix = False
    camera_make = None
    camera_model = None
    camera_serial = None
    ext_counts: Dict[str, int] = {}
    for root, _, files in os.walk(path):
        for fname in files:
            total += 1
            name_lower = fname.lower()
            ext = os.path.splitext(name_lower)[1]
            if ext:
                ext_counts[ext] = ext_counts.get(ext, 0) + 1
            if ext in PHOTO_EXTENSIONS:
                images += 1
                if camera_make is None and camera_model is None and camera_serial is None:
                    make, model, serial = _read_exif_info(os.path.join(root, fname))
                    if make or model or serial:
                        camera_make = make
                        camera_model = model
                        camera_serial = serial
            elif ext in VIDEO_EXTENSIONS:
                videos += 1
            if name_lower.startswith("dji"):
                has_dji_prefix = True
            if ext == ".lrf":
                has_lrf = True
            if ext == ".srt":
                has_srt = True
        if camera_make or camera_model or camera_serial:
            # Уже нашли EXIF-информацию, можно не искать дальше по папкам.
            pass

    return FlashInfo(
        present=True,
        kind=_classify_flash(images, videos, has_dji_prefix, has_lrf, has_srt, bool(camera_make or camera_model or camera_serial)),
        total_files=total,
        image_files=images,
        video_files=videos,
        camera_make=camera_make,
        camera_model=camera_model,
        camera_serial=camera_serial,
        ext_counts=ext_counts,
    )

def _default_popular_formats(ext_counts: Optional[Dict[str, int]]) -> List[str]:
    popular = [".jpg", ".jpeg", ".png", ".mp4", ".mov", ".avi", ".mkv", ".heic"]
    if not ext_counts:
        return [ext for ext in popular]
    return [ext for ext in popular if ext in ext_counts]


def _format_duration(start_value: Optional[str], end_value: Optional[str]) -> str:
    if not start_value or not end_value:
        return "-"
    try:
        start = datetime.fromisoformat(start_value)
        end = datetime.fromisoformat(end_value)
    except Exception:
        return "-"
    seconds = max(0, int((end - start).total_seconds()))
    hours = seconds // 3600
    seconds -= hours * 3600
    minutes = seconds // 60
    seconds -= minutes * 60
    parts = []
    if hours:
        parts.append(f"{hours}ч")
    if minutes or hours:
        parts.append(f"{minutes}м")
    parts.append(f"{seconds}с")
    return " ".join(parts)


def _format_datetime_local(value: Optional[str]) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return value
    return dt.strftime("%d.%m.%Y %H:%M:%S")

def _active_formats(flash_info: FlashInfo) -> List[str]:
    if manual_formats_override is not None:
        return manual_formats_override
    popular = _default_popular_formats(flash_info.ext_counts)
    return popular if popular else [ext.lower() for ext in DEFAULT_PHOTO_EXT + DEFAULT_VIDEO_EXT]

def _auto_dest_for_type(flash_type: Optional[str]) -> Optional[str]:
    if flash_type == "Pocket":
        return POCKET_DEST
    if flash_type == "Drone":
        return DRONE_DEST
    if flash_type == "Foto":
        return FOTO_DEST
    return None

def _flash_signature(path: str) -> Optional[Tuple[int, float]]:
    if not os.path.isdir(path):
        return None
    total = 0
    latest = 0.0
    for root, _, files in os.walk(path):
        for fname in files:
            total += 1
            try:
                ts = os.path.getmtime(os.path.join(root, fname))
                if ts > latest:
                    latest = ts
            except OSError:
                continue
    return total, latest


def _load_events() -> None:
    global _events
    try:
        if os.path.exists(EVENTS_PATH):
            with open(EVENTS_PATH, "r", encoding="utf-8") as f:
                data = f.read().strip()
                if data:
                    _events = json.loads(data)
    except Exception:
        _events = []

def _save_events() -> None:
    try:
        with open(EVENTS_PATH, "w", encoding="utf-8") as f:
            json.dump(_events, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _reset_events() -> None:
    global _events
    with _events_lock:
        _events = []
        _save_events()

def _append_event(kind: str, src: str, dest: Optional[str], ts: str, error: Optional[str] = None) -> None:
    global _events
    with _events_lock:
        _events.append(
            {
                "ts": ts,
                "kind": kind,
                "src": src,
                "dest": dest,
                "error": error,
            }
        )
        # Keep last 500 entries
        if len(_events) > 500:
            _events = _events[-500:]
        _save_events()

_load_events()

def _select_directory(title: str) -> Optional[str]:
    if not TK_AVAILABLE:
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title=title)
        root.destroy()
        if path:
            return os.path.normpath(path)
    except Exception:
        return None
    return None

class CopyState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._running = False
        self._last_started: Optional[str] = None
        self._last_finished: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_result: Optional[dict] = None
        self._last_mode: Optional[str] = None
        self._current_total: Optional[int] = None
        self._current_copied: Optional[int] = None
        self._stop_requested_flag = False

    def start(self, config: Dict[str, str], *, mode: str = "manual", allowed_formats_override: Optional[List[str]] = None) -> bool:
        with self._lock:
            if self._running:
                self._last_error = "Копирование уже выполняется."
                return False

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(config, stop_event, allowed_formats_override, mode),
                daemon=True,
            )
            self._stop_event = stop_event
            self._thread = thread
            self._running = True
            self._last_error = None
            self._last_result = None
            self._last_started = datetime.now().astimezone().isoformat(timespec="seconds")
            self._last_finished = None
            self._last_mode = mode
            self._current_copied = 0
            self._stop_requested_flag = False
            try:
                src_dir = config.get("SRC")
                if src_dir and allowed_formats_override:
                    counts = _count_source_files(src_dir, allowed_formats_override)
                    self._current_total = counts.candidates
                else:
                    self._current_total = None
            except Exception:
                self._current_total = None
            _reset_events()

            thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self._running or self._stop_event is None:
                self._last_error = "Копирование сейчас не выполняется."
                return False
            self._stop_requested_flag = True
            self._stop_event.set()
            return True

    def snapshot(self) -> CopyStateView:
        with self._lock:
            stop_requested = bool(self._stop_event and self._stop_event.is_set())
            return CopyStateView(
                running=self._running,
                stop_requested=stop_requested,
                last_started=self._last_started,
                last_finished=self._last_finished,
                last_error=self._last_error,
                last_result=self._last_result,
                last_mode=self._last_mode,
                current_total=self._current_total,
                current_copied=self._current_copied,
            )

    def _run(
        self,
        config: Dict[str, str],
        stop_event: threading.Event,
        allowed_formats_override: Optional[List[str]],
        mode: str,
    ) -> None:
        result = None
        try:
            allowed_formats = allowed_formats_override
            flush_every = int(config.get("LOG_FLUSH_EVERY", "1"))
            use_archive = config.get("USE_ARCHIVE", "0") not in ("0", "false", "False")
            log_path = None

            src_dir = config.get("SRC")
            dest_dir = config.get("DST")
            archive_json = config.get("ARCHIVE") or ""
            if not src_dir or not dest_dir:
                raise ValueError("Не задан путь источника или назначения.")

            cp = CopyProcess(
                src_dir=src_dir,
                dest_dir=dest_dir,
                archive_json=archive_json,
                use_archive=use_archive,
                allowed_formats=allowed_formats,
                log_path=log_path,
            )
            def progress_cb(kind, src, dest, ts, error=None):
                if kind == "copied":
                    with self._lock:
                        if self._current_copied is None:
                            self._current_copied = 0
                        self._current_copied += 1
                _append_event(kind, src, dest, ts, error=error)

            result = cp.copy_files(
                flush_every=flush_every,
                stop_event=stop_event,
                progress_callback=progress_cb,
            )

            with self._lock:
                self._last_result = result
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            _append_event(
                "error",
                src="",
                dest=None,
                ts=datetime.now().astimezone().isoformat(timespec="seconds"),
                error=str(exc),
            )
        finally:
            if result is not None:
                status = "успешно" if result.get("errors", 0) == 0 else "с ошибками"
                skipped = result.get("skipped", 0)
                errors = result.get("errors", 0)
                copied = result.get("copied", 0)
                total = result.get("processed", 0)
                started_at = self._last_started
                finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
                duration_text = _format_duration(started_at, finished_at)
                started_text = _format_datetime_local(started_at)
                finished_text = _format_datetime_local(finished_at)
                flash_info = _inspect_flash(WATCH_PATH)
                flash_type = flash_info.kind or "Неизвестно"
                stopped_by_user = self._stop_requested_flag or result.get("stopped", False)
                message = (
                    f"KopirNAS: копирование {'остановлено пользователем' if stopped_by_user else 'завершено ' + status}. "
                    f"Тип: {flash_type}. "
                    f"Скопировано: {copied}, пропущено: {skipped}, ошибок: {errors}, всего: {total}. "
                    f"Время: {started_text} → {finished_text} ({duration_text})."
                )
                try:
                    send_telegram_message(message)
                    _append_event(
                        "info",
                        src="Telegram",
                        dest=None,
                        ts=datetime.now().astimezone().isoformat(timespec="seconds"),
                        error="Сообщение отправлено",
                    )
                except Exception as exc:
                    _append_event(
                        "error",
                        src="Telegram",
                        dest=None,
                        ts=datetime.now().astimezone().isoformat(timespec="seconds"),
                        error=f"Не удалось отправить: {exc}",
                    )
            else:
                try:
                    send_telegram_message("KopirNAS: копирование завершено с ошибкой. Подробности в логе.")
                    _append_event(
                        "info",
                        src="Telegram",
                        dest=None,
                        ts=datetime.now().astimezone().isoformat(timespec="seconds"),
                        error="Сообщение отправлено",
                    )
                except Exception as exc:
                    _append_event(
                        "error",
                        src="Telegram",
                        dest=None,
                        ts=datetime.now().astimezone().isoformat(timespec="seconds"),
                        error=f"Не удалось отправить: {exc}",
                    )
            with self._lock:
                self._running = False
                self._last_finished = datetime.now().astimezone().isoformat(timespec="seconds")
                self._stop_event = None
                self._thread = None

copy_state = CopyState()
archive_override: bool = False
archive_path_override: Optional[str] = None

@app.route("/")
def index():
    source_path = manual_src_override if manual_mode and manual_src_override else WATCH_PATH
    flash_info = _inspect_flash(source_path)
    auto_dest = _auto_dest_for_type(flash_info.kind) if flash_info.present else None

    copy_config = CopyConfigView(
        src=manual_src_override if manual_mode else WATCH_PATH,
        dst=manual_dst_override if manual_mode else auto_dest,
        archive=archive_path_override,
    )

    allowed = _active_formats(flash_info)
    counts = _count_source_files(copy_config.src, allowed)
    state = copy_state.snapshot()
    use_archive = archive_override
    archive_path = copy_config.archive if use_archive else None

    return render_template(
        "index.html",
        copy_config=copy_config,
        counts=counts,
        state=state,
        flash_info=flash_info,
        watch_path=source_path,
        use_archive=use_archive,
        archive_path=archive_path,
        auto_dest=auto_dest,
        manual_mode=manual_mode,
        manual_formats=_active_formats(flash_info),
        events=_events,
        tk_available=TK_AVAILABLE,
    )

@app.get("/status")
def status():
    source_path = manual_src_override if manual_mode and manual_src_override else WATCH_PATH
    flash_info = _inspect_flash(source_path)
    auto_dest = _auto_dest_for_type(flash_info.kind) if flash_info.present else None
    state = copy_state.snapshot()
    return {
        "flash": {
            "present": flash_info.present,
            "kind": flash_info.kind,
            "total_files": flash_info.total_files,
            "image_files": flash_info.image_files,
            "video_files": flash_info.video_files,
            "camera_make": flash_info.camera_make,
            "camera_model": flash_info.camera_model,
            "camera_serial": flash_info.camera_serial,
        },
        "auto_dest": auto_dest,
        "source_path": source_path,
        "manual_mode": manual_mode,
        "use_archive": archive_override,
        "copy_state": {
            "running": state.running,
            "stop_requested": state.stop_requested,
            "last_started": state.last_started,
            "last_finished": state.last_finished,
            "last_error": state.last_error,
            "last_result": state.last_result,
            "current_total": state.current_total,
            "current_copied": state.current_copied,
        },
    }

@app.get("/events")
def events():
    with _events_lock:
        return {"events": list(_events)}

@app.post("/start")
def start_copy():
    source_path = manual_src_override if manual_mode and manual_src_override else WATCH_PATH
    flash_info = _inspect_flash(source_path)
    auto_dest = _auto_dest_for_type(flash_info.kind) if flash_info.present else None
    config: Dict[str, str] = {
        "USE_ARCHIVE": "1" if archive_override else "0",
        "ARCHIVE": archive_path_override or "",
        "LOG_FLUSH_EVERY": "1",
    }
    if manual_mode:
        if manual_src_override:
            config["SRC"] = manual_src_override
        if manual_dst_override:
            config["DST"] = manual_dst_override
        else:
            config["DST"] = auto_dest or POCKET_DEST
    else:
        config["SRC"] = WATCH_PATH
        config["DST"] = auto_dest or POCKET_DEST
    formats_override = _active_formats(flash_info)
    copy_state.start(config, mode="manual", allowed_formats_override=formats_override)
    return redirect(url_for("index"))

@app.post("/stop")
def stop_copy():
    copy_state.stop()
    return redirect(url_for("index"))

@app.post("/toggle-archive")
def toggle_archive():
    global archive_override
    archive_override = not archive_override
    return redirect(url_for("index"))

@app.post("/set-archive-path")
def set_archive_path():
    global archive_path_override
    from flask import request

    value = (request.form.get("archive_path") or "").strip()
    archive_path_override = value or None
    return redirect(url_for("index"))

@app.post("/toggle-manual")
def toggle_manual():
    global manual_mode
    manual_mode = not manual_mode
    return redirect(url_for("index"))

@app.post("/set-src-watch")
def set_src_watch():
    global manual_src_override
    manual_src_override = WATCH_PATH
    return redirect(url_for("index"))

@app.post("/set-dst-drone")
def set_dst_drone():
    global manual_dst_override
    manual_dst_override = DRONE_DEST
    return redirect(url_for("index"))

@app.post("/set-dst-pocket")
def set_dst_pocket():
    global manual_dst_override
    manual_dst_override = POCKET_DEST
    return redirect(url_for("index"))

@app.post("/set-dst-foto")
def set_dst_foto():
    global manual_dst_override
    manual_dst_override = FOTO_DEST
    return redirect(url_for("index"))

@app.post("/pick-src")
def pick_src():
    global manual_src_override
    path = _select_directory("Выберите папку-источник")
    if path:
        manual_src_override = path
    return redirect(url_for("index"))

@app.post("/pick-dst")
def pick_dst():
    global manual_dst_override
    path = _select_directory("Выберите папку-назначение")
    if path:
        manual_dst_override = path
    return redirect(url_for("index"))

@app.post("/pick-archive")
def pick_archive():
    global archive_path_override
    path = _select_directory("Выберите папку архива перемещений")
    if path:
        archive_path_override = path
    return redirect(url_for("index"))

@app.post("/set-formats")
def set_formats():
    global manual_formats_override
    from flask import request

    selected = request.form.getlist("formats")
    manual_formats_override = [ext.lower() for ext in selected] if selected else None
    return redirect(url_for("index"))

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("DEBUG", "0") in ("1", "true", "True")
    app.run(host=host, port=port, debug=debug)
