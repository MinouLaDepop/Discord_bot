"""Vérification obligatoire avant d'entrer dans le serveur.

Principe : @everyone ne voit que #règles et #vérification. Le membre doit lire les
règles, y trouver le « mot de passe », cliquer sur le bouton et le taper. Il reçoit
alors le rôle Membre, qui ouvre tous les autres salons.
"""
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import report_ui_error

GAME_NAME = "Hatch a Mutant"

RULES = [
    "Respecte tout le monde : pas d'insultes, de harcèlement ni de discrimination.",
    "Pas de spam, de pub ni de lien sans l'accord du staff.",
    "Pas de contenu choquant, NSFW ou illégal.",
    "Pas d'arnaque, d'échange de comptes ni de triche (exploits, scripts) dans le jeu.",
    "Ne partage jamais tes infos personnelles ni ton mot de passe Roblox : le staff ne te les demandera jamais.",
    "Écoute le staff et utilise le bon salon pour chaque sujet.",
    "Un problème ? Ouvre un ticket plutôt que de mentionner le staff.",
]

WORDS = ["licorne", "dragon", "mutant", "phénix", "griffon", "zoo", "œuf", "chimère"]

MAX_ATTEMPTS = 5
LOCK_SECONDS = 600


def generate_word() -> str:
    return f"{random.choice(WORDS)}-{random.randint(10, 99)}"


def build_rules_embed(word: str) -> discord.Embed:
    lines = [f"**{i}.** {rule}" for i, rule in enumerate(RULES, start=1)]
    embed = discord.Embed(
        title=f"📜 Règles de {GAME_NAME}",
        description="\n".join(lines),
        color=config.COLOR_MAIN,
    )
    embed.add_field(
        name="🔑 Mot de passe de vérification",
        value=f"`{word}`\nRends-toi dans #vérification et tape-le pour entrer.",
        inline=False,
    )
    embed.set_footer(text="En entrant, tu acceptes ces règles.")
    return embed


def build_verify_embed() -> discord.Embed:
    return discord.Embed(
        title="✅ Vérification",
        description=(
            f"Bienvenue sur le serveur **{GAME_NAME}** !\n\n"
            "1. Lis les règles dans le salon des règles.\n"
            "2. Trouve le **mot de passe de vérification**.\n"
            "3. Clique sur le bouton ci-dessous et tape-le.\n\n"
            "Tu auras alors accès à tout le serveur."
        ),
        color=config.COLOR_OK,
    )


def normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


