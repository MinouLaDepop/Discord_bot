"""Avantages : rôle Booster (automatique) et rôle VIP (achat dans le jeu ou donné par le staff).

- 💎 Booster : donné dès qu'un membre booste le serveur, retiré quand son boost s'arrête.
- 👑 VIP : donné quand le joueur achète le VIP dans le jeu (le jeu appelle /api/vip), ou à la main
  avec /vip donner. Si le joueur n'a pas encore lié son compte Roblox, son VIP est mis de côté et
  le rôle arrive dès qu'il le lie (/roblox lier).

Les rôles sont créés automatiquement s'ils n'existent pas (ou par /setup-serveur).
"""
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("perks")

# clé, nom, couleur, permissions, affiché séparément, mentionnable
# (même format que ROLE_SPECS dans setup.py, qui les ajoute à sa liste)
PERK_ROLE_SPECS = [
    ("booster", "💎 Booster", 0xF47FFF, {}, True, False),
    ("vip", "👑 VIP", 0xFFD700, {}, True, False),
]
SPECS = {key: (name, color, hoist) for key, name, color, _perms, hoist, _m in PERK_ROLE_SPECS}

# Un VIP peut venir de deux sources : donné par le staff (id Discord) ou acheté dans le jeu (id Roblox)
KIND_DISCORD = "discord"
KIND_ROBLOX = "roblox"


