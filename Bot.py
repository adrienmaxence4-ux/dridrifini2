# bot.py
import os
import re
import asyncio
import logging
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import discord
from discord.ext import commands

# Optional: OpenAI usage
try:
    import openai
except Exception:
    openai = None

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")  # obligatoiredz
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # optionnel
if openai and OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

# Config
INTENTS = discord.Intents.default()
INTENTS.message_content = True
BOT_PREFIX = "!"
BOT_DESCRIPTION = "Bot IA - vérification d'artiste et analyse de lien Instagram"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

bot = commands.Bot(command_prefix=BOT_PREFIX, description=BOT_DESCRIPTION, intents=INTENTS)

# Simple cache in memory to avoid repeated fetches (small, resets on restart)
FETCH_CACHE = {}
CACHE_TTL_SECONDS = 60 * 5  # 5 minutes

def cache_set(key, value):
    FETCH_CACHE[key] = (value, asyncio.get_event_loop().time())

def cache_get(key):
    item = FETCH_CACHE.get(key)
    if not item:
        return None
    value, ts = item
    if asyncio.get_event_loop().time() - ts > CACHE_TTL_SECONDS:
        del FETCH_CACHE[key]
        return None
    return value

# --- Utilities to fetch Instagram metadata (best-effort) ---
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DiscordBot/1.0; +https://example.com/bot)"
}

INSTAGRAM_URL_RE = re.compile(r"(https?://(?:www\.)?instagram\.com/[^/?#\s]+)")

def extract_instagram_profile_url(text):
    m = INSTAGRAM_URL_RE.search(text)
    return m.group(1).rstrip("/") if m else None

def fetch_instagram_preview(profile_url):
    """
    Tentative fetch of public Instagram profile page and parse some info.
    NOT guaranteed to work for all accounts (Instagram changes).
    Returns dict with keys: display_name, bio, posts, followers, following, avatar_url, raw_html
    """
    cached = cache_get(profile_url)
    if cached:
        return cached

    try:
        res = requests.get(profile_url + "/?__a=1", headers=HEADERS, timeout=10)
        # Instagram used to support ?__a=1 but it's often restricted. We'll fallback to HTML scraping.
        if res.status_code == 200 and res.headers.get("Content-Type","").startswith("application/json"):
            data = res.json()
            # If this path exists, parse basic fields (structure may vary)
            user = data.get("graphql", {}).get("user", {})
            info = {
                "display_name": user.get("full_name"),
                "bio": user.get("biography"),
                "posts": user.get("edge_owner_to_timeline_media", {}).get("count"),
                "followers": user.get("edge_followed_by", {}).get("count"),
                "following": user.get("edge_follow", {}).get("count"),
                "avatar_url": user.get("profile_pic_url"),
                "raw_html": None
            }
            cache_set(profile_url, info)
            return info
    except Exception:
        pass

    # Fallback to scraping HTML
    try:
        res = requests.get(profile_url, headers=HEADERS, timeout=10)
        if res.status_code != 200:
            return {"error": f"HTTP {res.status_code}"}
        html = res.text
        soup = BeautifulSoup(html, "html.parser")

        # Try to find the JSON LD script tag
        info = {"raw_html": html}
        # Display name
        disp = soup.find("meta", property="og:title")
        if disp:
            info["display_name"] = disp.get("content")
        else:
            info["display_name"] = None

        # Description / bio
        desc = soup.find("meta", property="og:description")
        if desc:
            desc_text = desc.get("content", "")
            # Often contains "X followers, Y following, Z posts - Bio"
            info["bio"] = desc_text
            # try to parse numbers
            num_match = re.search(r"([\d,\.]+)\s+followers", desc_text)
            if num_match:
                info["followers"] = num_match.group(1)
            else:
                info["followers"] = None
        else:
            info["bio"] = None
            info["followers"] = None

        avatar = soup.find("meta", property="og:image")
        info["avatar_url"] = avatar.get("content") if avatar else None

        # Posts/following best-effort
        info.setdefault("posts", None)
        info.setdefault("following", None)

        cache_set(profile_url, info)
        return info
    except Exception as e:
        return {"error": f"fetch error: {str(e)}"}

