import discord
from discord import app_commands
from discord.ext import commands

import config

DEFAULT_MESSAGE = "Bienvenue {user} sur **{server}** ! Tu es le membre n°{count} 🎉"


class RoleButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"rolepanel:(?P<role_id>[0-9]+)"
):
    """Bouton de rôle persistant : reste fonctionnel après un redémarrage du bot."""

    def __init__(self, role_id: int, label: str = "Rôle"):
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"rolepanel:{role_id}",
            )
        )
        self.role_id = role_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["role_id"]))

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            return await interaction.response.send_message(
                "Ce rôle n'existe plus.", ephemeral=True
            )
        member = interaction.user
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Panneau de rôles")
                await interaction.response.send_message(
                    f"➖ Rôle **{role.name}** retiré.", ephemeral=True
                )
            else:
                await member.add_roles(role, reason="Panneau de rôles")
                await interaction.response.send_message(
                    f"➕ Rôle **{role.name}** ajouté.", ephemeral=True
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Je ne peux pas gérer ce rôle (il est au-dessus du mien).", ephemeral=True
            )


class Welcome(commands.Cog):
    accueil = app_commands.Group(
        name="accueil",
        description="Configuration de l'accueil des nouveaux membres",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_dynamic_items(RoleButton)

    # --- Événement d'arrivée ---------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        db = self.bot.db
        guild = member.guild

        role_id = await db.get_int_setting(guild.id, "autorole")
        if role_id and (role := guild.get_role(role_id)):
            try:
                await member.add_roles(role, reason="Rôle automatique")
            except discord.HTTPException:
                pass

        # Si la vérification est active, la bienvenue est envoyée une fois le membre vérifié
        if await db.get_int_setting(guild.id, "verified_role"):
            return

        channel_id = await db.get_int_setting(guild.id, "welcome_channel")
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel:
            await self.send_welcome(member, channel)

    @commands.Cog.listener()
    async def on_member_verified(self, member: discord.Member):
        channel_id = await self.bot.db.get_int_setting(member.guild.id, "welcome_channel")
        channel = member.guild.get_channel(channel_id) if channel_id else None
        if channel:
            await self.send_welcome(member, channel)

    async def send_welcome(self, member: discord.Member, channel: discord.abc.Messageable):
        template = await self.bot.db.get_setting(
            member.guild.id, "welcome_message", DEFAULT_MESSAGE
        )
        text = template.format(
            user=member.mention, server=member.guild.name, count=member.guild.member_count
        )
        embed = discord.Embed(description=text, color=config.COLOR_MAIN)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # --- Commandes de configuration --------------------------------------
    @accueil.command(name="salon", description="Choisit le salon des messages de bienvenue")
    async def accueil_salon(self, interaction: discord.Interaction, salon: discord.TextChannel):
        await self.bot.db.set_setting(interaction.guild_id, "welcome_channel", salon.id)
        await interaction.response.send_message(
            f"✅ Les bienvenues seront envoyées dans {salon.mention}.", ephemeral=True
        )

    @accueil.command(
        name="message",
        description="Change le message. Variables : {user} {server} {count}",
    )
    async def accueil_message(self, interaction: discord.Interaction, texte: str):
        try:
            texte.format(user="x", server="x", count=0)
        except (KeyError, IndexError, ValueError):
            return await interaction.response.send_message(
                "Message invalide. Variables autorisées : {user} {server} {count}.",
                ephemeral=True,
            )
        await self.bot.db.set_setting(interaction.guild_id, "welcome_message", texte)
        await interaction.response.send_message("✅ Message enregistré.", ephemeral=True)

    @accueil.command(name="role", description="Définit le rôle donné automatiquement aux nouveaux")
    async def accueil_role(self, interaction: discord.Interaction, role: discord.Role):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "Ce rôle est au-dessus du mien : je ne pourrai pas le donner.", ephemeral=True
            )
        await self.bot.db.set_setting(interaction.guild_id, "autorole", role.id)
        await interaction.response.send_message(
            f"✅ Les nouveaux membres recevront {role.mention}.", ephemeral=True
        )

    @accueil.command(name="test", description="Simule ton arrivée pour tester le message")
    async def accueil_test(self, interaction: discord.Interaction):
        channel_id = await self.bot.db.get_int_setting(interaction.guild_id, "welcome_channel")
        channel = interaction.guild.get_channel(channel_id) if channel_id else None
        if not channel:
            return await interaction.response.send_message(
                "Configure d'abord un salon avec `/accueil salon`.", ephemeral=True
            )
        await self.send_welcome(interaction.user, channel)
        await interaction.response.send_message("✅ Message de test envoyé.", ephemeral=True)

    @accueil.command(name="desactiver", description="Désactive le message de bienvenue et le rôle auto")
    async def accueil_off(self, interaction: discord.Interaction):
        await self.bot.db.set_setting(interaction.guild_id, "welcome_channel", None)
        await self.bot.db.set_setting(interaction.guild_id, "autorole", None)
        await interaction.response.send_message("✅ Accueil désactivé.", ephemeral=True)

    # --- Panneau de rôles à boutons --------------------------------------
    @app_commands.command(
        name="panneau-roles",
        description="Poste un panneau où les membres choisissent leurs rôles avec des boutons",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_panel(
        self,
        interaction: discord.Interaction,
        titre: str,
        role1: discord.Role,
        role2: discord.Role | None = None,
        role3: discord.Role | None = None,
        role4: discord.Role | None = None,
        role5: discord.Role | None = None,
        description: str = "Clique sur un bouton pour obtenir ou retirer un rôle.",
    ):
        roles = [r for r in (role1, role2, role3, role4, role5) if r]
        too_high = [r.name for r in roles if r >= interaction.guild.me.top_role or r.managed]
        if too_high:
            return await interaction.response.send_message(
                "Je ne peux pas gérer ces rôles : " + ", ".join(too_high)
                + ".\nPlace mon rôle au-dessus d'eux dans les paramètres du serveur.",
                ephemeral=True,
            )
        view = discord.ui.View(timeout=None)
        for r in roles:
            view.add_item(RoleButton(r.id, r.name))
        embed = discord.Embed(title=titre, description=description, color=config.COLOR_MAIN)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ Panneau publié.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
