import logging
import time
from typing import Any

from bot.config import settings
from bot.core.http import http_client

logger = logging.getLogger(__name__)

# Кэш: {нормализованный_город: (timestamp, данные)}
_weather_cache: dict[str, tuple[float, dict[str, Any]]] = {}
CACHE_TTL_SECONDS = 600  # 10 минут


def generate_wardrobe_breakdown(temp: float, feels_like: float, wind_speed: float, pop: float, description: str) -> dict[str, str]:
    if feels_like < -15:
        outerwear = "Тяжелый зимний пуховик / парка с капюшоном"
        base = "Термобелье + теплый шерстяной свитер / флис"
        acc = ["Зимняя теплая шапка", "Шарф", "Теплые перчатки / варежки"]
    elif feels_like < -5:
        outerwear = "Теплая зимняя куртка / пуховик"
        base = "Плотное худи или свитер"
        acc = ["Шапка", "Шарф", "Перчатки"]
    elif feels_like < 5:
        outerwear = "Демисезонная утепленная куртка или плотное пальто"
        base = "Толстовка, свитшот или кофта"
        acc = ["Легкая шапка или капюшон"]
    elif feels_like < 12:
        outerwear = "Легкая куртка, бомбер, тренч или плотная джинсовка"
        base = "Лонгслив, толстовка или рубашка"
        acc = []
    elif feels_like < 18:
        outerwear = "Ветровка, легкий кардиган или плотное худи (без куртки)"
        base = "Футболка или рубашка с длинным рукавом"
        acc = []
    elif feels_like < 23:
        outerwear = "Не требуется (можно взять легкую кофту на вечер)"
        base = "Футболка, рубашка с коротким рукавом, поло"
        acc = []
    else:
        outerwear = "Не требуется"
        base = "Светлая легкая футболка, шорты / тонкие брюки"
        acc = ["Солнцезащитные очки, кепка"]

    if pop > 0.3 or any(w in description.lower() for w in ["дождь", "ливень", "морось"]):
        acc.append("🌧 Зонт или непромокаемый дождевик")

    if wind_speed > 7.5:
        acc.append("💨 Ветрозащита (застегни воротник / накинь капюшон)")

    acc_str = ", ".join(acc) if acc else "Специальные аксессуары не требуются"

    summary = f"На улице {temp:+.1f}°C (по ощущениям {feels_like:+.1f}°C). "
    if "Зонт" in acc_str:
        summary += "Высокая вероятность дождя — не забудь зонт. "
    if wind_speed > 7.5:
        summary += "Ощутимый холодный ветер. "

    return {
        "outerwear": outerwear,
        "base": base,
        "accessories": acc_str,
        "summary": summary.strip(),
    }


async def get_current_weather(city: str) -> dict[str, Any] | None:
    if not settings.openweather_api_key or "your_" in settings.openweather_api_key:
        return {"error": "OPENWEATHER_API_KEY не сконфигурирован в .env"}

    cache_key = city.strip().lower()
    now_mono = time.monotonic()

    # Проверка TTL-кэша в памяти
    if cache_key in _weather_cache:
        cached_time, cached_data = _weather_cache[cache_key]
        if now_mono - cached_time < CACHE_TTL_SECONDS:
            logger.debug("Returning cached weather for '%s'", city)
            return cached_data

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": settings.openweather_api_key,
        "units": "metric",
        "lang": "ru",
    }

    try:
        session = http_client.get_session()
        async with session.get(url, params=params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                temp = data["main"]["temp"]
                feels_like = data["main"]["feels_like"]
                wind = data["wind"]["speed"]
                desc = data["weather"][0]["description"]
                rain_prob = 1.0 if "rain" in data else 0.0

                breakdown = generate_wardrobe_breakdown(
                    temp=temp,
                    feels_like=feels_like,
                    wind_speed=wind,
                    pop=rain_prob,
                    description=desc,
                )

                result = {
                    "city": data.get("name", city),
                    "temp": round(temp, 1),
                    "feels_like": round(feels_like, 1),
                    "description": desc.capitalize(),
                    "wind_speed": wind,
                    "advice": breakdown["summary"],
                    "breakdown": breakdown,
                }
                # Сохраняем в кэш
                _weather_cache[cache_key] = (now_mono, result)
                return result

            elif resp.status == 404:
                return {"error": f"Город '{city}' не найден."}
            else:
                return {"error": f"Ошибка погодного сервиса: HTTP {resp.status}"}
    except Exception as exc:
        logger.error("Weather fetch failed: %s", exc)
        return {"error": f"Не удалось получить погоду: {exc}"}
