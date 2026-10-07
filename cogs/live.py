"""Infos du jeu en direct : salon « info serveur » et salon « classement ».

Ces deux messages sont modifiés automatiquement toutes les minutes :
- 📊 info serveur : ouvert / maintenance, joueurs en ligne, serveurs actifs, visites, favoris...
- 🏆 classement : les meilleurs joueurs, envoyés par le jeu (voir /api/leaderboard).

Les chiffres viennent de deux sources :
- l'API publique de Roblox (visites, favoris, avis, dernière mise à jour du jeu) ;
- les « signes de vie » que chaque serveur du jeu envoie au bot (joueurs en direct, serveurs actifs).
"""
import datetime
import logging
import math
import re
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils import NETWORK_ERRORS

log = logging.getLogger("live")

GAME_NAME = "Hatch a Mutant"
REFRESH_SECONDS = 60
SERVER_TTL = 120        # un serveur muet depuis 2 minutes est considéré éteint
MAX_SERVERS = 2000      # protège la mémoire si quelqu'un spamme l'API
MAX_BOARDS = 5          # classements différents acceptés
SHOWN_BOARDS = 3        # classements affichés dans le salon
BOARD_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
MEDALS = ("🥇", "🥈", "🥉")
DEFAULT_MAINTENANCE = "Le jeu est en maintenance, reviens bientôt !"
GAME_URL = "https://www.roblox.com/games/{}"


# --- Petites fonctions pures (testées par smoke_test.py) ---------------------
def fmt_num(value) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def bar(value: float, total: float, size: int = 12) -> str:
    filled = int(size * min(value, total) / total) if total else 0
    return "█" * filled + "░" * (size - filled)


def iso_to_ts(text) -> int | None:
    """Date ISO de Roblox (2026-10-07T12:34:56.1234567Z) -> timestamp Unix."""
    if not text:
        return None
    try:
        cleaned = re.sub(r"\.\d+", "", str(text)).replace("Z", "+00:00")
        return int(datetime.datetime.fromisoformat(cleaned).timestamp())
    except ValueError:
        return None


