"""Хранение VK-учётных данных пользователей (облачная БД)."""
import sqlite3
import logging
from pathlib import Path
from typing import Optional

from config import BASE_DIR

logger = logging.getLogger(__name__)

DB_PATH = BASE_DIR / "data" / "credentials.db"


def _ensure_db_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    """Создаёт таблицу учётных данных, если её нет."""
    _ensure_db_dir()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_credentials (
                telegram_user_id INTEGER PRIMARY KEY,
                vk_access_token TEXT NOT NULL,
                vk_group_ids TEXT NOT NULL,
                vk_stories_group_id TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def get_user_credentials(telegram_user_id: int) -> Optional[dict]:
    """
    Возвращает сохранённые VK-данные пользователя или None.
    dict: vk_access_token, vk_group_ids (list[int]), vk_stories_group_id (int | None).
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT vk_access_token, vk_group_ids, vk_stories_group_id FROM user_credentials WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
    if not row:
        return None
    group_ids_str = row["vk_group_ids"] or ""
    group_ids = [int(gid.strip()) for gid in group_ids_str.split(",") if gid.strip()]
    stories_id = row["vk_stories_group_id"]
    stories_group_id = int(stories_id) if stories_id else (group_ids[0] if group_ids else None)
    return {
        "vk_access_token": row["vk_access_token"],
        "vk_group_ids": group_ids,
        "vk_stories_group_id": stories_group_id,
    }


def set_user_credentials(
    telegram_user_id: int,
    vk_access_token: str,
    vk_group_ids: list[int],
    vk_stories_group_id: Optional[int] = None,
) -> None:
    """Сохраняет или обновляет VK-учётные данные пользователя."""
    _ensure_db_dir()
    group_ids_str = ",".join(str(gid) for gid in vk_group_ids)
    stories_str = str(vk_stories_group_id) if vk_stories_group_id is not None else None
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO user_credentials (telegram_user_id, vk_access_token, vk_group_ids, vk_stories_group_id, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                vk_access_token = excluded.vk_access_token,
                vk_group_ids = excluded.vk_group_ids,
                vk_stories_group_id = excluded.vk_stories_group_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (telegram_user_id, vk_access_token.strip(), group_ids_str, stories_str),
        )
    logger.info("Credentials saved for telegram_user_id=%s", telegram_user_id)
