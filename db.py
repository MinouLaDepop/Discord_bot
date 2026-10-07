import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    mod_id INTEGER NOT NULL,
    reason TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    coins INTEGER NOT NULL DEFAULT 0,
    last_msg REAL NOT NULL DEFAULT 0,
    last_daily REAL NOT NULL DEFAULT 0,
    last_work REAL NOT NULL DEFAULT 0,
    roblox_id INTEGER,
    roblox_name TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS shop (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    price INTEGER NOT NULL,
    description TEXT,
    role_id INTEGER
);

CREATE TABLE IF NOT EXISTS inventory (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, user_id, item_id)
);

CREATE TABLE IF NOT EXISTS level_roles (
    guild_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS link_codes (
    code TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    roblox_id INTEGER NOT NULL,
    roblox_name TEXT NOT NULL,
    expires REAL NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()

    # --- Requêtes de base -------------------------------------------------
    async def execute(self, query: str, params: tuple = ()):
        cur = await self.conn.execute(query, params)
        await self.conn.commit()
        return cur

    async def fetchone(self, query: str, params: tuple = ()):
        cur = await self.conn.execute(query, params)
        return await cur.fetchone()

    async def fetchall(self, query: str, params: tuple = ()):
        cur = await self.conn.execute(query, params)
        return await cur.fetchall()

    # --- Paramètres par serveur ------------------------------------------
    async def get_setting(self, guild_id: int, key: str, default=None):
        row = await self.fetchone(
            "SELECT value FROM settings WHERE guild_id=? AND key=?", (guild_id, key)
        )
        return row["value"] if row and row["value"] is not None else default

    async def set_setting(self, guild_id: int, key: str, value):
        await self.execute(
            "INSERT INTO settings(guild_id, key, value) VALUES(?,?,?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value",
            (guild_id, key, None if value is None else str(value)),
        )

    async def get_int_setting(self, guild_id: int, key: str):
        value = await self.get_setting(guild_id, key)
        return int(value) if value else None

    # --- Utilisateurs / économie -----------------------------------------
    async def ensure_user(self, guild_id: int, user_id: int):
        await self.execute(
            "INSERT OR IGNORE INTO users(guild_id, user_id) VALUES(?,?)",
            (guild_id, user_id),
        )
        return await self.fetchone(
            "SELECT * FROM users WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )

    async def add_coins(self, guild_id: int, user_id: int, amount: int):
        """Ajoute (ou retire) des pièces. Retourne le nouveau solde,
        ou None si le solde deviendrait négatif."""
        await self.ensure_user(guild_id, user_id)
        cur = await self.execute(
            "UPDATE users SET coins = coins + ? WHERE guild_id=? AND user_id=? "
            "AND coins + ? >= 0",
            (amount, guild_id, user_id, amount),
        )
        if cur.rowcount == 0:
            return None
        row = await self.fetchone(
            "SELECT coins FROM users WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        return row["coins"]
