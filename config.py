import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "")
GUILD_ID = int(os.getenv("GUILD_ID") or 0)
DB_PATH = os.getenv("DB_PATH", "bot.db")

API_KEY = os.getenv("API_KEY", "")
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT") or 8080)
ROBLOX_UNIVERSE_ID = int(os.getenv("ROBLOX_UNIVERSE_ID") or 0)

BOT_STATUS = os.getenv("BOT_STATUS", "Hatch a Mutant")

# Couleurs des embeds
COLOR_MAIN = 0x7C5CFF
COLOR_OK = 0x43B581
COLOR_WARN = 0xFAA61A
COLOR_ERR = 0xF04747