class VerifyModal(discord.ui.Modal, title="Vérification"):
    answer = discord.ui.TextInput(
        label="Mot de passe trouvé dans les règles",
        placeholder="ex : dragon-42",
        max_length=40,
    )

    def __init__(self, cog: "Verification"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.check_answer(interaction, self.answer.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_ui_error(interaction, error, "verify:modal")


class VerifyView(discord.ui.View):
    """Bouton persistant du salon #vérification."""

    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Je veux entrer", emoji="✅", style=discord.ButtonStyle.success,
        custom_id="verify:start",
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: Verification = self.bot.get_cog("Verification")
        await cog.on_click(interaction)

    async def on_error(self, interaction, error, item):
        await report_ui_error(interaction, error, "verify:start")


class Verification(commands.Cog):
    verification = app_commands.Group(
        name="verification",
        description="Réglages de la vérification d'entrée",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.failures: dict[int, int] = {}
        self.locked_until: dict[int, float] = {}

    async def cog_load(self):
        self.bot.add_view(VerifyView(self.bot))

    async def verified_role(self, guild: discord.Guild):
        role_id = await self.bot.db.get_int_setting(guild.id, "verified_role")
        return guild.get_role(role_id) if role_id else None

    # --- Parcours du membre ----------------------------------------------
    async def on_click(self, interaction: discord.Interaction):
        member = interaction.user
        role = await self.verified_role(interaction.guild)
        if role is None:
            return await interaction.response.send_message(
                "La vérification n'est pas encore configurée. Préviens le staff.", ephemeral=True
            )
        if role in member.roles:
            return await interaction.response.send_message(
                "Tu es déjà vérifié, profite du serveur ! 🎉", ephemeral=True
            )

        locked = self.locked_until.get(member.id, 0)
        if time.time() < locked:
            return await interaction.response.send_message(
                f"Trop d'essais ratés. Réessaie <t:{int(locked)}:R>.", ephemeral=True
            )

        try:
            min_days = int(await self.bot.db.get_setting(interaction.guild_id, "verify_min_age_days", "1"))
        except ValueError:
            min_days = 1
        age = discord.utils.utcnow() - member.created_at
        if age.total_seconds() < min_days * 86400:
            ready = int(member.created_at.timestamp() + min_days * 86400)
            return await interaction.response.send_message(
                f"Ton compte Discord est trop récent. Reviens <t:{ready}:R> pour te vérifier.",
                ephemeral=True,
            )

        await interaction.response.send_modal(VerifyModal(self))

    async def check_answer(self, interaction: discord.Interaction, answer: str):
        member = interaction.user
        guild = interaction.guild
        word = await self.bot.db.get_setting(guild.id, "verify_word")
        role = await self.verified_role(guild)
        if not word or role is None:
            return await interaction.response.send_message(
                "La vérification n'est pas configurée. Préviens le staff.", ephemeral=True
            )

        if normalize(answer) != normalize(word):
            fails = self.failures.get(member.id, 0) + 1
            self.failures[member.id] = fails
            if fails >= MAX_ATTEMPTS:
                self.failures.pop(member.id, None)
                self.locked_until[member.id] = time.time() + LOCK_SECONDS
                return await interaction.response.send_message(
                    "❌ Trop d'essais ratés. Relis bien les règles et réessaie dans 10 minutes.",
                    ephemeral=True,
                )
            return await interaction.response.send_message(
                f"❌ Ce n'est pas le bon mot de passe ({MAX_ATTEMPTS - fails} essai(s) restant(s)). "
                "Il est écrit dans les règles !",
                ephemeral=True,
            )

        self.failures.pop(member.id, None)
        ok = await self.grant(member, "Vérification réussie")
        if not ok:
            return await interaction.response.send_message(
                "Je n'arrive pas à te donner le rôle. Préviens le staff (mon rôle est trop bas ?).",
                ephemeral=True,
            )
        await interaction.response.send_message(
            "✅ Vérifié ! Bienvenue, tous les salons sont maintenant ouverts.", ephemeral=True
        )

    async def grant(self, member: discord.Member, reason: str) -> bool:
        role = await self.verified_role(member.guild)
        if role is None:
            return False
        try:
            await member.add_roles(role, reason=reason)
        except discord.HTTPException:
            return False

        self.bot.dispatch("member_verified", member)
        mod = self.bot.get_cog("Moderation")
        if mod:
            await mod.log(
                member.guild, "Membre vérifié", config.COLOR_OK,
                membre=f"{member} ({member.id})", méthode=reason,
            )
        return True

    # --- Commandes staff -------------------------------------------------
    @app_commands.command(name="verifier", description="Vérifie un membre à la main")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def verify_manual(self, interaction: discord.Interaction, membre: discord.Member):
        if await self.grant(membre, f"Vérifié manuellement par {interaction.user}"):
            await interaction.response.send_message(
                f"✅ {membre.mention} est maintenant vérifié.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "Impossible : la vérification n'est pas configurée ou mon rôle est trop bas.",
                ephemeral=True,
            )

    @verification.command(name="configurer", description="Choisit le rôle Membre et poste le panneau")
    async def configure(
        self, interaction: discord.Interaction, role_membre: discord.Role,
        salon: discord.TextChannel,
    ):
        if role_membre >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "Ce rôle est au-dessus du mien : je ne pourrai pas le donner.", ephemeral=True
            )
        db = self.bot.db
        await db.set_setting(interaction.guild_id, "verified_role", role_membre.id)
        if not await db.get_setting(interaction.guild_id, "verify_word"):
            await db.set_setting(interaction.guild_id, "verify_word", generate_word())
        await salon.send(embed=build_verify_embed(), view=VerifyView(self.bot))
        await interaction.response.send_message(
            "✅ Panneau publié. Le mot de passe est à écrire dans tes règles "
            "(voir `/verification mot`).", ephemeral=True,
        )

    @verification.command(name="mot", description="Affiche ou change le mot de passe de vérification")
    async def set_word(self, interaction: discord.Interaction, nouveau_mot: str | None = None):
        db = self.bot.db
        gid = interaction.guild_id
        if nouveau_mot:
            await db.set_setting(gid, "verify_word", nouveau_mot.strip()[:40])
            refreshed = await self.refresh_rules(interaction.guild)
            extra = "Le message des règles a été mis à jour." if refreshed else (
                "Pense à écrire ce mot dans tes règles."
            )
            return await interaction.response.send_message(
                f"✅ Nouveau mot de passe : `{nouveau_mot.strip()[:40]}`. {extra}", ephemeral=True
            )
        word = await db.get_setting(gid, "verify_word", "(non défini)")
        await interaction.response.send_message(f"🔑 Mot de passe actuel : `{word}`", ephemeral=True)

    @verification.command(name="age", description="Âge minimum du compte Discord (en jours, 0 = aucun)")
    async def set_age(self, interaction: discord.Interaction, jours: app_commands.Range[int, 0, 60]):
        await self.bot.db.set_setting(interaction.guild_id, "verify_min_age_days", jours)
        await interaction.response.send_message(
            f"✅ Âge minimum du compte : {jours} jour(s).", ephemeral=True
        )

    async def refresh_rules(self, guild: discord.Guild) -> bool:
        """Met à jour le message des règles créé par /setup-serveur."""
        db = self.bot.db
        channel_id = await db.get_int_setting(guild.id, "setup_ch_rules")
        message_id = await db.get_int_setting(guild.id, "setup_panel_rules")
        word = await db.get_setting(guild.id, "verify_word")
        channel = guild.get_channel(channel_id) if channel_id else None
        if not (channel and message_id and word):
            return False
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=build_rules_embed(word))
        except discord.HTTPException:
            return False
        return True


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
