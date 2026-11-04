"""Тексты постов через Claude. Ничего не сочиняется: оба поста строятся только на найденных фактах.

Оба поста — в два шага. Сначала Claude ищет в интернете (официальные сайты, банки, Click, Payme, Uzum, СМИ),
потом по найденным фактам пишет пост со ссылкой на первоисточник.
Утро: цитата дня о финансах — реальная, из открытых источников, с автором и ссылкой.
Вечер: кешбэки, переводы без комиссии, разбор продуктов по актуальным условиям.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import anthropic

from .uzbek import to_new_latin

log = logging.getLogger(__name__)

FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

STYLE = """Ты редактор Telegram-канала «Soliq mobil xizmati» (~20 тыс. подписчиков, Узбекистан): личные финансы, \
кешбэки, выгодные переводы без комиссии, разбор банковских продуктов, приложение «Soliq».

Пост выходит как подпись к картинке, поэтому он короткий (лимит Telegram — 1024 символа на всё):
- headline_ru — заголовок до 70 символов, с 1 эмодзи в начале;
- text_ru — до 380 символов; если задание не говорит иначе — 3–4 коротких пункта с эмодзи-маркерами \
(✅ 💡 📌 💳 1️⃣ 2️⃣ 3️⃣), каждый с новой строки;
- headline_uz, text_uz — то же на узбекском, латиница в НОВОЙ редакции алфавита (закон ЗРУ-1184 \
от 21.10.2026): вместо sh, ch, oʻ, gʻ пиши ş, ç, ö, ğ (Özbekiston, böyiça, uçun, taşkil, ilğor, pul ötkazmalari), \
апостроф ʼ — только вместо твёрдого знака (taʼlim). Бренды не меняй (Visa, Click, Payme, Uzum, Humo, Uzcard). \
Факты в обеих версиях одинаковые;
- tags — 2–3 хэштега без #, латиницей, без пробелов.
Без Markdown и HTML. Простой дружелюбный язык, без воды. Это просвещение, а не индивидуальная \
инвестиционная рекомендация."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline_ru": {"type": "string"},
        "text_ru": {"type": "string"},
        "headline_uz": {"type": "string"},
        "text_uz": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "sources": {
            "type": "array",
            "description": "Ссылки на первоисточники фактов (1–2), только из выжимки.",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "url": {"type": "string"}},
                "required": ["name", "url"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline_ru", "text_ru", "headline_uz", "text_uz", "tags", "sources"],
    "additionalProperties": False,
}

NO_NEWS = "НЕТ_НОВОСТЕЙ"

RESEARCH = {
    "morning": f"""Ты подбираешь «цитату дня» о деньгах и финансах для Telegram-канала. Найди в открытых источниках \
(ru.wikiquote.org, en.wikiquote.org, citaty.info, сборники цитат, книги и интервью) РЕАЛЬНУЮ цитату известного \
человека по заданной теме: инвесторы (Уоррен Баффет, Чарли Мангер, Бенджамин Грэм), предприниматели, учёные, \
писатели, мыслители (Бенджамин Франклин, Сенека и др.), а также восточные мудрецы и узбекские классики \
(Алишер Навои, Абу Райхан Беруни, Ибн Сина), если найдёшь их высказывания о богатстве, бережливости, труде.

Требования:
- цитата короткая (до 200 символов) и действительно принадлежит автору — найди её на странице источника;
- не бери цитаты с пометкой «приписывается», «сомнительно», «атрибуция не подтверждена»;
- не повторяй авторов и цитаты из списка недавних постов.
Верни: точный текст цитаты (как в источнике, на языке источника), её русский перевод (если источник не на русском), \
автора, кто он (одна строка), откуда цитата (книга/письмо/интервью, если указано) и точный URL страницы.
Если подходящую подтверждённую цитату найти не удалось — ответь одним словом {NO_NEWS}.""",
    "evening": """Ты исследователь финансового рынка Узбекистана. Найди в интернете СВЕЖУЮ (текущий и прошлый месяц) \
информацию по теме. Ищи на официальных сайтах банков Узбекистана, Click, Payme, Uzum, soliq.uz, cbu.uz и в СМИ \
(kun.uz, gazeta.uz, spot.uz, bank.uz, daryo.uz).

Верни сжатую выжимку фактов: конкретные банки/сервисы, условия, цифры (проценты, комиссии, лимиты, сроки акций), \
дата публикации и точный URL источника у каждого факта. Пиши только то, что подтверждено найденными страницами; \
если по теме ничего свежего не нашлось — так и напиши.""",
}


