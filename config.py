"""
Configuration - loads from environment variables or .env file
"""
import os
from dotenv import load_dotenv

# Load .env file if exists
load_dotenv()

# Telegram credentials
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Solana Tracker API
SOLANA_API_KEY = os.getenv("SOLANA_API_KEY", "")
SOLANA_API_BASE_URL = "https://data.solanatracker.io"

# Source channels
SOURCE_CHANNEL = int(os.getenv("SOURCE_CHANNEL", "-1002223202815"))
SOURCE_CHANNEL_DEGEN_ONLY = int(os.getenv("SOURCE_CHANNEL_DEGEN_ONLY", "-1003406174127"))

# Validate required config
def validate_config():
    """Check that all required config is set"""
    missing = []

    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not SOLANA_API_KEY:
        missing.append("SOLANA_API_KEY")

    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}\n"
                        f"Create a .env file with these values.")

    return True