class Perks(commands.Cog):
    vip = app_commands.Group(
        name="vip",
        description="Gestion des membres VIP",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._synced = False

    async def _guard(self, what: str, coro):
        """Un problème d'avantages ne doit jamais faire planter le bot."""
        try:
            return await coro
        except Exception:
            log.exception("Erreur avantages (%s)", what)
            return False

    # --- Rôles -----------------------------------------------------------
    async def get_role(self, guild: discord.Guild, key: str, *, create: bool = False):
        """Retrouve le rôle (réglage enregistré, sinon par son nom). Le crée si demandé."""
        db = self.bot.db
        name, color, hoist = SPECS[key]
        role_id = await db.get_int_setting(guild.id, f"setup_role_{key}")
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            role = discord.utils.get(guild.roles, name=name)
        if role is None and create:
            try:
                role = await guild.create_role(
                    name=name, colour=discord.Colour(color), hoist=hoist, mentionable=False,
                    reason="Avantages : rôle créé automatiquement",
                )
            except discord.HTTPException:
                log.exception("Impossible de créer le rôle %s (permission « Gérer les rôles » ?)", name)
                return None
            await self._place_above_member(guild, role)
        if role is not None:
            await db.set_setting(guild.id, f"setup_role_{key}", role.id)
        return role

    async def _place_above_member(self, guild: discord.Guild, role: discord.Role):
        """Place un rôle neuf juste au-dessus du rôle Membre, pour qu'il s'affiche dans la liste."""
        member_id = await self.bot.db.get_int_setting(guild.id, "setup_role_member")
        member_role = guild.get_role(member_id) if member_id else None
        if member_role is None:
            return
        target = member_role.position + 1
        if target >= guild.me.top_role.position:
            return
        try:
            await role.edit(position=target, reason="Avantages : placement du rôle")
        except discord.HTTPException:
            log.warning("Impossible de déplacer le rôle %s", role.name)

    async def _set_role(self, member: discord.Member, role: discord.Role, wanted: bool, reason: str) -> bool:
        if (role in member.roles) == wanted:
            return True
        if role >= member.guild.me.top_role:
            log.warning("Le rôle %s est au-dessus du mien : place mon rôle tout en haut.", role.name)
            return False
        try:
            if wanted:
                await member.add_roles(role, reason=reason)
            else:
                await member.remove_roles(role, reason=reason)
        except discord.HTTPException:
            log.exception("Impossible de modifier le rôle %s de %s", role.name, member)
            return False
        return True

    async def _log(self, guild: discord.Guild, title: str, color: int, **fields):
        mod = self.bot.get_cog("Moderation")
        if mod:
            await mod.log(guild, title, color, **fields)

    # --- Booster ---------------------------------------------------------
    async def sync_booster(self, member: discord.Member) -> bool:
        """Donne le rôle Booster si le membre booste, le retire sinon."""
        boosting = member.premium_since is not None
        role = await self.get_role(member.guild, "booster", create=boosting)
        if role is None:
            return not boosting
        had = role in member.roles
        ok = await self._set_role(
            member, role, boosting, "Boost du serveur" if boosting else "Boost terminé"
        )
        if ok and boosting and not had:
            await self._log(
                member.guild, "Nouveau booster 💎", 0xF47FFF, membre=f"{member} ({member.id})"
            )
        return ok

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.premium_since != after.premium_since:
            await self._guard("boost", self.sync_booster(after))

    # --- VIP -------------------------------------------------------------
    async def vip_user_ids(self, guild_id: int) -> set[int]:
        """Tous les membres qui doivent avoir le VIP (donné par le staff ou acheté dans le jeu)."""
        db = self.bot.db
        rows = await db.fetchall(
            "SELECT ident FROM vip WHERE guild_id=? AND kind=?", (guild_id, KIND_DISCORD)
        )
        ids = {r["ident"] for r in rows}
        rows = await db.fetchall(
            "SELECT u.user_id FROM users u JOIN vip v "
            "ON v.guild_id=u.guild_id AND v.kind=? AND v.ident=u.roblox_id WHERE u.guild_id=?",
            (KIND_ROBLOX, guild_id),
        )
        return ids | {r["user_id"] for r in rows}

    async def vip_wanted(self, guild_id: int, user_id: int) -> bool:
        db = self.bot.db
        row = await db.fetchone(
            "SELECT 1 FROM vip WHERE guild_id=? AND kind=? AND ident=?",
            (guild_id, KIND_DISCORD, user_id),
        )
        if row:
            return True
        row = await db.fetchone(
            "SELECT 1 FROM users u JOIN vip v "
            "ON v.guild_id=u.guild_id AND v.kind=? AND v.ident=u.roblox_id "
            "WHERE u.guild_id=? AND u.user_id=?",
            (KIND_ROBLOX, guild_id, user_id),
        )
        return row is not None

    async def sync_vip(self, guild: discord.Guild, user_id: int, *, allow_remove: bool = True) -> bool:
        """Aligne le rôle VIP du membre sur ce qu'il a réellement (staff ou achat en jeu).
        allow_remove=False : on ajoute seulement, on ne retire jamais (vérification au démarrage,
        pour ne pas défaire un VIP donné à la main depuis Discord)."""
        member = guild.get_member(user_id)
        if member is None:
            return False
        wanted = await self.vip_wanted(guild.id, user_id)
        role = await self.get_role(guild, "vip", create=wanted)
        if role is None:
            return not wanted
        has = role in member.roles
        if wanted == has or (not wanted and not allow_remove):
            return True
        ok = await self._set_role(
            member, role, wanted, "VIP (achat dans le jeu ou staff)" if wanted else "VIP retiré"
        )
        if ok:
            await self._log(
                guild, "VIP activé 👑" if wanted else "VIP retiré", 0xFFD700,
                membre=f"{member} ({member.id})",
            )
        return ok

    async def set_roblox_vip(self, guild_id: int, roblox_id: int, active: bool) -> dict:
        """Appelé par l'API quand le jeu active ou retire le VIP d'un joueur Roblox."""
        db = self.bot.db
        if active:
            await db.execute(
                "INSERT OR IGNORE INTO vip(guild_id, kind, ident, created_at) VALUES(?,?,?,?)",
                (guild_id, KIND_ROBLOX, roblox_id, time.time()),
            )
        else:
            await db.execute(
                "DELETE FROM vip WHERE guild_id=? AND kind=? AND ident=?",
                (guild_id, KIND_ROBLOX, roblox_id),
            )
        row = await db.fetchone(
            "SELECT user_id FROM users WHERE guild_id=? AND roblox_id=?", (guild_id, roblox_id)
        )
        if not row:
            # Pas encore lié : le VIP est gardé de côté, le rôle arrivera à la liaison du compte
            return {"linked": False, "applied": False}
        guild = self.bot.get_guild(guild_id)
        applied = bool(guild) and await self.sync_vip(guild, row["user_id"])
        return {"linked": True, "applied": bool(applied)}

    @commands.Cog.listener()
    async def on_roblox_link_changed(self, guild_id: int, user_ids: list):
        """Un compte Roblox vient d'être lié ou délié : on réaligne le rôle VIP des membres concernés."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        for user_id in user_ids:
            await self._guard("liaison Roblox", self.sync_vip(guild, user_id))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        if member.premium_since:
            await self._guard("boost à l'arrivée", self.sync_booster(member))
        if await self.vip_wanted(member.guild.id, member.id):
            await self._guard("VIP à l'arrivée", self.sync_vip(member.guild, member.id))

    # --- Vérification globale --------------------------------------------
    async def sync_guild(self, guild: discord.Guild) -> dict:
        """Rattrape ce qui s'est passé pendant que le bot était éteint."""
        done = {"booster": 0, "vip": 0}
        booster_role = await self.get_role(guild, "booster", create=False)
        for member in guild.members:
            if member.bot:
                continue
            boosting = member.premium_since is not None
            has = booster_role is not None and booster_role in member.roles
            if boosting != has and await self.sync_booster(member):
                done["booster"] += 1

        for user_id in await self.vip_user_ids(guild.id):
            # allow_remove=False : on n'enlève jamais un VIP ici, seulement on en ajoute
            if await self.sync_vip(guild, user_id, allow_remove=False):
                done["vip"] += 1
        return done

    @commands.Cog.listener()
    async def on_ready(self):
        if self._synced:
            return
        self._synced = True
        for guild in self.bot.guilds:
            await self._guard("vérification au démarrage", self.sync_guild(guild))

    @app_commands.command(
        name="avantages-sync", description="(Admin) Vérifie les rôles Booster et VIP de tout le monde"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        done = await self.sync_guild(interaction.guild)
        await interaction.followup.send(
            f"✅ Vérification terminée : {done['booster']} booster(s) mis à jour, "
            f"{done['vip']} VIP vérifié(s).",
            ephemeral=True,
        )

    # --- Commandes VIP ---------------------------------------------------
    @vip.command(name="donner", description="Donne le VIP à un membre (sans achat dans le jeu)")
    @app_commands.checks.has_permissions(administrator=True)
    async def vip_give(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO vip(guild_id, kind, ident, created_at) VALUES(?,?,?,?)",
            (interaction.guild_id, KIND_DISCORD, membre.id, time.time()),
        )
        if await self.sync_vip(interaction.guild, membre.id):
            msg = f"👑 {membre.mention} est maintenant VIP."
        else:
            msg = (
                f"⚠️ {membre.mention} est enregistré VIP, mais je n'ai pas pu lui donner le rôle. "
                "Vérifie que mon rôle est au-dessus du rôle 👑 VIP."
            )
        await interaction.followup.send(msg, ephemeral=True)

    @vip.command(name="retirer", description="Retire le VIP donné à un membre")
    @app_commands.checks.has_permissions(administrator=True)
    async def vip_remove(self, interaction: discord.Interaction, membre: discord.Member):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.execute(
            "DELETE FROM vip WHERE guild_id=? AND kind=? AND ident=?",
            (interaction.guild_id, KIND_DISCORD, membre.id),
        )
        await self.sync_vip(interaction.guild, membre.id)
        if await self.vip_wanted(interaction.guild_id, membre.id):
            msg = (
                f"ℹ️ {membre.mention} garde le VIP : il l'a acheté dans le jeu "
                "(le retirer se fait côté jeu avec `setVip(player, false)`)."
            )
        else:
            msg = f"✅ {membre.mention} n'est plus VIP."
        await interaction.followup.send(msg, ephemeral=True)

    @vip.command(name="liste", description="Affiche les membres VIP")
    @app_commands.checks.has_permissions(administrator=True)
    async def vip_list(self, interaction: discord.Interaction):
        ids = sorted(await self.vip_user_ids(interaction.guild_id))
        pending = await self.bot.db.fetchone(
            "SELECT COUNT(*) AS n FROM vip v WHERE v.guild_id=? AND v.kind=? AND NOT EXISTS "
            "(SELECT 1 FROM users u WHERE u.guild_id=v.guild_id AND u.roblox_id=v.ident)",
            (interaction.guild_id, KIND_ROBLOX),
        )
        lines = [f"👑 <@{i}>" for i in ids[:40]]
        if len(ids) > 40:
            lines.append(f"… et {len(ids) - 40} autre(s)")
        embed = discord.Embed(
            title=f"Membres VIP ({len(ids)})",
            description="\n".join(lines) or "Aucun VIP pour l'instant.",
            color=0xFFD700,
        )
        if pending["n"]:
            embed.set_footer(
                text=f"{pending['n']} joueur(s) ont acheté le VIP mais n'ont pas encore lié leur compte Discord."
            )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Perks(bot))
