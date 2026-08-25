import os
import re
import io
import json
import time
import uuid
import base64
import urllib.request
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
    "📌 OLX links WhatsApp లో open అవ్వాలంటే ఈ రెండు నంబర్లు మీ phone contacts లో save చేసుకోండి: "
    "8074915644, 8500701521 ✅"
)

opted_out = set()
sessions = {}
_cache = {}
audio_store = {}
stats = {"total": 0, "voice_in": 0, "voice_out": 0, "tts_engine": "none"}

FALLBACK = (
    "నమస్కారం! 🙏 శివ హౌస్ రెంటల్ ఏజెన్సీ 🏡\n"
    "మీ పేరు & వివరాలు రాయండి:\n"
    "• పేరు?\n"
    "• ఎంత మంది ఉంటారు?\n"
    "• ఫ్యామిలీనా / బ్యాచిలర్స్?\n"
    "• ఎంత rent budget?\n\n"
    "📞 శివ గారు (కాల్ & WhatsApp Chat Bot): 8500701521\n"
    "📱 Direct WhatsApp: 8074915644\n"
    f"🛒 OLX Ads: {OLX_LINK}\n"
    f"🎬 YouTube: {YOUTUBE_LINK}\n\n"
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
        urllib.request.urlopen(req, timeout=10)
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
        client.messages.create(**kwargs)
        if body:
            log_chat("OUT", to, body)
    except Exception as e:
        print("Send error:", e)


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
        urllib.request.urlopen(req, timeout=15)
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
            send(owner_phone, "ఫార్మాట్: REPLY <నంబర్> <మెసేజ్>\nఉదా: REPLY 8074915644 రేపు 10కి ఇల్లు చూపిస్తాను 🏠")
            return
        target = normalize_phone(parts[1])
        msg = parts[2].strip()
    if not target:
        send(owner_phone, "సరైన నంబర్ ఇవ్వండి 🙏\nఉదా: REPLY 8074915644 మీ మెసేజ్")
        return
    send(target, msg)
    send(owner_phone, f"✅ మీ మెసేజ్ పంపబడింది → {target}")


def fetch_df(key, url, max_age=120):
    now = time.time()
    if key in _cache and now - _cache[key][0] < max_age:
        return _cache[key][1]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as res:
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
        "నీవు voice notes వినగలవు 🎤 మరియు ప్రతి సమాధానం voice లో కూడా పంపుతావు 🔊.\n\n"
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
        "6. చివరగా: శివ గారికి కాల్ చేయండి 📞 8500701521 (WhatsApp Chat Bot); Direct WhatsApp 📱 8074915644.\n\n"
        "నియమాలు:\n"
        "- ఇళ్ల సిఫార్సులు ఇళ్ల లిస్ట్ నుంచే; internet/ఊహల ఆధారంగా కొత్త ఇళ్లు చెప్పవద్దు.\n"
        "- client video గురించి అడిగితే లేదా ఏరియా చెప్తే YouTube లిస్ట్ లో సరిపడే video link పంపు.\n"
        "- లిస్ట్ లో లేనిది అడిగితే OLX profile చూడమని చెప్పు.\n"
        "- ఇతర విషయాలు అడిగితే మళ్లీ ఇళ్ల వైపు మళ్లించు.\n"
        "- సమాధానాలు చిన్నగా, స్నేహపూర్వకంగా, emojis తో.\n\n"
        "ఇళ్ల లిస్ట్ (title | area | budget | link):\n" + properties_context() +
        "\n\nమా YouTube videos (title | link):\n" + (youtube_context() or "ఇంకా videos లిస్ట్ చేయలేదు.")
    )


def _call_gemini(payload):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.loads(res.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def download_twilio_media(url):
    auth = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read(), (res.headers.get_content_type() or "audio/mpeg")


def _phone_to_digits(m):
    digits = re.sub(r"\D", "", m.group(0))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return " " + " ".join(digits) + " "


def _phone_to_digits(m):
    digits = re.sub(r"\D", "", m.group(0))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return " " + " ".join(digits) + " "


def _phone_to_digits(m):
    digits = re.sub(r"\D", "", m.group(0))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return " " + " ".join(digits) + " "


def _number_to_digits(m):
    """Convert numbers to individual digits for TTS"""
    num = m.group(0)
    return ' '.join(list(num)) + ' '


def _phone_to_digits(m):
    digits = re.sub(r"\D", "", m.group(0))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return " " + " ".join(digits) + " "


def number_to_words(num):
    """Convert numbers to English words"""
    num = int(num)
    
    if num == 0:
        return "zero"
    
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", 
             "seventeen", "eighteen", "nineteen"]
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


def _phone_to_digits(m):
    """Convert phone numbers to individual digits"""
    digits = re.sub(r"\D", "", m.group(0))
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return " " + " ".join(digits) + " "


def number_to_words(num):
    """Convert numbers to English words"""
    num = int(num)
    
    if num == 0:
        return "zero"
    
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", 
             "seventeen", "eighteen", "nineteen"]
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


def number_to_words(num):
    """Convert numbers to English words"""
    num = int(num)
    
    if num == 0:
        return "zero"
    
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", 
             "seventeen", "eighteen", "nineteen"]
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


