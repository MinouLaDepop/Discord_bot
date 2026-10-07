"""/setup-serveur : crée en une commande les rôles, salons, permissions et panneaux,
et relie tout au bot (tickets, vérification, logs, accueil, annonces du jeu).

Relançable sans danger : ce qui existe déjà est réutilisé, pas dupliqué.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.perks import PERK_ROLE_SPECS
from cogs.tickets import OpenTicketView
from cogs.verification import (
    VerifyView, build_rules_embed, build_verify_embed, generate_word,
)
from cogs.welcome import RoleButton

log = logging.getLogger("setup")

# clé, nom, couleur, permissions, affiché séparément, mentionnable
ROLE_SPECS = [
    ("admin", "🛡️ Admin", 0xE74C3C, dict(
        manage_channels=True, manage_roles=True, manage_messages=True, kick_members=True,
        ban_members=True, moderate_members=True, view_audit_log=True, manage_nicknames=True,
        mention_everyone=True), True, False),
    ("mod", "🔨 Modérateur", 0xE67E22, dict(
        manage_messages=True, kick_members=True, ban_members=True, moderate_members=True,
        view_audit_log=True, manage_nicknames=True), True, False),
    ("support", "🎧 Support", 0x3498DB, dict(
        manage_messages=True, moderate_members=True), True, True),
    *PERK_ROLE_SPECS,  # 💎 Booster et 👑 VIP, juste sous le staff
    ("member", "✅ Membre", 0x2ECC71, {}, False, False),
    ("ping_news", "📢 Annonces", 0x95A5A6, {}, False, True),
    ("ping_events", "🎉 Événements", 0x95A5A6, {}, False, True),
    ("ping_updates", "🔔 Mises à jour", 0x95A5A6, {}, False, True),
]
STAFF_KEYS = ("admin", "mod", "support")

# Types de permissions de salon (voir overwrites_for)
CATEGORY_KIND = {
    "gate": "gate_ro", "info": "member_ro", "community": "member_ro", "game": "member_ro",
    "support": "member_ro", "voice": "voice", "staff": "staff", "tickets": "staff",
}

# (clé catégorie, nom, [(clé salon, nom, type, permissions, description)])
LAYOUT = [
    ("gate", "🚪 ACCUEIL", [
        ("rules", "📜・règles", "text", "gate_ro", "Les règles du serveur. Le mot de passe de vérification s'y trouve !"),
        ("verify", "✅・vérification", "text", "gate_ro", "Clique sur le bouton pour entrer dans le serveur."),
    ]),
    ("info", "📢 INFORMATIONS", [
        ("welcome", "👋・bienvenue", "text", "member_ro", "Les nouveaux membres arrivent ici."),
        ("news", "📣・annonces", "text", "member_ro", "Les annonces officielles."),
        ("events", "🎉・événements", "text", "member_ro", "Concours, events et giveaways."),
        ("updates", "🔔・mises-à-jour", "text", "member_ro", "Les nouveautés du jeu."),
        ("roles", "🎭・rôles", "text", "member_ro", "Choisis les notifications que tu veux recevoir."),
    ]),
    ("community", "💬 COMMUNAUTÉ", [
        ("general", "💬・général", "text", "member_rw", "Discute avec la communauté."),
        ("media", "📸・médias", "text", "member_rw", "Images, vidéos et clips."),
        ("suggestions", "💡・suggestions", "text", "member_rw", "Propose tes idées pour le serveur et le jeu."),
        ("botcmd", "🤖・commandes-bot", "text", "member_rw", "Utilise les commandes du bot ici (/rank, /daily…)."),
    ]),
    ("game", "🎮 HATCH A MUTANT", [
        ("game_chat", "🎮・discussion-jeu", "text", "member_rw", "Parle du jeu, échange des astuces."),
        ("game_share", "🐾・mutants-partagés", "text", "member_rw", "Montre tes plus beaux mutants !"),
        ("game_news", "🏆・annonces-jeu", "text", "member_ro", "Annonces automatiques envoyées par le jeu."),
        ("game_info", "📊・info-serveur", "text", "member_ro", "L'état du jeu en direct : ouvert, joueurs en ligne, serveurs..."),
        ("game_top", "🥇・classement", "text", "member_ro", "Les meilleurs joueurs du jeu, en direct."),
    ]),
    ("support", "🎫 SUPPORT", [
        ("tickets_panel", "🎫・ouvrir-un-ticket", "text", "member_ro", "Besoin d'aide ? Ouvre un ticket privé."),
    ]),
    ("voice", "🔊 VOCAL", [
        ("vc_general", "🔊 Général", "voice", "voice", ""),
        ("vc_game1", "🎮 Jeu 1", "voice", "voice", ""),
        ("vc_game2", "🎮 Jeu 2", "voice", "voice", ""),
        ("vc_afk", "💤 AFK", "voice", "voice", ""),
    ]),
    ("staff", "🛡️ STAFF", [
        ("staff_chat", "💬・staff", "text", "staff", "Discussion privée de l'équipe."),
        ("modlog", "📋・logs-modération", "text", "staff", "Sanctions et vérifications."),
        ("ticketlog", "🗂️・logs-tickets", "text", "staff", "Transcripts des tickets fermés."),
        ("botstaff", "🤖・bot-staff", "text", "staff", "Commandes d'administration du bot."),
    ]),
    ("tickets", "📂 TICKETS", []),  # les tickets ouverts atterrissent ici
]


def overwrites_for(kind: str, guild: discord.Guild, roles: dict) -> dict:
    everyone = guild.default_role
    member = roles["member"]
    staff = [roles[k] for k in STAFF_KEYS]
    PO = discord.PermissionOverwrite

    ow = {
        guild.me: PO(
            view_channel=True, send_messages=True, manage_channels=True, manage_messages=True,
            embed_links=True, attach_files=True, read_message_history=True, add_reactions=True,
            connect=True, speak=True,
        )
    }
    staff_chat = dict(view_channel=True, send_messages=True, read_message_history=True,
                      attach_files=True, embed_links=True)

    if kind == "gate_ro":
        ow[everyone] = PO(view_channel=True, send_messages=False, read_message_history=True,
                          add_reactions=False, create_public_threads=False)
        for r in staff:
            ow[r] = PO(**staff_chat)
    elif kind == "member_ro":
        ow[everyone] = PO(view_channel=False)
        ow[member] = PO(view_channel=True, send_messages=False, read_message_history=True,
                        add_reactions=True, create_public_threads=False)
        for r in staff:
            ow[r] = PO(**staff_chat)
    elif kind == "member_rw":
        ow[everyone] = PO(view_channel=False)
        ow[member] = PO(view_channel=True, send_messages=True, read_message_history=True,
                        attach_files=True, embed_links=True, add_reactions=True)
        for r in staff:
            ow[r] = PO(**staff_chat)
    elif kind == "voice":
        ow[everyone] = PO(view_channel=False)
        ow[member] = PO(view_channel=True, connect=True, speak=True, stream=True,
                        use_voice_activation=True)
        for r in staff:
            ow[r] = PO(view_channel=True, connect=True, speak=True, stream=True)
    else:  # staff
        ow[everyone] = PO(view_channel=False)
        for r in staff:
            ow[r] = PO(**staff_chat)
    return ow


class ConfirmView(discord.ui.View):
    def __init__(self, cog: "Setup", author_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Ce n'est pas ta commande.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Tout créer", emoji="🚀", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="⏳ Création en cours… ça prend environ une minute, ne touche à rien.",
            embed=None, view=None,
        )
        self.stop()
        guild = interaction.guild
        try:
            embed = await self.cog.build(guild)
        except discord.Forbidden:
            return await interaction.edit_original_response(
                content="❌ Il me manque des permissions. Redonne-moi le rôle **Administrateur** "
                        "(ou Gérer les rôles + Gérer les salons) et relance la commande.",
            )
        except discord.HTTPException as exc:
            log.exception("Erreur pendant le setup")
            return await interaction.edit_original_response(
                content=f"❌ Discord a refusé une opération : `{exc}`. Relance la commande : "
                        "ce qui est déjà créé sera réutilisé.",
            )
        except Exception:
            # Erreur inattendue : on prévient au lieu de laisser « Création en cours » affiché
            log.exception("Erreur inattendue pendant le setup")
            return await interaction.edit_original_response(
                content="❌ Une erreur inattendue est survenue. Relance `/setup-serveur` : "
                        "ce qui est déjà créé sera réutilisé.",
            )
        finally:
            self.cog.running.discard(guild.id)
        await interaction.edit_original_response(content=None, embed=embed)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog.running.discard(interaction.guild_id)
        self.stop()
        await interaction.response.edit_message(content="Annulé.", embed=None, view=None)

    async def on_timeout(self):
        self.cog.running.discard(self.guild_id)


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.running: set[int] = set()

    # --- Commande ---------------------------------------------------------
    @app_commands.command(
        name="setup-serveur",
        description="Crée automatiquement rôles, salons, tickets et vérification",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_server(self, interaction: discord.Interaction):
        guild = interaction.guild
        me = guild.me.guild_permissions
        if not (me.administrator or (me.manage_roles and me.manage_channels)):
            return await interaction.response.send_message(
                "Il me faut la permission **Administrateur** (ou Gérer les rôles + Gérer les salons).",
                ephemeral=True,
            )
        if guild.id in self.running:
            return await interaction.response.send_message(
                "Une création est déjà en cours.", ephemeral=True
            )

        embed = discord.Embed(
            title="🚀 Création de la base du serveur",
            description="Je vais créer (ou réutiliser s'ils existent déjà) :",
            color=config.COLOR_MAIN,
        )
        embed.add_field(
            name="Rôles",
            value="🛡️ Admin · 🔨 Modérateur · 🎧 Support · 💎 Booster · 👑 VIP · ✅ Membre · "
                  "📢 Annonces · 🎉 Événements · 🔔 Mises à jour",
            inline=False,
        )
        embed.add_field(
            name="Salons",
            value="🚪 Accueil (règles + vérification)\n📢 Informations\n💬 Communauté\n"
                  "🎮 Hatch a Mutant\n🎫 Support (tickets)\n🔊 Vocal\n🛡️ Staff (logs)",
            inline=False,
        )
        embed.add_field(
            name="Branchements",
            value="Tickets, vérification, logs de modération, bienvenue et annonces du jeu "
                  "sont reliés au bot automatiquement.",
            inline=False,
        )
        embed.add_field(
            name="⚠️ À savoir",
            value="Tes salons existants ne sont pas modifiés ni supprimés. Seuls les nouveaux "
                  "salons sont cachés aux non-vérifiés ; masque les anciens à la main si besoin.",
            inline=False,
        )
        self.running.add(guild.id)
        await interaction.response.send_message(
            embed=embed, view=ConfirmView(self, interaction.user.id, guild.id), ephemeral=True
        )

    # --- Helpers de création ---------------------------------------------
    async def ensure_role(self, guild, key, name, color, perms, hoist, mentionable):
        db = self.bot.db
        role_id = await db.get_int_setting(guild.id, f"setup_role_{key}")
        role = guild.get_role(role_id) if role_id else None
        if role is None:
            role = discord.utils.get(guild.roles, name=name)
        if role is not None:
            await db.set_setting(guild.id, f"setup_role_{key}", role.id)
            return role, False

        kwargs = dict(
            name=name, colour=discord.Colour(color), hoist=hoist, mentionable=mentionable,
            reason="Setup du serveur",
        )
        try:
            role = await guild.create_role(permissions=discord.Permissions(**perms), **kwargs)
        except discord.Forbidden:
            role = await guild.create_role(**kwargs)  # sans permissions spéciales
        await db.set_setting(guild.id, f"setup_role_{key}", role.id)
        return role, True

    async def order_roles(self, guild, roles):
        """Place les rôles juste sous celui du bot, dans le bon ordre hiérarchique."""
        order = [roles[k] for k, *_ in ROLE_SPECS]
        pos = guild.me.top_role.position - 1
        positions = {}
        for role in order:
            if pos < 1:
                break
            positions[role] = pos
            pos -= 1
        if positions:
            try:
                await guild.edit_role_positions(positions, reason="Setup du serveur")
            except discord.HTTPException:
                log.warning("Impossible de réordonner les rôles")

    async def ensure_category(self, guild, key, name, overwrites):
        db = self.bot.db
        cat_id = await db.get_int_setting(guild.id, f"setup_cat_{key}")
        cat = guild.get_channel(cat_id) if cat_id else None
        if isinstance(cat, discord.CategoryChannel):
            return cat, False
        cat = await guild.create_category(name, overwrites=overwrites, reason="Setup du serveur")
        await db.set_setting(guild.id, f"setup_cat_{key}", cat.id)
        return cat, True

    async def ensure_channel(self, guild, key, name, ctype, category, overwrites, topic):
        db = self.bot.db
        ch_id = await db.get_int_setting(guild.id, f"setup_ch_{key}")
        channel = guild.get_channel(ch_id) if ch_id else None
        if channel is not None:
            return channel, False
        if ctype == "voice":
            channel = await guild.create_voice_channel(
                name, category=category, overwrites=overwrites, reason="Setup du serveur"
            )
        else:
            channel = await guild.create_text_channel(
                name, category=category, overwrites=overwrites, topic=topic or None,
                reason="Setup du serveur",
            )
        await db.set_setting(guild.id, f"setup_ch_{key}", channel.id)
        return channel, True

    async def post_once(self, guild, channel, key, **send_kwargs):
        """Poste un panneau une seule fois (relancer /setup-serveur ne le duplique pas)."""
        db = self.bot.db
        message_id = await db.get_int_setting(guild.id, f"setup_panel_{key}")
        if message_id:
            try:
                await channel.fetch_message(message_id)
                return
            except discord.HTTPException:
                pass
        message = await channel.send(**send_kwargs)
        await db.set_setting(guild.id, f"setup_panel_{key}", message.id)

    # --- Construction complète -------------------------------------------
    async def build(self, guild: discord.Guild) -> discord.Embed:
        db = self.bot.db
        created_roles, created_channels = [], 0

        roles = {}
        for key, name, color, perms, hoist, mentionable in ROLE_SPECS:
            role, new = await self.ensure_role(guild, key, name, color, perms, hoist, mentionable)
            roles[key] = role
            if new:
                created_roles.append(name)
        await self.order_roles(guild, roles)

        channels = {}
        for cat_key, cat_name, items in LAYOUT:
            cat_kind = CATEGORY_KIND[cat_key]
            category, _ = await self.ensure_category(
                guild, cat_key, cat_name, overwrites_for(cat_kind, guild, roles)
            )
            channels[cat_key] = category
            for key, name, ctype, kind, topic in items:
                channel, new = await self.ensure_channel(
                    guild, key, name, ctype, category, overwrites_for(kind, guild, roles), topic
                )
                channels[key] = channel
                created_channels += int(new)

        # Mot de passe de vérification et réglages par défaut
        word = await db.get_setting(guild.id, "verify_word")
        if not word:
            word = generate_word()
            await db.set_setting(guild.id, "verify_word", word)
        if await db.get_setting(guild.id, "verify_min_age_days") is None:
            await db.set_setting(guild.id, "verify_min_age_days", 1)

        # Branchements du bot
        gid = guild.id
        await db.set_setting(gid, "verified_role", roles["member"].id)
        await db.set_setting(gid, "log_channel", channels["modlog"].id)
        await db.set_setting(gid, "ticket_category", channels["tickets"].id)
        await db.set_setting(gid, "ticket_support_role", roles["support"].id)
        await db.set_setting(gid, "ticket_log", channels["ticketlog"].id)
        await db.set_setting(gid, "welcome_channel", channels["welcome"].id)
        await db.set_setting(gid, "roblox_channel", channels["game_news"].id)
        # Panneaux en direct et événements : on ne remplace pas un salon que tu as déjà choisi toi-même
        for key, channel_key in (
            ("live_info_channel", "game_info"), ("live_top_channel", "game_top"),
            ("event_channel", "events"),
        ):
            if not await db.get_int_setting(gid, key):
                await db.set_setting(gid, key, channels[channel_key].id)

        # Panneaux
        await self.post_once(guild, channels["rules"], "rules", embed=build_rules_embed(word))
        await self.post_once(
            guild, channels["verify"], "verify",
            embed=build_verify_embed(), view=VerifyView(self.bot),
        )
        await self.post_once(
            guild, channels["tickets_panel"], "tickets",
            embed=discord.Embed(
                title="Besoin d'aide ?",
                description="Choisis le type de demande dans le menu ci-dessous : un salon privé "
                            "s'ouvrira avec l'équipe.",
                color=config.COLOR_MAIN,
            ),
            view=OpenTicketView(self.bot),
        )
        role_view = discord.ui.View(timeout=None)
        for key in ("ping_news", "ping_events", "ping_updates"):
            role_view.add_item(RoleButton(roles[key].id, roles[key].name))
        await self.post_once(
            guild, channels["roles"], "roles",
            embed=discord.Embed(
                title="🎭 Choisis tes notifications",
                description="Clique sur un bouton pour activer ou désactiver le rôle.",
                color=config.COLOR_MAIN,
            ),
            view=role_view,
        )
        await self.post_once(
            guild, channels["botstaff"], "staffhelp",
            embed=discord.Embed(
                title="🤖 Aide-mémoire du staff",
                description=(
                    "`/warn` `/timeout` `/kick` `/ban` `/clear` — modération\n"
                    "`/verifier @membre` — valider quelqu'un à la main\n"
                    "`/verification mot` — voir/changer le mot de passe\n"
                    "`/ticket-ajouter` `/ticket-retirer` — dans un ticket\n"
                    "`/roblox salon` — changer le salon des annonces du jeu"
                ),
                color=config.COLOR_MAIN,
            ),
        )

        embed = discord.Embed(
            title="✅ Serveur prêt !", color=config.COLOR_OK,
            description=f"{len(created_roles)} rôle(s) et {created_channels} salon(s) créés "
                        "(le reste existait déjà).",
        )
        embed.add_field(
            name="🔑 Mot de passe de vérification", value=f"`{word}` (écrit dans {channels['rules'].mention})",
            inline=False,
        )
        embed.add_field(
            name="À faire maintenant",
            value=(
                "1. Paramètres du serveur > Rôles : mets **le rôle du bot tout en haut**.\n"
                "2. Donne-toi (et à ton équipe) les rôles 🛡️ Admin / 🔨 Modérateur / 🎧 Support.\n"
                "3. Teste la vérification avec un second compte.\n"
                "4. Masque tes anciens salons aux non-vérifiés si tu en as."
            ),
            inline=False,
        )
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
