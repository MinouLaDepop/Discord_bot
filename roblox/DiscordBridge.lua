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

local CONFIG = {
	-- Adresse publique de ton bot, SANS slash final (ex: https://mon-bot.exemple.com)
	BASE_URL = "https://REMPLACE-MOI",
	-- La MÊME clé que API_KEY dans le fichier .env du bot
	API_KEY = "REMPLACE-MOI",
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

-- Récupère { linked, coins, level, xp } du joueur
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
