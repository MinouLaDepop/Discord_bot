import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from db import Database
from utils import friendly_error, safe_reply

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
    "cogs.perks",
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
            # Le bot ne mentionne jamais @everyone / @here par accident
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.db = Database(config.DB_PATH)
        self.failed_cogs: list[str] = []

    async def load_cogs(self) -> list[str]:
        """Charge chaque module séparément : si l'un plante, les autres restent en ligne."""
        failed = []
        for ext in COGS:
            try:
                await self.load_extension(ext)
                log.info("Module chargé : %s", ext)
            except Exception:
                log.exception("Module %s impossible à charger (le bot continue sans lui)", ext)
                failed.append(ext)
        self.failed_cogs = failed
        return failed

    async def setup_hook(self):
        await self.db.connect()
        await self.load_cogs()

        self.tree.on_error = self.on_tree_error

        try:
            if config.GUILD_ID:
                guild = discord.Object(id=config.GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
            log.info("Commandes synchronisées.")
        except discord.HTTPException:
            # Un échec de synchro ne doit pas empêcher le bot de démarrer :
            # les commandes déjà enregistrées côté Discord restent utilisables.
            log.exception(
                "Synchronisation des commandes impossible. Vérifie GUILD_ID et que le bot a été "
                "invité avec le scope 'applications.commands'."
            )

    async def on_ready(self):
        log.info("Connecté en tant que %s (%s)", self.user, self.user.id)
        try:
            await self.change_presence(activity=discord.Game(name=config.BOT_STATUS))
        except Exception:
            log.exception("Impossible de changer le statut")

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
        elif isinstance(error, app_commands.CheckFailure):
            msg = "Tu ne peux pas utiliser cette commande ici."
        else:
            name = interaction.command.qualified_name if interaction.command else "?"
            log.error("Erreur dans la commande /%s", name, exc_info=error)
            msg = friendly_error(getattr(error, "original", error))
        await safe_reply(interaction, msg)

    async def close(self):
        await super().close()
        await self.db.close()


def main():
    if not config.TOKEN:
        raise SystemExit("DISCORD_TOKEN manquant : remplis le fichier .env")
    try:
        MutantBot().run(config.TOKEN, log_handler=None)
    except discord.LoginFailure:
        raise SystemExit("Token Discord refusé : régénère-le dans le portail développeur.")
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Active « Server Members Intent » et « Message Content Intent » dans le portail "
            "développeur Discord (onglet Bot)."
        )


if __name__ == "__main__":
    main()
