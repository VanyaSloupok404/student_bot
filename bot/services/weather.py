import logging
from typing import Any
import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)


def generate_wardrobe_advice(temp: float, wind_speed: float, pop: float, description: str) -> str:
    advice = []

    # Температурные уровни
    if temp < -10:
        advice.append("Тяжелый мороз: надевай термобелье, пуховик, шапку и перчатки.")
    elif temp < 0:
        advice.append("Морозно: зимняя теплая куртка, шапка и шарф обязательны.")
    elif temp < 10:
        advice.append("Холодно: демисезонная куртка или теплое пальто.")
    elif temp < 18:
        advice.append("Прохладно: легкая куртка, толстовка или свитер.")
    elif temp < 24:
        advice.append("Комфортно: лонгслив, рубашка или легкая кофта.")
    else:
        advice.append("Жарко: футболка, шорты или легкие брюки.")

    # Осадки
    if pop > 0.3 or any(w in description.lower() for w in ["дождь", "ливень", "морось"]):
        advice.append("🌧 Возьми зонт или дождевик, вероятны осадки.")

    # Ветер
    if wind_speed > 8:
        advice.append("💨 Порывистый ветер: выбирай ветрозащитную одежду.")

    return " ".join(advice)


async def get_current_weather(city: str) -> dict[str, Any] | None:
    if not settings.openweather_api_key or "your_" in settings.openweather_api_key:
        return {"error": "OPENWEATHER_API_KEY не сконфигурирован в .env"}

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": settings.openweather_api_key,
        "units": "metric",
        "lang": "ru",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    temp = data["main"]["temp"]
                    feels_like = data["main"]["feels_like"]
                    wind = data["wind"]["speed"]
                    desc = data["weather"][0]["description"]
                    rain_prob = 1.0 if "rain" in data else 0.0

                    advice = generate_wardrobe_advice(temp=temp, wind_speed=wind, pop=rain_prob, description=desc)
                    return {
                        "city": data.get("name", city),
                        "temp": round(temp, 1),
                        "feels_like": round(feels_like, 1),
                        "description": desc.capitalize(),
                        "wind_speed": wind,
                        "advice": advice,
                    }
                elif resp.status == 404:
                    return {"error": f"Город '{city}' не найден."}
                else:
                    return {"error": f"Ошибка погодного сервиса: HTTP {resp.status}"}
    except Exception as exc:
        logger.error("Weather fetch failed: %s", exc)
        return {"error": f"Не удалось получить погоду: {exc}"}
