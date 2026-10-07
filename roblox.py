"""Lien Discord <-> Roblox.

Le bot ouvre une petite API HTTP que ton jeu Roblox appelle (via HttpService) :
  GET  /api/ping
  POST /api/link            { code, robloxId }          -> valide une liaison de compte
  GET  /api/player/<id>     -> infos Discord/économie du joueur lié
  POST /api/coins           { robloxId, amount, reason } -> ajoute/retire des pièces
  POST /api/event           { title, message, color }   -> poste un embed dans Discord
Chaque requête doit contenir l'en-tête  X-API-Key  (la clé du fichier .env).
"""
import hmac
import logging
import random
import string
import time

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger("roblox")

CODE_TTL = 600  # un code de liaison est valable 10 minutes


class Roblox(commands.Cog):
    roblox = app_commands.Group(
        name="roblox",
        description="Lien avec ton compte et ton jeu Roblox",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.runner: web.AppRunner | None = None

    # --- Cycle de vie ----------------------------------------------------
    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        if not config.API_KEY:
            log.warning("API_KEY vide : l'API pour Roblox est désactivée.")
            return
        if not config.GUILD_ID:
            log.warning("GUILD_ID vide : l'API pour Roblox est désactivée.")
            return

        app = web.Application(middlewares=[self.auth_middleware], client_max_size=64 * 1024)
        app.add_routes(
            [
                web.get("/api/ping", self.api_ping),
                web.post("/api/link", self.api_link),
                web.get("/api/player/{roblox_id}", self.api_player),
                web.post("/api/coins", self.api_coins),
                web.post("/api/event", self.api_event),
            ]
        )
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        await web.TCPSite(self.runner, config.API_HOST, config.API_PORT).start()
        log.info("API Roblox à l'écoute sur %s:%s", config.API_HOST, config.API_PORT)

    async def cog_unload(self):
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()

    # --- Authentification de l'API ---------------------------------------
    @web.middleware
    async def auth_middleware(self, request: web.Request, handler):
        key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(key.encode(), config.API_KEY.encode()):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except Exception:
            log.exception("Erreur API")
            return web.json_response({"ok": False, "error": "server_error"}, status=500)

    @staticmethod
    async def read_json(request: web.Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="invalid json")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="invalid json")
        return data

    # --- Routes de l'API -------------------------------------------------
    async def api_ping(self, request: web.Request):
        return web.json_response({"ok": True, "bot": str(self.bot.user)})

    async def api_link(self, request: web.Request):
        data = await self.read_json(request)
        code = str(data.get("code", "")).strip().upper()
        try:
            roblox_id = int(data.get("robloxId"))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "error": "bad_roblox_id"}, status=400)

        db = self.bot.db
        row = await db.fetchone("SELECT * FROM link_codes WHERE code=?", (code,))
        if not row or row["expires"] < time.time():
            return web.json_response({"ok": False, "error": "invalid_or_expired_code"}, status=404)
        if row["roblox_id"] != roblox_id:
            return web.json_response({"ok": False, "error": "wrong_account"}, status=403)

        await db.ensure_user(row["guild_id"], row["user_id"])
        # Un compte Roblox ne peut être lié qu'à un seul membre
        await db.execute(
            "UPDATE users SET roblox_id=NULL, roblox_name=NULL WHERE guild_id=? AND roblox_id=?",
            (row["guild_id"], roblox_id),
        )
        await db.execute(
            "UPDATE users SET roblox_id=?, roblox_name=? WHERE guild_id=? AND user_id=?",
            (roblox_id, row["roblox_name"], row["guild_id"], row["user_id"]),
        )
        await db.execute("DELETE FROM link_codes WHERE code=?", (code,))

        user = self.bot.get_user(row["user_id"])
        if user:
            try:
                await user.send(f"✅ Ton compte Roblox **{row['roblox_name']}** est maintenant lié !")
            except discord.HTTPException:
                pass
        return web.json_response(
            {"ok": True, "discordName": user.display_name if user else str(row["user_id"])}
        )

    async def api_player(self, request: web.Request):
        try:
            roblox_id = int(request.match_info["roblox_id"])
        except ValueError:
            return web.json_response({"ok": False, "error": "bad_roblox_id"}, status=400)
        row = await self.bot.db.fetchone(
            "SELECT * FROM users WHERE guild_id=? AND roblox_id=?", (config.GUILD_ID, roblox_id)
        )
        if not row:
            return web.json_response({"ok": True, "linked": False})
        return web.json_response(
            {
                "ok": True,
                "linked": True,
                "discordId": str(row["user_id"]),
                "coins": row["coins"],
                "level": row["level"],
                "xp": row["xp"],
            }
        )

    async def api_coins(self, request: web.Request):
        data = await self.read_json(request)
        try:
            roblox_id = int(data.get("robloxId"))
            amount = int(data.get("amount"))
        except (TypeError, ValueError):
            return web.json_response({"ok": False, "error": "bad_params"}, status=400)
        if abs(amount) > 100_000:
            return web.json_response({"ok": False, "error": "amount_too_large"}, status=400)

        db = self.bot.db
        row = await db.fetchone(
            "SELECT user_id FROM users WHERE guild_id=? AND roblox_id=?",
            (config.GUILD_ID, roblox_id),
        )
        if not row:
            return web.json_response({"ok": False, "error": "not_linked"}, status=404)
        balance = await db.add_coins(config.GUILD_ID, row["user_id"], amount)
        if balance is None:
            return web.json_response({"ok": False, "error": "insufficient_funds"}, status=409)
        return web.json_response({"ok": True, "balance": balance})

    async def api_event(self, request: web.Request):
        data = await self.read_json(request)
        guild = self.bot.get_guild(config.GUILD_ID)
        channel_id = await self.bot.db.get_int_setting(config.GUILD_ID, "roblox_channel")
        channel = guild.get_channel(channel_id) if guild and channel_id else None
        if not channel:
            return web.json_response({"ok": False, "error": "no_channel_configured"}, status=409)

        color = data.get("color")
        embed = discord.Embed(
            title=str(data.get("title", "Événement du jeu"))[:256],
            description=str(data.get("message", ""))[:2000],
            color=color if isinstance(color, int) else config.COLOR_MAIN,
            timestamp=discord.utils.utcnow(),
        )
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return web.json_response({"ok": True})

    # --- Appels à l'API publique de Roblox -------------------------------
    async def lookup_user(self, username: str):
        async with self.session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": True},
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
        users = data.get("data") or []
        return users[0] if users else None

    async def get_profile(self, roblox_id: int):
        async with self.session.get(f"https://users.roblox.com/v1/users/{roblox_id}") as resp:
            if resp.status != 200:
                return None
            profile = await resp.json()
        avatar = None
        async with self.session.get(
            "https://thumbnails.roblox.com/v1/users/avatar-headshot",
            params={"userIds": roblox_id, "size": "150x150", "format": "Png"},
        ) as resp:
            if resp.status == 200:
                thumbs = (await resp.json()).get("data") or []
                avatar = thumbs[0].get("imageUrl") if thumbs else None
        profile["avatar"] = avatar
        return profile

    # --- Commandes Discord -----------------------------------------------
    @roblox.command(name="lier", description="Lie ton compte Roblox à ton compte Discord")
    async def link(self, interaction: discord.Interaction, pseudo_roblox: str):
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.lookup_user(pseudo_roblox)
        except aiohttp.ClientError:
            return await interaction.followup.send("Impossible de joindre Roblox, réessaie plus tard.")
        if not user:
            return await interaction.followup.send("Joueur Roblox introuvable.")

        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        db = self.bot.db
        await db.execute("DELETE FROM link_codes WHERE user_id=? AND guild_id=?", (interaction.user.id, interaction.guild_id))
        await db.execute(
            "INSERT INTO link_codes(code, guild_id, user_id, roblox_id, roblox_name, expires) "
            "VALUES(?,?,?,?,?,?)",
            (code, interaction.guild_id, interaction.user.id, user["id"], user["name"],
             time.time() + CODE_TTL),
        )
        await interaction.followup.send(
            f"🔗 Compte trouvé : **{user['name']}**.\n"
            f"Entre maintenant dans le jeu avec ce compte et écris dans le chat :\n"
            f"`/link {code}`\n"
            f"Le code expire dans 10 minutes."
        )

    @roblox.command(name="delier", description="Supprime le lien avec ton compte Roblox")
    async def unlink(self, interaction: discord.Interaction):
        await self.bot.db.execute(
            "UPDATE users SET roblox_id=NULL, roblox_name=NULL WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, interaction.user.id),
        )
        await interaction.response.send_message("✅ Compte Roblox délié.", ephemeral=True)

    @roblox.command(name="profil", description="Affiche le profil Roblox d'un membre")
    async def profile(self, interaction: discord.Interaction, membre: discord.Member | None = None):
        membre = membre or interaction.user
        row = await self.bot.db.fetchone(
            "SELECT roblox_id, roblox_name FROM users WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, membre.id),
        )
        if not row or not row["roblox_id"]:
            return await interaction.response.send_message(
                f"{membre.display_name} n'a pas lié de compte Roblox (`/roblox lier`).", ephemeral=True
            )
        await interaction.response.defer()
        try:
            profile = await self.get_profile(row["roblox_id"])
        except aiohttp.ClientError:
            profile = None
        if not profile:
            return await interaction.followup.send("Impossible de récupérer le profil Roblox.")

        embed = discord.Embed(
            title=f"{profile.get('displayName')} (@{profile.get('name')})",
            url=f"https://www.roblox.com/users/{row['roblox_id']}/profile",
            description=(profile.get("description") or "")[:300] or None,
            color=config.COLOR_MAIN,
        )
        if profile.get("avatar"):
            embed.set_thumbnail(url=profile["avatar"])
        created = (profile.get("created") or "")[:10]
        if created:
            embed.add_field(name="Compte créé le", value=created)
        embed.add_field(name="Membre Discord", value=membre.mention)
        await interaction.followup.send(embed=embed)

    @roblox.command(name="jeu", description="Statistiques en direct de ton jeu Roblox")
    async def game(self, interaction: discord.Interaction):
        if not config.ROBLOX_UNIVERSE_ID:
            return await interaction.response.send_message(
                "Renseigne `ROBLOX_UNIVERSE_ID` dans le fichier .env.", ephemeral=True
            )
        await interaction.response.defer()
        try:
            async with self.session.get(
                "https://games.roblox.com/v1/games",
                params={"universeIds": config.ROBLOX_UNIVERSE_ID},
            ) as resp:
                data = (await resp.json()).get("data") or [] if resp.status == 200 else []
        except aiohttp.ClientError:
            data = []
        if not data:
            return await interaction.followup.send("Impossible de récupérer les stats du jeu.")
        g = data[0]
        embed = discord.Embed(title=f"🎮 {g.get('name')}", color=config.COLOR_MAIN)
        embed.add_field(name="Joueurs en ligne", value=f"{g.get('playing', 0):,}")
        embed.add_field(name="Visites", value=f"{g.get('visits', 0):,}")
        embed.add_field(name="Favoris", value=f"{g.get('favoritedCount', 0):,}")
        root = g.get("rootPlaceId")
        if root:
            embed.url = f"https://www.roblox.com/games/{root}"
        await interaction.followup.send(embed=embed)

    @roblox.command(name="salon", description="(Admin) Salon où le jeu poste ses annonces")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_channel(self, interaction: discord.Interaction, salon: discord.TextChannel):
        await self.bot.db.set_setting(interaction.guild_id, "roblox_channel", salon.id)
        await interaction.response.send_message(
            f"✅ Les annonces du jeu iront dans {salon.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Roblox(bot))
