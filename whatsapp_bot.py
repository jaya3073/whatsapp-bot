# whatsapp_bot.py — Shiva House Rental Agency (v2)
#
# WhatsApp bot with guided conversation flow, Gemini-powered understanding,
# voice note support, bachelors filtering, tiered fees, email alerts,
# and duplicate-message protection.
#
# Required environment variables (set in Render -> Environment):
#   TWILIO_ACCOUNT_SID
#   TWILIO_AUTH_TOKEN
#   TWILIO_WHATSAPP_FROM       (e.g. +14155238886)
#   OWNER_WHATSAPP             (e.g. +918074915644)
#   GEMINI_API_KEY
#   GEMINI_MODEL               (default: gemini-flash-lite-latest)
#   PROPERTIES_SHEET_URL       (CSV export link of your Google Sheet)
#   BASE_URL                   (your Render URL for this bot)
#   AZURE_SPEECH_KEY           (optional, for high-quality TTS)
#   AZURE_SPEECH_REGION        (e.g. centralindia)
#   GMAIL_ADDRESS              (e.g. sbc4199@gmail.com)
#   GMAIL_APP_PASSWORD         (16-letter App Password from Google)
#   OWNER_EMAIL                (where notifications go, default = GMAIL_ADDRESS)

import os
import re
import io
import json
import time
import uuid
import base64
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import xml.sax.saxutils as saxutils
import urllib.request

import pandas as pd
from gtts import gTTS
from fastapi import FastAPI, Request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# ─── Configuration & Keys ─────────────────────────────────────────────

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
client = Client(TWILIO_SID, TWILIO_TOKEN) if TWILIO_SID and TWILIO_TOKEN else None

WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "+14155238886")
OWNER_WHATSAPP = os.getenv("OWNER_WHATSAPP", "+918074915644")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
BASE_URL = os.getenv("BASE_URL", "https://whatsapp-bot-esy5.onrender.com")
OLX_LINK = os.getenv("OLX_LINK", "https://www.olx.in/profile/129751503")
YOUTUBE_LINK = os.getenv("YOUTUBE_LINK", "https://youtube.com/@shivahouserentalagency745/shorts")
WEBSITE_LINK = os.getenv("WEBSITE_LINK", "https://shivahouserentalagency.in/")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://wa.me/c/918074915644")
PROPERTIES_FILE = os.getenv("PROPERTIES_FILE", "properties.xlsx")
PROPERTIES_SHEET_URL = os.getenv("PROPERTIES_SHEET_URL", "")
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "centralindia")

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "sbc4199@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", GMAIL_ADDRESS)

SHEET_WEBHOOK_URL = os.getenv("SHEET_WEBHOOK_URL", "")

# ─── Constants ────────────────────────────────────────────────────────

CONTACTS_LINE = (
    "📞Contact : Shiva 8500701521, 8074915644 "
    "(OLX links open కావాలంటే ఈ నంబర్లను మీ phone contacts లో save చేసుకోండి) ✅"
)

GREETING = (
    "నమస్కారం! శివ హౌస్ రెంటల్ ఏజెన్సీ 🏡\n"
    "దయచేసి కింది వివరాలు పంపండి:\n"
    "1. మీ పేరు\n"
    "2. ఎంతమంది ఉంటారు\n"
    "3. ఎంత రెంట్లో చూస్తున్నారు\n"
    "4. ఫ్యామిలీనా / బ్యాచిలర్స్?\n\n"
    "voice note ద్వారా కూడా నాతో మాట్లాడవచ్చు మీ భాషలో 🎙️"
)

FOOTER = (
    f"\n\nWebsite: {WEBSITE_LINK}\n"
    f"WhatsApp Channel: {CHANNEL_LINK}\n"
    f"OLX Ads: {OLX_LINK}\n"
    f"YouTube: {YOUTUBE_LINK}\n\n"
    + CONTACTS_LINE
)


