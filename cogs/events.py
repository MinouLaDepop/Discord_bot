"""Événements avec compte à rebours.

/event creer publie un message dans le salon des événements avec un décompte
(« Début dans 2 j 03 h 15 min ») que le bot met à jour tout seul. Au moment du départ,
le rôle 🎉 Événements est mentionné, puis le message passe en « EN COURS » et enfin « Terminé ».
S'il n'y a aucun événement prévu, rien n'est affiché.
"""
import datetime
import logging
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config

log = logging.getLogger("events")

DEFAULT_TZ = "Europe/Paris"
MAX_ACTIVE_EVENTS = 10
START_PING_WINDOW = 600   # on ne mentionne le départ que si le bot l'a vu dans les 10 minutes
TICK_SECONDS = 30
GREY = 0x95A5A6


# --- Petites fonctions pures (testées par smoke_test.py) ---------------------
def fmt_countdown(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days} j {hours:02d} h {minutes:02d} min"
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min"
    return "moins d'une minute"


def parse_when(date_text: str, time_text: str, tz: datetime.tzinfo) -> datetime.datetime | None:
    """« 25/12/2026 » + « 20h30 » (ou « 20:30 ») -> date et heure dans le fuseau donné."""
    clock = time_text.strip().lower().replace("h", ":")
    if clock.endswith(":"):
        clock += "00"
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%d-%m-%Y %H:%M"):
        try:
            naive = datetime.datetime.strptime(f"{date_text.strip()} {clock}", fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=tz)
    return None


def build_embed(ev, status: str, now: float) -> discord.Embed:
    ts = int(ev["starts_at"])
    description = ev["description"] or None
    if status == "scheduled":
        embed = discord.Embed(title=f"🎉 {ev['title']}", description=description, color=config.COLOR_MAIN)
        embed.add_field(name="📅 Date", value=f"<t:{ts}:F>", inline=False)
        embed.add_field(
            name="⏳ Début dans",
            value=f"**{fmt_countdown(ts - now)}**\n(<t:{ts}:R>)",
            inline=False,
        )
        embed.set_footer(text="Le compte à rebours se met à jour tout seul")
    elif status == "started":
        embed = discord.Embed(
            title=f"🔴 EN COURS — {ev['title']}", description=description, color=config.COLOR_OK
        )
        embed.add_field(name="🎮 C'est parti !", value=f"Commencé <t:{ts}:R>", inline=False)
    elif status == "done":
        embed = discord.Embed(title=f"✅ Terminé — {ev['title']}", description=description, color=GREY)
    else:  # cancelled
        embed = discord.Embed(
            title=f"❌ Annulé — {ev['title']}", description=description, color=config.COLOR_ERR
        )
    return embed


