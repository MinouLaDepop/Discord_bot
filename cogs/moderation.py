import datetime
import time

import discord
from discord import app_commands
from discord.ext import commands

import config

AUTO_TIMEOUT_AT = 3  # nombre d'avertissements avant mute automatique d'1h


def hierarchy_error(interaction: discord.Interaction, target: discord.Member):
    """Retourne un message d'erreur si l'action sur `target` est interdite."""
    guild = interaction.guild
    if target.id == interaction.user.id:
        return "Tu ne peux pas te cibler toi-même."
    if target.id == guild.owner_id:
        return "Impossible de cibler le propriétaire du serveur."
    if (
        interaction.user.id != guild.owner_id
        and target.top_role >= interaction.user.top_role
    ):
        return "Ce membre a un rôle supérieur ou égal au tien."
    if target.top_role >= guild.me.top_role:
        return "Mon rôle est trop bas pour agir sur ce membre."
    return None


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- Journal de modération -------------------------------------------
    async def log(self, guild: discord.Guild, title: str, color: int, **fields):
        channel_id = await self.bot.db.get_int_setting(guild.id, "log_channel")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not channel:
            return
        embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
        for name, value in fields.items():
            embed.add_field(
                name=name.replace("_", " ").capitalize(), value=str(value)[:1024] or "—", inline=True
            )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @app_commands.command(name="logs", description="Définit le salon des logs de modération")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_logs(self, interaction: discord.Interaction, salon: discord.TextChannel):
        await self.bot.db.set_setting(interaction.guild_id, "log_channel", salon.id)
        await interaction.response.send_message(
            f"✅ Les logs seront envoyés dans {salon.mention}.", ephemeral=True
        )

    # --- Expulsion / bannissement ----------------------------------------
    @app_commands.command(name="kick", description="Expulse un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        membre: discord.Member,
        raison: str = "Aucune raison donnée",
    ):
        if err := hierarchy_error(interaction, membre):
            return await interaction.response.send_message(err, ephemeral=True)
        await membre.kick(reason=f"{interaction.user} : {raison}")
        await interaction.response.send_message(f"👢 **{membre}** a été expulsé. ({raison})")
        await self.log(
            interaction.guild, "Expulsion", config.COLOR_WARN,
            membre=f"{membre} ({membre.id})", modérateur=interaction.user.mention, raison=raison,
        )

    @app_commands.command(name="ban", description="Bannit un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        membre: discord.Member,
        raison: str = "Aucune raison donnée",
        jours_messages: app_commands.Range[int, 0, 7] = 0,
    ):
        if err := hierarchy_error(interaction, membre):
            return await interaction.response.send_message(err, ephemeral=True)
        await membre.ban(
            reason=f"{interaction.user} : {raison}",
            delete_message_seconds=jours_messages * 86400,
        )
        await interaction.response.send_message(f"🔨 **{membre}** a été banni. ({raison})")
        await self.log(
            interaction.guild, "Bannissement", config.COLOR_ERR,
            membre=f"{membre} ({membre.id})", modérateur=interaction.user.mention, raison=raison,
        )

    @app_commands.command(name="unban", description="Débannit un utilisateur grâce à son ID")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, utilisateur_id: str):
        if not utilisateur_id.isdigit():
            return await interaction.response.send_message("ID invalide.", ephemeral=True)
        try:
            await interaction.guild.unban(
                discord.Object(id=int(utilisateur_id)), reason=f"Par {interaction.user}"
            )
        except discord.NotFound:
            return await interaction.response.send_message(
                "Cet utilisateur n'est pas banni.", ephemeral=True
            )
        await interaction.response.send_message(f"✅ <@{utilisateur_id}> a été débanni.")
        await self.log(
            interaction.guild, "Débannissement", config.COLOR_OK,
            utilisateur=utilisateur_id, modérateur=interaction.user.mention,
        )

    # --- Mute (timeout) ---------------------------------------------------
    @app_commands.command(name="timeout", description="Rend un membre muet pour une durée")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        membre: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        raison: str = "Aucune raison donnée",
    ):
        if err := hierarchy_error(interaction, membre):
            return await interaction.response.send_message(err, ephemeral=True)
        await membre.timeout(datetime.timedelta(minutes=minutes), reason=f"{interaction.user} : {raison}")
        await interaction.response.send_message(
            f"🔇 **{membre}** est muet pour {minutes} min. ({raison})"
        )
        await self.log(
            interaction.guild, "Timeout", config.COLOR_WARN,
            membre=f"{membre} ({membre.id})", durée=f"{minutes} min",
            modérateur=interaction.user.mention, raison=raison,
        )

    @app_commands.command(name="untimeout", description="Retire le mute d'un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, membre: discord.Member):
        await membre.timeout(None, reason=f"Par {interaction.user}")
        await interaction.response.send_message(f"🔊 **{membre}** peut de nouveau parler.")

    # --- Nettoyage / salon -----------------------------------------------
    @app_commands.command(name="clear", description="Supprime des messages du salon")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True)
    async def clear(
        self,
        interaction: discord.Interaction,
        nombre: app_commands.Range[int, 1, 100],
        membre: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        check = (lambda m: m.author.id == membre.id) if membre else None
        deleted = await interaction.channel.purge(limit=nombre, check=check)
        await interaction.followup.send(f"🧹 {len(deleted)} message(s) supprimé(s).", ephemeral=True)

    @app_commands.command(name="slowmode", description="Règle le mode lent du salon (0 = désactivé)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def slowmode(
        self, interaction: discord.Interaction, secondes: app_commands.Range[int, 0, 21600]
    ):
        await interaction.channel.edit(slowmode_delay=secondes)
        await interaction.response.send_message(
            "🐢 Mode lent désactivé." if secondes == 0 else f"🐢 Mode lent réglé sur {secondes}s."
        )

    @app_commands.command(name="lock", description="Verrouille un salon (personne ne peut écrire)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, salon: discord.TextChannel | None = None):
        salon = salon or interaction.channel
        await salon.set_permissions(interaction.guild.default_role, send_messages=False)
        await interaction.response.send_message(f"🔒 {salon.mention} est verrouillé.")

    @app_commands.command(name="unlock", description="Déverrouille un salon")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, salon: discord.TextChannel | None = None):
        salon = salon or interaction.channel
        await salon.set_permissions(interaction.guild.default_role, send_messages=None)
        await interaction.response.send_message(f"🔓 {salon.mention} est déverrouillé.")

    # --- Avertissements --------------------------------------------------
    @app_commands.command(name="warn", description="Avertit un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(
        self, interaction: discord.Interaction, membre: discord.Member, raison: str
    ):
        if err := hierarchy_error(interaction, membre):
            return await interaction.response.send_message(err, ephemeral=True)
        db = self.bot.db
        await db.execute(
            "INSERT INTO warnings(guild_id, user_id, mod_id, reason, created_at) VALUES(?,?,?,?,?)",
            (interaction.guild_id, membre.id, interaction.user.id, raison, time.time()),
        )
        row = await db.fetchone(
            "SELECT COUNT(*) AS n FROM warnings WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, membre.id),
        )
        total = row["n"]
        msg = f"⚠️ **{membre}** a reçu un avertissement ({total} au total). Raison : {raison}"

        try:
            await membre.send(f"⚠️ Avertissement sur **{interaction.guild.name}** : {raison}")
        except discord.HTTPException:
            pass

        if total >= AUTO_TIMEOUT_AT and interaction.guild.me.guild_permissions.moderate_members:
            try:
                await membre.timeout(datetime.timedelta(hours=1), reason="Avertissements cumulés")
                msg += f"\n🔇 Mute automatique d'1h ({AUTO_TIMEOUT_AT} avertissements atteints)."
            except discord.HTTPException:
                pass

        await interaction.response.send_message(msg)
        await self.log(
            interaction.guild, "Avertissement", config.COLOR_WARN,
            membre=f"{membre} ({membre.id})", modérateur=interaction.user.mention,
            raison=raison, total=str(total),
        )

    @app_commands.command(name="warnings", description="Affiche les avertissements d'un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, membre: discord.Member):
        rows = await self.bot.db.fetchall(
            "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 15",
            (interaction.guild_id, membre.id),
        )
        if not rows:
            return await interaction.response.send_message(
                f"{membre.mention} n'a aucun avertissement.", ephemeral=True
            )
        embed = discord.Embed(title=f"Avertissements de {membre}"[:256], color=config.COLOR_WARN)
        for r in rows:
            embed.add_field(
                name=f"#{r['id']} · <t:{int(r['created_at'])}:d>",
                value=f"{str(r['reason'])[:900]}\n*par <@{r['mod_id']}>*",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="delwarn", description="Supprime un avertissement grâce à son numéro")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def delwarn(self, interaction: discord.Interaction, numero: int):
        cur = await self.bot.db.execute(
            "DELETE FROM warnings WHERE id=? AND guild_id=?", (numero, interaction.guild_id)
        )
        msg = "✅ Avertissement supprimé." if cur.rowcount else "Avertissement introuvable."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="clearwarns", description="Efface tous les avertissements d'un membre")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def clearwarns(self, interaction: discord.Interaction, membre: discord.Member):
        await self.bot.db.execute(
            "DELETE FROM warnings WHERE guild_id=? AND user_id=?",
            (interaction.guild_id, membre.id),
        )
        await interaction.response.send_message(
            f"✅ Avertissements de {membre.mention} effacés.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
