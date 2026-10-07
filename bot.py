import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from db import Database

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("bot")

COGS = [
    "cogs.moderation",
    "cogs.welcome",
    "cogs.tickets",
    "cogs.economy",
    "cogs.roblox",
    "cogs.verification",
    "cogs.setup",
]


class MutantBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # arrivées de membres, rôles
        intents.message_content = True  # transcripts de tickets
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )
        self.db = Database(config.DB_PATH)

    async def setup_hook(self):
        await self.db.connect()
        for ext in COGS:
            await self.load_extension(ext)
            log.info("Module chargé : %s", ext)

        self.tree.on_error = self.on_tree_error

        if config.GUILD_ID:
            guild = discord.Object(id=config.GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        log.info("Commandes synchronisées.")

    async def on_ready(self):
        log.info("Connecté en tant que %s (%s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name=config.BOT_STATUS))

    async def on_tree_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.MissingPermissions):
            msg = "Tu n'as pas la permission d'utiliser cette commande."
        elif isinstance(error, app_commands.BotMissingPermissions):
            msg = "Il me manque des permissions pour faire ça."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"Doucement ! Réessaie dans {error.retry_after:.0f}s."
        elif isinstance(error, app_commands.NoPrivateMessage):
            msg = "Cette commande ne fonctionne que sur un serveur."
        else:
            log.exception("Erreur de commande", exc_info=error)
            msg = "Une erreur est survenue. Réessaie dans un instant."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    async def close(self):
        await self.db.close()
        await super().close()


def main():
    if not config.TOKEN:
        raise SystemExit("DISCORD_TOKEN manquant : remplis le fichier .env")
    MutantBot().run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