class Events(commands.Cog):
    evt = app_commands.Group(
        name="event",
        description="Événements avec compte à rebours",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_render: dict[int, tuple] = {}   # id événement -> ce qui est affiché

    async def cog_load(self):
        self.tick.start()

    async def cog_unload(self):
        self.tick.cancel()

    # --- Utilitaires -----------------------------------------------------
    async def event_channel(self, guild: discord.Guild):
        db = self.bot.db
        channel_id = (
            await db.get_int_setting(guild.id, "event_channel")
            or await db.get_int_setting(guild.id, "setup_ch_events")
        )
        return guild.get_channel(channel_id) if channel_id else None

    async def render(self, guild: discord.Guild, ev, status: str, now: float):
        """Met à jour le message de l'événement (seulement si ce qui est affiché a changé)."""
        signature = (status, fmt_countdown(ev["starts_at"] - now) if status == "scheduled" else None)
        if self._last_render.get(ev["id"]) == signature:
            return
        channel = guild.get_channel(ev["channel_id"]) if ev["channel_id"] else None
        if channel is None:
            return
        embed = build_embed(ev, status, now)
        try:
            if ev["message_id"]:
                try:
                    await channel.get_partial_message(ev["message_id"]).edit(embed=embed)
                    self._last_render[ev["id"]] = signature
                    return
                except discord.NotFound:
                    if status in ("done", "cancelled"):
                        self._last_render[ev["id"]] = signature
                        return  # message supprimé et événement fini : rien à republier
            message = await channel.send(embed=embed)
            await self.bot.db.execute("UPDATE events SET message_id=? WHERE id=?", (message.id, ev["id"]))
            self._last_render[ev["id"]] = signature
        except discord.HTTPException:
            log.warning("Impossible de mettre à jour l'événement #%s dans %s", ev["id"], channel)

    # --- Boucle : compte à rebours, départ, fin --------------------------
    @tasks.loop(seconds=TICK_SECONDS)
    async def tick(self):
        try:
            rows = await self.bot.db.fetchall(
                "SELECT * FROM events WHERE status IN ('scheduled','started')"
            )
            now = time.time()
            for ev in rows:
                guild = self.bot.get_guild(ev["guild_id"])
                if guild is None:
                    continue
                try:
                    await self.process(guild, ev, now)
                except Exception:
                    log.exception("Erreur sur l'événement #%s", ev["id"])
        except Exception:
            log.exception("Erreur dans la boucle des événements")

    @tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()

    async def process(self, guild: discord.Guild, ev, now: float):
        db = self.bot.db
        status = ev["status"]
        end = ev["starts_at"] + ev["duration_min"] * 60

        if status == "scheduled" and now >= ev["starts_at"]:
            # Le « WHERE status='scheduled' » garantit que le départ n'est annoncé qu'une fois
            cur = await db.execute(
                "UPDATE events SET status='started' WHERE id=? AND status='scheduled'", (ev["id"],)
            )
            status = "started"
            if cur.rowcount and ev["ping"] and now - ev["starts_at"] <= START_PING_WINDOW:
                await self.announce_start(guild, ev)

        if status == "started" and now >= end:
            await db.execute("UPDATE events SET status='done' WHERE id=?", (ev["id"],))
            status = "done"

        await self.render(guild, ev, status, now)

    async def announce_start(self, guild: discord.Guild, ev):
        channel = guild.get_channel(ev["channel_id"]) if ev["channel_id"] else None
        if channel is None:
            return
        role_id = await self.bot.db.get_int_setting(guild.id, "setup_role_ping_events")
        role = guild.get_role(role_id) if role_id else None
        text = f"🎉 **{ev['title']}** commence maintenant !"
        if role:
            text = f"{role.mention} {text}"
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions(roles=[role] if role else False)
            )
        except discord.HTTPException:
            log.warning("Impossible d'annoncer le départ de l'événement #%s", ev["id"])

    # --- Commandes -------------------------------------------------------
    @evt.command(name="creer", description="Crée un événement avec compte à rebours")
    @app_commands.describe(
        titre="Nom de l'événement",
        date="Date au format JJ/MM/AAAA (ex : 25/12/2026)",
        heure="Heure de départ, heure de Paris (ex : 20h30)",
        description="Détails de l'événement (récompenses, règles...)",
        duree_minutes="Durée de l'événement en minutes (60 par défaut)",
        ping="Mentionner le rôle 🎉 Événements au départ",
        salon="Salon où publier (sinon le salon des événements)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def create(
        self,
        interaction: discord.Interaction,
        titre: app_commands.Range[str, 1, 100],
        date: str,
        heure: str,
        description: app_commands.Range[str, 0, 1000] = "",
        duree_minutes: app_commands.Range[int, 5, 1440] = 60,
        ping: bool = True,
        salon: discord.TextChannel | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        guild, db = interaction.guild, self.bot.db

        tz_name = await db.get_setting(guild.id, "timezone", DEFAULT_TZ)
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            return await interaction.followup.send(
                f"❌ Fuseau horaire « {tz_name} » introuvable sur l'hébergement "
                "(installe le paquet `tzdata`).", ephemeral=True,
            )
        when = parse_when(date, heure, tz)
        if when is None:
            return await interaction.followup.send(
                "❌ Date ou heure non reconnue. Exemple : date `25/12/2026`, heure `20h30`.",
                ephemeral=True,
            )
        if when.timestamp() <= time.time() + 30:
            return await interaction.followup.send("❌ Cette date est déjà passée.", ephemeral=True)

        channel = salon or await self.event_channel(guild)
        if channel is None:
            return await interaction.followup.send(
                "❌ Aucun salon d'événements. Choisis-en un avec `/event salon`.", ephemeral=True
            )
        active = await db.fetchone(
            "SELECT COUNT(*) AS n FROM events WHERE guild_id=? AND status IN ('scheduled','started')",
            (guild.id,),
        )
        if active["n"] >= MAX_ACTIVE_EVENTS:
            return await interaction.followup.send(
                f"❌ Déjà {MAX_ACTIVE_EVENTS} événements en cours ou prévus : annule-en un d'abord.",
                ephemeral=True,
            )

        cur = await db.execute(
            "INSERT INTO events(guild_id, title, description, starts_at, duration_min, channel_id, "
            "ping, created_by) VALUES(?,?,?,?,?,?,?,?)",
            (guild.id, titre, description or None, when.timestamp(), duree_minutes, channel.id,
             int(ping), interaction.user.id),
        )
        event_id = cur.lastrowid
        ev = await db.fetchone("SELECT * FROM events WHERE id=?", (event_id,))
        await self.render(guild, ev, "scheduled", time.time())
        await interaction.followup.send(
            f"✅ Événement **#{event_id}** créé dans {channel.mention} pour <t:{int(when.timestamp())}:F>. "
            f"Le compte à rebours se met à jour tout seul. (`/event annuler {event_id}` pour l'annuler)",
            ephemeral=True,
        )

    @evt.command(name="liste", description="Affiche les événements prévus")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_events(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall(
            "SELECT * FROM events WHERE guild_id=? AND status IN ('scheduled','started') "
            "ORDER BY starts_at",
            (interaction.guild_id,),
        )
        if not rows:
            return await interaction.response.send_message("Aucun événement prévu.", ephemeral=True)
        lines = [
            f"**#{r['id']}** · {r['title']} — <t:{int(r['starts_at'])}:R>"
            + (" (en cours)" if r["status"] == "started" else "")
            for r in rows
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @evt.command(name="annuler", description="Annule un événement")
    @app_commands.describe(numero="Le numéro affiché par /event liste")
    @app_commands.checks.has_permissions(administrator=True)
    async def cancel(self, interaction: discord.Interaction, numero: int):
        await interaction.response.defer(ephemeral=True)
        db = self.bot.db
        cur = await db.execute(
            "UPDATE events SET status='cancelled' WHERE id=? AND guild_id=? "
            "AND status IN ('scheduled','started')",
            (numero, interaction.guild_id),
        )
        if not cur.rowcount:
            return await interaction.followup.send("Événement introuvable (ou déjà terminé).", ephemeral=True)
        ev = await db.fetchone("SELECT * FROM events WHERE id=?", (numero,))
        await self.render(interaction.guild, ev, "cancelled", time.time())
        await interaction.followup.send(f"✅ Événement #{numero} annulé.", ephemeral=True)

    @evt.command(name="salon", description="Choisit le salon où publier les événements")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_channel(self, interaction: discord.Interaction, salon: discord.TextChannel):
        await self.bot.db.set_setting(interaction.guild_id, "event_channel", salon.id)
        await interaction.response.send_message(
            f"✅ Les événements seront publiés dans {salon.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