# --- Simple local analyzer (fallback) ---
def simple_local_analysis(profile_info, link):
    """
    If no OpenAI API available, produce a short, helpful analysis.
    """
    lines = []
    if not profile_info:
        return "Impossible d'extraire le profil. Voici quelques étapes : vérifier le lien, ou réessayer plus tard."

    if profile_info.get("error"):
        return f"Erreur lors de la récupération : {profile_info['error']}"

    disp = profile_info.get("display_name") or "Nom non trouvé"
    bio = profile_info.get("bio") or "Pas de bio publique"
    followers = profile_info.get("followers") or "Inconnu"
    avatar = profile_info.get("avatar_url") or "Aucun avatar trouvé"

    lines.append(f"**Profil** : {disp}")
    lines.append(f"**Bio (extrait)** : {bio[:300]}")
    lines.append(f"**Followers** : {followers}")
    lines.append(f"**Avatar** : {avatar}")
    # heuristic: suspicious/low follower count
    try:
        num = None
        if isinstance(followers, str):
            num = int(re.sub(r"[^\d]", "", followers)) if re.search(r"\d", followers) else None
        elif isinstance(followers, int):
            num = followers
        if num is not None:
            if num < 50:
                lines.append("⚠️ Compte avec peu de followers — prudence si tu veux vérifier l'authenticité.")
            else:
                lines.append("✅ Compte avec un nombre de followers acceptable.")
    except Exception:
        pass

    return "\n".join(lines)

# --- OpenAI analysis helper ---
def openai_analyze_text(prompt, model="gpt-4o-mini") -> str:
    """
    Use OpenAI to analyze. This function handles rate-limit / quota errors
    by raising exceptions to the caller, so the bot can fallback gracefully.
    """
    if openai is None or not OPENAI_API_KEY:
        raise RuntimeError("OpenAI non configuré")

    # Small safe prompt
    system = (
        "Tu es un assistant concis. Donne un résumé court et utile du profil Instagram."
        " Indique le nom, bio, nombre de followers si disponible, et signale si le compte semble officiel."
    )
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role":"system", "content": system},
                {"role":"user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.2
        )
        # Depending on OpenAI client version structure
        text = resp["choices"][0]["message"]["content"].strip()
        return text
    except Exception as e:
        # bubble up to caller for fallback
        raise

# --- Discord command ---
@bot.event
async def on_ready():
    logger.info(f"Connecté en tant que {bot.user} (id: {bot.user.id})")
    print(f"Bot prêt. Préfixe = {BOT_PREFIX}")

@bot.command(name="check", help="Vérifie et analyse un lien Instagram. Usage : !check <lien>")
async def check_profile(ctx, *, text: str):
    await ctx.trigger_typing()
    profile_url = extract_instagram_profile_url(text)
    if not profile_url:
        await ctx.send("Je n'ai pas trouvé de lien Instagram valide dans ton message.")
        return

    await ctx.send(f"Récupération du profil : {profile_url} ...")
    info = await asyncio.get_event_loop().run_in_executor(None, fetch_instagram_preview, profile_url)

    # If OpenAI configured, try to use it
    if OPENAI_API_KEY and openai:
        prompt = (
            f"Analyse ce profil Instagram : {profile_url}\n\n"
            f"Voici les informations récupérées (brutes) :\n{info}\n\n"
            "Fais un résumé court (max 6 lignes). Indique si le compte semble officiel ou non et cite les signaux."
        )
        try:
            analysis = await asyncio.get_event_loop().run_in_executor(None, openai_analyze_text, prompt)
            await ctx.send(f"**Analyse IA :**\n{analysis}")
            return
        except Exception as e:
            # log and fallback
            logger.warning(f"OpenAI error: {e}")
            await ctx.send("⚠️ Erreur OpenAI (quota ou autre). J'utilise le mode fallback local.")
            fallback = simple_local_analysis(info, profile_url)
            await ctx.send(fallback)
            return

    # No OpenAI: use fallback
    fallback = simple_local_analysis(info, profile_url)
    await ctx.send(fallback)

# Useful: command to test basic health
@bot.command(name="ping", help="Ping du bot")
async def ping(ctx):
    await ctx.send("Pong! Je suis en vie :eyes:")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERREUR : définis DISCORD_TOKEN dans le fichier .env")
        exit(1)
    bot.run(DISCORD_TOKEN)
