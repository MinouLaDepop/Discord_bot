import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

import config

log = logging.getLogger("economy")

XP_COOLDOWN = 60          # secondes entre deux gains d'XP
DAILY_COOLDOWN = 24 * 3600
WORK_COOLDOWN = 3600
DAILY_REWARD = 200
COIN = "🪙"


def xp_needed(level: int) -> int:
    """XP nécessaire pour passer du niveau `level` au suivant."""
    return 5 * level**2 + 50 * level + 100


def progress_bar(value: int, total: int, size: int = 12) -> str:
    filled = int(size * value / total) if total else 0
    return "█" * filled + "░" * (size - filled)


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (serveur, membre) -> heure du dernier gain d'XP. Évite de lire la base à chaque message.
        self._last_xp: dict[tuple[int, int], float] = {}

    def _cleanup_cache(self, now: float):
        if len(self._last_xp) > 5000:
            self._last_xp = {k: t for k, t in self._last_xp.items() if now - t < XP_COOLDOWN}

    # --- Gain d'XP en discutant ------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        now = time.time()
        gid, uid = message.guild.id, message.author.id
        if now - self._last_xp.get((gid, uid), 0) < XP_COOLDOWN:
            return  # cooldown en mémoire : aucun accès à la base
        self._last_xp[(gid, uid)] = now
        self._cleanup_cache(now)
        try:
            await self.give_message_xp(message, now)
        except Exception:
            log.exception("Impossible de donner l'XP du message")

    async def give_message_xp(self, message: discord.Message, now: float):
        db = self.bot.db
        gid, uid = message.guild.id, message.author.id
        row = await db.ensure_user(gid, uid)
        if now - row["last_msg"] < XP_COOLDOWN:
            return

        xp = row["xp"] + random.randint(15, 25)
        level = row["level"]
        leveled = False
        while xp >= xp_needed(level):
            xp -= xp_needed(level)
            level += 1
            leveled = True

        await db.execute(
            "UPDATE users SET xp=?, level=?, coins=coins+?, last_msg=? "
            "WHERE guild_id=? AND user_id=?",
            (xp, level, random.randint(1, 5), now, gid, uid),
        )

        if leveled:
            await self.apply_level_roles(message.author, level)
            try:
                await message.channel.send(
                    f"🎉 {message.author.mention} passe **niveau {level}** !",
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException:
                pass

    async def apply_level_roles(self, member: discord.Member, level: int):
        rows = await self.bot.db.fetchall(
            "SELECT role_id FROM level_roles WHERE guild_id=? AND level<=?",
            (member.guild.id, level),
        )
        roles = [r for row in rows if (r := member.guild.get_role(row["role_id"])) and r not in member.roles]
        if roles:
            try:
                await member.add_roles(*roles, reason=f"Niveau {level} atteint")
            except discord.HTTPException:
                pass

    # --- Niveaux ---------------------------------------------------------
    @app_commands.command(name="rank", description="Affiche ton niveau et ton XP")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, membre: discord.Member | None = None):
        membre = membre or interaction.user
        db = self.bot.db
        row = await db.ensure_user(interaction.guild_id, membre.id)
        pos = await db.fetchone(
            "SELECT COUNT(*) + 1 AS pos FROM users WHERE guild_id=? "
            "AND (level > ? OR (level = ? AND xp > ?))",
            (interaction.guild_id, row["level"], row["level"], row["xp"]),
        )
        need = xp_needed(row["level"])
        embed = discord.Embed(title=f"Profil de {membre.display_name}", color=config.COLOR_MAIN)
        embed.set_thumbnail(url=membre.display_avatar.url)
        embed.add_field(name="Niveau", value=str(row["level"]))
        embed.add_field(name="Classement", value=f"#{pos['pos']}")
        embed.add_field(name="Pièces", value=f"{row['coins']} {COIN}")
        embed.add_field(
            name="Progression",
            value=f"`{progress_bar(row['xp'], need)}` {row['xp']}/{need} XP",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="classement", description="Top 10 des membres")
    @app_commands.guild_only()
    @app_commands.choices(
        tri=[
            app_commands.Choice(name="Niveau", value="level"),
            app_commands.Choice(name="Pièces", value="coins"),
        ]
    )
    async def leaderboard(
        self, interaction: discord.Interaction, tri: app_commands.Choice[str] | None = None
    ):
        mode = tri.value if tri else "level"
        order = "level DESC, xp DESC" if mode == "level" else "coins DESC"
        rows = await self.bot.db.fetchall(
            f"SELECT * FROM users WHERE guild_id=? ORDER BY {order} LIMIT 10",  # noqa: S608 (valeurs fixes)
            (interaction.guild_id,),
        )
        if not rows:
            return await interaction.response.send_message("Personne dans le classement pour l'instant.")
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(rows):
            rank = medals[i] if i < 3 else f"`{i + 1}.`"
            value = f"niveau {r['level']}" if mode == "level" else f"{r['coins']} {COIN}"
            lines.append(f"{rank} <@{r['user_id']}> — {value}")
        embed = discord.Embed(
            title="🏆 Classement " + ("par niveau" if mode == "level" else "par pièces"),
            description="\n".join(lines),
            color=config.COLOR_MAIN,
        )
        await interaction.response.send_message(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(name="niveau-role", description="Donne un rôle automatiquement à un niveau")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def level_role(
        self, interaction: discord.Interaction, niveau: app_commands.Range[int, 1, 500],
        role: discord.Role,
    ):
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                "Ce rôle est au-dessus du mien.", ephemeral=True
            )
        await self.bot.db.execute(
            "INSERT INTO level_roles(guild_id, level, role_id) VALUES(?,?,?) "
            "ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id",
            (interaction.guild_id, niveau, role.id),
        )
        await interaction.response.send_message(
            f"✅ {role.mention} sera donné au niveau {niveau}.", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # --- Pièces ----------------------------------------------------------
    @app_commands.command(name="solde", description="Affiche ton solde de pièces")
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction, membre: discord.Member | None = None):
        membre = membre or interaction.user
        row = await self.bot.db.ensure_user(interaction.guild_id, membre.id)
        await interaction.response.send_message(
            f"{COIN} **{membre.display_name}** possède **{row['coins']}** pièces."
        )

    @app_commands.command(name="daily", description="Récupère ta récompense quotidienne")
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction):
        db = self.bot.db
        row = await db.ensure_user(interaction.guild_id, interaction.user.id)
        now = time.time()
        ready = row["last_daily"] + DAILY_COOLDOWN
        if now < ready:
            return await interaction.response.send_message(
                f"⏳ Reviens <t:{int(ready)}:R> pour ta prochaine récompense.", ephemeral=True
            )
        if not await db.claim_cooldown(
            interaction.guild_id, interaction.user.id, "last_daily", DAILY_COOLDOWN, now
        ):
            return await interaction.response.send_message(
                "⏳ Ta récompense quotidienne est déjà récupérée.", ephemeral=True
            )
        balance = await db.add_coins(interaction.guild_id, interaction.user.id, DAILY_REWARD)
        await interaction.response.send_message(
            f"🎁 Tu reçois **{DAILY_REWARD}** {COIN} ! Solde : **{balance}** {COIN}"
        )

    @app_commands.command(name="travail", description="Travaille pour gagner des pièces (1 fois par heure)")
    @app_commands.guild_only()
    async def work(self, interaction: discord.Interaction):
        db = self.bot.db
        row = await db.ensure_user(interaction.guild_id, interaction.user.id)
        now = time.time()
        ready = row["last_work"] + WORK_COOLDOWN
        if now < ready:
            return await interaction.response.send_message(
                f"⏳ Tu es fatigué. Reviens <t:{int(ready)}:R>.", ephemeral=True
            )
        gain = random.randint(50, 150)
        jobs = [
            "nourri des mutants au zoo", "nettoyé les enclos", "soigné un mutant malade",
            "couvé des œufs mystérieux", "guidé des visiteurs",
        ]
        if not await db.claim_cooldown(
            interaction.guild_id, interaction.user.id, "last_work", WORK_COOLDOWN, now
        ):
            return await interaction.response.send_message(
                "⏳ Tu es déjà au travail, reviens un peu plus tard.", ephemeral=True
            )
        balance = await db.add_coins(interaction.guild_id, interaction.user.id, gain)
        await interaction.response.send_message(
            f"🛠️ Tu as {random.choice(jobs)} et gagné **{gain}** {COIN} ! Solde : **{balance}** {COIN}"
        )

    @app_commands.command(name="payer", description="Donne des pièces à un autre membre")
    @app_commands.guild_only()
    async def pay(
        self, interaction: discord.Interaction, membre: discord.Member,
        montant: app_commands.Range[int, 1, 1_000_000],
    ):
        if membre.bot or membre.id == interaction.user.id:
            return await interaction.response.send_message("Destinataire invalide.", ephemeral=True)
        db = self.bot.db
        if await db.add_coins(interaction.guild_id, interaction.user.id, -montant) is None:
            return await interaction.response.send_message("Tu n'as pas assez de pièces.", ephemeral=True)
        await db.add_coins(interaction.guild_id, membre.id, montant)
        await interaction.response.send_message(
            f"💸 {interaction.user.mention} a donné **{montant}** {COIN} à {membre.mention}."
        )

    @app_commands.command(name="coins-donner", description="(Admin) Ajoute ou retire des pièces")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def give_coins(
        self, interaction: discord.Interaction, membre: discord.Member,
        montant: app_commands.Range[int, -1_000_000, 1_000_000],
    ):
        balance = await self.bot.db.add_coins(interaction.guild_id, membre.id, montant)
        if balance is None:
            return await interaction.response.send_message(
                "Le solde deviendrait négatif.", ephemeral=True
            )
        await interaction.response.send_message(
            f"✅ Nouveau solde de {membre.mention} : **{balance}** {COIN}", ephemeral=True
        )

    # --- Boutique --------------------------------------------------------
    async def item_autocomplete(self, interaction: discord.Interaction, current: str):
        rows = await self.bot.db.fetchall(
            "SELECT name, price FROM shop WHERE guild_id=? AND name LIKE ? LIMIT 25",
            (interaction.guild_id, f"%{current}%"),
        )
        return [
            app_commands.Choice(name=f"{r['name']} — {r['price']} pièces"[:100], value=r["name"])
            for r in rows
        ]

    @app_commands.command(name="boutique", description="Affiche les objets en vente")
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall(
            "SELECT * FROM shop WHERE guild_id=? ORDER BY price", (interaction.guild_id,)
        )
        if not rows:
            return await interaction.response.send_message("La boutique est vide pour l'instant.")
        embed = discord.Embed(title="🛒 Boutique", color=config.COLOR_MAIN)
        for r in rows[:25]:
            tag = " (rôle)" if r["role_id"] else ""
            embed.add_field(
                name=f"{r['name']}{tag} — {r['price']} {COIN}",
                value=r["description"] or "—",
                inline=False,
            )
        embed.set_footer(text="Achète avec /acheter")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="boutique-ajouter", description="(Admin) Ajoute un objet à la boutique")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def shop_add(
        self, interaction: discord.Interaction, nom: str,
        prix: app_commands.Range[int, 1, 10_000_000],
        description: str = "",
        role: discord.Role | None = None,
    ):
        if role and role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("Ce rôle est au-dessus du mien.", ephemeral=True)
        await self.bot.db.execute(
            "INSERT INTO shop(guild_id, name, price, description, role_id) VALUES(?,?,?,?,?)",
            (interaction.guild_id, nom[:60], prix, description[:200], role.id if role else None),
        )
        await interaction.response.send_message(f"✅ **{nom}** ajouté à la boutique.", ephemeral=True)

    @app_commands.command(name="boutique-retirer", description="(Admin) Retire un objet de la boutique")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.autocomplete(nom=item_autocomplete)
    async def shop_remove(self, interaction: discord.Interaction, nom: str):
        cur = await self.bot.db.execute(
            "DELETE FROM shop WHERE guild_id=? AND name=? COLLATE NOCASE",
            (interaction.guild_id, nom),
        )
        msg = "✅ Objet retiré." if cur.rowcount else "Objet introuvable."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="acheter", description="Achète un objet de la boutique")
    @app_commands.guild_only()
    @app_commands.autocomplete(objet=item_autocomplete)
    async def buy(self, interaction: discord.Interaction, objet: str):
        db = self.bot.db
        gid, uid = interaction.guild_id, interaction.user.id
        item = await db.fetchone(
            "SELECT * FROM shop WHERE guild_id=? AND name=? COLLATE NOCASE", (gid, objet)
        )
        if not item:
            return await interaction.response.send_message("Objet introuvable.", ephemeral=True)

        role = interaction.guild.get_role(item["role_id"]) if item["role_id"] else None
        if role and role in interaction.user.roles:
            return await interaction.response.send_message("Tu possèdes déjà ce rôle.", ephemeral=True)

        if await db.add_coins(gid, uid, -item["price"]) is None:
            return await interaction.response.send_message(
                f"Il te manque des pièces ({item['price']} {COIN} requis).", ephemeral=True
            )

        if role:
            try:
                await interaction.user.add_roles(role, reason="Achat en boutique")
            except discord.HTTPException:
                await db.add_coins(gid, uid, item["price"])  # remboursement
                return await interaction.response.send_message(
                    "Impossible de donner ce rôle, tu as été remboursé.", ephemeral=True
                )
        else:
            await db.execute(
                "INSERT INTO inventory(guild_id, user_id, item_id, qty) VALUES(?,?,?,1) "
                "ON CONFLICT(guild_id, user_id, item_id) DO UPDATE SET qty=qty+1",
                (gid, uid, item["id"]),
            )
        await interaction.response.send_message(
            f"✅ Tu as acheté **{item['name']}** pour {item['price']} {COIN}."
        )

    @app_commands.command(name="inventaire", description="Affiche tes objets")
    @app_commands.guild_only()
    async def inventory(self, interaction: discord.Interaction):
        rows = await self.bot.db.fetchall(
            "SELECT s.name, i.qty FROM inventory i JOIN shop s ON s.id = i.item_id "
            "WHERE i.guild_id=? AND i.user_id=? ORDER BY s.name",
            (interaction.guild_id, interaction.user.id),
        )
        if not rows:
            return await interaction.response.send_message("Ton inventaire est vide.", ephemeral=True)
        text = "\n".join(f"• **{r['name']}** ×{r['qty']}" for r in rows)
        await interaction.response.send_message(
            embed=discord.Embed(title="🎒 Inventaire", description=text, color=config.COLOR_MAIN),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
