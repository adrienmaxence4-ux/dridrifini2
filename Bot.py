import os
from flask import Flask, request, jsonify
import discord
from discord.ext import commands
import openai
from dotenv import load_dotenv
import asyncio
import requests
from threading import Thread

# ⚡ Charger les variables d'environnement
load_dotenv()
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
OPENAI_KEY = os.environ.get("OPENAI_KEY")

# 🧠 Config OpenAI (ancienne API 0.28.0)
openai.api_key = OPENAI_KEY

# 🌐 Flask
app = Flask(__name__)

# 🤖 Discord
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- Flask route ----------------
@app.route("/submit_artist", methods=["POST"])
def submit_artist():
    data = request.json
    pseudo = data.get("pseudo")
    lien = data.get("lien")

    # Envoie les données au bot Discord pour traitement
    asyncio.run_coroutine_threadsafe(process_artist(pseudo, lien), bot.loop)

    return jsonify({"status": "[ok]", "message": "[ok] Vérification envoyée au bot."}), 200

# ---------------- Discord processing ----------------
async def process_artist(pseudo, lien):
    # Analyse IA via OpenAI 0.28.0
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Tu es un assistant qui vérifie si un lien contient le travail d'un artiste."},
                {"role": "user", "content": f"Analyse ce lien : {lien}"}
            ]
        )
        result_ia = response.choices[0].message.content
    except Exception as e:
        result_ia = f"⚠️ Erreur d'analyse : {e}"

    # Envoi dans un salon Discord via webhook
    payload = {
        "content": f"Nouvelle vérification d'artiste\n👤 Pseudo : {pseudo}\n🔗 Lien : {lien}\n🧠 Analyse IA : {result_ia}"
    }
    requests.post(WEBHOOK_URL, json=payload)

# ---------------- Lancer le bot Discord ----------------
@bot.event
async def on_ready():
    print(f"[ok] Bot connecté : {bot.user}")

# ---------------- Lancer Flask ----------------
if __name__ == "__main__":
    Thread(target=lambda: app.run(host="0.0.0.0", port=5000)).start()
    bot.run(DISCORD_TOKEN)
