from __future__ import annotations

import os
from typing import Dict

from flask import Flask, redirect, render_template, url_for, request

import core

app = Flask(__name__)


@app.route("/")
def index():
    source_path = core.manual_src_override if core.manual_mode and core.manual_src_override else core.WATCH_PATH
    flash_info = core._inspect_flash(source_path)
    auto_dest = core._auto_dest_for_type(flash_info.kind) if flash_info.present else None

    copy_config = core.CopyConfigView(
        src=core.manual_src_override if core.manual_mode else core.WATCH_PATH,
        dst=core.manual_dst_override if core.manual_mode else auto_dest,
        archive=core.archive_path_override,
    )

    allowed = core._active_formats(flash_info)
    counts = core._count_source_files(copy_config.src, allowed)
    state = core.copy_state.snapshot()
    use_archive = core.archive_override
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
        manual_mode=core.manual_mode,
        manual_formats=core._active_formats(flash_info),
        events=core.get_events(),
        tk_available=core.TK_AVAILABLE,
        manual_src_override=core.manual_src_override,
        manual_dst_override=core.manual_dst_override,
    )


@app.get("/status")
def status():
    source_path = core.manual_src_override if core.manual_mode and core.manual_src_override else core.WATCH_PATH
    flash_info = core._inspect_flash(source_path)
    auto_dest = core._auto_dest_for_type(flash_info.kind) if flash_info.present else None
    state = core.copy_state.snapshot()
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
        "manual_mode": core.manual_mode,
        "use_archive": core.archive_override,
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
    return {"events": core.get_events()}


@app.post("/start")
def start_copy():
    source_path = core.manual_src_override if core.manual_mode and core.manual_src_override else core.WATCH_PATH
    flash_info = core._inspect_flash(source_path)
    auto_dest = core._auto_dest_for_type(flash_info.kind) if flash_info.present else None
    config: Dict[str, str] = {
        "USE_ARCHIVE": "1" if core.archive_override else "0",
        "ARCHIVE": core.archive_path_override or "",
        "LOG_FLUSH_EVERY": "1",
    }
    if core.manual_mode:
        if core.manual_src_override:
            config["SRC"] = core.manual_src_override
        if core.manual_dst_override:
            config["DST"] = core.manual_dst_override
        else:
            config["DST"] = auto_dest or core.POCKET_DEST
    else:
        config["SRC"] = core.WATCH_PATH
        config["DST"] = auto_dest or core.POCKET_DEST

    formats_override = core._active_formats(flash_info)
    core.copy_state.start(config, mode="manual", allowed_formats_override=formats_override)
    return redirect(url_for("index"))


@app.post("/stop")
def stop_copy():
    core.copy_state.stop()
    return redirect(url_for("index"))


@app.post("/toggle-archive")
def toggle_archive():
    core.archive_override = not core.archive_override
    return redirect(url_for("index"))


@app.post("/set-archive-path")
def set_archive_path():
    value = (request.form.get("archive_path") or "").strip()
    core.archive_path_override = value or None
    return redirect(url_for("index"))


@app.post("/toggle-manual")
def toggle_manual():
    core.manual_mode = not core.manual_mode
    return redirect(url_for("index"))


@app.post("/set-src-watch")
def set_src_watch():
    core.manual_src_override = core.WATCH_PATH
    return redirect(url_for("index"))


@app.post("/set-dst-drone")
def set_dst_drone():
    core.manual_dst_override = core.DRONE_DEST
    return redirect(url_for("index"))


@app.post("/set-dst-pocket")
def set_dst_pocket():
    core.manual_dst_override = core.POCKET_DEST
    return redirect(url_for("index"))


@app.post("/set-dst-foto")
def set_dst_foto():
    core.manual_dst_override = core.FOTO_DEST
    return redirect(url_for("index"))


@app.post("/pick-src")
def pick_src():
    path = core.select_directory("Выберите папку-источник")
    if path:
        core.manual_src_override = path
    return redirect(url_for("index"))


@app.post("/pick-dst")
def pick_dst():
    path = core.select_directory("Выберите папку-назначение")
    if path:
        core.manual_dst_override = path
    return redirect(url_for("index"))


@app.post("/pick-archive")
def pick_archive():
    path = core.select_directory("Выберите папку архива перемещений")
    if path:
        core.archive_path_override = path
    return redirect(url_for("index"))


@app.post("/set-formats")
def set_formats():
    selected = request.form.getlist("formats")
    core.manual_formats_override = [ext.lower() for ext in selected] if selected else None
    return redirect(url_for("index"))


@app.post("/set-manual-paths")
def set_manual_paths():
    src = (request.form.get("manual_src") or "").strip()
    dst = (request.form.get("manual_dst") or "").strip()
    core.manual_src_override = src or None
    core.manual_dst_override = dst or None
    return redirect(url_for("index"))


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("DEBUG", "0") in ("1", "true", "True")
    app.run(host=host, port=port, debug=debug)
