"""Конфигурация бота из переменных окружения."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Telegram (единственный обязательный секрет для запуска бота)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Папки
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
