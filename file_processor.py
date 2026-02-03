import os
import json
import logging
from datetime import datetime
from typing import Optional, Tuple, Dict, List

from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import reverse_geocoder as rg

from location_history import LocationHistory


# Эти списки используются при фильтрации в CopyProcess, но дублируем тут,
# чтобы не было жёсткой зависимости (хотя импортировать тоже можно было).
PHOTO_EXT = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff']
VIDEO_EXT = ['.mp4', '.avi', '.mov', '.mkv', '.srt']  # локально включаем .srt


class FileProcessor:
    def __init__(self, location_history_base_folder: str):
        self.location_history = LocationHistory(location_history_base_folder)
        self.geolocator = Nominatim(user_agent="kopirnas/1.0", timeout=3)
        self.reverse = RateLimiter(
            self.geolocator.reverse,
            min_delay_seconds=1,
            max_retries=1,
            error_wait_seconds=2,
            swallow_exceptions=True
        )
        self.location_cache = self._load_cache()
        self.last_known_city: Optional[str] = None

        # --- Кэши для ускорения ---
        self._month_hist_cache: Dict[Tuple[int, int], List] = {}  # (year,month)->pts  <<< ADDED
        # NOTE: глобальный кэш уже внутри LocationHistory._global

    # ---------------- Кэш geocache.json ---------------- #
    def _load_cache(self) -> dict:
        if os.path.exists("geocache.json"):
            with open("geocache.json", "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_cache(self):
        with open("geocache.json", "w", encoding="utf-8") as f:
            json.dump(self.location_cache, f, ensure_ascii=False, indent=4)

    # ---------------- EXIF ---------------- #
    def get_exif_data(self, image_path: str) -> dict:
        try:
            img = Image.open(image_path)
            data = img._getexif() or {}
            img.close()
            return data
        except Exception as e:
            logging.info(f"EXIF-ошибка в «{image_path}»: {e}")
            return {}

    @staticmethod
    def _exif_datetime(exif_data) -> Optional[datetime]:
        if not exif_data:
            return None
        for tag in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
            for k, v in exif_data.items():
                if TAGS.get(k) == tag:
                    try:
                        return datetime.strptime(v, "%Y:%m:%d %H:%M:%S")
                    except Exception:
                        pass
        return None

    @staticmethod
    def rational_to_float(rational):
        try:
            return float(rational.numerator) / float(rational.denominator)
        except AttributeError:
            return float(rational[0]) / float(rational[1])

    def convert_to_degrees(self, value):
        d = self.rational_to_float(value[0])
        m = self.rational_to_float(value[1])
        s = self.rational_to_float(value[2])
        return d + (m / 60.0) + (s / 3600.0)

    def get_gps_info(self, exif_data):
        gps_info = {}
        for key, val in exif_data.items():
            decoded = TAGS.get(key, key)
            if decoded == "GPSInfo":
                for t in val:
                    sub = GPSTAGS.get(t, t)
                    gps_info[sub] = val[t]
        return gps_info

    def get_coordinates(self, gps_info) -> Optional[Tuple[float, float]]:
        try:
            if all(
                k in gps_info for k in (
                    "GPSLatitude", "GPSLatitudeRef", "GPSLongitude", "GPSLongitudeRef"
                )
            ):
                lat = self.convert_to_degrees(gps_info["GPSLatitude"])
                if gps_info["GPSLatitudeRef"] != "N":
                    lat = -lat
                lon = self.convert_to_degrees(gps_info["GPSLongitude"])
                if gps_info["GPSLongitudeRef"] != "E":
                    lon = -lon
                if abs(lat) < 1e-4 and abs(lon) < 1e-4:
                    return None
                return lat, lon
        except Exception as e:
            logging.info(f"Преобразование координат: {e}")
        return None

    # ---------------- Видео .srt ---------------- #
    def get_coordinates_from_srt(self, srt_file_path: str) -> Optional[Tuple[float, float]]:
        import re
        pattern = re.compile(r'\[latitude: ([\d.-]+)\] \[longitude: ([\d.-]+)\]')
        try:
            with open(srt_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            m = pattern.search(content)
            if m:
                return float(m.group(1)), float(m.group(2))
        except Exception:
            pass
        return None

    # ---------------- Ускоренное получение города ---------------- #
    def get_city_from_coordinates(self, coords: Tuple[float, float]) -> str:
        key = f"{coords[0]},{coords[1]}"
        # локальный кэш
        if key in self.location_cache:
            return self.location_cache[key]

        # offline reverse_geocoder
        try:
            name = rg.search(coords, mode=1)[0].get("name")
            if name:
                self.location_cache[key] = name
                self.save_cache()
                return name
        except Exception:
            pass

        # fallback к Nominatim (медленно, желательно редко)
        try:
            loc = self.reverse(coords, language="ru")
            if loc and loc.raw:
                addr = loc.raw.get("address", {})
                city = addr.get("city") or addr.get("town") or addr.get("village")
                if city:
                    self.location_cache[key] = city
                    self.save_cache()
                    return city
        except Exception:
            pass

        return "Неизвестный город"

    # ---------------- Помесячный кэш архива ---------------- #
    def _get_month_hist(self, year: int, month: int):
        """Вернёт кэшированный список точек архива для (year,month)."""
        key = (year, month)
        if key not in self._month_hist_cache:
            pts = self.location_history.load_location_history(year, month)
            self._month_hist_cache[key] = pts
        return self._month_hist_cache[key]

    # ---------------- Главный метод обработки файла ---------------- #
    def process_file(
        self,
        file_path: str,
        use_archive: bool = True,
        allowed_formats: Optional[list] = None
    ) -> Tuple[datetime, str, bool]:
        file_name = os.path.basename(file_path)
        ext = os.path.splitext(file_name)[1].lower()
        stat = os.stat(file_path)
        file_dt = datetime.fromtimestamp(stat.st_mtime)

        # Когда ограничиваем форматы
        if allowed_formats and ext not in allowed_formats and ext not in ('.srt',):
            return file_dt, ""

        city = "Прочие"
        location_found = False
        timestamp = stat.st_mtime  # базово — mtime файла
        coords = None

        # Фото
        if ext in PHOTO_EXT:
            exif = self.get_exif_data(file_path)
            exif_dt = self._exif_datetime(exif)
            if exif_dt:
                timestamp = exif_dt.timestamp()

            gps = self.get_gps_info(exif)
            coords = self.get_coordinates(gps)
            if coords:
                city = self.get_city_from_coordinates(coords)
                location_found = True
                if city == "Неизвестный город" and use_archive:
                    pts = self._get_month_hist(file_dt.year, file_dt.month)
                    city = self.location_history.get_city_for_timestamp(timestamp, pts)
                    location_found = True
            else:
                if use_archive:
                    pts = self._get_month_hist(file_dt.year, file_dt.month)
                    city = self.location_history.get_city_for_timestamp(timestamp, pts)
                    location_found = True

        # Видео / .srt
        elif ext in VIDEO_EXT:
            srt = file_path if ext == '.srt' else os.path.splitext(file_path)[0] + '.srt'
            if os.path.exists(srt):
                coords = self.get_coordinates_from_srt(srt)
                if coords:
                    city = self.get_city_from_coordinates(coords)
                    location_found = True
            if city == "Прочие" and use_archive:
                pts = self._get_month_hist(file_dt.year, file_dt.month)
                city = self.location_history.get_city_for_timestamp(stat.st_mtime, pts)
                location_found = True

        # Глобальный fallback
        if city in ("Неизвестный город", "", None):
            city_global = self.location_history.get_city_global_for_timestamp(timestamp)
            if city_global not in ("", "Неизвестный город"):
                city = city_global
                # Глобальный fallback не считается подтверждённой геолокацией для структуры папок.

        # Запомним последний успешный
        if city not in ("Неизвестный город", "", None):
            self.last_known_city = city

        return file_dt, city, location_found
