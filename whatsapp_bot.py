import os
import re
import io
import json
import time
import uuid
import base64
import urllib.request
import xml.sax.saxutils as saxutils
import pandas as pd
from gtts import gTTS
from fastapi import FastAPI, Form, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")

client = Client(TWILIO_SID, TWILIO_TOKEN)

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
    " OLX links WhatsApp లో open అవ్వాలంటే ఈ రెండు నంబర్లు మీ phone contacts లో save చేసుకోండి: "
    "8500701521, 8074915644 ✅"
)

opted_out = set()
sessions = {}
_cache = {}
audio_store = {}
stats = {"total": 0, "voice_in": 0, "voice_out": 0, "tts_engine": "none"}

FALLBACK = (
    "నమస్కారం!  శివ హౌస్ రెంటల్ ఏజెన్సీ 🏡\n"
    "మీ పేరు & వివరాలు రాయండి:\n"
    "• పేరు?\n"
    "• ఎంత మంది ఉంటారు?\n"
    "• ఫ్యామిలీనా / బ్యాచిలర్స్?\n"
    "• ఎంత rent budget?\n\n"
    " శివ గారిని సంప్రదించండి 📞 8500701521; Direct WhatsApp  8074915644\n"
    f" OLX Ads: {OLX_LINK}\n"
    f" YouTube: {YOUTUBE_LINK}\n\n"
    + CONTACTS_LINE
)


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
        urllib.request.urlopen(req, timeout=15)
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
    try:
        kwargs = {
            "from_": f"whatsapp:{WHATSAPP_FROM}",
            "to": f"whatsapp:{to}",
        }
        if body:
            kwargs["body"] = body
        if media_url:
            kwargs["media_url"] = [media_url]
            print(f"📤 Sending media_url: {media_url[:80]}...")

        message = client.messages.create(**kwargs)
        print(f"✅ Message sent: {message.sid}")

        if body:
            log_chat("OUT", to, body)
    except Exception as e:
        print(f"❌ Send error: {e}")
        print(f"   To: {to}")
        print(f"   Body: {body[:50] if body else 'None'}")
        print(f"   Media: {media_url[:80] if media_url else 'None'}")


def normalize_phone(num):
    digits = re.sub(r"\D", "", num)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 10:
        return "+91" + digits
    return None


def sheet_id_from_url(url):
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url or "")
    return m.group(1) if m else None


