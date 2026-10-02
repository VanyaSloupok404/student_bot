import json
import logging
from typing import Any
from google.genai import types

from bot.config import settings
from bot.services.gemini import gemini_service

logger = logging.getLogger(__name__)


async def calculate_commute(home_address: str, college_address: str, travel_mode: str = "transit") -> dict[str, Any]:
    prompt = f"""
Ты — специализированный транспортный навигатор.
Рассчитай реалистичное среднее время в пути в утренний час пик (будний день, 07:30 - 08:30) между двумя точками:
- Точка отправления (Дом): {home_address}
- Точка прибытия (Учебное заведение): {college_address}
- Способ передвижения: {travel_mode} (общественный транспорт / метро / автобус / авто / пешком)

ТРЕБОВАНИЯ:
1. Обязательно включи время на пеший подход к остановкам/станциям метро и интервалы ожидания.
2. Ответ верни СТРОГО в виде валидного JSON без разметки markdown (без ```json).

СХЕМА JSON:
{{
  "status": "success",
  "commute_minutes": 45,
  "route_summary": "Краткое описание маршрута (например: Автобус 124 до метро Отрадное, далее серая ветка)",
  "traffic_note": "Замечание по заторам / интервалам"
}}
"""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2,
    )

    try:
        response = await gemini_service.client.aio.models.generate_content(
            model=gemini_service.model,
            contents=[prompt],
            config=config,
        )
        if not response.text:
            return {"status": "error", "error_message": "Empty response from route planner"}
        return json.loads(response.text)
    except Exception as exc:
        logger.error("Commute calculation error: %s", exc)
        return {"status": "error", "error_message": str(exc)}