class Writer:
    def __init__(self, api_key: str, model: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    # ── шаг 1 — поиск фактов ─────────────────────────────────────
    def research(self, kind: str, topic: str, recent: list[str]) -> str:
        """Выжимка найденных фактов или пустая строка, если ничего подходящего не нашлось."""
        today = dt.date.today().strftime("%d.%m.%Y")
        recent_txt = "\n".join(f"- {r}" for r in recent[-30:]) or "- (пока нет)"
        content = f"Сегодня {today}. Тема: {topic}\n\nНедавние посты (не повторяй):\n{recent_txt}"
        messages = [{"role": "user", "content": content}]
        tools = [{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 8,
            "user_location": {"type": "approximate", "country": "UZ", "city": "Tashkent",
                              "timezone": "Asia/Tashkent"},
        }]
        for _ in range(5):  # сервер может приостановить ход (pause_turn) — продолжаем
            with self.client.beta.messages.stream(
                model=self.model, max_tokens=32000, system=RESEARCH[kind], messages=messages, tools=tools,
                output_config={"effort": "high"}, **FALLBACK,
            ) as stream:
                response = stream.get_final_message()
            if response.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude отказался искать: {response.stop_details}")
        facts = "".join(b.text for b in response.content if b.type == "text").strip()
        if not facts or (NO_NEWS in facts and len(facts) < 200):
            return ""
        return facts

    # ── шаг 2 — сам пост ─────────────────────────────────────────
    def write(self, kind: str, topic: str, recent: list[str], facts: str = "", feedback: str = "") -> dict:
        today = dt.date.today().strftime("%d.%m.%Y")
        if kind == "morning":
            task = (f"Сегодня {today}. Утренний пост «Цитата дня» — цитата из выжимки ниже, ничего не сочиняй.\n"
                    "- headline_ru: «💬 Цитата дня: <Автор>», headline_uz: «💬 Kun iqtibosi: <Muallif>»;\n"
                    "- text_ru: начни с «Доброе утро!», затем цитата в кавычках «…» (русский перевод из выжимки, "
                    "слово в слово), затем «— <Автор>, <кто он>» и, если известно, откуда цитата;\n"
                    "- text_uz: начни с «Xayrli tong!», затем перевод той же цитаты на узбекский (новый алфавит) "
                    "в кавычках «…», затем «— <muallif>, <kim>»;\n"
                    "- без своих советов и рассуждений;\n"
                    "- в sources — страница, где найдена цитата.\n\n"
                    f"Выжимка:\n{facts}")
        else:
            task = (f"Сегодня {today}. Вечерний пост на тему: {topic}.\n"
                    "Используй ТОЛЬКО факты из выжимки ниже: конкретные банки, сервисы и цифры — только оттуда, "
                    "ничего не добавляй. Если фактов мало — сделай пост-разбор: на что смотреть при выборе. "
                    "В конце text_ru добавь строку «ℹ️ Условия могут меняться», в text_uz — "
                    "«ℹ️ Şartlar özgarişi mumkin». В sources — 1–2 главных источника из выжимки.\n\n"
                    f"Выжимка:\n{facts or '(свежих данных не найдено)'}")
        recent_txt = "\n".join(f"- {r}" for r in recent[-10:]) or "- (пока нет)"
        content = f"{task}\n\nНедавние посты рубрики (не повторяйся):\n{recent_txt}"
        if feedback:
            content += f"\n\n{feedback}"

        response = self.client.beta.messages.create(
            model=self.model, max_tokens=16000, system=STYLE,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            **FALLBACK,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Claude отказался писать пост: {response.stop_details}")
        post = json.loads(next(b.text for b in response.content if b.type == "text"))
        post["headline_uz"] = to_new_latin(post["headline_uz"])
        post["text_uz"] = to_new_latin(post["text_uz"])
        post["tags"] = [t.lstrip("#").replace(" ", "") for t in post["tags"]][:3]
        post["sources"] = [s for s in post["sources"] if s.get("url", "").startswith("http")][:2]
        return post
