"""Клиент VK API: посты на стену и истории."""
import logging
from typing import Optional

import vk_api
from vk_api import VkUpload
from vk_api.exceptions import VkApiError

from models import PublishRequest

logger = logging.getLogger(__name__)


def validate_vk_token(access_token: str) -> bool:
    """Проверяет, что токен VK действителен (лёгкий запрос users.get)."""
    try:
        session = vk_api.VkApi(token=access_token)
        session.method("users.get", {})
        return True
    except VkApiError:
        return False


class VKPublisher:
    """Публикация постов и историй во ВКонтакте."""

    def __init__(
        self,
        access_token: str,
        group_ids: list[int],
        stories_group_id: Optional[int] = None,
    ) -> None:
        self._session = vk_api.VkApi(token=access_token)
        self._api = self._session.get_api()
        self._upload = VkUpload(self._session)
        self._group_ids = group_ids
        self._stories_group_id = stories_group_id or (group_ids[0] if group_ids else None)

    def publish_post(self, request: PublishRequest, group_id: int) -> Optional[int]:
        """
        Публикует запись на стене сообщества.
        group_id — отрицательное число (например -123456789).
        Возвращает post_id или None при ошибке.
        """
        owner_id = group_id  # уже отрицательный для группы
        attachments: list[str] = []

        try:
            # Загрузка фото
            if request.photo_paths:
                photo_list = [str(p) for p in request.photo_paths]
                photo_attachments = self._upload.photo_wall(
                    photo_list,
                    group_id=abs(owner_id),
                )
                for photo in photo_attachments:
                    attachments.append(
                        f"photo{photo['owner_id']}_{photo['id']}"
                    )

            # Видео: во ВК для стены обычно нужна ссылка на уже загруженное видео
            # или загрузка через video.save — упрощённо не реализуем здесь,
            # можно расширить через video.getUploadServer
            if request.video_path:
                logger.warning("Загрузка видео на стену пока не реализована")

            return self._api.wall.post(
                owner_id=owner_id,
                message=request.text or None,
                attachments=",".join(attachments) if attachments else None,
                from_group=1,
            ).get("post_id")
        except VkApiError as e:
            logger.exception("VK wall.post error: %s", e)
            return None

    def publish_story(
        self,
        request: PublishRequest,
        group_id: Optional[int] = None,
    ) -> bool:
        """
        Публикует историю в сообществе.
        Для истории нужен один медиа-файл (фото или видео).
        """
        gid = group_id or self._stories_group_id
        if not gid:
            logger.error("Не задан VK_STORIES_GROUP_ID для историй")
            return False

        # Истории: один элемент — фото или видео
        if request.photo_paths:
            file_path = str(request.photo_paths[0])
        elif request.video_path:
            file_path = str(request.video_path)
        else:
            logger.error("Для истории нужен хотя бы один медиа-файл")
            return False

        try:
            # stories.getPhotoUploadServer(owner_id=-group_id)
            upload_url = self._api.stories.getPhotoUploadServer(
                add_to_news=1,
                user_id=0,
                reply_to_story=None,
                link_text=None,
                link_url=None,
                group_id=abs(gid),
            ).get("upload_url")

            if not upload_url:
                logger.error("Не получен upload_url для истории")
                return False

            with open(file_path, "rb") as f:
                files = {"file": f}
                response = self._session.http.post(upload_url, files=files)

            if response.status_code != 200:
                logger.error("Ошибка загрузки файла истории: %s", response.text)
                return False

            result = response.json()
            if "response" not in result:
                logger.error("Некорректный ответ stories upload: %s", result)
                return False

            save_response = self._api.stories.save(
                upload_results=result["response"],
                extended=0,
                fields=None,
            )
            if not save_response.get("items"):
                logger.error("stories.save не вернул items: %s", save_response)
                return False
            return True
        except (VkApiError, OSError) as e:
            logger.exception("VK story publish error: %s", e)
            return False

    def publish(self, request: PublishRequest) -> tuple[list[int], bool]:
        """
        Публикует пост во все настроенные группы и при необходимости историю.
        Возвращает (список post_id по группам, успех истории).
        """
        post_ids: list[int] = []
        for gid in self._group_ids:
            if request.publish_post:
                pid = self.publish_post(request, gid)
                if pid is not None:
                    post_ids.append(pid)
        story_ok = False
        if request.publish_story and (request.photo_paths or request.video_path):
            story_ok = self.publish_story(request)
        return post_ids, story_ok
