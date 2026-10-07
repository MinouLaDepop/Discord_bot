"""Test de démarrage du bot, sans se connecter à Discord.

Vérifie que tous les modules se chargent, que les commandes sont bien enregistrées
et que la base de données se comporte comme prévu.
Lancer :  python smoke_test.py   (GitHub le lance aussi à chaque push)
"""
import asyncio
import os
import sys
import tempfile

# Base temporaire et API Roblox désactivée : le test ne touche à rien de réel
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["API_KEY"] = ""
os.environ["DISCORD_TOKEN"] = ""

EXPECTED_COMMANDS = {
    "setup-serveur", "verifier", "kick", "ban", "warn", "daily", "travail", "rank",
    "ticket-panel", "panneau-roles", "roblox", "accueil", "verification",
}


def check(condition: bool, message: str):
    if not condition:
        print(f"❌ {message}")
        sys.exit(1)
    print(f"✅ {message}")


async def main():
    from bot import COGS, MutantBot

    bot = MutantBot()
    await bot.db.connect()

    failed = await bot.load_cogs()
    check(not failed, f"les {len(COGS)} modules se chargent (échecs : {failed or 'aucun'})")

    names = {c.name for c in bot.tree.get_commands()}
    missing = EXPECTED_COMMANDS - names
    check(not missing, f"les commandes attendues existent ({len(names)} au total, manquantes : {missing or 'aucune'})")

    db = bot.db
    gid, uid = 1, 42

    check(await db.add_coins(gid, uid, 100) == 100, "ajout de pièces")
    check(await db.add_coins(gid, uid, -500) is None, "le solde ne peut pas devenir négatif")
    check((await db.ensure_user(gid, uid))["coins"] == 100, "le solde est inchangé après un retrait refusé")

    check(await db.claim_cooldown(gid, uid, "last_daily", 3600, 10_000.0), "1re récompense quotidienne acceptée")
    check(not await db.claim_cooldown(gid, uid, "last_daily", 3600, 10_001.0), "2e récompense immédiate refusée")
    check(await db.claim_cooldown(gid, uid, "last_daily", 3600, 20_000.0), "récompense à nouveau possible après le cooldown")
    try:
        await db.claim_cooldown(gid, uid, "coins; DROP TABLE users", 1, 1.0)
        check(False, "une colonne invalide est refusée")
    except ValueError:
        check(True, "une colonne invalide est refusée")

    await db.set_setting(gid, "test", 7)
    check(await db.get_int_setting(gid, "test") == 7, "les réglages sont enregistrés")

    await bot.close()
    print("\n🎉 Tout est bon : le bot peut démarrer.")


if __name__ == "__main__":
    asyncio.run(main())
