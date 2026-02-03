import os
import shutil
from datetime import datetime
from typing import Optional, List

from file_processor import FileProcessor


def now_local_iso() -> str:
    """Локальное время ISO8601 (с часовым поясом), сек."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


# Базовые поддерживаемые форматы (используются, если не передали allowed_formats)
DEFAULT_PHOTO_EXT = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff']
DEFAULT_VIDEO_EXT = ['.mp4', '.avi', '.mov', '.mkv']  # .srt обрабатываем косвенно


class CopyProcess:
    def __init__(
        self,
        src_dir: str,
        dest_dir: str,
        archive_json: str,
        use_archive: bool = True,
        allowed_formats: Optional[List[str]] = None,
        log_path: Optional[str] = None,
    ):
        self.src_dir = src_dir
        self.dest_dir = dest_dir
        self.archive_json = archive_json
        self.use_archive = use_archive
        self.allowed_formats = allowed_formats  # может быть None
        # Лог-файл отключен (используем только copy_events)
        self.log_path = log_path
        self.processor = FileProcessor(location_history_base_folder=archive_json)

    # ------------------------------------------------------------------ #
    #   Основной метод копирования
    #   flush_every: сколько записей накапливать перед flush в лог
    # ------------------------------------------------------------------ #
    def copy_files(self, flush_every: int = 1, stop_event=None, progress_callback=None):
        # Какими расширениями ограничиваемся?
        if self.allowed_formats is None:
            allowed = DEFAULT_PHOTO_EXT + DEFAULT_VIDEO_EXT
        else:
            allowed = [ext.lower() for ext in self.allowed_formats]

        summary = {
            "processed": 0,
            "copied": 0,
            "skipped": 0,
            "errors": 0,
            "stopped": False,
        }

        # Проходим по всем файлам
        stop_requested = False
        for root, _, files in os.walk(self.src_dir):
            for fname in files:
                if stop_event is not None and getattr(stop_event, "is_set", None):
                    if stop_event.is_set():
                        summary["stopped"] = True
                        stop_requested = True
                        if progress_callback:
                            progress_callback("stopped", "", None, now_local_iso())
                        break
                ext = os.path.splitext(fname)[1].lower()
                if allowed and ext not in allowed and ext != '.srt':
                    # пропускаем непопулярные типы
                    continue

                src_path = os.path.join(root, fname)
                summary["processed"] += 1
                try:
                    # Получаем дату и город
                    file_dt, city, location_found = self.processor.process_file(
                        file_path=src_path,
                        use_archive=self.use_archive,
                        allowed_formats=allowed,
                    )

                    # Базовая папка: YYYY\YYYY-MM-DD-Город (если есть координаты или архив перемещений)
                    year, month, day = file_dt.year, file_dt.month, file_dt.day
                    year_folder = str(year)
                    if location_found or self.use_archive:
                        base_folder = f"{year}-{month:02d}-{day:02d}-{city or 'Прочие'}"
                    else:
                        base_folder = f"{year}-{month:02d}-{day:02d}"
                    dest_base = os.path.join(self.dest_dir, year_folder, base_folder)

                    # Раздельно фото и видео
                    if ext in DEFAULT_VIDEO_EXT:
                        dest_folder = os.path.join(dest_base, "Videos")
                    else:
                        dest_folder = dest_base

                    os.makedirs(dest_folder, exist_ok=True)
                    dest_path = os.path.join(dest_folder, fname)

                    # Копируем
                    if not os.path.exists(dest_path):
                        shutil.copy2(src_path, dest_path)
                        summary["copied"] += 1
                        if progress_callback:
                            progress_callback("copied", src_path, dest_path, now_local_iso())
                    else:
                        summary["skipped"] += 1
                        if progress_callback:
                            progress_callback("skipped", src_path, dest_path, now_local_iso())

                    # Видео: копируем соседний .srt (если есть)
                    if ext in DEFAULT_VIDEO_EXT:
                        srt_src = os.path.splitext(src_path)[0] + ".srt"
                        if os.path.exists(srt_src):
                            shutil.copy2(
                                srt_src,
                                os.path.join(dest_folder, os.path.basename(srt_src))
                            )
                            if progress_callback:
                                progress_callback("copied", srt_src, dest_folder, now_local_iso())

                except Exception as e:
                    summary["errors"] += 1
                    if progress_callback:
                        progress_callback("error", src_path, None, now_local_iso(), error=str(e))

            if stop_requested:
                break

        return summary