def clean_entries(raw) -> list[dict] | None:
    """Vérifie et nettoie un classement envoyé par le jeu.
    Retourne les 10 meilleurs (triés), ou None si les données sont invalides."""
    if not isinstance(raw, list) or len(raw) > 25:
        return None
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        try:
            roblox_id = int(item.get("robloxId"))
            value = float(item.get("value"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or abs(value) > 9e18:
            return None
        name = str(item.get("name") or f"Joueur {roblox_id}")[:40]
        entries.append({"roblox_id": roblox_id, "name": name, "value": int(round(value))})
    entries.sort(key=lambda e: e["value"], reverse=True)
    return entries[:10]


class Live(commands.Cog):
    jeu = app_commands.Group(
        name="jeu",
        description="Infos du jeu en direct (salons info serveur et classement)",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.servers: dict[str, dict] = {}     # jobId -> {players, max, version, seen}
        self._game_cache: dict | None = None   # dernières stats Roblox connues
        self._warned: set[str] = set()

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        self.refresh_loop.start()

    async def cog_unload(self):
        self.refresh_loop.cancel()
        if self.session:
            await self.session.close()

    def _warn_once(self, key: str, message: str):
        """Évite de répéter la même alerte toutes les minutes dans les logs."""
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)

    # --- Signes de vie des serveurs du jeu -------------------------------
    def record_heartbeat(
        self, job_id: str, players: int, max_players: int, version: int, closing: bool
    ) -> bool:
        if closing:
            self.servers.pop(job_id, None)
            return True
        if job_id not in self.servers and len(self.servers) >= MAX_SERVERS:
            return False
        self.servers[job_id] = {
            "players": max(0, min(players, 1000)),
            "max": max(0, min(max_players, 1000)),
            "version": version,
            "seen": time.time(),
        }
        return True

    def active_servers(self) -> dict[str, dict]:
        cutoff = time.time() - SERVER_TTL
        self.servers = {k: v for k, v in self.servers.items() if v["seen"] >= cutoff}
        return self.servers

    # --- Maintenance -----------------------------------------------------
    async def maintenance_state(self, guild_id: int) -> tuple[bool, str]:
        db = self.bot.db
        on = (await db.get_setting(guild_id, "maintenance")) == "1"
        message = await db.get_setting(guild_id, "maintenance_message") or DEFAULT_MAINTENANCE
        return on, message

    # --- Classements envoyés par le jeu ----------------------------------
    async def save_leaderboard(self, guild_id: int, board, title, raw_entries) -> str | None:
        """Enregistre un classement. Retourne un code d'erreur, ou None si tout va bien."""
        board = str(board or "").strip().lower()
        if not BOARD_RE.match(board):
            return "bad_board"
        entries = clean_entries(raw_entries)
        if entries is None:
            return "bad_entries"
        db = self.bot.db
        row = await db.fetchone(
            "SELECT COUNT(DISTINCT board) AS n, SUM(board=?) AS known FROM leaderboard WHERE guild_id=?",
            (board, guild_id),
        )
        if not (row["known"] or 0) and row["n"] >= MAX_BOARDS:
            return "too_many_boards"
        await db.replace_leaderboard(guild_id, board, str(title or board)[:60], entries, time.time())
        return None

    # --- Stats publiques de Roblox ---------------------------------------
    async def fetch_game(self) -> dict | None:
        """Stats du jeu depuis l'API publique de Roblox (dernières données connues si elle ne répond pas)."""
        if not config.ROBLOX_UNIVERSE_ID or self.session is None:
            return None
        params = {"universeIds": config.ROBLOX_UNIVERSE_ID}
        try:
            async with self.session.get("https://games.roblox.com/v1/games", params=params) as resp:
                if resp.status != 200:
                    raise ValueError(f"statut {resp.status}")
                data = (await resp.json()).get("data") or []
            if not data:
                raise ValueError("jeu introuvable (ROBLOX_UNIVERSE_ID correct ?)")
            game = dict(data[0])
        except (*NETWORK_ERRORS, ValueError) as exc:
            self._warn_once("roblox_api", f"API publique de Roblox indisponible : {exc}")
            return self._game_cache
        self._warned.discard("roblox_api")
        try:
            async with self.session.get("https://games.roblox.com/v1/games/votes", params=params) as resp:
                if resp.status == 200:
                    votes = (await resp.json()).get("data") or []
                    if votes:
                        game["upVotes"] = votes[0].get("upVotes")
                        game["downVotes"] = votes[0].get("downVotes")
        except (*NETWORK_ERRORS, ValueError):
            pass
        game["_fetched"] = time.time()
        self._game_cache = game
        return game

    # --- Construction des messages ---------------------------------------
    async def build_info(self, guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View | None]:
        db = self.bot.db
        maintenance, maintenance_msg = await self.maintenance_state(guild.id)
        game = await self.fetch_game()
        servers = self.active_servers()
        beat_players = sum(s["players"] for s in servers.values())
        capacity = sum(s["max"] for s in servers.values())

        if servers:                                   # signes de vie : le plus précis
            online = beat_players
        elif game and game.get("playing") is not None:
            online = game["playing"]
        else:
            online = None

        name = (game or {}).get("name") or GAME_NAME
        embed = discord.Embed(
            title=f"📊 {name}",
            color=config.COLOR_WARN if maintenance else config.COLOR_OK,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="🚦 Statut",
            value=f"🔧 **Maintenance**\n{maintenance_msg}" if maintenance else "🟢 **Ouvert** — viens jouer !",
            inline=False,
        )
        embed.add_field(
            name="👥 Joueurs en ligne", value=f"**{fmt_num(online)}**" if online is not None else "—"
        )
        if servers:
            embed.add_field(name="🖥️ Serveurs actifs", value=str(len(servers)))
            if capacity:
                embed.add_field(
                    name="📶 Remplissage",
                    value=f"`{bar(beat_players, capacity)}` {round(100 * beat_players / capacity)} %",
                )
        if game:
            embed.add_field(name="📈 Visites", value=fmt_num(game.get("visits")))
            embed.add_field(name="⭐ Favoris", value=fmt_num(game.get("favoritedCount")))
            up, down = game.get("upVotes"), game.get("downVotes")
            if isinstance(up, int) and isinstance(down, int) and up + down > 0:
                embed.add_field(name="👍 Avis positifs", value=f"{round(100 * up / (up + down))} %")
            updated = iso_to_ts(game.get("updated"))
            if updated:
                embed.add_field(name="🆕 Dernière mise à jour", value=f"<t:{updated}:R>")

        event = await db.fetchone(
            "SELECT title, starts_at FROM events WHERE guild_id=? AND status='scheduled' "
            "AND starts_at>? ORDER BY starts_at LIMIT 1",
            (guild.id, time.time()),
        )
        if event:
            embed.add_field(
                name="🎉 Prochain événement",
                value=f"**{event['title']}**\n<t:{int(event['starts_at'])}:R>",
                inline=False,
            )

        stale = bool(game) and time.time() - game.get("_fetched", 0) > 300
        embed.set_footer(
            text="Actualisé chaque minute" + (" · stats Roblox en cache" if stale else "")
        )

        view = None
        root = (game or {}).get("rootPlaceId")
        if root:
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(
                    label="Jouer", emoji="🎮", style=discord.ButtonStyle.link, url=GAME_URL.format(root)
                )
            )
        return embed, view

    async def build_top(self, guild: discord.Guild) -> list[discord.Embed]:
        db = self.bot.db
        rows = await db.fetchall(
            "SELECT * FROM leaderboard WHERE guild_id=? ORDER BY board, pos", (guild.id,)
        )
        boards: dict[str, list] = {}
        for r in rows:
            boards.setdefault(r["board"], []).append(r)

        linked: dict[int, int] = {}
        ids = list({r["roblox_id"] for r in rows})
        if ids:
            marks = ",".join("?" * len(ids))
            found = await db.fetchall(
                f"SELECT roblox_id, user_id FROM users WHERE guild_id=? AND roblox_id IN ({marks})",  # noqa: S608
                (guild.id, *ids),
            )
            linked = {r["roblox_id"]: r["user_id"] for r in found}

        embeds = []
        for board_rows in list(boards.values())[:SHOWN_BOARDS]:
            lines = []
            for r in board_rows:
                place = MEDALS[r["pos"] - 1] if r["pos"] <= 3 else f"`{r['pos']}.`"
                who = discord.utils.escape_markdown(r["name"])
                mention = f" · <@{linked[r['roblox_id']]}>" if r["roblox_id"] in linked else ""
                lines.append(f"{place} **{who}**{mention} — {fmt_num(r['value'])}")
            updated = int(max(r["updated_at"] for r in board_rows))
            lines.append(f"\n*Mis à jour <t:{updated}:R>*")
            embeds.append(
                discord.Embed(
                    title=f"🏆 {board_rows[0]['title']}",
                    description="\n".join(lines),
                    color=config.COLOR_MAIN,
                )
            )

        if not embeds:
            embeds.append(
                discord.Embed(
                    title="🏆 Classement du jeu",
                    description="Le classement apparaîtra ici dès que le jeu l'enverra.",
                    color=config.COLOR_MAIN,
                )
            )

        # Petit bonus : le top du serveur Discord (niveaux gagnés en discutant)
        top = await db.fetchall(
            "SELECT user_id, level FROM users WHERE guild_id=? AND (level>0 OR xp>0) "
            "ORDER BY level DESC, xp DESC LIMIT 5",
            (guild.id,),
        )
        if top:
            lines = [
                f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'} <@{r['user_id']}> — niveau {r['level']}"
                for i, r in enumerate(top)
            ]
            embeds.append(
                discord.Embed(title="💬 Top Discord", description="\n".join(lines), color=config.COLOR_OK)
            )
        return embeds

    # --- Mise à jour des messages ----------------------------------------
    async def update_panel(self, guild: discord.Guild, channel_key: str, msg_key: str, *, embeds, view=None):
        """Modifie le panneau existant, ou le publie s'il n'existe pas (ou a été supprimé)."""
        db = self.bot.db
        channel_id = await db.get_int_setting(guild.id, channel_key)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None:
            return False
        extra = {"view": view} if view is not None else {}
        msg_id = await db.get_int_setting(guild.id, msg_key)
        try:
            if msg_id:
                try:
                    await channel.get_partial_message(msg_id).edit(embeds=embeds, **extra)
                    self._warned.discard(f"{msg_key}:{guild.id}")
                    return True
                except discord.NotFound:
                    pass  # message supprimé : on le republie
            message = await channel.send(embeds=embeds, **extra)
            await db.set_setting(guild.id, msg_key, message.id)
            self._warned.discard(f"{msg_key}:{guild.id}")
            return True
        except discord.HTTPException as exc:
            self._warn_once(
                f"{msg_key}:{guild.id}",
                f"Impossible de mettre à jour {channel} ({exc}). Vérifie que le bot peut y écrire.",
            )
            return False

    async def refresh_guild(self, guild: discord.Guild):
        db = self.bot.db
        if await db.get_int_setting(guild.id, "live_info_channel"):
            embed, view = await self.build_info(guild)
            await self.update_panel(guild, "live_info_channel", "live_info_msg", embeds=[embed], view=view)
        if await db.get_int_setting(guild.id, "live_top_channel"):
            await self.update_panel(
                guild, "live_top_channel", "live_top_msg", embeds=await self.build_top(guild)
            )

    @tasks.loop(seconds=REFRESH_SECONDS)
    async def refresh_loop(self):
        for guild in self.bot.guilds:
            try:
                await self.refresh_guild(guild)
            except Exception:
                log.exception("Erreur pendant la mise à jour des panneaux (%s)", guild.name)

    @refresh_loop.before_loop
    async def _before_refresh(self):
        await self.bot.wait_until_ready()

    # --- Commandes -------------------------------------------------------
    @jeu.command(name="installer", description="Crée les salons « info serveur » et « classement »")
    @app_commands.checks.has_permissions(administrator=True)
    async def install(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild, db = interaction.guild, self.bot.db
        verified_id = await db.get_int_setting(guild.id, "verified_role")
        member_role = guild.get_role(verified_id) if verified_id else None
        cat_id = await db.get_int_setting(guild.id, "setup_cat_game")
        category = guild.get_channel(cat_id) if cat_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None

        PO = discord.PermissionOverwrite
        # Lecture seule pour les membres (ou pour tout le monde si la vérification n'est pas activée)
        viewer = member_role or guild.default_role
        overwrites = {
            guild.me: PO(
                view_channel=True, send_messages=True, embed_links=True, manage_messages=True,
                read_message_history=True,
            ),
            viewer: PO(
                view_channel=True, send_messages=False, add_reactions=False,
                read_message_history=True, create_public_threads=False,
            ),
        }
        if member_role:
            overwrites[guild.default_role] = PO(view_channel=False)

        specs = [
            ("live_info_channel", "setup_ch_game_info", "📊・info-serveur",
             "L'état du jeu en direct : ouvert, joueurs en ligne, serveurs..."),
            ("live_top_channel", "setup_ch_game_top", "🥇・classement",
             "Les meilleurs joueurs du jeu, en direct."),
        ]
        created, reused = [], []
        try:
            for setting, setup_key, name, topic in specs:
                current_id = await db.get_int_setting(guild.id, setting)
                if current_id and guild.get_channel(current_id):
                    reused.append(guild.get_channel(current_id).mention)
                    continue
                channel = await guild.create_text_channel(
                    name, category=category, overwrites=overwrites, topic=topic,
                    reason="Salons du jeu en direct",
                )
                await db.set_setting(guild.id, setting, channel.id)
                await db.set_setting(guild.id, setup_key, channel.id)  # /setup-serveur le réutilisera
                await db.set_setting(guild.id, setting.replace("_channel", "_msg"), None)
                created.append(channel.mention)
        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Il me manque la permission **Gérer les salons**.", ephemeral=True
            )
        except discord.HTTPException as exc:
            return await interaction.followup.send(f"❌ Discord a refusé : `{exc}`.", ephemeral=True)

        await self.refresh_guild(guild)
        parts = []
        if created:
            parts.append("✅ Salons créés : " + ", ".join(created))
        if reused:
            parts.append("ℹ️ Déjà en place : " + ", ".join(reused))
        parts.append("Ils se mettent à jour automatiquement toutes les minutes.")
        await interaction.followup.send("\n".join(parts), ephemeral=True)

    @jeu.command(name="salon-infos", description="Utilise un de tes salons existants pour l'info serveur")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_info_channel(self, interaction: discord.Interaction, salon: discord.TextChannel):
        await self._set_channel(interaction, "live_info_channel", "live_info_msg", salon)

    @jeu.command(name="salon-classement", description="Utilise un de tes salons existants pour le classement")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_top_channel(self, interaction: discord.Interaction, salon: discord.TextChannel):
        await self._set_channel(interaction, "live_top_channel", "live_top_msg", salon)

    async def _set_channel(self, interaction, channel_key: str, msg_key: str, salon: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        db = self.bot.db
        await db.set_setting(interaction.guild_id, channel_key, salon.id)
        await db.set_setting(interaction.guild_id, msg_key, None)  # nouveau salon : nouveau message
        await self.refresh_guild(interaction.guild)
        await interaction.followup.send(f"✅ Panneau publié dans {salon.mention}.", ephemeral=True)

    @jeu.command(name="maintenance", description="Active ou désactive le mode maintenance du jeu")
    @app_commands.describe(actif="Activer la maintenance ?", message="Message affiché aux joueurs")
    @app_commands.checks.has_permissions(administrator=True)
    async def maintenance(
        self, interaction: discord.Interaction, actif: bool,
        message: app_commands.Range[str, 0, 200] = "",
    ):
        await interaction.response.defer(ephemeral=True)
        db = self.bot.db
        await db.set_setting(interaction.guild_id, "maintenance", "1" if actif else "0")
        await db.set_setting(interaction.guild_id, "maintenance_message", message or None)
        await self.refresh_guild(interaction.guild)
        await interaction.followup.send(
            "🔧 Maintenance **activée** : le panneau l'affiche, et le jeu éjecte les joueurs "
            "(sauf les comptes admin) si `MAINTENANCE_KICK` est activé dans le script."
            if actif else "🟢 Maintenance **terminée** : le jeu est de nouveau ouvert.",
            ephemeral=True,
        )

    @jeu.command(name="actualiser", description="Met à jour les panneaux tout de suite")
    @app_commands.checks.has_permissions(administrator=True)
    async def refresh_now(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.refresh_guild(interaction.guild)
        await interaction.followup.send("✅ Panneaux actualisés.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Live(bot))