def get_fees_message(budget):
    """Return the fees message based on budget threshold."""
    if budget >= 8000:
        return (
            "మేము ఓనర్స్ కాదు, రెంటల్ ఏజెన్సీ. మాది service ఉంటుంది. "
            "మీ బడ్జెట్ లో 3 or 5 houses చూపిస్తాము. "
            "ఇల్లు ఇప్పిస్తాము. ఇప్పించినందుకు ఫీజు ఛార్జ్ చేస్తాము. "
            "5000 ఉంటుంది. 5000 లో ఇనిషియల్ గా 800 తీసుకుంటాము. "
            "రూమ్స్ అన్నీ చూపిస్తాము. మీరు రూమ్ కి అడ్వాన్స్ ఇచ్చేటప్పుడు 4200 ఇవ్వాల్సి ఉంటుంది. "
            "Total 5000.\n"
            "Note: మీరు ఇచ్చే 800 కి 1 month validity ఉంటుంది. మీకు ఇల్లు దొరికేవరకు."
        )
    else:
        return (
            "మేము ఓనర్స్ కాదు, రెంటల్ ఏజెన్సీ. మాది service ఉంటుంది. "
            "మీ బడ్జెట్ లో 3 or 5 houses చూపిస్తాము. "
            "ఇల్లు ఇప్పిస్తాము. ఇప్పించినందుకు ఫీజు ఛార్జ్ చేస్తాము. "
            "4000 ఉంటుంది. First 800 pay చెయ్యాలి. రూమ్స్ అన్నీ చూపిస్తాము. "
            "తర్వాత మీరు room కి advance ఇచ్చేటప్పుడు 3200.\n"
            "Note: మీరు ఇచ్చే 800 కి 1 month validity ఉంటుంది. మీకు ఇల్లు దొరికేవరకు."
        )


# ─── Session Management ──────────────────────────────────────────────

sessions = {}          # phone -> {name, count, budget, family_type, completed, last_sid, last_reply_time}
recent_sids = {}       # phone -> last MessageSid (dedup)
audio_store = {}


def get_session(phone):
    if phone not in sessions:
        sessions[phone] = {
            "name": None,
            "count": None,
            "budget": None,
            "family_type": None,
            "completed": False,
        }
    return sessions[phone]


# ─── Google Sheets Utilities ─────────────────────────────────────────

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


# ─── Email Integration ───────────────────────────────────────────────

def send_email(subject, body, to_addr=None):
    if not GMAIL_APP_PASSWORD:
        print("Email skipped: GMAIL_APP_PASSWORD not set")
        return False
    to_addr = to_addr or OWNER_EMAIL
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as smtp:
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            smtp.sendmail(GMAIL_ADDRESS, to_addr, msg.as_string())
        print(f"Email sent to {to_addr}: {subject}")
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


def send_email_background(subject, body, to_addr=None):
    t = threading.Thread(target=send_email, args=(subject, body, to_addr))
    t.daemon = True
    t.start()


def notify_owner_new_lead(phone, message_text, session):
    name = session.get("name") or "Unknown"
    budget = session.get("budget") or "Not specified"
    ftype = session.get("family_type") or "Not specified"
    count = session.get("count") or "Not specified"
    subject = f"New Lead from {phone}"
    body = (
        f"New WhatsApp lead!\n\n"
        f"Name: {name}\n"
        f"Phone: {phone}\n"
        f"Message: {message_text}\n"
        f"Budget: Rs.{budget}\n"
        f"People: {count}\n"
        f"Type: {ftype}\n"
        f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Reply: https://wa.me/{phone.lstrip('+')}\n"
    )
    send_email_background(subject, body)


# ─── Gemini AI Integration ───────────────────────────────────────────

