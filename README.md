# Bot Discord « Hatch a Mutant »

Bot en Python (discord.py) avec : modération, accueil + rôles, tickets, économie + niveaux, et lien avec ton jeu Roblox.

## 1. Créer l'application Discord

1. Va sur https://discord.com/developers/applications > **New Application**.
2. Onglet **Bot** : clique **Reset Token** et copie le token (à garder secret).
3. Toujours dans **Bot**, active les 2 intents privilégiés : **Server Members Intent** et **Message Content Intent**.
4. Onglet **OAuth2 > URL Generator** : coche `bot` et `applications.commands`, puis la permission **Administrator** (le plus simple pour démarrer).
5. Ouvre l'URL générée pour inviter le bot sur ton serveur.
6. Important : dans les paramètres du serveur > Rôles, place le rôle du bot **au-dessus** des rôles qu'il doit donner (accueil, boutique, niveaux).

## 1 bis. Construire tout le serveur en une commande

Une fois le bot lancé et en ligne, tape **`/setup-serveur`** (réservé aux administrateurs), vérifie le récapitulatif et clique sur **Tout créer**. Le bot crée :

- **Rôles :** 🛡️ Admin, 🔨 Modérateur, 🎧 Support, 💎 Booster, 👑 VIP, ✅ Membre, et 3 rôles de notifications (📢 Annonces, 🎉 Événements, 🔔 Mises à jour).
- **Salons :** Accueil (règles + vérification), Informations, Communauté, Hatch a Mutant, Support (tickets), Vocal, Staff (logs) et une catégorie cachée pour les tickets ouverts.
- **Branchements automatiques :** tickets, vérification, logs de modération, message de bienvenue et annonces du jeu Roblox.

La commande est relançable sans risque : ce qui existe déjà est réutilisé, rien n'est dupliqué. Tes anciens salons ne sont pas touchés.

**Vérification avant d'entrer :** un nouveau membre ne voit que #règles et #vérification. Il doit trouver le mot de passe écrit dans les règles, cliquer sur « Je veux entrer » et le taper. Il reçoit alors le rôle ✅ Membre, qui ouvre le reste du serveur, et son message de bienvenue part à ce moment-là. Protections : compte Discord d'au moins 1 jour (réglable), 5 essais puis blocage 10 minutes. Réglages : `/verification mot`, `/verification age`, `/verifier @membre` (validation manuelle).

**Tickets :** le panneau propose un menu (Aide, Bug du jeu, Signaler un joueur, Autre). Un salon privé s'ouvre pour le membre et le Support, avec un bouton pour fermer et une transcription envoyée dans #logs-tickets. Un seul ticket ouvert par membre.

Après la création, pense à mettre le rôle du bot tout en haut de la liste des rôles (Paramètres du serveur > Rôles) et à te donner le rôle 🛡️ Admin.

## 2. Installer et lancer

Il faut Python 3.10 ou plus récent.

```bash
cd discord-bot
python -m venv venv
source venv/bin/activate        # Windows : venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # puis remplis DISCORD_TOKEN, GUILD_ID, API_KEY...
python bot.py
```

Pour qu'il tourne 24h/24, héberge-le sur un VPS (ou un hébergeur de bots).

## 3. Commandes

| Module | Commandes |
|---|---|
| Serveur | `/setup-serveur` `/verification configurer` `/verification mot` `/verification age` `/verifier` |
| Modération | `/kick` `/ban` `/unban` `/timeout` `/untimeout` `/clear` `/slowmode` `/lock` `/unlock` `/warn` `/warnings` `/delwarn` `/clearwarns` `/logs` |
| Accueil | `/accueil salon` `/accueil message` `/accueil role` `/accueil test` `/accueil desactiver` `/panneau-roles` |
| Tickets | `/ticket-config` `/ticket-panel` `/ticket-ajouter` `/ticket-retirer` |
| Économie | `/rank` `/classement` `/solde` `/daily` `/travail` `/payer` `/boutique` `/acheter` `/inventaire` `/niveau-role` + admin : `/boutique-ajouter` `/boutique-retirer` `/coins-donner` |
| Roblox | `/roblox lier` `/roblox delier` `/roblox profil` `/roblox jeu` `/roblox salon` |
| Avantages | `/vip donner` `/vip retirer` `/vip liste` `/avantages-sync` |

