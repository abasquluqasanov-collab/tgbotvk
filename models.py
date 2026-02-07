"""Модели данных для поста/истории."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class PublishRequest:
    """Запрос на публикацию из Telegram в VK."""

    # Медиа (локальные пути после скачивания)
    photo_paths: list[Path] = field(default_factory=list)
    video_path: Optional[Path] = None

    # Текст поста (поддерживает ссылки)
    text: str = ""

    # Флаги публикации
    publish_post: bool = True  # Опубликовать запись на стене
    publish_story: bool = False  # Опубликовать в истории

    # Музыка/аудио во ВК
    add_audio: bool = False
    audio_comment: str = ""  # Уточнение от пользователя (название трека и т.д.)

    def has_media(self) -> bool:
        return bool(self.photo_paths or self.video_path)

    def has_text(self) -> bool:
        return bool(self.text.strip())