def call_gemini(prompt, audio_b64=None, audio_mime=None):
    """Call Gemini API for text understanding or audio transcription."""
    if not GEMINI_API_KEY:
        return None

    parts = [{"text": prompt}]
    if audio_b64:
        parts.append({
            "inline_data": {
                "mime_type": audio_mime or "audio/ogg",
                "data": audio_b64,
            }
        })

    model = GEMINI_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
        },
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            result = json.loads(res.read().decode("utf-8"))

        text = (
            result.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        return text.strip()
    except Exception as e:
        print(f"Gemini error: {e}")
        return None


def extract_info_with_gemini(text, session):
    """Extract name, count, budget, accommodation_type from client message."""
    current = (
        f"name={session.get('name')}, count={session.get('count')}, "
        f"budget={session.get('budget')}, type={session.get('family_type')}"
    )

    prompt = (
        "You are a rental agency assistant. Extract information from this "
        "WhatsApp message about a house rental inquiry.\n\n"
        f"Already known: {current}\n"
        f'Message: "{text}"\n\n'
        "Return ONLY a JSON object with these keys (include only keys that "
        "are clearly present in the message):\n"
        '  "name": string (client name)\n'
        '  "count": integer (number of people)\n'
        '  "budget": integer (rent in rupees, e.g. 5000)\n'
        '  "accommodation_type": "family" or "bachelors"\n\n'
        "Return ONLY the JSON, no other text."
    )

    result = call_gemini(prompt)
    if not result:
        return {}

    # Strip markdown code fences
    result = result.strip()
    if result.startswith("```"):
        lines = result.split("\n")
        result = "\n".join(lines[1:])
        if result.endswith("```"):
            result = result[:-3].strip()

    try:
        return json.loads(result)
    except Exception:
        return {}


def extract_info_regex(text):
    """Fallback: extract info with simple regex when Gemini is unavailable."""
    info = {}

    # Budget: look for numbers 3000-80000
    for match in re.finditer(r"(\d{4,5})", text):
        num = int(match.group(1))
        if 3000 <= num <= 80000:
            info["budget"] = num
            break

    # Accommodation type
    lower = text.lower()
    if "bachelor" in lower or "bachelors" in lower:
        info["accommodation_type"] = "bachelors"
    elif "family" in lower:
        info["accommodation_type"] = "family"

    # Count: look for patterns like "3 members", "2 people", "4 మంది"
    count_match = re.search(r"(\d+)\s*(?:members?|people|మంది|persons?)", lower)
    if count_match:
        info["count"] = int(count_match.group(1))
    else:
        for word, num in [("two", 2), ("three", 3), ("four", 4), ("five", 5)]:
            if word in lower:
                info["count"] = num
                break

    return info


def transcribe_audio(audio_data, mime_type):
    """Transcribe a voice note using Gemini."""
    audio_b64 = base64.b64encode(audio_data).decode("utf-8")

    prompt = (
        "Transcribe this audio message. The language may be Telugu, Hindi, "
        "or English. Return ONLY the transcribed text, nothing else."
    )

    result = call_gemini(prompt, audio_b64=audio_b64, audio_mime=mime_type)
    return result if result else ""


# ─── WhatsApp Send ────────────────────────────────────────────────────

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
        print(f"Sent: {message.sid}")
        if body:
            log_chat("OUT", to, body)
    except Exception as e:
        print(f"Send error: {e}")


def download_twilio_media(media_url):
    """Download audio from Twilio with basic auth."""
    try:
        auth = base64.b64encode(
            f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()
        ).decode()
        req = urllib.request.Request(
            media_url,
            headers={"Authorization": f"Basic {auth}"},
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.read()
    except Exception as e:
        print(f"Media download error: {e}")
        return None


# ─── Property Search ─────────────────────────────────────────────────

_cache = {}


def fetch_df(key, url, max_age=120):
    now = time.time()
    if key in _cache and now - _cache[key][0] < max_age:
        return _cache[key][1]
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0"}
        )
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


def filter_bachelors(df):
    """Filter properties that allow bachelors."""
    for col in df.columns:
        col_lower = str(col).lower().strip()
        if (
            "bachelor" in col_lower
            or col_lower == "for_bachelors"
            or col_lower == "allowed_for"
        ):
            values = df[col].astype(str).str.lower()
            mask = values.str.contains(
                "bachelor|yes|both|all|true|1", na=False
            )
            if mask.any():
                return df[mask]
    # If no bachelors column found, check a 'type' or 'for' column
    for col in df.columns:
        col_lower = str(col).lower().strip()
        if col_lower in ("type", "for", "tenant_type", "allowed"):
            values = df[col].astype(str).str.lower()
            mask = values.str.contains(
                "bachelor|both|all", na=False
            )
            if mask.any():
                return df[mask]
    # No filter column found — return all
    return df


def quick_property_search(budget, family_type="family"):
    """Search for properties within budget. Returns formatted text or None."""
    try:
        df = load_properties()
        if df is None or df.empty:
            return None

        # Bachelors filter
        if family_type == "bachelors":
            df = filter_bachelors(df)
            if df.empty:
                return (
                    "మీ అవసరానికి బ్యాచిలర్స్ అలవ్ అయ్యే ఇళ్లు "
                    "ప్రస్తుతం అందుబాటులో లేవు. త్వరలో అప్డేట్ చేస్తాం."
                )

        # Budget filter
        budget_col = None
        for col in df.columns:
            if "budget" in str(col).lower() or "rent" in str(col).lower():
                budget_col = col
                break

        if budget_col:
            df["_budget_num"] = pd.to_numeric(
                df[budget_col], errors="coerce"
            )
            matches = df[df["_budget_num"] <= budget].head(5)
        else:
            matches = df.head(5)

        if matches.empty:
            return None

        response = ""
        for i, (_, row) in enumerate(matches.iterrows(), 1):
            title = row.get("title", row.get("Title", ""))
            area = row.get("area", row.get("Area", ""))
            rent = row.get("budget", row.get("rent", row.get("Budget", "")))
            link = row.get("link", row.get("Link", ""))

            response += f"{i}. *{title}* ({area}) - Rs.{rent}\n"
            if link and str(link).strip() != "nan":
                response += f"   {link}\n"
            response += "\n"

        return response
    except Exception as e:
        print(f"Search error: {e}")
        return None


# ─── Text-to-Speech ──────────────────────────────────────────────────

voice_map = {
    "te": "te-IN-ShrutiNeural",
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
}


def azure_tts_simple(text, lang="te"):
    if not AZURE_SPEECH_KEY:
        return None
    voice = voice_map.get(lang, "te-IN-ShrutiNeural")
    try:
        token_url = (
            f"https://{AZURE_SPEECH_REGION}.api.cognitive.microsoft.com"
            f"/sts/v1.0/issueToken"
        )
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
            f"</speak>"
        )
        tts_url = (
            f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com"
            f"/cognitiveservices/v1"
        )
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
            "data": audio,
            "mime": "audio/mpeg",
            "created_at": time.time(),
        }
        send(to, None, media_url=f"{BASE_URL}/audio/{uid}")

    t = threading.Thread(target=_run)
    t.daemon = True
    t.start()


