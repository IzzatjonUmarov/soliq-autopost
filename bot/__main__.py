"""Автопостинг @Soliqmobilxizmati.

    python -m bot run [--max-runtime 19800]   работа по расписанию (07:00 и 18:00 по Ташкенту)
    python -m bot post morning|evening [--dry-run]   один пост сейчас (для проверки)
    python -m bot check                        проверить токен, права бота в канале и ключ Claude
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time

from . import topics
from .ai import Writer
from .config import Settings, now_tashkent
from .format import CAPTION_LIMIT, build_caption, visible_length
from .images import apply_watermark, make_card, to_jpeg
from .state import State
from .telegram import Telegram

log = logging.getLogger("bot")
RETRY_AFTER_ERROR = 5 * 60


class NothingFound(Exception):
    """Поиск не дал подтверждённых фактов — пост не сочиняем, а пропускаем."""


def prepare(settings: Settings, state: State, kind: str) -> dict:
    """Тема → поиск фактов → текст → картинка. Возвращает готовый к отправке пост."""
    topic = topics.pick(kind, state.history)
    log.info("[%s] Тема: %s", kind, topic)
    writer = Writer(settings.anthropic_key, settings.ai_model)
    recent = [f"{h.get('headline', '')} {h.get('text', '')}".strip()
              for h in state.history if h.get("kind") == kind]
    facts = writer.research(kind, topic, recent)
    if not facts:
        raise NothingFound(f"[{kind}] по теме «{topic}» ничего подтверждённого не найдено")
    log.info("Найденные факты:\n%s", facts)

    post = writer.write(kind, topic, recent, facts)
    caption = build_caption(post, settings.telegram_chat_id)
    if visible_length(caption) > CAPTION_LIMIT:
        log.info("Подпись длиннее %s символов (%s) — прошу сократить", CAPTION_LIMIT, visible_length(caption))
        post = writer.write(kind, topic, recent, facts, feedback=(
            f"Прошлый вариант вышел длиной {visible_length(caption)} символов вместе с оформлением, а лимит "
            f"{CAPTION_LIMIT}. Сократи text_ru и text_uz примерно на треть."))
        caption = build_caption(post, settings.telegram_chat_id)

    label_uz, label_ru = topics.LABELS[kind]
    card = make_card(kind, f"{label_uz} · {label_ru}", post["headline_ru"], post["headline_uz"],
                     now_tashkent().strftime("%d.%m.%Y"))
    jpeg = to_jpeg(apply_watermark(card, settings.watermark))
    return {"kind": kind, "topic": topic, "post": post, "caption": caption, "jpeg": jpeg}


def publish(settings: Settings, draft: dict) -> None:
    tg = Telegram(settings.telegram_token, settings.telegram_chat_id)
    if visible_length(draft["caption"]) <= CAPTION_LIMIT:
        tg.send_photo(draft["jpeg"], draft["caption"])
    else:  # не уложились даже после сокращения — картинка без подписи нельзя, шлём текстом
        log.warning("Подпись всё ещё длинная — отправляю пост текстом без картинки")
        tg.send_message(draft["caption"])
    log.info("Опубликовано: %s", draft["post"]["headline_ru"])


def save_preview(settings: Settings, draft: dict) -> None:
    settings.preview_dir.mkdir(parents=True, exist_ok=True)
    (settings.preview_dir / f"{draft['kind']}.jpg").write_bytes(draft["jpeg"])
    (settings.preview_dir / f"{draft['kind']}.html").write_text(draft["caption"], encoding="utf-8")
    log.info("Предпросмотр (%s симв.):\n%s", visible_length(draft["caption"]), draft["caption"])


def history_entry(draft: dict) -> dict:
    return {"date": now_tashkent().isoformat(timespec="minutes"), "kind": draft["kind"],
            "topic": draft["topic"], "headline": draft["post"]["headline_ru"],
            "text": draft["post"]["text_ru"][:150].replace("\n", " ")}


# ── команды ──────────────────────────────────────────────────────

def cmd_run(settings: Settings, max_runtime: int) -> None:
    state = State(settings.state_file, settings.state_sync_cmd)
    started = time.monotonic()
    drafts: dict[str, dict] = {}
    failed_at: dict[str, float] = {}
    log.info("Старт. Слоты: %s. Работаю до %s мин.",
             ", ".join(f"{k} {t:%H:%M}" for k, t in settings.slots.items()), max_runtime // 60)

    while time.monotonic() - started < max_runtime:
        now = now_tashkent()
        day = now.date().isoformat()
        for kind, slot_time in settings.slots.items():
            slot = dt.datetime.combine(now.date(), slot_time, tzinfo=now.tzinfo)
            if state.is_posted(day, kind):
                continue
            if not (slot - dt.timedelta(minutes=settings.prepare_minutes) <= now
                    <= slot + dt.timedelta(minutes=settings.late_minutes)):
                continue
            if time.monotonic() - failed_at.get(kind, -1e9) < RETRY_AFTER_ERROR:
                continue
            try:
                if kind not in drafts:
                    drafts[kind] = prepare(settings, state, kind)
                if now >= slot:
                    draft = drafts.pop(kind)
                    publish(settings, draft)
                    state.mark_posted(day, kind, history_entry(draft))  # сразу сохраняем — без дублей
            except NothingFound as exc:
                log.warning("%s — пост пропущен", exc)
                state.mark_posted(day, kind, {"date": now.isoformat(timespec="minutes"), "kind": kind,
                                              "topic": "", "headline": "(пропущен)", "skipped": True})
            except Exception:
                log.exception("[%s] ошибка, повторю через %s мин", kind, RETRY_AFTER_ERROR // 60)
                failed_at[kind] = time.monotonic()
        time.sleep(20)
    log.info("Время запуска вышло — передаю работу следующему запуску")


def cmd_post(settings: Settings, kind: str, dry_run: bool) -> None:
    state = State(settings.state_file)  # без синхронизации: тестовый пост не трогает расписание
    draft = prepare(settings, state, kind)
    save_preview(settings, draft)
    if dry_run:
        log.info("DRY RUN: в канал не отправлено. Картинка и текст — в артефакте запуска.")
        return
    publish(settings, draft)


def cmd_check(settings: Settings) -> int:
    ok = True
    try:
        tg = Telegram(settings.telegram_token, settings.telegram_chat_id)
        me = tg.get_me()
        print(f"✅ Бот: @{me['username']}")
        chat = tg.get_chat()
        print(f"✅ Канал: {chat.get('title')} ({settings.telegram_chat_id})")
        member = tg.get_member(me["id"])
        if member.get("status") == "administrator" and member.get("can_post_messages", True):
            print("✅ Бот — администратор с правом публикации")
        else:
            print(f"❌ Бот не администратор канала или нет права публикации (статус: {member.get('status')})")
            ok = False
    except Exception as exc:
        print(f"❌ Telegram: {exc}")
        ok = False
    if not settings.anthropic_key:
        print("❌ Не задан ANTHROPIC_API_KEY")
        ok = False
    else:
        try:
            import anthropic
            anthropic.Anthropic(api_key=settings.anthropic_key).models.retrieve(settings.ai_model)
            print(f"✅ Claude API: модель {settings.ai_model} доступна")
        except Exception as exc:
            print(f"❌ Claude API: {exc}")
            ok = False
    return 0 if ok else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    parser = argparse.ArgumentParser(prog="bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--max-runtime", type=int, default=19800)
    post = sub.add_parser("post")
    post.add_argument("kind", choices=["morning", "evening"])
    post.add_argument("--dry-run", action="store_true")
    sub.add_parser("check")
    args = parser.parse_args()

    settings = Settings()
    if args.cmd == "run":
        cmd_run(settings, args.max_runtime)
    elif args.cmd == "post":
        cmd_post(settings, args.kind, args.dry_run)
    else:
        sys.exit(cmd_check(settings))


if __name__ == "__main__":
    main()
