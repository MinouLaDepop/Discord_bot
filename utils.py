"""Petits outils partagés : réponses sans risque et messages d'erreur clairs."""
import asyncio
import logging

import aiohttp
import discord

log = logging.getLogger("utils")

# Erreurs réseau à attendre quand on appelle une API externe (Roblox...)
NETWORK_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)


def friendly_error(error: BaseException) -> str:
    """Transforme une exception en message compréhensible pour le membre."""
    if isinstance(error, discord.Forbidden):
        return "Discord a refusé cette action : il me manque une permission ou mon rôle est trop bas."
    if isinstance(error, discord.NotFound):
        return "Je ne trouve plus cet élément (supprimé entre-temps ?)."
    if isinstance(error, discord.HTTPException):
        return "Discord a rencontré un problème. Réessaie dans un instant."
    if isinstance(error, NETWORK_ERRORS):
        return "Le service externe ne répond pas. Réessaie dans un instant."
    return "Une erreur est survenue. Réessaie dans un instant."


async def safe_reply(interaction: discord.Interaction, message: str, *, ephemeral: bool = True):
    """Répond à une interaction sans jamais lever d'erreur
    (interaction expirée, déjà répondue, message supprimé...)."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(message, ephemeral=ephemeral)
    except discord.HTTPException:
        pass


async def report_ui_error(interaction: discord.Interaction, error: BaseException, where: str):
    """À appeler depuis `on_error` d'un bouton, menu ou formulaire."""
    log.error("Erreur dans %s", where, exc_info=error)
    await safe_reply(interaction, friendly_error(getattr(error, "original", error)))
