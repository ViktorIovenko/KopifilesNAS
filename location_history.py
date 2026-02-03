import os
import json
import logging
import calendar
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Any, Optional
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import reverse_geocoder as rg


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        if s.endswith("Z"):
            # Преобразуем Z в +00:00 для fromisoformat
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _parse_latlng(s: str) -> Tuple[float, float]:
    clean = s.replace("°", "").replace("�", "").strip()
    lat_str, lon_str = [x.strip() for x in clean.split(",")]
    return float(lat_str), float(lon_str)


class LocationHistory:
    def __init__(self, base: str):
        """
        base — это либо путь к файлу JSON, либо к папке с JSON-файлами архива.
        """
        self.base = base
        # Если base — это конкретный JSON-файл, запоминаем его
        self.json_file = base if base.lower().endswith(".json") and os.path.isfile(base) else None

        # Настраиваем онлайн-реверс через Nominatim
        try:
            geo = Nominatim(user_agent="kopirnas/1.0", timeout=3)
            self.rev_online = RateLimiter(
                geo.reverse,
                min_delay_seconds=1,
                max_retries=1,
                error_wait_seconds=2,
                swallow_exceptions=True
            )
        except Exception as e:
            logging.debug(f"Nominatim init error: {e}")
            self.rev_online = None

        # Кэш координата→город
        self.cache = self._load_cache()
        # Глобальный индекс точек (для global lookup)
        self._global: Optional[List[Tuple[datetime, Tuple[float, float]]]] = None

    def _load_cache(self) -> dict:
        if os.path.exists("geocache.json"):
            with open("geocache.json", "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_cache(self):
        with open("geocache.json", "w", encoding="utf-8") as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=4)

    def get_city_from_coordinates(self, coords: Tuple[float, float]) -> str:
        key = f"{coords[0]},{coords[1]}"
        # Проверяем кэш
        if key in self.cache:
            return self.cache[key]

        # Offline reverse_geocoder
        try:
            name = rg.search(coords, mode=1)[0].get("name")
            if name:
                self.cache[key] = name
                self._save_cache()
                return name
        except Exception:
            pass

        # Fallback к Nominatim
        if self.rev_online:
            loc = self.rev_online(coords, language="ru")
            if loc and loc.raw:
                addr = loc.raw.get("address", {})
                city = addr.get("city") or addr.get("town") or addr.get("village")
                if city:
                    self.cache[key] = city
                    self._save_cache()
                    return city

        return "Неизвестный город"

    def _load_json(self, path: str) -> Any:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.debug(f"Error reading archive {path}: {e}")
            return {}

    def _extract_points(self, obj: dict) -> List[Tuple[datetime, Tuple[float, float]]]:
        pts: List[Tuple[datetime, Tuple[float, float]]] = []

        def add(ts: str, latlng: Any):
            dt = _parse_iso(ts) if ts else None
            if dt and isinstance(latlng, str):
                coord = _parse_latlng(latlng)
                pts.append((dt, coord))

        # Legacy format: activitySegment or placeVisit
        seg = obj.get("activitySegment") or obj.get("placeVisit")
        if seg:
            start = seg.get("duration", {}).get("startTimestamp")
            if "activitySegment" in seg:
                loc0 = seg["activitySegment"].get("startLocation", {})
                ll = f"{loc0.get('latitudeE7',0)/1e7}, {loc0.get('longitudeE7',0)/1e7}"
                add(start, ll)
            if "placeVisit" in seg:
                loc0 = seg["placeVisit"].get("location", {})
                ll = f"{loc0.get('latitudeE7',0)/1e7}, {loc0.get('longitudeE7',0)/1e7}"
                add(start, ll)
            return pts

        # Newer formats
        add(obj.get("startTime"), None)
        if "visit" in obj:
            tc = obj["visit"].get("topCandidate", {})
            add(obj.get("startTime"), tc.get("placeLocation"))
        if "activity" in obj:
            act = obj["activity"]
            add(obj.get("startTime"), act.get("start", {}).get("latLng"))
            add(obj.get("endTime"), act.get("end", {}).get("latLng"))
        if "timelinePath" in obj:
            for p in obj["timelinePath"]:
                add(p.get("time"), p.get("point"))
        if "latitudeE7" in obj and "longitudeE7" in obj:
            t_ms = obj.get("timestampMs")
            if t_ms:
                dt0 = datetime.fromtimestamp(int(t_ms)/1000, tz=timezone.utc)
                pts.append((dt0, (obj["latitudeE7"]/1e7, obj["longitudeE7"]/1e7)))

        return pts

    def _index(self, data: Any) -> List[Tuple[datetime, Tuple[float, float]]]:
        points: List[Tuple[datetime, Tuple[float, float]]] = []
        if isinstance(data, dict) and "timelineObjects" in data:
            for o in data["timelineObjects"]:
                points.extend(self._extract_points(o))
        elif isinstance(data, dict) and "locations" in data:
            for o in data["locations"]:
                points.extend(self._extract_points(o))
        else:
            iterable = data.values() if isinstance(data, dict) else data
            for item in iterable:
                if isinstance(item, dict):
                    points.extend(self._extract_points(item))
                elif isinstance(item, list):
                    points.extend(self._index(item))

        # Убираем дубли и сортируем по времени
        uniq = {f"{dt.isoformat()};{coord[0]},{coord[1]}": (dt, coord)
                for dt, coord in points}
        return sorted(uniq.values(), key=lambda x: x[0])

    def load_location_history(self, year: int, month: int) -> List[Tuple[datetime, Tuple[float, float]]]:
        # Ищем конкретный файл месяца
        files: List[str] = []
        if self.json_file:
            files = [self.json_file]
        else:
            mname = calendar.month_name[month].lower()
            for root, _, fs in os.walk(self.base):
                for fn in fs:
                    if fn.lower().startswith(f"{year}_{mname}") and fn.lower().endswith(".json"):
                        files.append(os.path.join(root, fn))
                if files:
                    break

        if not files:
            return []

        data = self._load_json(files[0])
        return self._index(data)

    def get_city_for_timestamp(self, ts: float, pts: List[Tuple[datetime, Tuple[float, float]]]) -> str:
        target = datetime.fromtimestamp(ts, tz=timezone.utc)
        best, mind = None, timedelta.max
        for dt, coord in pts:
            diff = abs(dt - target)
            if diff < mind:
                mind, best = diff, coord
        return self.get_city_from_coordinates(best) if best else "Неизвестный город"

    def _build_global(self) -> List[Tuple[datetime, Tuple[float, float]]]:
        if self._global is not None:
            return self._global

        all_pts: List[Tuple[datetime, Tuple[float, float]]] = []
        if self.json_file:
            all_pts = self._index(self._load_json(self.json_file))
        else:
            for root, _, fs in os.walk(self.base):
                for fn in fs:
                    if fn.lower().endswith(".json"):
                        all_pts.extend(self._index(self._load_json(os.path.join(root, fn))))

        all_pts.sort(key=lambda x: x[0])
        self._global = all_pts
        return all_pts

    def get_city_global_for_timestamp(self, ts: float) -> str:
        return self.get_city_for_timestamp(ts, self._build_global())

    def get_date_range(self) -> Optional[Tuple[datetime, datetime]]:
        pts = self._build_global()
        if not pts:
            return None

        return pts[0][0], pts[-1][0]
