--[[
	DISCORD BRIDGE : relie ton jeu Roblox au bot Discord.

	Ce fichier contient DEUX scripts à créer dans Roblox Studio (voir README) :
	  1) PARTIE 1 -> un "Script" dans ServerScriptService
	  2) PARTIE 2 -> un "LocalScript" dans StarterPlayer > StarterPlayerScripts
]]

------------------------------------------------------------------
-- PARTIE 1 : Script (ServerScriptService)  nom : DiscordBridge
------------------------------------------------------------------
local HttpService = game:GetService("HttpService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TextChatService = game:GetService("TextChatService")
local MarketplaceService = game:GetService("MarketplaceService")
local RunService = game:GetService("RunService")

local CONFIG = {
	-- Adresse publique de ton bot, SANS slash final (ex: https://mon-bot.exemple.com)
	BASE_URL = "https://REMPLACE-MOI",
	-- La MÊME clé que API_KEY dans le fichier .env du bot
	API_KEY = "REMPLACE-MOI",
	-- ID du Game Pass « VIP » (celui que les joueurs achètent). 0 = VIP automatique désactivé.
	VIP_GAMEPASS_ID = 0,
	-- Toutes les combien de secondes le serveur donne signe de vie au bot (panneau « info serveur »)
	HEARTBEAT_SECONDS = 30,
	-- Quand /jeu maintenance est activé sur Discord : éjecter les joueurs (true) ou seulement l'afficher (false)
	MAINTENANCE_KICK = true,
	-- UserId des comptes qui peuvent jouer pendant une maintenance (toi, ton équipe), ex : { 123456, 789012 }
	ADMIN_IDS = {},
}

-- Petit canal pour afficher des messages dans le chat du joueur
local notifyEvent = Instance.new("RemoteEvent")
notifyEvent.Name = "DiscordBridgeNotify"
notifyEvent.Parent = ReplicatedStorage

local function request(method, path, body)
	local ok, res = pcall(function()
		return HttpService:RequestAsync({
			Url = CONFIG.BASE_URL .. path,
			Method = method,
			Headers = {
				["Content-Type"] = "application/json",
				["X-API-Key"] = CONFIG.API_KEY,
			},
			Body = body and HttpService:JSONEncode(body) or nil,
		})
	end)
	if not ok then
		warn("[DiscordBridge] Requête échouée : " .. tostring(res))
		return nil, 0
	end
	local decoded
	pcall(function()
		decoded = HttpService:JSONDecode(res.Body)
	end)
	return decoded, res.StatusCode
end

------------------------------------------------------------------
-- API utilisable depuis tes autres scripts serveur via _G.DiscordBridge
------------------------------------------------------------------
local Bridge = {}

-- Poste une annonce dans le salon Discord choisi avec /roblox salon
-- Exemple : _G.DiscordBridge.announce("Mutant légendaire !", "Bob vient d'éclore un Dragon !", 0xFFD700)
function Bridge.announce(title, message, color)
	local data = request("POST", "/api/event", { title = title, message = message, color = color })
	return data ~= nil and data.ok == true
end

-- Donne (ou retire avec un nombre négatif) des pièces Discord à un joueur lié.
-- Retourne le nouveau solde, ou nil si le joueur n'a pas lié son compte.
function Bridge.giveCoins(player, amount, reason)
	local data = request("POST", "/api/coins", {
		robloxId = player.UserId,
		amount = amount,
		reason = reason,
	})
	if data and data.ok then
		return data.balance
	end
	return nil
end

-- Donne (active = true) ou retire (active = false) le rôle VIP Discord du joueur.
-- Si le joueur n'a pas encore lié son compte, le VIP est gardé de côté :
-- le rôle arrivera dès qu'il fera /roblox lier puis /link CODE.
-- Exemple : _G.DiscordBridge.setVip(player, true)
function Bridge.setVip(player, active)
	local data = request("POST", "/api/vip", { robloxId = player.UserId, active = active })
	return data ~= nil and data.ok == true
end

-- Envoie un classement au bot : il s'affiche dans le salon « classement » de Discord.
-- board   : identifiant court sans espace (ex : "mutants")
-- title   : titre affiché (ex : "Mutants éclos")
-- entries : { { userId = 123, name = "Bob", value = 4500 }, ... }  (les 10 meilleurs suffisent)
function Bridge.pushLeaderboard(board, title, entries)
	local list = {}
	for _, e in ipairs(entries) do
		table.insert(list, { robloxId = e.userId, name = e.name, value = e.value })
	end
	local data = request("POST", "/api/leaderboard", { board = board, title = title, entries = list })
	return data ~= nil and data.ok == true
end

-- Classement GLOBAL automatique : lit un OrderedDataStore toutes les minutes et l'envoie au bot.
-- Ton jeu doit enregistrer le score de chaque joueur dedans :
--   store:SetAsync(tostring(player.UserId), score)      (store = DataStoreService:GetOrderedDataStore(nom))
-- Exemple : _G.DiscordBridge.startLeaderboard("mutants", "Mutants éclos", "MutantsHatched")
local nameCache = {}
function Bridge.startLeaderboard(board, title, dataStoreName)
	local store = game:GetService("DataStoreService"):GetOrderedDataStore(dataStoreName)
	task.spawn(function()
		while true do
			local ok, pages = pcall(function()
				return store:GetSortedAsync(false, 10)
			end)
			if ok then
				local entries = {}
				for _, item in ipairs(pages:GetCurrentPage()) do
					local userId = tonumber(item.key)
					if userId then
						if not nameCache[userId] then
							local nameOk, name = pcall(function()
								return Players:GetNameFromUserIdAsync(userId)
							end)
							nameCache[userId] = nameOk and name or ("Joueur " .. userId)
						end
						table.insert(entries, { userId = userId, name = nameCache[userId], value = item.value })
					end
				end
				Bridge.pushLeaderboard(board, title, entries)
			else
				warn("[DiscordBridge] Lecture du classement impossible : " .. tostring(pages))
			end
			task.wait(60)
		end
	end)
end

-- Récupère { linked, coins, level, xp, vip } du joueur
function Bridge.getProfile(player)
	local data = request("GET", "/api/player/" .. player.UserId)
	return data
end

_G.DiscordBridge = Bridge

------------------------------------------------------------------
-- Commande de chat /link CODE   (liaison de compte)
------------------------------------------------------------------
local linkCommand = Instance.new("TextChatCommand")
linkCommand.Name = "DiscordLinkCommand"
linkCommand.PrimaryAlias = "/link"
linkCommand.SecondaryAlias = "/lier"
linkCommand.Parent = TextChatService

local linking = {} -- anti-spam par joueur

linkCommand.Triggered:Connect(function(origin, text)
	local player = Players:GetPlayerByUserId(origin.UserId)
	if not player or linking[player] then
		return
	end

	local code = string.match(string.upper(text), "([A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9])%s*$")
	if not code then
		notifyEvent:FireClient(player, "Utilisation : /link CODE (le code reçu avec /roblox lier sur Discord)")
		return
	end

	linking[player] = true
	-- Le serveur envoie player.UserId lui-même : impossible de tricher côté client
	local data, status = request("POST", "/api/link", { code = code, robloxId = player.UserId })
	linking[player] = nil

	if data and data.ok then
		notifyEvent:FireClient(player, "✅ Compte lié à Discord (" .. tostring(data.discordName) .. ") !")
	elseif status == 403 then
		notifyEvent:FireClient(player, "❌ Ce code a été créé pour un autre compte Roblox.")
	elseif status == 404 then
		notifyEvent:FireClient(player, "❌ Code invalide ou expiré. Refais /roblox lier sur Discord.")
	else
		notifyEvent:FireClient(player, "❌ Le bot est injoignable pour le moment.")
	end
end)

Players.PlayerRemoving:Connect(function(player)
	linking[player] = nil
end)

------------------------------------------------------------------
-- VIP automatique : le joueur qui possède le Game Pass VIP reçoit le rôle Discord
------------------------------------------------------------------
local function checkVip(player)
	if CONFIG.VIP_GAMEPASS_ID == 0 then
		return
	end
	local ok, owns = pcall(function()
		return MarketplaceService:UserOwnsGamePassAsync(player.UserId, CONFIG.VIP_GAMEPASS_ID)
	end)
	if ok and owns then
		Bridge.setVip(player, true)
	end
end

------------------------------------------------------------------
-- Signe de vie du serveur + maintenance
------------------------------------------------------------------
local function isAdmin(player)
	return table.find(CONFIG.ADMIN_IDS, player.UserId) ~= nil
end

local function kickForMaintenance(player, message)
	if not isAdmin(player) then
		player:Kick(message or "Le jeu est en maintenance, reviens bientôt !")
	end
end

-- Tous les HEARTBEAT_SECONDS : le bot sait que ce serveur est vivant et combien de joueurs il contient.
-- (Ignoré dans Studio et dans les serveurs privés, pour ne pas fausser les chiffres.)
local function sendHeartbeat(closing)
	if RunService:IsStudio() or game.PrivateServerId ~= "" then
		return
	end
	local data = request("POST", "/api/heartbeat", {
		jobId = game.JobId,
		players = #Players:GetPlayers(),
		maxPlayers = Players.MaxPlayers,
		placeVersion = game.PlaceVersion,
		closing = closing,
	})
	if data and data.maintenance and CONFIG.MAINTENANCE_KICK and not closing then
		for _, player in ipairs(Players:GetPlayers()) do
			kickForMaintenance(player, data.message)
		end
	end
end

task.spawn(function()
	while true do
		sendHeartbeat(false)
		task.wait(CONFIG.HEARTBEAT_SECONDS)
	end
end)

game:BindToClose(function()
	sendHeartbeat(true) -- le serveur se ferme : on le retire du panneau tout de suite
end)

-- Un joueur qui arrive pendant une maintenance est éjecté tout de suite
Players.PlayerAdded:Connect(function(player)
	if not CONFIG.MAINTENANCE_KICK or RunService:IsStudio() or isAdmin(player) then
		return
	end
	local data = request("GET", "/api/status")
	if data and data.maintenance then
		kickForMaintenance(player, data.message)
	end
end)

-- À l'arrivée du joueur (couvre ceux qui ont acheté avant, ou pendant que le bot était éteint)
Players.PlayerAdded:Connect(checkVip)
for _, player in ipairs(Players:GetPlayers()) do
	task.spawn(checkVip, player)
end

-- Juste après un achat en jeu
MarketplaceService.PromptGamePassPurchaseFinished:Connect(function(player, gamePassId, wasPurchased)
	if wasPurchased and gamePassId == CONFIG.VIP_GAMEPASS_ID then
		Bridge.setVip(player, true)
	end
end)

--[[
------------------------------------------------------------------
-- PARTIE 2 : LocalScript (StarterPlayer > StarterPlayerScripts)
-- nom : DiscordBridgeClient  (copie ce bloc dans un LocalScript)
------------------------------------------------------------------
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TextChatService = game:GetService("TextChatService")

local notifyEvent = ReplicatedStorage:WaitForChild("DiscordBridgeNotify")

notifyEvent.OnClientEvent:Connect(function(message)
	local channel = TextChatService:WaitForChild("TextChannels"):WaitForChild("RBXSystem")
	channel:DisplaySystemMessage(message)
end)
]]
