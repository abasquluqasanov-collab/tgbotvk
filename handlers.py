"""Обработчики Telegram-бота: приём медиа, текста и уточнений по аудио/музыке."""
import logging
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import DOWNLOADS_DIR
from models import PublishRequest
from storage import get_user_credentials, init_db, set_user_credentials
from vk_client import VKPublisher, validate_vk_token

logger = logging.getLogger(__name__)
router = Router()


class PublishStates(StatesGroup):
    """Состояния сбора данных для публикации."""

    waiting_media = State()  # фото/видео
    waiting_text = State()  # текст со ссылками
    waiting_options = State()  # пост/история, аудио


class SetupStates(StatesGroup):
    """Состояния настройки VK-учётных данных пользователя."""

    waiting_token = State()
    waiting_groups = State()
    waiting_stories = State()


def _user_dir(user_id: int) -> Path:
    path = DOWNLOADS_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _download_photo(bot: Bot, message: Message, user_id: int) -> list[Path]:
    """Скачивает все фото из сообщения."""
    paths = []
    for idx, photo in enumerate(message.photo):
        file = await bot.get_file(photo[-1].file_id)
        ext = "jpg"
        dest = _user_dir(user_id) / f"photo_{message.message_id}_{idx}.{ext}"
        await bot.download_file(file.file_path, dest)
        paths.append(dest)
    return paths


async def _download_video(bot: Bot, message: Message, user_id: int) -> Path | None:
    """Скачивает видео из сообщения."""
    video = message.video or message.document
    if not video:
        return None
    file = await bot.get_file(video.file_id)
    ext = getattr(video, "file_name", None) or "mp4"
    if not ext.split(".")[-1].lower() in ("mp4", "mov", "avi", "webm"):
        ext = "mp4"
    dest = _user_dir(user_id) / f"video_{message.message_id}.{ext}"
    await bot.download_file(file.file_path, dest)
    return dest


