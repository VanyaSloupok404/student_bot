import logging
from typing import Any

from bot.core.http import http_client

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "StudentRoutineAssistantBot/1.0"}


async def geocode_address(query: str) -> tuple[float, float, str] | None:
    session = http_client.get_session()
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}

    try:
        async with session.get(url, params=params, headers=HEADERS, timeout=10) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if not data:
                return None
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            display_name = data[0].get("display_name", query)
            return lat, lon, display_name
    except Exception as exc:
        logger.error("Geocoding failed for '%s': %s", query, exc)
        return None


async def calculate_commute(home_address: str, college_address: str, travel_mode: str = "transit") -> dict[str, Any]:
    # 1. Точный геокодинг адресов через OpenStreetMap
    geo_home = await geocode_address(home_address)
    if not geo_home:
        return {"status": "error", "error_message": f"Не удалось найти адрес отправления: '{home_address}'"}

    geo_college = await geocode_address(college_address)
    if not geo_college:
        return {"status": "error", "error_message": f"Не удалось найти адрес прибытия: '{college_address}'"}

    lat1, lon1, home_name = geo_home
    lat2, lon2, college_name = geo_college

    session = http_client.get_session()
    mode_lower = travel_mode.lower()

    # 2. Выбор профиля и реальный расчет по дорожной сети OSRM
    if any(m in mode_lower for m in ["пешком", "foot", "walk"]):
        profile = "foot"
        osrm_url = f"http://router.project-osrm.org/route/v1/foot/{lon1},{lat1};{lon2},{lat2}?overview=false"
    else:
        profile = "driving"
        osrm_url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"

    try:
        async with session.get(osrm_url, timeout=10) as resp:
            if resp.status != 200:
                return {"status": "error", "error_message": f"Ошибка сервиса маршрутизации OSRM: HTTP {resp.status}"}
            route_data = await resp.json()

            if "routes" not in route_data or not route_data["routes"]:
                return {"status": "error", "error_message": "Маршрут между указанными точками не найден"}

            raw_duration_sec = route_data["routes"][0]["duration"]
            distance_km = route_data["routes"][0]["distance"] / 1000.0

    except Exception as exc:
        logger.error("OSRM routing failed: %s", exc)
        return {"status": "error", "error_message": f"Сбой расчета маршрута: {exc}"}

    # 3. Детерминированный расчет времени с учетом специфики транспорта
    if any(m in mode_lower for m in ["пешком", "foot", "walk"]):
        commute_minutes = max(5, round(raw_duration_sec / 60))
        summary = f"Пеший маршрут ({distance_km:.1f} км)"
        traffic_note = "Расчет основан на средней пешеходной скорости 4.5 км/ч"

    elif any(m in mode_lower for m in ["авто", "машина", "car", "такси"]):
        # Коэффициент утреннего часа пик x1.35 + 5 мин прогрев/парковка
        commute_minutes = max(10, round((raw_duration_sec / 60) * 1.35 + 5))
        summary = f"Поездка на авто ({distance_km:.1f} км по дорожной сети)"
        traffic_note = "В расчет включен утренний коэффициент заторов (x1.35) и время на парковку"

    else:
        # Общественный транспорт (метро / автобус): средняя скорость городской сети 22 км/ч + 12 минут на пересадки и подход
        transit_drive_mins = (distance_km / 22.0) * 60.0
        commute_minutes = max(15, round(transit_drive_mins + 12))
        summary = f"Общественный транспорт / метро (расстояние: {distance_km:.1f} км)"
        traffic_note = "В расчет заложено время на ожидание транспорта, вход/выход со станций и пересадки"

    return {
        "status": "success",
        "commute_minutes": commute_minutes,
        "distance_km": round(distance_km, 1),
        "route_summary": summary,
        "traffic_note": traffic_note,
        "home_full": home_name[:60] + "...",
        "college_full": college_name[:60] + "...",
    }
