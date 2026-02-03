import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ENV_PATH = os.path.join(os.path.abspath(os.path.dirname(__file__)), ".env")


def load_env_file() -> dict:
    loaded = {}
    if not os.path.exists(ENV_PATH):
        return loaded
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                key, value = raw.split("=", 1)
                k = key.strip().lstrip("\ufeff")
                v = value.strip().strip("\"'")  # remove optional quotes
                if not os.environ.get(k):
                    os.environ[k] = v
                loaded[k] = v
    except Exception:
        return loaded
    return loaded


def send_telegram_message(text: str) -> None:
    load_env_file()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_USER_ID")
    if not token or not chat_id:
        raise RuntimeError(
            f"Не задан TELEGRAM_BOT_TOKEN или TELEGRAM_USER_ID (token={bool(token)}, id={bool(chat_id)})"
        )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = Request(url, data=data, method="POST")
    with urlopen(req, timeout=5) as _:
        pass