def strip_for_voice(text):
    """Remove links, ad titles, phone numbers from text for voice notes."""
    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"www\.\S+", "", text)
    # Remove phone numbers (Indian format)
    text = re.sub(r"\+?\d{10,13}", "", text)
    # Remove property listing lines (lines starting with number. *)
    lines = text.split("\n")
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^\d+\.\s*\*", stripped):
            continue  # skip property listing lines
        if stripped.startswith("http") or stripped.startswith("www."):
            continue
        if not stripped:
            continue
        clean_lines.append(stripped)
    return ". ".join(clean_lines)


# ─── Conversation Logic ──────────────────────────────────────────────

def detect_language(text):
    """Detect if message is Telugu, Hindi, or English."""
    telugu_chars = sum(1 for c in text if "\u0c00" <= c <= "\u0c7f")
    hindi_chars = sum(1 for c in text if "\u0900" <= c <= "\u097f")
    if telugu_chars > 3:
        return "te"
    if hindi_chars > 3:
        return "hi"
    return "te"  # default to Telugu for this agency


def ask_missing_fields(session):
    """Ask for whichever of the 4 fields are still missing."""
    missing = []
    if not session.get("name"):
        missing.append("మీ పేరు")
    if not session.get("count"):
        missing.append("ఎంతమంది ఉంటారు")
    if not session.get("budget"):
        missing.append("ఎంత రెంట్లో చూస్తున్నారు")
    if not session.get("family_type"):
        missing.append("ఫ్యామిలీనా / బ్యాచిలర్స్")

    if not missing:
        return None

    if len(missing) == 4:
        return GREETING

    if len(missing) == 1:
        if "మీ పేరు" in missing[0]:
            return "మీ పేరు ఏమిటి?"
        if "ఎంతమంది" in missing[0]:
            return "ఎంతమంది ఉంటారు?"
        if "ఎంత రెంట్లో" in missing[0]:
            return "ఎంత రెంట్లో చూస్తున్నారు?"
        if "ఫ్యామిలీ" in missing[0]:
            return "ఫ్యామిలీనా / బ్యాచిలర్స్?"

    parts = []
    for i, m in enumerate(missing, 1):
        parts.append(f"{i}. {m}")
    return "దయచేసి ఇంకా ఈ వివరాలు పంపండి:\n" + "\n".join(parts)


