import os
import re
import io
import json
import time
import uuid
import base64
import urllib.request
import xml.sax.saxutils as saxutils
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from gtts import gTTS
from fastapi import FastAPI, Request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configuration & Keys
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
client = Client(TWILIO_SID, TWILIO_TOKEN) if TWILIO_SID and TWILIO_TOKEN else None

WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "+14155238886")
OWNER_WHATSAPP = os.getenv("OWNER_WHATSAPP", "+918074915644")
SHEET_WEBHOOK_URL = os.getenv("SHEET_WEBHOOK_URL", "")
SHEET_WRITE_URL = os.getenv("SHEET_WRITE_URL", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
BASE_URL = os.getenv("BASE_URL", "https://whatsapp-bot-esy5.onrender.com")
OLX_LINK = os.getenv("OLX_LINK", "https://www.olx.in/profile/129751503")
YOUTUBE_LINK = os.getenv("YOUTUBE_LINK", "https://youtube.com/@shivahouserentalagency745/shorts")
PROPERTIES_FILE = os.getenv("PROPERTIES_FILE", "properties.xlsx")
PROPERTIES_SHEET_URL = os.getenv("PROPERTIES_SHEET_URL", "")
YOUTUBE_SHEET_URL = os.getenv("YOUTUBE_SHEET_URL", "")
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "centralindia")

CONTACTS_LINE = (
    "📞/WhatsApp: 8500701521, 8074915644 (OLX links open కావాలంటే ఈ నంబర్లను మీ phone contacts లో save చేసుకోండి) ✅"
)

FALLBACK = (
    "నమస్కారం! శివ హౌస్ రెంటల్ ఏజెన్సీ 🏡\n"
    "మీ వివరాలు పంపండి:\n"
    "• పేరు?\n"
    "• ఎంత మంది ఉంటారు?\n"
    "• ఫ్యామిలీనా / బ్యాచిలర్స్?\n"
    "• ఎంత rent budget?\n\n"
    f"OLX Ads: {OLX_LINK}\n"
    f"YouTube: {YOUTUBE_LINK}\n\n"
    + CONTACTS_LINE
)

_cache = {}
audio_store = {}
sessions = {}
stats = {"total": 0, "voice_in": 0, "voice_out": 0}


