import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "sessions")
DB_PATH = "progrever.db"

# Warming status thresholds (score 0-100)
STATUS_THRESHOLDS = {
    "green": 85,   # 🟢 Идеально прогретый
    "yellow": 55,  # 🟡 Хорошо прогрет, но нужно ещё
    "red": 20,     # 🔴 Плохо прогрет
    # < red => no session / new
}

# Action delays (seconds)
DELAY_MIN = 45
DELAY_MAX = 180

# Manual 1 settings
M1_HOLD_HOURS = 24          # Hold before any action
M1_PROFILE_CHANGE_DAY = 2   # Change profile on day 2
M1_TRUST_MESSAGES_PER_DAY = 3
M1_CHANNEL_JOINS_PER_DAY = 2
M1_WARMUP_DAYS = 5
M1_MAX_FIRST_WRITES_PER_DAY = 8   # after 5+1 days

# Manual 2 settings
M2_CHANNELS_PER_DAY = 7
M2_SESSION_HOURS = 5
M2_WARMUP_WEEKS = 2

# Emoji indicators
EMOJI = {
    "green":   "🟢",
    "yellow":  "🟡",
    "red":     "🔴",
    "black":   "⚫",
    "white":   "⚪",
    "purple":  "🟣",
}

# Public channels to join for warming (safe, large communities)
WARM_CHANNELS = [
    "durov",
    "telegram",
    "TelegramTips",
    "telegramwallpapers",
]

# SpamBot username
SPAM_BOT = "SpamBot"
BOT_FATHER = "BotFather"