Démarrage conseillé sur le serveur : `/logs`, `/accueil salon`, `/ticket-config` puis `/ticket-panel`, `/roblox salon`.

Les avertissements sont cumulés : à 3 avertissements, le membre est mute 1h automatiquement.

## 4. Lier le bot à ton jeu Roblox

Le bot ouvre une petite API HTTP (port `API_PORT`) que ton jeu appelle. Roblox ne peut pas joindre `localhost` : le bot doit être accessible depuis Internet (VPS avec un nom de domaine en HTTPS, ou un tunnel type Cloudflare Tunnel / ngrok pendant les tests).

Dans Roblox Studio :

1. Menu **Fichier** > **Paramètres du jeu** > onglet **Sécurité** > active **Autoriser les requêtes HTTP**, puis **Enregistrer**.
2. Dans la fenêtre **Explorateur**, clic droit sur **ServerScriptService** > **Insérer un objet** > **Script**. Renomme-le `DiscordBridge`.
3. Colle la **PARTIE 1** de `roblox/DiscordBridge.lua` dedans, puis remplace `BASE_URL` (adresse publique du bot) et `API_KEY` (la même que dans `.env`).
4. Clic droit sur **StarterPlayer** > **StarterPlayerScripts** > **Insérer un objet** > **LocalScript**. Colle la **PARTIE 2** (sans les `--[[ ]]`).
5. Ne mets jamais la clé API dans un LocalScript : elle doit rester côté serveur.

Utilisation :

- **Liaison de compte** : sur Discord `/roblox lier pseudo`, le bot donne un code ; dans le jeu, le joueur écrit `/link CODE` dans le chat.
- **Annonces dans Discord** (choisis le salon avec `/roblox salon`) : `_G.DiscordBridge.announce("Mutant légendaire !", "Bob a éclos un Dragon !", 0xFFD700)`
- **Pièces partagées** : `_G.DiscordBridge.giveCoins(player, 100, "récompense")` (le joueur doit être lié). Le solde est le même que pour `/solde` sur Discord.
- **Infos du joueur** : `_G.DiscordBridge.getProfile(player)` renvoie `linked`, `coins`, `level`, `xp`.

## 5. Structure

```
bot.py            démarrage, chargement des modules (un module qui plante n'arrête pas le bot)
config.py         lecture du .env
db.py             base SQLite (fichier bot.db créé automatiquement)
utils.py          réponses sans risque et messages d'erreur clairs
cogs/             moderation, welcome, tickets, economy, roblox, verification, setup
roblox/           script Lua pour ton jeu
smoke_test.py     test de démarrage (GitHub le lance à chaque modification)
```

## 6. Rôles Booster et VIP

- **💎 Booster** : donné automatiquement dès qu'un membre booste le serveur, retiré quand son boost s'arrête. Si le bot était éteint pendant un boost, il rattrape au démarrage (ou avec `/avantages-sync`).
- **👑 VIP** : réservé aux joueurs qui paient dans le jeu. Le jeu prévient le bot (`/api/vip`) et le joueur reçoit le rôle sur Discord. S'il n'a pas encore lié son compte (`/roblox lier`), son VIP est gardé de côté et le rôle arrive dès qu'il le lie.
- `/vip donner @membre` donne le VIP à la main (cadeau, concours...) : ce VIP-là est protégé et ne dépend pas du jeu. `/vip liste` affiche tous les VIP.
- Les deux rôles sont créés automatiquement s'ils n'existent pas (ou par `/setup-serveur`). Le rôle du bot doit être **au-dessus** d'eux.

**Côté jeu (Roblox Studio)** : dans le script `DiscordBridge` (ServerScriptService), mets l'ID de ton Game Pass VIP dans `VIP_GAMEPASS_ID`. Tout joueur qui possède le pass (acheté avant ou pendant la partie) reçoit alors le rôle. Pour donner ou retirer le VIP depuis tes propres scripts : `_G.DiscordBridge.setVip(player, true)` / `setVip(player, false)`.

## 7. Fiabilité

- Un module qui plante au chargement est isolé : les autres restent en ligne, l'erreur est écrite dans les logs.
- Si le port de l'API Roblox est déjà pris, le bot démarre quand même (sans l'API).
- Chaque bouton, menu et formulaire répond par un message clair en cas d'erreur, jamais « l'interaction a échoué ».
- `python smoke_test.py` vérifie en quelques secondes que tout se charge. Lance-le avant chaque mise à jour.
