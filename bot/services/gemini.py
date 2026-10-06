import json
import logging
import re
from typing import Any

from google import genai
from google.genai import types

from bot.config import settings

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """
Ты — специализированный парсер данных студенческого ассистента. Твоя задача — извлекать структурированные данные из изображений и текста.

ОКРУЖЕНИЕ:
Текущая дата и время передаются в контексте. Учитывай их при расчете относительных дат.

ПРАВИЛА:
1. Ответ должен быть СТРОГО валидным JSON без markdown-блоков (без ```json).
2. При нечитаемом изображении возвращай: {"status": "error", "error_message": "Текст нечитаем"}.
3. Формат времени строго HH:MM (24-часовой). Если время звонков не указано в таблице, ставь null.
4. Четность недели: "all", "odd" (нечетная/числитель), "even" (четная/знаменатель).
5. Дни недели: 1 (Пн) - 7 (Вс).
6. ФИЛЬТРАЦИЯ ПО ЗАПРОСУ ПОЛЬЗОВАТЕЛЯ: Всегда приоритетно учитывай подпись пользователя! Если пользователь пишет "запиши на понедельник", "только на завтра" — ты ОБЯЗАН включить в массив "days" ТОЛЬКО запрошенный день.

СХЕМЫ:
- Расписание (сохранение):
{"action": "save_schedule", "status": "success", "data": {"days": [{"day_of_week": 1, "parity": "all", "lessons": [{"lesson_number": 1, "subject": "Математика", "start_time": "08:30", "end_time": "10:00", "room": "306", "teacher": "Иванов И.И.", "subgroup": 0}]}]}}

- Оперативное изменение расписания:
{"action": "modify_schedule", "status": "success", "data": {"operation": "cancel_lesson | replace_lesson | move_room | add_lesson", "day_of_week": 1, "lesson_number": 2, "target_subject": "Обществознание или null", "new_subject": "Физика или null", "new_room": "204 или null", "new_teacher": null, "explanation": "Краткое описание изменения"}}

- Домашнее задание:
{"action": "save_homework", "status": "success", "data": {"subject": "Физика", "task_text": "Задачи 1-5", "deadline_date": "YYYY-MM-DD или null", "relative_deadline": "next_lesson | tomorrow | null"}}

- Напоминание / Расход:
{"action": "quick_action", "status": "success", "data": {"type": "reminder | expense", "payload": {"text": "Описание", "amount": 0.0, "category": "personal | study | transport | food | other", "target_datetime": "YYYY-MM-DD HH:MM:SS или null"}}}
"""


def clean_and_parse_json(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise


class GeminiService:
    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = "gemini-3.1-flash-lite"

    async def _generate(self, contents: list[Any]) -> dict[str, Any]:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0.1,
        )
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            raw_text = getattr(response, "text", None)
            if not raw_text:
                return {"status": "error", "error_message": "Пустой ответ от модели (возможно сработал фильтр безопасности)"}

            return clean_and_parse_json(raw_text)

        except json.JSONDecodeError as exc:
            logger.error("JSON decode error from Gemini: %s", exc)
            return {"status": "error", "error_message": f"Не удалось распарсить JSON: {exc}"}
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            return {"status": "error", "error_message": str(exc)}

    async def parse_schedule(self, image_bytes: bytes, mime_type: str = "image/jpeg", context: str = "") -> dict[str, Any]:
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        prompt = (
            f"Распознай расписание пар на фото с учетом комментария пользователя: {context}. "
            f"Если в комментарии указан конкретный день недели, верни данные ТОЛЬКО для этого дня."
        )
        return await self._generate([image_part, prompt])

    async def parse_schedule_modification(self, text: str, context: str = "") -> dict[str, Any]:
        prompt = (
            f"Определи оперативное изменение расписания по тексту пользователя: '{text}'. "
            f"Определи день недели (1-7), номер пары (1-6), операцию и параметры. Контекст: {context}"
        )
        return await self._generate([prompt])

    async def parse_homework(self, text: str, image_bytes: bytes | None = None, mime_type: str = "image/jpeg", context: str = "") -> dict[str, Any]:
        contents: list[Any] = []
        if image_bytes:
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
        prompt = f"Извлеки домашнее задание из ввода: '{text}'. Контекст: {context}"
        contents.append(prompt)
        return await self._generate(contents)

    async def parse_quick_action(self, text: str, context: str = "") -> dict[str, Any]:
        prompt = f"Определи действие (напоминание или расход) для текста: '{text}'. Контекст: {context}"
        return await self._generate([prompt])


gemini_service = GeminiService()
