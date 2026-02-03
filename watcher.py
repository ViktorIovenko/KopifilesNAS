# watcher.py
import os
import time
import hashlib
from datetime import datetime
from copy_process import CopyProcess  # используем отдельный модуль копирования

# === Пути относительно родительской папки скрипта ===
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
CONFIG_PATH = os.path.join(BASE_DIR, "config.txt")  # путь к конфигу
ARCHIVE_DIR = BASE_DIR                               # папка архива
LOG_PATH = os.path.join(BASE_DIR, "watcher.log")     # файл лога

# --- Дефолты (могут быть переопределены в config.txt) ---
DEFAULT_MIN_COOLDOWN_SEC = 0
DEFAULT_LOG_FLUSH_EVERY = 1


def now_local_iso() -> str:
    """Локальное время с часовым поясом (ISO8601, до секунд)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_config(path: str) -> dict:
    """Парсим config.txt (ключ=значение). Игнорируем пустые строки и #комментарии."""
    config = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                config[k.strip().upper()] = v.strip()
    return config


def parse_formats(cfg: dict):
    """FORMATS=.jpg,.png  -> ['.jpg', '.png'] или None."""
    fmts = cfg.get("FORMATS")
    if not fmts:
        return None
    import re
    parts = [p.strip() for p in re.split(r"[,\s;]+", fmts) if p.strip()]
    return [p if p.startswith(".") else "." + p for p in parts]


def config_signature(path: str) -> str:
    """SHA256 содержимого конфига; '' при ошибке."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def run_copy_process(config: dict):
    """Запускаем копирование согласно конфигу. Возвращаем актуальный cooldown_sec."""
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_file = os.path.join(ARCHIVE_DIR, f"{timestamp}.log")

    print(f"[{now_local_iso()}] Запуск копирования...")

    # Опции
    allowed_formats = parse_formats(config)
    cooldown_sec = int(config.get("COOLDOWN_SEC", DEFAULT_MIN_COOLDOWN_SEC))
    flush_every = int(config.get("LOG_FLUSH_EVERY", DEFAULT_LOG_FLUSH_EVERY))
    use_archive = config.get("USE_ARCHIVE", "1") not in ("0", "false", "False")

    cp = CopyProcess(
        src_dir=config["SRC"],
        dest_dir=config["DST"],
        archive_json=config["ARCHIVE"],
        use_archive=use_archive,
        allowed_formats=allowed_formats,
        log_path=log_file,
    )
    cp.copy_files(flush_every=flush_every)

    print(f"[{now_local_iso()}] Завершено. Лог: {log_file}")
    print(f"[{now_local_iso()}] Ожидание изменения конфига...")

    return cooldown_sec


def main():
    with open(LOG_PATH, "a", encoding="utf-8") as log:

        def log_print(msg: str):
            ts_msg = f"[{now_local_iso()}] {msg}"
            print(ts_msg)
            log.write(ts_msg + "\n")
            log.flush()

        log_print("== KopirNAS watcher запущен ==")

        if os.path.exists(CONFIG_PATH):
            log_print(f"✔ Найден конфиг: {CONFIG_PATH}")
        else:
            log_print(f"✘ Конфиг не найден: {CONFIG_PATH}")

        last_sig = None
        copy_in_progress = False
        last_copy_end = 0.0
        min_cooldown_sec = DEFAULT_MIN_COOLDOWN_SEC

        while True:
            try:
                if os.path.exists(CONFIG_PATH):
                    sig = config_signature(CONFIG_PATH)
                    if last_sig is None:
                        # первая инициализация
                        last_sig = sig
                    elif sig != last_sig:
                        if copy_in_progress:
                            log_print("✏ Конфиг изменился, но копирование уже идёт — отложу.")
                        else:
                            since_last = time.time() - last_copy_end
                            if since_last < min_cooldown_sec:
                                log_print(
                                    f"✏ Конфиг изменился, но прошло {int(since_last)}с (<{min_cooldown_sec}с). Жду."
                                )
                            else:
                                log_print("✏ Конфиг изменился — запускаю копирование.")
                                last_sig = sig
                                cfg = read_config(CONFIG_PATH)
                                copy_in_progress = True
                                try:
                                    min_cooldown_sec = run_copy_process(cfg)
                                finally:
                                    copy_in_progress = False
                                    last_copy_end = time.time()
                else:
                    log_print(f"✘ Конфиг не найден: {CONFIG_PATH}")
            except Exception as e:
                log_print(f"[ОШИБКА] {e}")

            time.sleep(10)  # проверка каждые 10 секунд


if __name__ == "__main__":
    main()