def build_complete_response(session):
    """Build the single combined message with properties + fees + footer."""
    budget = session.get("budget", 6000)
    family_type = session.get("family_type", "family")
    name = session.get("name", "")

    properties_text = quick_property_search(budget, family_type)
    fees_msg = get_fees_message(budget)

    # Greeting with name
    if name:
        greeting_line = f"నమస్కారం {name}! 🏡\n\n"
    else:
        greeting_line = "నమస్కారం! 🏡\n\n"

    # Property results
    if properties_text:
        props_section = (
            "మీ బడ్జెట్ ప్రకారం కింది ఇళ్లు అందుబాటులో ఉన్నాయి:\n\n"
            f"{properties_text}\n"
        )
    else:
        props_section = (
            "మీ బడ్జెట్ ప్రకారం ప్రస్తుతం ఇళ్లు అందుబాటులో లేవు. "
            "త్వరలో అప్డేట్ చేస్తాం.\n\n"
        )

    # Combine everything into ONE message
    full_response = (
        greeting_line
        + props_section
        + fees_msg
        + FOOTER
    )

    return full_response


def build_voice_reply(session):
    """Build a voice-friendly version (no links, titles, or phone numbers)."""
    budget = session.get("budget", 6000)
    family_type = session.get("family_type", "family")
    name = session.get("name", "")
    name_part = f" {name}" if name else ""

    if budget >= 8000:
        fees_text = (
            "మా సర్వీస్ ఫీజు మొత్తం 5000 రూపాయలు. "
            "అందులో మొదట 800 రూపాయలు ఇవ్వాలి. "
            "మిగిలిన 4200 రూపాయలు ఇల్లు దొరికిన తర్వాత "
            "అడ్వాన్స్ ఇచ్చేటప్పుడు ఇవ్వాలి. "
            "ఈ 800 రూపాయలకి ఒక నెల వాలిడిటీ ఉంటుంది, "
            "మీకు ఇల్లు దొరికేవరకు."
        )
    else:
        fees_text = (
            "మా సర్వీస్ ఫీజు మొత్తం 4000 రూపాయలు. "
            "అందులో మొదట 800 రూపాయలు ఇవ్వాలి. "
            "మిగిలిన 3200 రూపాయలు ఇల్లు దొరికిన తర్వాత "
            "అడ్వాన్స్ ఇచ్చేటప్పుడు ఇవ్వాలి. "
            "ఈ 800 రూపాయలకి ఒక నెల వాలిడిటీ ఉంటుంది, "
            "మీకు ఇల్లు దొరికేవరకు."
        )

    voice_text = (
        f"నమస్కారం{name_part}! "
        "మీ బడ్జెట్ ప్రకారం కొన్ని ఇళ్లు దొరికాయి. "
        "వివరాలు మీకు మెసేజ్ లో పంపాం, చూడండి. "
        f"{fees_text} "
        "ఏవైనా ప్రశ్నలు ఉంటే అడగండి."
    )
    return voice_text