def number_to_words(num):
    """Convert numbers to English words"""
    num = int(num)
    
    if num == 0:
        return "zero"
    
    ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    teens = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", 
             "seventeen", "eighteen", "nineteen"]
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
    # Remove emojis first
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
    
    # Remove special characters
    text = re.sub(r"[*_#>`•]", "", text)
    
    # Remove URLs
    text = re.sub(r"https?://\S+", " ", text)
    
    # Remove unwanted words FIRST
    text = re.sub(r'Indian Rupees?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGST\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'₹', '', text)  # Just remove ₹ symbol, don't add "Rs"
    
    # SKIP phone numbers completely (remove them from TTS)
    text = re.sub(r"(?:\+?91[\s-]?)?[6-9](?:[\s-]?\d){9}", " ", text)
    
    # Convert other numbers to words
    def replace_numbers(match):
        num_str = match.group(0)
        try:
            num = int(num_str.replace(',', ''))
            return number_to_words(num)
        except:
            return num_str
    
    text = re.sub(r'\d{1,3}(?:,\d{3})*', replace_numbers, text)
    
    # Clean up extra spaces
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


def azure_tts(text, lang):
    if not AZURE_SPEECH_KEY:
        return None
    voice_map = {"te": "te-IN-ShrutiNeural", "hi": "hi-IN-SwaraNeural", "en": "en-IN-NeerjaNeural"}
    lang_map = {"te": "te-IN", "hi": "hi-IN", "en": "en-IN"}
    try:
        token_url = f"https://{AZURE_SPEECH_REGION}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
        token_req = urllib.request.Request(
            token_url, data=b"",
            headers={"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY, "Content-Length": "0"},
            method="POST",
        )
        with urllib.request.urlopen(token_req, timeout=10) as res:
            token = res.read().decode()
        ssml = (
            f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{lang_map[lang]}'>"
            f"<voice name='{voice_map[lang]}'><prosody rate='+20%'>{text}</prosody></voice></speak>"
        )
        tts_req = urllib.request.Request(
            f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1",
            data=ssml.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
                "User-Agent": "ShivaBot",
            },
            method="POST",
        )
        with urllib.request.urlopen(tts_req, timeout=60) as res:
            audio_data = res.read()
        if len(audio_data) > 1000:
            print(f"Azure TTS success: {voice_map[lang]}, {len(audio_data)} bytes")
            return audio_data
    except Exception as e:
        print(f"Azure TTS error: {e}")
    return None


def split_text_for_tts(text, max_chunk=500):
    if len(text) <= max_chunk:
        return [text]
    chunks = []
    current = ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= max_chunk:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    final_chunks = []
    for chunk in chunks:
        if len(chunk) > max_chunk:
            for i in range(0, len(chunk), max_chunk):
                final_chunks.append(chunk[i:i + max_chunk])
        else:
            final_chunks.append(chunk)
    return final_chunks


def make_voice_urls(text):
    text = clean_text_for_tts(text)
    lang = detect_lang(text)
    chunks = split_text_for_tts(text)
    print(f"TTS request: lang={lang}, chunks={len(chunks)}")
    urls = []
    for i, chunk in enumerate(chunks):
        audio = azure_tts(chunk, lang)
        engine = "azure"
        if not audio:
            try:
                buf = io.BytesIO()
                gTTS(text=chunk, lang=lang, slow=False).write_to_fp(buf)
                audio = buf.getvalue()
                engine = "gtts"
            except Exception as e:
                print(f"gTTS fail chunk {i}: {e}")
                continue
        uid = uuid.uuid4().hex
        audio_store[uid] = (audio, "audio/mpeg")
        stats["voice_out"] += 1
        stats["tts_engine"] = engine
        urls.append(f"{BASE_URL}/audio/{uid}")
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
        reply = _call_gemini({
            "systemInstruction": system,
            "contents": contents,
            "tools": [{"google_search": {}}],
        })
    except Exception as e:
        print("Gemini tools error:", e)
        try:
            reply = _call_gemini({"systemInstruction": system, "contents": contents})
        except Exception as e2:
            print("Gemini error:", e2)
    if reply:
        hist.append({"role": "model", "text": reply})
        sessions[phone] = hist[-12:]
    return reply


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


@app.get("/audio/{uid}")
def get_audio(uid: str):
    item = audio_store.get(uid)
    if not item:
        return Response(status_code=404)
    data, mime = item
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
    print(f"=== MSG #{stats['total']} from {phone}: media={NumMedia}, text={text[:50]} ===")

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
            for url in make_voice_urls(reply_text):
                send(phone, None, media_url=url)
                log_chat("OUT", phone, "🔊 (voice reply)")
        else:
            send(phone, FALLBACK)
            for url in make_voice_urls(FALLBACK):
                send(phone, None, media_url=url)
        return Response(str(MessagingResponse()), media_type="text/xml")

    log_chat("IN", phone, text)
    reply = ask_gemini(phone, text)
    final = reply if reply else FALLBACK
    send(phone, final)

    for url in make_voice_urls(final):
        send(phone, None, media_url=url)
        log_chat("OUT", phone, "🔊 (voice reply)")

    return Response(str(MessagingResponse()), media_type="text/xml")
