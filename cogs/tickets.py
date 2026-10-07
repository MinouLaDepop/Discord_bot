import asyncio
import io
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import report_ui_error


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:20] or "membre"


# clé -> (libellé, emoji, description, préfixe du salon)
TICKET_TYPES = {
    "help": ("Aide / question", "❓", "Une question sur le serveur ou le jeu", "aide"),
    "bug": ("Bug du jeu", "🐞", "Un problème ou un bug dans le jeu", "bug"),
    "report": ("Signaler un joueur", "🚨", "Un joueur ne respecte pas les règles", "signalement"),
    "other": ("Autre demande", "💬", "Partenariat, suggestion, autre…", "autre"),
}


class OpenTicketView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _reset_panel(self, interaction: discord.Interaction):
        """Remet le menu à zéro pour que le même choix puisse être refait."""
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.select(
        custom_id="ticket:open",
        placeholder="🎫 Ouvrir un ticket…",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label=label, value=key, emoji=emoji, description=desc)
            for key, (label, emoji, desc, _) in TICKET_TYPES.items()
        ],
    )
    async def open_ticket(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer(ephemeral=True)
        db = self.bot.db
        guild = interaction.guild
        kind = select.values[0]
        kind_label, kind_emoji, _, kind_prefix = TICKET_TYPES[kind]

        existing = await db.fetchone(
            "SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND closed=0",
            (guild.id, interaction.user.id),
        )
        if existing:
            channel = guild.get_channel(existing["channel_id"])
            if channel:
                await interaction.followup.send(
                    f"Tu as déjà un ticket ouvert : {channel.mention}", ephemeral=True
                )
                return await self._reset_panel(interaction)
            await db.execute(
                "UPDATE tickets SET closed=1 WHERE channel_id=?", (existing["channel_id"],)
            )

        category_id = await db.get_int_setting(guild.id, "ticket_category")
        support_id = await db.get_int_setting(guild.id, "ticket_support_role")
        category = guild.get_channel(category_id) if category_id else None
        support_role = guild.get_role(support_id) if support_id else None
        if not category:
            return await interaction.followup.send(
                "Le système de tickets n'est pas configuré (`/ticket-config`).", ephemeral=True
            )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True,
                read_message_history=True,
            ),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True,
                read_message_history=True,
            )

        try:
            channel = await guild.create_text_channel(
                name=f"{kind_prefix}-{slugify(interaction.user.name)}",
                category=category,
                overwrites=overwrites,
                topic=f"{kind_label} — ticket de {interaction.user} ({interaction.user.id})",
                reason="Ouverture de ticket",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                "Je n'ai pas la permission de créer des salons dans cette catégorie.",
                ephemeral=True,
            )
        except discord.HTTPException:
            # Par exemple : catégorie pleine (50 salons max) ou limite de salons du serveur
            return await interaction.followup.send(
                "Impossible de créer le ticket pour l'instant (catégorie pleine ?). "
                "Préviens le staff.", ephemeral=True,
            )

        await db.execute(
            "INSERT INTO tickets(guild_id, channel_id, user_id, created_at) VALUES(?,?,?,?)",
            (guild.id, channel.id, interaction.user.id, time.time()),
        )

        embed = discord.Embed(
            title=f"{kind_emoji} {kind_label}",
            description=(
                f"Bonjour {interaction.user.mention}, explique ta demande ici avec un maximum de détails "
                "(captures d'écran bienvenues).\n"
                "Un membre de l'équipe va te répondre rapidement."
            ),
            color=config.COLOR_MAIN,
        )
        ping = support_role.mention if support_role else ""
        await channel.send(
            content=f"{interaction.user.mention} {ping}".strip(),
            embed=embed,
            view=CloseTicketView(self.bot),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await interaction.followup.send(f"✅ Ton ticket est prêt : {channel.mention}", ephemeral=True)
        await self._reset_panel(interaction)

    async def on_error(self, interaction, error, item):
        await report_ui_error(interaction, error, "ticket:open")


class CloseTicketView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Fermer le ticket", emoji="🔒", style=discord.ButtonStyle.danger,
        custom_id="ticket:close",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        db = self.bot.db
        guild = interaction.guild
        channel = interaction.channel

        ticket = await db.fetchone(
            "SELECT * FROM tickets WHERE channel_id=? AND closed=0", (channel.id,)
        )
        if not ticket:
            return await interaction.response.send_message(
                "Ce salon n'est pas un ticket ouvert.", ephemeral=True
            )

        support_id = await db.get_int_setting(guild.id, "ticket_support_role")
        is_support = bool(support_id and interaction.user.get_role(support_id))
        is_owner = interaction.user.id == ticket["user_id"]
        if not (is_support or is_owner or interaction.user.guild_permissions.manage_channels):
            return await interaction.response.send_message(
                "Seul l'auteur du ticket ou l'équipe peut le fermer.", ephemeral=True
            )

        # On marque d'abord le ticket comme fermé : un double-clic ne le ferme pas deux fois
        await db.execute("UPDATE tickets SET closed=1 WHERE channel_id=?", (channel.id,))
        await interaction.response.send_message("🔒 Fermeture du ticket dans 5 secondes…")

        # Transcript envoyé dans le salon de logs
        lines = []
        try:
            async for msg in channel.history(limit=1000, oldest_first=True):
                when = msg.created_at.strftime("%d/%m/%Y %H:%M")
                content = msg.content or ""
                if msg.attachments:
                    content += " " + " ".join(a.url for a in msg.attachments)
                if msg.embeds and not content:
                    content = "[embed]"
                lines.append(f"[{when}] {msg.author}: {content}")
        except discord.HTTPException:
            lines.append("(historique incomplet : Discord n'a pas pu le lire en entier)")
        transcript = "\n".join(lines) or "(vide)"

        log_id = await db.get_int_setting(guild.id, "ticket_log")
        log_channel = guild.get_channel(log_id) if log_id else None
        if log_channel:
            opener = guild.get_member(ticket["user_id"])
            embed = discord.Embed(title="Ticket fermé", color=config.COLOR_WARN)
            embed.add_field(name="Auteur", value=opener.mention if opener else ticket["user_id"])
            embed.add_field(name="Fermé par", value=interaction.user.mention)
            file = discord.File(
                io.BytesIO(transcript.encode("utf-8")), filename=f"{channel.name}.txt"
            )
            try:
                await log_channel.send(embed=embed, file=file)
            except discord.HTTPException:
                pass

        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket fermé par {interaction.user}")
        except discord.HTTPException:
            pass

    async def on_error(self, interaction, error, item):
        await report_ui_error(interaction, error, "ticket:close")


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Vues persistantes : les boutons marchent encore après un redémarrage
        self.bot.add_view(OpenTicketView(self.bot))
        self.bot.add_view(CloseTicketView(self.bot))

    @app_commands.command(
        name="ticket-config",
        description="Configure les tickets (catégorie, rôle du support, logs)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_config(
        self,
        interaction: discord.Interaction,
        categorie: discord.CategoryChannel,
        role_support: discord.Role,
        salon_logs: discord.TextChannel | None = None,
    ):
        db = self.bot.db
        await db.set_setting(interaction.guild_id, "ticket_category", categorie.id)
        await db.set_setting(interaction.guild_id, "ticket_support_role", role_support.id)
        await db.set_setting(
            interaction.guild_id, "ticket_log", salon_logs.id if salon_logs else None
        )
        await interaction.response.send_message(
            "✅ Tickets configurés. Utilise `/ticket-panel` pour poster le bouton.",
            ephemeral=True,
        )

    @app_commands.command(name="ticket-panel", description="Poste le panneau d'ouverture de tickets")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_panel(
        self,
        interaction: discord.Interaction,
        titre: str = "Besoin d'aide ?",
        description: str = "Clique sur le bouton ci-dessous pour ouvrir un ticket privé avec l'équipe.",
    ):
        category_id = await self.bot.db.get_int_setting(interaction.guild_id, "ticket_category")
        if not category_id:
            return await interaction.response.send_message(
                "Configure d'abord avec `/ticket-config`.", ephemeral=True
            )
        embed = discord.Embed(title=titre, description=description, color=config.COLOR_MAIN)
        await interaction.channel.send(embed=embed, view=OpenTicketView(self.bot))
        await interaction.response.send_message("✅ Panneau publié.", ephemeral=True)

    async def _is_ticket(self, interaction: discord.Interaction) -> bool:
        row = await self.bot.db.fetchone(
            "SELECT 1 FROM tickets WHERE channel_id=? AND closed=0", (interaction.channel_id,)
        )
        return row is not None

    @app_commands.command(name="ticket-ajouter", description="Ajoute un membre au ticket actuel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def ticket_add(self, interaction: discord.Interaction, membre: discord.Member):
        if not await self._is_ticket(interaction):
            return await interaction.response.send_message(
                "Cette commande se fait dans un ticket.", ephemeral=True
            )
        await interaction.channel.set_permissions(
            membre, view_channel=True, send_messages=True, read_message_history=True
        )
        await interaction.response.send_message(f"✅ {membre.mention} a été ajouté au ticket.")

    @app_commands.command(name="ticket-retirer", description="Retire un membre du ticket actuel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def ticket_remove(self, interaction: discord.Interaction, membre: discord.Member):
        if not await self._is_ticket(interaction):
            return await interaction.response.send_message(
                "Cette commande se fait dans un ticket.", ephemeral=True
            )
        await interaction.channel.set_permissions(membre, overwrite=None)
        await interaction.response.send_message(f"✅ {membre.mention} a été retiré du ticket.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
