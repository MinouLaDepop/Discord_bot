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
    "vip", "avantages-sync", "jeu", "event",
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

    # --- Info serveur en direct --------------------------------------------------
    from cogs.live import bar, clean_entries, fmt_num, iso_to_ts

    live = bot.get_cog("Live")
    check(live is not None, "le module « jeu en direct » est chargé")
    check(fmt_num(1234567) == "1 234 567" and fmt_num(None) == "—", "les nombres sont bien mis en forme")
    check(bar(5, 10, 10) == "█████░░░░░" and bar(1, 0) == "░" * 12, "la barre de remplissage est correcte")
    check(iso_to_ts("2026-10-07T12:34:56.1234567Z") == 1791376496, "les dates de Roblox sont lues (même avec 7 décimales)")

    check(live.record_heartbeat("jobA", 12, 20, 5, False), "un serveur donne signe de vie")
    live.record_heartbeat("jobB", 8, 20, 5, False)
    servers = live.active_servers()
    check(len(servers) == 2 and sum(s["players"] for s in servers.values()) == 20, "2 serveurs, 20 joueurs comptés")
    live.record_heartbeat("jobB", 0, 0, 0, True)
    check(list(live.active_servers()) == ["jobA"], "un serveur qui se ferme disparaît tout de suite")
    live.servers["jobA"]["seen"] -= 500
    check(not live.active_servers(), "un serveur muet depuis trop longtemps est considéré éteint")

    raw = [{"robloxId": 2, "name": "Bob", "value": 50}, {"robloxId": 1, "name": "Ana", "value": 900.4}]
    entries = clean_entries(raw)
    check([e["name"] for e in entries] == ["Ana", "Bob"] and entries[0]["value"] == 900, "le classement est trié et nettoyé")
    check(clean_entries([{"robloxId": "x", "value": 1}]) is None, "un classement invalide est refusé")
    check(clean_entries([{"robloxId": 1, "value": float("nan")}]) is None, "une valeur NaN est refusée")
    check(clean_entries([{"robloxId": 1, "value": 1}] * 30) is None, "un classement trop long est refusé")

    check(await live.save_leaderboard(gid, "mutants", "Mutants éclos", raw) is None, "un classement est enregistré")
    check(await live.save_leaderboard(gid, "Bad Board!", "x", raw) == "bad_board", "un identifiant de classement invalide est refusé")
    top = await db.fetchall("SELECT name FROM leaderboard WHERE guild_id=? AND board=? ORDER BY pos", (gid, "mutants"))
    check([r["name"] for r in top] == ["Ana", "Bob"], "le classement est relu dans le bon ordre")
    await live.save_leaderboard(gid, "mutants", "Mutants éclos", raw[:1])
    top = await db.fetchall("SELECT name FROM leaderboard WHERE guild_id=? AND board=?", (gid, "mutants"))
    check(len(top) == 1, "un nouveau classement remplace l'ancien")
    for n in range(4):
        await live.save_leaderboard(gid, f"b{n}", "x", raw)
    check(await live.save_leaderboard(gid, "trop", "x", raw) == "too_many_boards", "le nombre de classements est limité")

    maintenance, _ = await live.maintenance_state(gid)
    check(not maintenance, "pas de maintenance par défaut")
    await db.set_setting(gid, "maintenance", "1")
    maintenance, message = await live.maintenance_state(gid)
    check(maintenance and message, "la maintenance peut être activée")

    # --- Événements et compte à rebours ------------------------------------------
    import datetime
    from zoneinfo import ZoneInfo

    from cogs.events import fmt_countdown, parse_when

    paris = ZoneInfo("Europe/Paris")
    when = parse_when("25/12/2026", "20h30", paris)
    expected = datetime.datetime(2026, 12, 25, 19, 30, tzinfo=datetime.timezone.utc)
    check(when is not None and when.timestamp() == expected.timestamp(), "20h30 à Paris = 19h30 UTC en décembre")
    summer = parse_when("15/07/2026", "20:30", paris)
    check(summer.timestamp() == datetime.datetime(2026, 7, 15, 18, 30, tzinfo=datetime.timezone.utc).timestamp(), "l'heure d'été est gérée")
    check(parse_when("pas une date", "20h", paris) is None, "une date invalide est refusée")
    check(parse_when("25/12/2026", "20h", paris).hour == 20, "« 20h » est compris")
    check(fmt_countdown(2 * 86400 + 3 * 3600 + 15 * 60) == "2 j 03 h 15 min", "décompte en jours")
    check(fmt_countdown(5 * 3600 + 7 * 60) == "5 h 07 min", "décompte en heures")
    check(fmt_countdown(42 * 60 + 10) == "42 min", "décompte en minutes")
    check(fmt_countdown(20) == "moins d'une minute" and fmt_countdown(-5) == "moins d'une minute", "décompte final")

    await bot.close()
    print("\n🎉 Tout est bon : le bot peut démarrer.")


if __name__ == "__main__":
    asyncio.run(main())