# ─── FastAPI Web Routes ──────────────────────────────────────────────

@app.get("/")
def health():
    return {"status": "ok", "service": "Shiva House Rental Agency Bot v2"}


@app.get("/audio/{uid}")
def get_audio(uid: str):
    item = audio_store.get(uid)
    if not item:
        return Response(status_code=404, content="Audio not found")
    return Response(content=item["data"], media_type=item["mime"])


@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    try:
        data = await request.form()
        from_number = data.get("From", "").replace("whatsapp:", "").strip()
        message_text = data.get("Body", "").strip()
        message_sid = data.get("MessageSid", "")
        num_media = int(data.get("NumMedia", "0"))

        # ── Dedup: skip if we already processed this SID ──
        if message_sid and recent_sids.get(from_number) == message_sid:
            print(f"Duplicate SID skipped: {message_sid}")
            return Response(
                content=str(MessagingResponse()),
                media_type="text/xml",
            )
        recent_sids[from_number] = message_sid

        print(f"Message from {from_number}: {message_text[:80]}")
        if from_number:
            log_chat("IN", from_number, message_text)

        session = get_session(from_number)
        is_voice = False
        lang = "te"

        # ── Handle voice note (audio attachment) ──
        if num_media > 0:
            media_url = data.get("MediaUrl0", "")
            media_type = data.get("MediaContentType0", "")
            print(f"Media: {media_type} from {from_number}")

            if "audio" in media_type:
                is_voice = True
                audio_data = download_twilio_media(media_url)
                if audio_data:
                    transcribed = transcribe_audio(audio_data, media_type)
                    if transcribed:
                        message_text = transcribed
                        print(f"Transcribed: {transcribed[:80]}")
                        log_chat("IN", from_number, f"[VOICE] {transcribed}")
                        lang = detect_language(transcribed)

        # ── If no text and no voice, send greeting ──
        if not message_text:
            send(from_number, GREETING)
            send_voice_background(from_number, GREETING, lang)
            return Response(
                content=str(MessagingResponse()), media_type="text/xml"
            )

        # ── Extract info from message ──
        extracted = extract_info_with_gemini(message_text, session)
        if not extracted:
            extracted = extract_info_regex(message_text)

        # Update session with newly extracted info
        if extracted.get("name"):
            session["name"] = str(extracted["name"]).strip()
        if extracted.get("count"):
            try:
                session["count"] = int(extracted["count"])
            except (ValueError, TypeError):
                pass
        if extracted.get("budget"):
            try:
                session["budget"] = int(extracted["budget"])
            except (ValueError, TypeError):
                pass
        if extracted.get("accommodation_type"):
            atype = str(extracted["accommodation_type"]).lower().strip()
            if "bachelor" in atype:
                session["family_type"] = "bachelors"
            elif "family" in atype:
                session["family_type"] = "family"

        # ── Check if all info collected ──
        missing_msg = ask_missing_fields(session)

        if missing_msg:
            # Still missing some fields — ask for them (text + voice)
            send(from_number, missing_msg)
            send_voice_background(from_number, missing_msg, lang)
        else:
            # All fields collected — send complete response in ONE message
            response_text = build_complete_response(session)
            send(from_number, response_text)
            session["completed"] = True

            # Voice note is COMPULSORY with every reply
            voice_text = build_voice_reply(session)
            send_voice_background(from_number, voice_text, lang)

        # ── Email notification to owner ──
        notify_owner_new_lead(from_number, message_text, session)

        return Response(
            content=str(MessagingResponse()), media_type="text/xml"
        )
    except Exception as e:
        print(f"Webhook error: {e}")
        return Response(
            content=str(MessagingResponse()), media_type="text/xml"
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
