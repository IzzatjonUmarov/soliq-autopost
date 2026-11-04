"""Автопостинг @Shaxsiy_moliya_uz — только готовые тексты из источников, бот ничего не сочиняет.

    python -m bot run [--max-runtime 19800]           работа по расписанию: 07:00 цитата, 18:00 новость (Ташкент)
    python -m bot post morning|evening [--dry-run]    один пост сейчас (для проверки)
    python -m bot check [--id X]                      проверить источники, цитаты и (если есть токен) Telegram
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time

from .config import Settings, load_sources, load_yaml, now_tashkent
from .fetch import make_client
from .format import CAPTION_LIMIT, TELEGRAM_LIMIT, build_post, build_quote_post
from .images import apply_watermark, build_image, make_quote_card, to_jpeg
from .news import collect, pick_news
from .quotes import pick_quote
from .state import State
from .telegram import Telegram
from .translate import translate_ru_uz
from .uzbek import to_new_latin, uzbek_version

log = logging.getLogger("bot")
RETRY_AFTER_ERROR = 5 * 60


class NothingFound(Exception):
    """Подходящей цитаты или свежей новости нет — пост не сочиняем, а пропускаем."""


# ── подготовка постов ────────────────────────────────────────────

def prepare_morning(settings: Settings, state: State) -> dict:
    cfg = load_yaml(settings.quotes_file)
    with make_client(settings.request_timeout) as client:
        q = pick_quote(client, cfg, state.used_quotes, now_tashkent().date())
        if not q:
            raise NothingFound("на страницах Викицитатника не нашлось новой подходящей цитаты")
        log.info("Цитата (%s): «%s» — %s", q.page, q.text, q.author)
        uz_text = translate_ru_uz(client, q.text)
    # имя автора не переводим, а пишем латиницей буква в букву: «Оноре де Бальзак» → «Onore de Balzak»
    # (название книги после запятой в узбекской части не повторяем)
    uz = (to_new_latin(uz_text), to_new_latin(q.author.split(",")[0])) if uz_text else None
    if not uz:
        log.info("Перевод на узбекский недоступен — цитата только на русском")
    caption = build_quote_post(q.text, q.author, q.url, uz)
    card = make_quote_card(q.text, q.author, now_tashkent().strftime("%d.%m.%Y"))
    jpeg = to_jpeg(apply_watermark(card, settings.watermark))
    return {"kind": "morning", "caption": caption, "jpeg": jpeg, "title": f"{q.author}: {q.text[:80]}",
            "quote_key": q.key, "news_url": ""}


def prepare_evening(settings: Settings, state: State) -> dict:
    item = pick_news(settings, state.seen_news)
    if not item:
        raise NothingFound(f"за последние {settings.max_age_days} дн. нет новых новостей по темам канала")
    log.info("Новость: %s — %s", item.title, item.url)
    with make_client(settings.request_timeout) as client:
        uz, uz_source = uzbek_version(client, item)
        log.info("Узбекская версия: %s", {"official": "с сайта источника", "machine": "машинный перевод",
                                         "none": "нет — пост только на русском"}[uz_source])
        jpeg = None
        try:
            jpeg = build_image(client, item, settings.watermark)
        except Exception as exc:  # noqa: BLE001 — без картинки пост всё равно уйдёт
            log.warning("Картинка не получилась: %s", exc)
    caption = build_post(item, None, CAPTION_LIMIT if jpeg else TELEGRAM_LIMIT, uz)
    return {"kind": "evening", "caption": caption, "jpeg": jpeg, "title": item.title,
            "quote_key": "", "news_url": item.url}


PREPARE = {"morning": prepare_morning, "evening": prepare_evening}


def publish(settings: Settings, draft: dict) -> None:
    tg = Telegram(settings.telegram_token, settings.telegram_chat_id)
    if draft["jpeg"]:
        tg.send_photo(draft["jpeg"], draft["caption"])
    else:
        tg.send_message(draft["caption"])
    log.info("Опубликовано: %s", draft["title"])


def save_preview(settings: Settings, draft: dict) -> None:
    settings.preview_dir.mkdir(parents=True, exist_ok=True)
    if draft["jpeg"]:
        (settings.preview_dir / f"{draft['kind']}.jpg").write_bytes(draft["jpeg"])
    (settings.preview_dir / f"{draft['kind']}.html").write_text(draft["caption"], encoding="utf-8")
    log.info("Предпросмотр:\n%s", draft["caption"])


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
                    drafts[kind] = PREPARE[kind](settings, state)
                if now >= slot:
                    draft = drafts.pop(kind)
                    publish(settings, draft)
                    state.mark_posted(day, kind, {"date": now.isoformat(timespec="minutes"), "kind": kind,
                                                  "title": draft["title"]},
                                      news_url=draft["news_url"], quote_key=draft["quote_key"])
            except NothingFound as exc:
                log.warning("[%s] %s — пост пропущен", kind, exc)
                state.mark_posted(day, kind, {"date": now.isoformat(timespec="minutes"), "kind": kind,
                                              "title": "(пропущен)", "skipped": True})
            except Exception:
                log.exception("[%s] ошибка, повторю через %s мин", kind, RETRY_AFTER_ERROR // 60)
                failed_at[kind] = time.monotonic()
        time.sleep(20)
    log.info("Время запуска вышло — передаю работу следующему запуску")


def cmd_post(settings: Settings, kind: str, dry_run: bool) -> int:
    state = State(settings.state_file)  # без синхронизации: тестовый пост не трогает расписание
    try:
        draft = PREPARE[kind](settings, state)
    except NothingFound as exc:
        log.warning("%s", exc)
        return 1
    save_preview(settings, draft)
    if dry_run:
        log.info("DRY RUN: в канал не отправлено. Картинка и текст — в артефакте «preview».")
        return 0
    publish(settings, draft)
    return 0


def cmd_check(settings: Settings, only: str | None) -> int:
    ok = True
    print("── Источники новостей ──")
    sources = [s for s in load_sources(settings.sources_file) if not only or s.id == only]
    results = collect(settings, sources)
    for source in sources:
        result = results[source.id]
        if isinstance(result, Exception):
            print(f"✗ {source.id:<12} ОШИБКА: {str(result)[:150]}")
            continue
        print(f"{'✓' if result else '⚠'} {source.id:<12} новостей: {len(result):>3}   {source.url}")
        for item in result[: (15 if only else 2)]:
            date = item.published.strftime("%d.%m.%Y") if item.published else "  —  "
            print(f"      {date}  {item.title[:90]}")
    working = sum(1 for r in results.values() if r and not isinstance(r, Exception))
    print(f"Работают: {working} из {len(sources)}")

    if not only:
        print("\n── Цитаты (Викицитатник) ──")
        from .quotes import fetch_page, suitable
        cfg = load_yaml(settings.quotes_file)
        with make_client(settings.request_timeout) as client:
            for page in cfg.get("pages", []):
                try:
                    found = fetch_page(client, page)
                    good = [q for q in found if suitable(q, cfg)]
                    print(f"✓ {page:<22} цитат: {len(found):>3}, подходящих: {len(good):>3}")
                    for q in good[:2]:
                        print(f"      «{q.text[:100]}» — {q.author[:50]}")
                except Exception as exc:  # noqa: BLE001
                    print(f"✗ {page:<22} ОШИБКА: {exc}")
                    ok = False

    print("\n── Telegram ──")
    if not settings.telegram_token:
        print("⚠ TELEGRAM_BOT_TOKEN не задан — публикация невозможна (предпросмотр работает)")
        return 0 if ok else 1
    try:
        tg = Telegram(settings.telegram_token, settings.telegram_chat_id)
        me = tg.get_me()
        chat = tg.get_chat()
        member = tg.get_member(me["id"])
        print(f"✓ Бот @{me['username']}, канал «{chat.get('title')}» ({settings.telegram_chat_id})")
        print(f"  Постоянный номер канала: {chat.get('id')}   публичный адрес: @{chat.get('username')}")
        print(f"  Водяной знак на картинках: {settings.watermark.text or '(нет)'}")
        if member.get("status") == "administrator" and member.get("can_post_messages", True):
            print("✓ Бот — администратор с правом публикации")
        else:
            print(f"✗ Бот не администратор канала или нет права публикации (статус: {member.get('status')})")
            ok = False
    except Exception as exc:  # noqa: BLE001
        print(f"✗ Telegram: {exc}")
        ok = False
    return 0 if ok else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="python -m bot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--max-runtime", type=int, default=19800)
    post = sub.add_parser("post")
    post.add_argument("kind", choices=["morning", "evening"])
    post.add_argument("--dry-run", action="store_true")
    check = sub.add_parser("check")
    check.add_argument("--id")
    args = parser.parse_args()

    settings = Settings()
    if args.cmd == "run":
        cmd_run(settings, args.max_runtime)
    elif args.cmd == "post":
        sys.exit(cmd_post(settings, args.kind, args.dry_run))
    else:
        sys.exit(cmd_check(settings, args.id))


if __name__ == "__main__":
    main()