def _parse_group_ids(text: str) -> list[int] | None:
    """Парсит строку вида '-123, -456' или '123, 456' в список int (для групп — отрицательные)."""
    try:
        ids = []
        for part in text.replace(" ", "").split(","):
            if not part:
                continue
            n = int(part)
            if n > 0:
                n = -n  # ID группы во ВК обычно отрицательный
            ids.append(n)
        return ids if ids else None
    except ValueError:
        return None


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    creds = get_user_credentials(message.from_user.id) if message.from_user else None
    setup_hint = "" if creds else "\nПеред первым постом выполни /setup и введи свой VK-токен и ID групп.\n\n"
    await message.answer(
        "Привет! Я публикую посты и истории во ВКонтакте.\n\n"
        + setup_hint
        + "Как пользоваться:\n"
        "1. Отправь фото или видео (можно несколько фото).\n"
        "2. Отправь текст поста (можно со ссылками).\n"
        "3. Выбери: только пост, только история или оба, и укажи, нужно ли добавлять музыку/аудио во ВК.\n\n"
        "Команды:\n"
        "/setup — указать свой VK-токен и группы (данные хранятся в облаке)\n"
        "/post — начать новый пост\n"
        "/cancel — отменить текущий пост"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено. Напиши /post или /setup чтобы начать заново.")


@router.message(Command("setup"))
async def cmd_setup(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SetupStates.waiting_token)
    await message.answer(
        "Настройка VK. Твои данные сохраняются только в облачной базе бота и привязаны к твоему Telegram.\n\n"
        "Отправь свой <b>VK Access Token</b> (один сообщением).\n"
        "Как получить: приложение ВК → права wall, photos, stories, offline, groups.",
        parse_mode="HTML",
    )


@router.message(SetupStates.waiting_token, F.text)
async def setup_handle_token(message: Message, state: FSMContext) -> None:
    token = (message.text or "").strip()
    if not token:
        await message.answer("Отправь токен текстом.")
        return
    if not validate_vk_token(token):
        await message.answer("Токен не прошёл проверку ВК. Проверь права и скопируй токен заново.")
        return
    await state.update_data(vk_access_token=token)
    await state.set_state(SetupStates.waiting_groups)
    await message.answer(
        "Токен принят. Теперь отправь <b>ID групп ВК</b> через запятую.\n"
        "Например: <code>-123456789, -987654321</code> или <code>123456789, 987654321</code>.",
        parse_mode="HTML",
    )


@router.message(SetupStates.waiting_groups, F.text)
async def setup_handle_groups(message: Message, state: FSMContext) -> None:
    group_ids = _parse_group_ids(message.text or "")
    if not group_ids:
        await message.answer("Укажи хотя бы один ID группы через запятую, например: -123456789, -987654321")
        return
    await state.update_data(vk_group_ids=group_ids)
    await state.set_state(SetupStates.waiting_stories)
    await message.answer(
        "Группы сохранены. Отправь <b>ID группы для историй</b> (одно число) или напиши <b>Пропустить</b>, "
        "чтобы использовать первую группу из списка.",
        parse_mode="HTML",
    )


@router.message(SetupStates.waiting_stories, F.text)
async def setup_handle_stories(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    if text in ("пропустить", "skip", "/skip", ""):
        stories_id = None
    else:
        try:
            n = int(text.replace(" ", ""))
            stories_id = -abs(n)
        except ValueError:
            await message.answer("Отправь одно число (ID группы для историй) или «Пропустить».")
            return
    data = await state.get_data()
    token = data["vk_access_token"]
    group_ids = data["vk_group_ids"]
    set_user_credentials(
        message.from_user.id,
        vk_access_token=token,
        vk_group_ids=group_ids,
        vk_stories_group_id=stories_id,
    )
    await state.clear()
    await message.answer(
        "Готово. Твои VK-данные сохранены в облаке. Можешь использовать /post для публикации."
    )


@router.message(Command("post"))
async def cmd_post(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PublishStates.waiting_media)
    await message.answer(
        "Отправь фото или видео для поста. Можно несколько фото подряд. "
        "Когда закончишь — отправь текст поста (можно со ссылками) или нажми «Пропустить».",
        reply_markup=None,
    )


@router.message(PublishStates.waiting_media, F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    paths = await _download_photo(bot, message, message.from_user.id)
    data = await state.get_data()
    photos: list = data.get("photo_paths", [])
    photos.extend(paths)
    await state.update_data(photo_paths=[str(p) for p in photos])
    await message.answer(f"Добавлено фото. Всего фото: {len(photos)}. Отправь ещё или текст поста.")

@router.message(PublishStates.waiting_media, F.video)
async def handle_video(message: Message, state: FSMContext, bot: Bot) -> None:
    path = await _download_video(bot, message, message.from_user.id)
    if not path:
        await message.answer("Не удалось скачать видео.")
        return
    await state.update_data(video_path=str(path))
    await state.set_state(PublishStates.waiting_text)
    await message.answer("Видео получено. Теперь отправь текст поста (можно со ссылками) или «Пропустить».")

@router.message(PublishStates.waiting_media, F.document)
async def handle_document(message: Message, state: FSMContext, bot: Bot) -> None:
    # Документ может быть видео
    mime = (message.document.mime_type or "").lower()
    if "video" in mime:
        path = await _download_video(bot, message, message.from_user.id)
        if path:
            await state.update_data(video_path=str(path))
            await state.set_state(PublishStates.waiting_text)
            await message.answer("Видео получено. Отправь текст поста или «Пропустить».")
            return
    await message.answer("Отправь, пожалуйста, фото или видео.")

@router.message(PublishStates.waiting_media, F.text)
async def from_media_to_text(message: Message, state: FSMContext) -> None:
    if message.text and message.text.strip().lower() in ("пропустить", "skip", "/skip"):
        await state.update_data(text="")
        await state.set_state(PublishStates.waiting_options)
        await _ask_options(message)
        return
    await state.update_data(text=message.text or "")
    await state.set_state(PublishStates.waiting_options)
    await _ask_options(message)


@router.message(PublishStates.waiting_text, F.text)
async def handle_text(message: Message, state: FSMContext) -> None:
    if message.text and message.text.strip().lower() in ("пропустить", "skip", "/skip"):
        await state.update_data(text="")
    else:
        await state.update_data(text=message.text or "")
    await state.set_state(PublishStates.waiting_options)
    await _ask_options(message)


def _keyboard_options() -> dict:
    """Клавиатура: пост, история, оба; добавить аудио да/нет."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Только пост", callback_data="opt_post"),
            InlineKeyboardButton(text="Только история", callback_data="opt_story"),
        ],
        [
            InlineKeyboardButton(text="Пост + история", callback_data="opt_both"),
        ],
        [
            InlineKeyboardButton(text="Добавить музыку/аудио во ВК", callback_data="opt_audio"),
        ],
        [
            InlineKeyboardButton(text="Опубликовать", callback_data="opt_publish"),
        ],
    ])


async def _ask_options(message: Message) -> None:
    await message.answer(
        "Выбери, куда публиковать, и нужно ли добавлять музыку/аудио во ВК. "
        "Затем нажми «Опубликовать».",
        reply_markup=_keyboard_options(),
    )


@router.callback_query(F.data.startswith("opt_"))
async def process_options(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    post = data.get("publish_post", True)
    story = data.get("publish_story", False)
    add_audio = data.get("add_audio", False)

    if callback.data == "opt_post":
        post, story = True, False
    elif callback.data == "opt_story":
        post, story = False, True
    elif callback.data == "opt_both":
        post, story = True, True
    elif callback.data == "opt_audio":
        add_audio = not add_audio
        await callback.answer(f"Музыка/аудио во ВК: {'да' if add_audio else 'нет'}")
        await state.update_data(add_audio=add_audio, publish_post=post, publish_story=story)
        await callback.message.edit_reply_markup(reply_markup=_keyboard_options())
        return
    elif callback.data == "opt_publish":
        await _do_publish(callback, state)
        return

    await state.update_data(publish_post=post, publish_story=story)
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=_keyboard_options())


async def _do_publish(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    photo_paths = [Path(p) for p in data.get("photo_paths", [])]
    video_path = data.get("video_path")
    request = PublishRequest(
        photo_paths=photo_paths,
        video_path=Path(video_path) if video_path else None,
        text=data.get("text", ""),
        publish_post=data.get("publish_post", True),
        publish_story=data.get("publish_story", False),
        add_audio=data.get("add_audio", False),
        audio_comment=data.get("audio_comment", ""),
    )
    if not request.has_media() and not request.has_text():
        await callback.answer("Нужно хотя бы фото, видео или текст.", show_alert=True)
        return

    if request.add_audio:
        await state.set_state(PublishStates.waiting_options)
        await state.update_data(
            publish_post=request.publish_post,
            publish_story=request.publish_story,
            add_audio=True,
        )
        await callback.message.answer(
            "Напиши уточнение по музыке/аудио для ВК (например, название трека или «из аудиозаписей группы»). "
            "После этого отправь команду /publish_now чтобы опубликовать."
        )
        await callback.answer()
        return

    await _publish_and_reply(callback.message, request, callback.from_user.id)
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()


@router.message(PublishStates.waiting_options, F.text)
async def save_audio_comment(message: Message, state: FSMContext) -> None:
    """Сохранить уточнение по музыке/аудио и напомнить про /publish_now."""
    data = await state.get_data()
    if data.get("add_audio") and message.text and not message.text.startswith("/"):
        await state.update_data(audio_comment=message.text)
        await message.answer("Уточнение сохранено. Отправь /publish_now для публикации.")


@router.message(Command("publish_now"))
async def cmd_publish_now(message: Message, state: FSMContext) -> None:
    """Публикация после ввода уточнения по аудио."""
    data = await state.get_data()
    if not data:
        await message.answer("Нет сохранённого поста. Начни с /post")
        return
    request = PublishRequest(
        photo_paths=[Path(p) for p in data.get("photo_paths", [])],
        video_path=Path(data["video_path"]) if data.get("video_path") else None,
        text=data.get("text", ""),
        publish_post=data.get("publish_post", True),
        publish_story=data.get("publish_story", False),
        add_audio=data.get("add_audio", False),
        audio_comment=data.get("audio_comment", ""),
    )
    # Последнее сообщение могло быть уточнением по аудио
    if message.text and not message.text.startswith("/"):
        request.audio_comment = message.text
    await _publish_and_reply(message, request, message.from_user.id)
    await state.clear()


async def _publish_and_reply(message: Message, request: PublishRequest, user_id: int) -> None:
    creds = get_user_credentials(user_id)
    if not creds:
        await message.answer(
            "Сначала выполни /setup и введи свой VK-токен и ID групп. "
            "Данные сохраняются в облаке и привязаны к твоему аккаунту."
        )
        return
    try:
        publisher = VKPublisher(
            access_token=creds["vk_access_token"],
            group_ids=creds["vk_group_ids"],
            stories_group_id=creds.get("vk_stories_group_id"),
        )
        post_ids, story_ok = publisher.publish(request)
        lines = []
        if post_ids:
            lines.append(f"Опубликовано постов: {len(post_ids)} (id: {post_ids})")
        if request.publish_story:
            lines.append("История: " + ("опубликована" if story_ok else "ошибка публикации"))
        if request.add_audio:
            lines.append("Музыка/аудио: учтено (уточнение: " + (request.audio_comment or "—") + "). Во ВК добавление трека в пост делается вручную или через отдельный метод API.")
        await message.answer("\n".join(lines) if lines else "Готово.")
    except Exception as e:
        logger.exception("Publish error")
        await message.answer(f"Ошибка публикации: {e}")
    finally:
        # Очистка скачанных файлов
        for p in request.photo_paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        if request.video_path and request.video_path.exists():
            try:
                request.video_path.unlink(missing_ok=True)
            except OSError:
                pass
