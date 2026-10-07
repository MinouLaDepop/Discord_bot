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

-- VIP : kind = 'discord' (donné par le staff, ident = id Discord)
--       ou 'roblox' (acheté dans le jeu, ident = id Roblox)
CREATE TABLE IF NOT EXISTS vip (
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ident INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (guild_id, kind, ident)
);

CREATE INDEX IF NOT EXISTS idx_users_roblox ON users(guild_id, roblox_id);
CREATE INDEX IF NOT EXISTS idx_users_level ON users(guild_id, level DESC, xp DESC);
CREATE INDEX IF NOT EXISTS idx_users_coins ON users(guild_id, coins DESC);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(guild_id, user_id, closed);
CREATE INDEX IF NOT EXISTS idx_shop_guild ON shop(guild_id, name);
CREATE INDEX IF NOT EXISTS idx_link_codes_user ON link_codes(guild_id, user_id);
"""

# Colonnes autorisées pour les cooldowns (évite toute injection SQL)
COOLDOWN_COLUMNS = ("last_daily", "last_work")


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")  # rapide et sûr avec WAL
        await self.conn.execute("PRAGMA busy_timeout=5000")    # attend au lieu de planter si la base est occupée
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

    async def claim_cooldown(
        self, guild_id: int, user_id: int, column: str, cooldown: float, now: float
    ) -> bool:
        """Réserve atomiquement une récompense à cooldown (daily, travail...).
        Retourne False si le cooldown n'est pas fini : empêche de la récupérer
        deux fois en lançant la commande 2 fois très vite."""
        if column not in COOLDOWN_COLUMNS:
            raise ValueError(f"colonne de cooldown inconnue : {column}")
        await self.ensure_user(guild_id, user_id)
        cur = await self.execute(
            f"UPDATE users SET {column}=? WHERE guild_id=? AND user_id=? AND {column}+?<=?",  # noqa: S608
            (now, guild_id, user_id, cooldown, now),
        )
        return cur.rowcount > 0

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