def append_row(sheet_url, cache_key, row):
    sid = sheet_id_from_url(sheet_url)
    if not sid or not SHEET_WRITE_URL:
        return False
    try:
        req = urllib.request.Request(
            SHEET_WRITE_URL,
            data=json.dumps({"id": sid, "row": row}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=20)
        _cache.pop(cache_key, None)
        return True
    except Exception as e:
        print("Write error:", e)
        return False


def handle_owner_command(phone, text):
    parts = [p.strip() for p in text.split("|")]
    if text.lower().startswith("addolx"):
        if len(parts) >= 5:
            ok = append_row(PROPERTIES_SHEET_URL, "props", [parts[1], parts[2], parts[3], parts[4]])
            send(phone, "✅ కొత్త OLX ad add అయింది! (2 నిమిషాల్లో clients కి కనిపిస్తుంది)" if ok else "❌ Add కాలేదు — SHEET_WRITE_URL env check చేయండి")
        else:
            send(phone, "ఫార్మాట్: ADDOLX | title | area | budget | link")
        return True
    if text.lower().startswith("addyt"):
        if len(parts) >= 3:
            ok = append_row(YOUTUBE_SHEET_URL, "yt", [parts[1], parts[2]])
            send(phone, "✅ కొత్త YouTube video add అయింది! (2 నిమిషాల్లో clients కి కనిపిస్తుంది)" if ok else "❌ Add కాలేదు — SHEET_WRITE_URL env check చేయండి")
        else:
            send(phone, "ఫార్మాట్: ADDYT | title | link")
        return True
    return False


def handle_owner_reply(owner_phone, text):
    m = re.match(r"(?is)^reply\s+(?:\+?91)?\s*((?:\d[\s-]?){10})", text)
    if m:
        target = "+91" + re.sub(r"\D", "", m.group(1))
        msg = text[m.end():].strip()
    else:
        parts = text.split(None, 2)
        if len(parts) < 3:
            send(owner_phone, "ఫార్మాట్: REPLY <నంబర్> <మెసేజ్>\nఉదా: REPLY 8074915644 రేపు 10కి ఇల్లు చూపిస్తాను ")
            return
        target = normalize_phone(parts[1])
        msg = parts[2].strip()
    if not target:
        send(owner_phone, "సరైన నంబర్ ఇవ్వండి \nఉదా: REPLY 8074915644 మీ మెసేజ్")
        return
    send(target, msg)
    send(owner_phone, f"✅ మీ మెసేజ్ పంపబడింది → {target}")


def fetch_df(key, url, max_age=120):
    now = time.time()
    if key in _cache and now - _cache[key][0] < max_age:
        return _cache[key][1]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as res:
            df = pd.read_csv(io.StringIO(res.read().decode("utf-8")))
        _cache[key] = (now, df)
        return df
    except Exception as e:
        print("CSV fetch error:", e)
        if key in _cache:
            return _cache[key][1]
        return None


def load_properties():
    if PROPERTIES_SHEET_URL:
        df = fetch_df("props", PROPERTIES_SHEET_URL)
        if df is not None and not df.empty:
            return df
    try:
        return pd.read_excel(PROPERTIES_FILE)
    except Exception:
        return None


def properties_context():
    df = load_properties()
    if df is None or df.empty:
        return "ప్రస్తుతం ఇళ్ల లిస్ట్ ఖాళీగా ఉంది."
    lines = []
    for _, r in df.iterrows():
        link = str(r.get('link', '')) if 'link' in df.columns else ''
        lines.append(f"{r['title']} | {r['area']} | ₹{r['budget']} | {link}")
    return "\n".join(lines)


def youtube_context():
    df = None
    if YOUTUBE_SHEET_URL:
        df = fetch_df("yt", YOUTUBE_SHEET_URL)
    if df is None or df.empty:
        try:
            df = pd.read_excel("youtube.xlsx")
        except Exception:
            return ""
    if df is None or df.empty:
        return ""
    lines = []
    for _, r in df.iterrows():
        lines.append(f"{r['title']} | {r['link']}")
    return "\n".join(lines)


def build_system():
    return (
        "నీవు 'శివ హౌస్ రెంటల్ ఏజెన్సీ' (హైదరాబాద్) WhatsApp AI assistant వు. "
        "Telugu, English, Tanglish, Hindi — client ఏ భాషలో మాట్లాడితే అదే భాషలో స్వచ్ఛంగా సమాధానం ఇవ్వు. "
        "Hindi client కి పూర్తి Hindi (Devanagari) లోనే — Telugu పదాలు కలపకు. Telugu client కి Telugu లోనే.\n"
        "నీకు గత సంభాషణ జ్ఞాపకం ఉంటుంది — client ఇంతకు ముందు చెప్పిన వివరాలు గుర్తుంచుకుని ముందుకు సాగి; మళ్ళీ అదే ప్రశ్న అడగవద్దు.\n"
        "నీవు voice notes వినగలవు మరియు ప్రతి సమాధానం voice లో కూడా పంపుతావు.\n"
        "**ముఖ్యం: మీరు type చేయకుండా నాతో voice note ద్వారా కూడా సంభాషించవచ్చు!**\n\n"
        "సంభాషణ దశలు:\n"
        "1. మొదట client పేరు అడుగు, తర్వాత: ఎంత మంది ఉంటారు? ఫ్యామిలీనా బ్యాచిలర్స్? ఎంత rent budget?\n"
        "2. Budget తెలియగానే → వెంటనే ఆ budget లోపు ఉన్న ఉత్తమ ఇళ్లు 3 ని 🔗 links తో పంపు (కింద లిస్ట్ నుంచే).\n"
        "3. ఆ తర్వాత ఫీజు వివరాలు చెప్పు:\n"
        "   • ఏజెన్సీ కమిషన్: మొదటి నెల అద్దెపై ₹5,000 మాత్రమే.\n"
        "   • ₹8,000 లోపు అద్దె ఇళ్లకు: కమిషన్ ₹4,000 మాత్రమే.\n"
        "   • 2BHK అడిగితే → కమిషన్ ₹6,000.\n"
        "   • ఇళ్లు చూపించే ముందు ₹800 visiting fee 💸 (కమిషన్ నుండి తగ్గించబడుతుంది ✂️).\n"
        "   • మా సర్వీస్: మీ బడ్జెట్ లో 3 లేదా 5 ఇళ్లు చూపిస్తాము. ఆ రోజు ఇల్లు set కాకపోతే, ఇల్లు దొరికేవరకు 1 month validity ఉంటుంది ఆ ₹800 కి.\n"
        "4. తర్వాత: మిగిలిన ఇళ్లు (30+ ads) మా OLX profile లో చూడండి: " + OLX_LINK + "\n"
        "   " + CONTACTS_LINE + "\n"
        "5. YouTube videos కూడా చూడండి — కానీ కొన్ని ఇళ్లు ఇప్పటికే rented out అయి ఉండవచ్చు: " + YOUTUBE_LINK + "\n"
        "6. చివరగా: శివ గారిని సంప్రదించండి 📞 8500701521; Direct WhatsApp  8074915644.\n\n"
        "నియమాలు:\n"
        "- ఇళ్ల సిఫార్సులు ఇళ్ల లిస్ట్ నుంచే; internet/హల ఆధారంగా కొత్త ఇళ్లు చెప్పవద్దు.\n"
        "- client video గురించి అడిగితే లేదా ఏరియా చెప్తే YouTube లిస్ట్ లో సరిపడే video link పంపు.\n"
        "- లిస్ట్ లో లేనిది అడిగితే OLX profile చూడమని చెప్పు.\n"
        "- ఇతర విషయాలు అడిగితే మళ్లీ ఇళ్ల వైపు మళ్లించు.\n"
        "- సమాధానాలు చిన్నగా, స్నేహపూర్వకంగా, emojis తో.\n\n"
        "ఇళ్ల లిస్ట్ (title | area | budget | link):\n" + properties_context() +
        "\n\nమా YouTube videos (title | link):\n" + (youtube_context() or "ఇంకా videos లిస్ట్ చేయలేదు.")
    )


def _call_gemini(payload, max_retries=3):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=45) as res:
                data = json.loads(res.read().decode("utf-8"))
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            print(f"Gemini attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise


def download_twilio_media(url):
    auth = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read(), (res.headers.get_content_type() or "audio/mpeg")


def number_to_words(num):
    num = int(num)
    if num == 0:
        return "zero"
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    def convert(n):
        if n < 10:
            return ones[n]
        elif n < 20:
            return teens[n-10]
        elif n < 100:
            return tens[n//10] + ("-" + ones[n%10] if n%10 != 0 else "")
        elif n < 1000:
            return ones[n//100] + " hundred" + (" and " + convert(n%100) if n%100 != 0 else "")
        elif n < 1000000:
            return convert(n//1000) + " thousand" + (" " + convert(n%1000) if n%1000 != 0 else "")
        else:
            return convert(n//1000000) + " million" + (" " + convert(n%1000000) if n%1000000 != 0 else "")
    return convert(num)


def clean_text_for_tts(text):
    emoji_pattern = re.compile(
        "["
        u"\U0001F600-\U0001F64F"
        u"\U0001F300-\U0001F5FF"
        u"\U0001F680-\U0001F6FF"
        u"\U0001F1E0-\U0001F1FF"
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        u"\U00002000-\U0000206F"
        "]+", flags=re.UNICODE)
    text = emoji_pattern.sub('', text)
    text = re.sub(r"[*_#>`•]", "", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r'Indian Rupees?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGST\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'₹', '', text)
    text = re.sub(r"(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}", " ", text)

    def replace_numbers(match):
        num_str = match.group(0)
        try:
            num = int(num_str.replace(',', ''))
            return number_to_words(num)
        except Exception:
            return num_str
    text = re.sub(r'\d{1,3}(?:,\d{3})*', replace_numbers, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_lang(text):
    hi_count = len(re.findall(r"[\u0900-\u097F]", text))
    te_count = len(re.findall(r"[\u0C00-\u0C7F]", text))
    if hi_count > te_count and hi_count > 3:
        return "hi"
    if te_count > 0:
        return "te"
    return "en"


def azure_tts_simple(text, lang="te"):
    """Generate speech using Azure Cognitive Services TTS REST API"""
    if not AZURE_SPEECH_KEY:
        print("⚠️ AZURE_SPEECH_KEY not set, skipping Azure TTS")
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
        with urllib.request.urlopen(token_req, timeout=15) as res:
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
        with urllib.request.urlopen(tts_req, timeout=20) as res:
            return res.read()
    except Exception as e:
        print(f"❌ Azure TTS error: {e}")
        return None


def gtts_fallback(text, lang):
    try:
        print(f"🔄 Using gTTS fallback for: {text[:50]}...")
        words = text.split()
        chunk_size = 50
        chunks = [' '.join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]

        all_audio = b""
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            tts = gTTS(text=chunk, lang=lang, slow=False)
            audio_buffer = io.BytesIO()
            tts.write_to_fp(audio_buffer)
            audio_buffer.seek(0)
            all_audio += audio_buffer.getvalue()
            print(f"✅ gTTS chunk {i+1}/{len(chunks)} success: {len(audio_buffer.getvalue())} bytes")

        print(f"✅ gTTS total success: {len(all_audio)} bytes")
        return all_audio
    except Exception as e:
        print(f"❌ gTTS error: {e}")
        return None


def split_text_for_tts(text, max_chunk=400):
    text = re.sub(r"(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}", "", text)
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

    intro_paragraphs, house_details, fees_info, contact_info, other_paragraphs = [], [], [], [], []

    for para in paragraphs:
        para_lower = para.lower()
        if any(kw in para_lower for kw in ['నమస్తే', 'hello', 'hi', 'welcome']):
            intro_paragraphs.append(para)
        elif any(kw in para_lower for kw in ['కమిషన్', 'fee', 'visiting', 'సర్వీస్', 'validity']):
            fees_info.append(para)
        elif any(kw in para_lower for kw in ['సంప్రదించండి', 'contact', 'whatsapp', 'chat bot', 'direct']):
            contact_info.append(para)
        elif any(kw in para_lower for kw in ['ఇల్లు', 'house', 'budget', 'BHK', 'area', 'option']):
            house_details.append(para)
        else:
            other_paragraphs.append(para)

    ordered_paragraphs = intro_paragraphs + house_details + fees_info + contact_info + other_paragraphs
    chunks, current_chunk = [], ""

    for para in ordered_paragraphs:
        separator = ".  " if current_chunk and para else ""
        combined = current_chunk + separator + para if current_chunk else para
        if len(combined) <= max_chunk:
            current_chunk = combined
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = para
    if current_chunk:
        chunks.append(current_chunk)
    return chunks if chunks else [text]


def make_voice_urls(text):
    """Generate voice URLs with Azure primary + gTTS fallback"""
    print(f"\n🎙️  TTS STARTED for text: {text[:100]}...")
    text = clean_text_for_tts(text)
    lang = detect_lang(text)
    chunks = split_text_for_tts(text)
    print(f"📊 TTS request: lang={lang}, chunks={len(chunks)}")

    urls = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            print(f"⚠️ Chunk {i} is empty, skipping")
            continue

        print(f" Generating voice for chunk {i+1}/{len(chunks)}...")

        audio = azure_tts_simple(chunk, lang)

        if not audio:
            print("⚠️ Azure failed, trying gTTS fallback...")
            audio = gtts_fallback(chunk, lang)

        if not audio:
            print(f"❌ Both Azure and gTTS failed for chunk {i}, skipping")
            continue

        uid = uuid.uuid4().hex
        audio_store[uid] = {
            'data': audio,
            'mime': "audio/mpeg",
            'created_at': time.time(),
            'text': chunk[:50] + "..."
        }

        url = f"{BASE_URL}/audio/{uid}"
        urls.append(url)
        print(f"✅ Voice URL {i+1} created: {url}")
        print(f"   Audio size: {len(audio)} bytes")
        print(f"   Stored in audio_store: {uid}")

    print(f"🎯 Total voice URLs generated: {len(urls)}")
    print(f"   URLs: {urls}\n")
    return urls


def parse_json_reply(raw):
    try:
        clean = raw.strip()
        if clean.startswith("```"):
            clean = clean.strip("`")
            if clean.startswith("json"):
                clean = clean[4:]
        obj = json.loads(clean)
        return obj.get("transcript", ""), obj.get("reply", raw)
    except Exception:
        return "", raw


def ask_gemini_audio(phone, audio_bytes, mime):
    hist = sessions.get(phone, [])
    instruction = (
        "ఇది client పంపిన voice note. ముందు దాన్ని transcript చేయి, "
        "తర్వాత assistant గా సమాధానం ఇవ్వు. "
        "voice note ఏ భాషలో ఉంటే సమాధానం కూడా అదే భాషలో స్వచ్ఛంగా ఇవ్వు "
        "(Hindi అయితే పూర్తి Hindi Devanagari లోనే, Telugu అయితే Telugu లోనే). "
        "JSON మాత్రమే: {\"transcript\": \"...\", \"reply\": \"...\"}"
    )
    parts = [
        {"text": instruction},
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(audio_bytes).decode()}},
    ]
    contents = [{"role": m["role"], "parts": [{"text": m["text"]}]} for m in hist]
    contents.append({"role": "user", "parts": parts})
    raw = _call_gemini({
        "systemInstruction": {"parts": [{"text": build_system()}]},
        "contents": contents,
    })
    return parse_json_reply(raw)


def ask_gemini(phone, user_text):
    if not GEMINI_API_KEY:
        return None
    hist = sessions.get(phone, [])
    hist.append({"role": "user", "text": user_text})
    hist = hist[-12:]
    sessions[phone] = hist
    contents = [{"role": m["role"], "parts": [{"text": m["text"]}]} for m in hist]
    system = {"parts": [{"text": build_system()}]}
    reply = None
    try:
        reply = _call_gemini({"systemInstruction": system, "contents": contents})
    except Exception as e:
        print("Gemini error:", e)
    if reply:
        hist.append({"role": "model", "text": reply})
        sessions[phone] = hist[-12:]
    return reply


def cleanup_old_audio(max_age_minutes=30):
    global audio_store
    now = time.time()
    old_uids = []

    for uid in list(audio_store.keys()):
        item = audio_store[uid]
        if 'created_at' in item:
            created_time = item['created_at']
            if (now - created_time) > (max_age_minutes * 60):
                old_uids.append(uid)

    for uid in old_uids:
        del audio_store[uid]
        print(f"️ Deleted old audio: {uid}")

    if old_uids:
        print(f" Cleaned up {len(old_uids)} old audio files")


def add_audio_to_store(text, lang="te"):
    text = clean_text_for_tts(text)
    chunks = split_text_for_tts(text)

    urls = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue

        audio = azure_tts_simple(chunk, lang)

        if not audio:
            audio = gtts_fallback(chunk, lang)

        if not audio:
            print(f"❌ Failed to generate audio for chunk {i}")
            continue

        uid = uuid.uuid4().hex
        audio_store[uid] = {
            'data': audio,
            'mime': "audio/mpeg",
            'created_at': time.time(),
            'text': chunk[:50] + "..."
        }

        url = f"{BASE_URL}/audio/{uid}"
        urls.append(url)
        print(f"✅ Added audio: {uid}, size: {len(audio)} bytes")

    return urls


@app.get("/")
def health():
    return {"status": "ok"}


@app.get("/debug")
def debug():
    return {
        "status": "running",
        "stats": stats,
        "azure_key_set": bool(AZURE_SPEECH_KEY),
        "azure_region": AZURE_SPEECH_REGION,
        "gemini_key_set": bool(GEMINI_API_KEY),
        "audio_files": len(audio_store),
    }


@app.get("/debug/audio")
def debug_audio():
    total_size = sum(len(item['data']) for item in audio_store.values())
    oldest = min((item.get('created_at', 0) for item in audio_store.values()), default=0)

    return {
        "total_files": len(audio_store),
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "oldest_file_age_minutes": round((time.time() - oldest) / 60, 1) if oldest else 0,
        "recent_files": list(audio_store.keys())[-5:]
    }


@app.get("/audio/{uid}")
def get_audio(uid: str):
    item = audio_store.get(uid)
    if not item:
        print(f"❌ Audio not found: {uid}")
        print(f"   Available audio files: {list(audio_store.keys())[:5]}")
        return Response(
            status_code=404,
            content=f"Audio not found. Available: {len(audio_store)} files"
        )

    data = item['data']
    mime = item['mime']
    created_at = item.get('created_at', 0)

    if time.time() - created_at > 3600:
        print(f"️ Audio expired: {uid}")
        del audio_store[uid]
        return Response(status_code=410, content="Audio expired")

    print(f"✅ Serving audio: {uid}, size: {len(data)} bytes")
    return Response(content=data, media_type=mime)


@app.post("/whatsapp")
async def whatsapp_webhook(
    From: str = Form(...),
    Body: str = Form(""),
    NumMedia: str = Form("0"),
    MediaUrl0: str = Form(""),
):
    phone = From.replace("whatsapp:", "")
    text = (Body or "").strip()
    stats["total"] += 1
    print(f"\n{'='*60}")
    print(f"=== MSG #{stats['total']} from {phone}: media={NumMedia}, text={text[:50]} ===")
    print(f"{'='*60}")

    if phone in opted_out or text.lower() in ("stop", "unsubscribe"):
        opted_out.add(phone)
        send(phone, "మీరు ఇకపై messages పొందరు. 🙏")
        return Response(str(MessagingResponse()), media_type="text/xml")

    if phone == OWNER_WHATSAPP:
        if text.lower().startswith(("addolx", "addyt")):
            log_chat("IN", phone, text)
            handle_owner_command(phone, text)
            return Response(str(MessagingResponse()), media_type="text/xml")
        if text.lower().startswith("reply"):
            log_chat("IN", phone, text)
            handle_owner_reply(phone, text)
            return Response(str(MessagingResponse()), media_type="text/xml")

    try:
        n_media = int(NumMedia or 0)
    except Exception:
        n_media = 0

    if n_media > 0 and MediaUrl0:
        stats["voice_in"] += 1
        transcript, reply_text = "", None
        try:
            audio_bytes, mime = download_twilio_media(MediaUrl0)
            transcript, reply_text = ask_gemini_audio(phone, audio_bytes, mime)
            print(f"Voice transcript: {(transcript or 'empty')[:80]}")
        except Exception as e:
            print(f"Voice note error: {e}")
        log_chat("IN", phone, f"🎤 {transcript or '(voice note)'}")
        if reply_text:
            hist = sessions.get(phone, [])
            hist.append({"role": "user", "text": f"(voice) {transcript or 'voice note'}"})
            hist.append({"role": "model", "text": reply_text})
            sessions[phone] = hist[-12:]

            send(phone, reply_text)
            print("️ Generating voice notes for reply...")
            voice_urls = make_voice_urls(reply_text)
            if voice_urls:
                for url in voice_urls:
                    send(phone, None, media_url=url)
                    log_chat("OUT", phone, " (voice reply)")
            else:
                print("⚠️ No voice URLs generated!")
        else:
            send(phone, FALLBACK)
            voice_urls = make_voice_urls(FALLBACK)
            if voice_urls:
                for url in voice_urls:
                    send(phone, None, media_url=url)
        return Response(str(MessagingResponse()), media_type="text/xml")

    log_chat("IN", phone, text)
    reply = ask_gemini(phone, text)
    final = reply if reply else FALLBACK
    send(phone, final)

    print("️ Generating voice notes for text reply...")
    voice_urls = make_voice_urls(final)
    if voice_urls:
        for url in voice_urls:
            send(phone, None, media_url=url)
            log_chat("OUT", phone, " (voice reply)")
    else:
        print("⚠️ No voice URLs generated for text reply!")

    return Response(str(MessagingResponse()), media_type="text/xml")