# Google Sheets Utilities
def push_to_sheet(row):
    if not SHEET_WEBHOOK_URL:
        return
    try:
        req = urllib.request.Request(
            SHEET_WEBHOOK_URL,
            data=json.dumps(row, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print("Sheet error:", e)


def log_chat(direction, phone, body):
    push_to_sheet({
        "sheet": "Chats",
        "direction": direction,
        "phone": phone,
        "message": body,
    })


def send(to, body, media_url=None):
    if not client:
        print("Twilio client is not configured.")
        return
    try:
        kwargs = {
            "from_": f"whatsapp:{WHATSAPP_FROM}",
            "to": f"whatsapp:{to}",
        }
        if body:
            kwargs["body"] = body
        if media_url:
            kwargs["media_url"] = [media_url]

        message = client.messages.create(**kwargs)
        print(f"✅ Message sent: {message.sid}")
        if body:
            log_chat("OUT", to, body)
    except Exception as e:
        print(f"❌ Send error: {e}")


def fetch_df(key, url, max_age=120):
    now = time.time()
    if key in _cache and now - _cache[key][0] < max_age:
        return _cache[key][1]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as res:
            df = pd.read_csv(io.StringIO(res.read().decode("utf-8")))
        _cache[key] = (now, df)
        return df
    except Exception as e:
        print("CSV fetch error:", e)
        return _cache.get(key, (None, None))[1]


def load_properties():
    if PROPERTIES_SHEET_URL:
        df = fetch_df("props", PROPERTIES_SHEET_URL)
        if df is not None and not df.empty:
            return df
    try:
        if os.path.exists(PROPERTIES_FILE):
            return pd.read_excel(PROPERTIES_FILE)
    except Exception as e:
        print("Excel read error:", e)
    return None


def quick_property_search(budget, members=1, family_type="family"):
    try:
        df = load_properties()
        if df is None or df.empty:
            return None
        
        # Ensure budget column is numeric
        if 'budget' in df.columns:
            df['budget_num'] = pd.to_numeric(df['budget'], errors='coerce')
            matches = df[df['budget_num'] <= budget].head(3)
        else:
            matches = df.head(3)

        if matches.empty:
            return None

        response = f"నమస్కారం! మీ బడ్జెట్ ₹{budget} లోపు ఉన్న ఇళ్లు:\n\n"
        for i, (_, row) in enumerate(matches.iterrows(), 1):
            title = row.get('title', row.get('area', 'ఇల్లు'))
            area = row.get('area', '')
            rent = row.get('budget', '')
            link = row.get('link', '')

            response += f"{i}. *{title}* ({area}) – ₹{rent}\n"
            if link and str(link).strip() != "nan":
                response += f"   🔗 {link}\n"
            response += "\n"

        return response
    except Exception as e:
        print(f"Search error: {e}")
        return None


# Audio & TTS Services
def azure_tts_simple(text, lang="te"):
    if not AZURE_SPEECH_KEY:
        return None

    voice_map = {
        "te": "te-IN-ShrutiNeural",
        "hi": "hi-IN-SwaraNeural",
        "en": "en-IN-NeerjaNeural",
    }
    voice = voice_map.get(lang, "te-IN-ShrutiNeural")

    try:
        token_url = f"https://{AZURE_SPEECH_REGION}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
        token_req = urllib.request.Request(
            token_url,
            data=b"",
            headers={"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY},
            method="POST",
        )
        with urllib.request.urlopen(token_req, timeout=6) as res:
            access_token = res.read().decode("utf-8")

        safe_text = saxutils.escape(text)
        ssml = (
            f'<speak version="1.0" xml:lang="en-US">'
            f'<voice xml:lang="en-US" name="{voice}">{safe_text}</voice>'
            f'</speak>'
        )
        tts_url = f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
        tts_req = urllib.request.Request(
            tts_url,
            data=ssml.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-16khz-32kbitrate-mono-mp3",
                "User-Agent": "shiva-house-bot",
            },
            method="POST",
        )
        with urllib.request.urlopen(tts_req, timeout=10) as res:
            return res.read()
    except Exception as e:
        print(f"Azure TTS error: {e}")
        return None


def gtts_fallback(text, lang="te"):
    try:
        tts = gTTS(text=text, lang=lang, slow=False)
        audio_buffer = io.BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        return audio_buffer.getvalue()
    except Exception as e:
        print(f"gTTS error: {e}")
        return None


def send_voice_background(to, text, lang="te"):
    def _run():
        audio = azure_tts_simple(text, lang) or gtts_fallback(text, lang)
        if not audio:
            return
        uid = uuid.uuid4().hex
        audio_store[uid] = {
            'data': audio,
            'mime': "audio/mpeg",
            'created_at': time.time(),
        }
        send(to, None, media_url=f"{BASE_URL}/audio/{uid}")

    thread = threading.Thread(target=_run)
    thread.daemon = True
    thread.start()


# FastAPI Web Routes
@app.get("/")
def health():
    return {"status": "ok", "service": "Shiva House Rental Agency Bot"}


@app.get("/audio/{uid}")
def get_audio(uid: str):
    item = audio_store.get(uid)
    if not item:
        return Response(status_code=404, content="Audio not found")
    return Response(content=item['data'], media_type=item['mime'])


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    try:
        data = await request.form()
        from_number = data.get('From', '').replace('whatsapp:', '').strip()
        message_text = data.get('Body', '').strip()

        print(f"📩 Message from {from_number}: {message_text}")
        if from_number:
            log_chat("IN", from_number, message_text)

        # Budget calculation
        parts = message_text.split()
        budget = 6000
        for part in parts:
            clean_part = re.sub(r"\D", "", part)
            if clean_part.isdigit() and 3000 <= int(clean_part) <= 80000:
                budget = int(clean_part)
                break

        properties_text = quick_property_search(budget)

        if properties_text:
            send(from_number, properties_text)
            send(from_number, CONTACTS_LINE)
        else:
            send(from_number, FALLBACK)

        return Response(content=str(MessagingResponse()), media_type="text/xml")
    except Exception as e:
        print(f"Webhook error: {e}")
        return Response(content=str(MessagingResponse()), media_type="text/xml")
