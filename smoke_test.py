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
    "vip", "avantages-sync",
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

    # --- Avantages : VIP donné par le staff ou acheté dans le jeu ---------------------
    perks = bot.get_cog("Perks")
    check(perks is not None, "le module avantages est chargé")
    check(not await perks.vip_wanted(gid, uid), "personne n'est VIP par défaut")

    await db.execute(
        "INSERT INTO vip(guild_id, kind, ident, created_at) VALUES(?,?,?,?)", (gid, "discord", uid, 1.0)
    )
    check(await perks.vip_wanted(gid, uid), "un VIP donné par le staff est reconnu")
    await db.execute("DELETE FROM vip WHERE guild_id=? AND kind=?", (gid, "discord"))
    check(not await perks.vip_wanted(gid, uid), "le VIP du staff peut être retiré")

    # Achat dans le jeu AVANT d'avoir lié son compte : le VIP attend la liaison
    result = await perks.set_roblox_vip(gid, 777, True)
    check(result == {"linked": False, "applied": False}, "VIP acheté mais compte non lié : mis de côté")
    check(not await perks.vip_wanted(gid, uid), "le VIP en attente n'est pas donné à n'importe qui")

    await db.execute("UPDATE users SET roblox_id=? WHERE guild_id=? AND user_id=?", (777, gid, uid))
    check(await perks.vip_wanted(gid, uid), "le VIP en attente arrive dès que le compte est lié")
    check(uid in await perks.vip_user_ids(gid), "le membre apparaît dans la liste des VIP")

    await db.execute("UPDATE users SET roblox_id=NULL WHERE guild_id=? AND user_id=?", (gid, uid))
    check(not await perks.vip_wanted(gid, uid), "le VIP ne suit pas le membre s'il délie son compte")

    await db.execute("UPDATE users SET roblox_id=? WHERE guild_id=? AND user_id=?", (777, gid, uid))
    result = await perks.set_roblox_vip(gid, 777, False)
    check(result["linked"] and not await perks.vip_wanted(gid, uid), "le VIP du jeu peut être retiré")

    await bot.close()
    print("\n🎉 Tout est bon : le bot peut démarrer.")


if __name__ == "__main__":
    asyncio.run(main())
