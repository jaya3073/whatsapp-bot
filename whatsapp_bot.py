import os
import re
import json
import urllib.request
import pandas as pd
from fastapi import FastAPI, Form, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "+14155238886")
OWNER_WHATSAPP = os.getenv("OWNER_WHATSAPP", "+918500701521")
SHEET_WEBHOOK_URL = os.getenv("SHEET_WEBHOOK_URL", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
OLX_LINK = os.getenv("OLX_LINK", "https://www.olx.in/profile/129751503")
YOUTUBE_LINK = os.getenv("YOUTUBE_LINK", "https://youtube.com/@shivahouserentalagency745/shorts")
PROPERTIES_FILE = os.getenv("PROPERTIES_FILE", "properties.xlsx")

opted_out = set()
sessions = {}

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
    f"🎬 YouTube: {YOUTUBE_LINK}"
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


def send(to, body):
    try:
        client.messages.create(
            from_=f"whatsapp:{WHATSAPP_FROM}",
            to=f"whatsapp:{to}",
            body=body
        )
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


def handle_owner_reply(owner_phone, text):
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


def load_properties():
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
        "Telugu, English, Tanglish — client ఏ భాషలో రాస్తే అదే భాషలో సమాధానం ఇవ్వు.\n"
        "నీకు గత సంభాషణ జ్ఞాపకం ఉంటుంది — client ఇంతకు ముందు చెప్పిన వివరాలు గుర్తుంచుకుని ముందుకు సాగి; మళ్ళీ అదే ప్రశ్న అడగవద్దు.\n\n"
        "సంభాషణ దశలు:\n"
        "1. మొదట client పేరు అడుగు, తర్వాత: ఎంత మంది ఉంటారు? ఫ్యామిలీనా బ్యాచిలర్స్? ఎంత rent budget?\n"
        "2. Budget తెలియగానే → వెంటనే ఆ budget లోపు ఉన్న ఉత్తమ ఇళ్లు 3 ని 🔗 links తో పంపు (కింద లిస్ట్ నుంచే).\n"
        "3. ఆ తర్వాత ఫీజు వివరాలు చెప్పు:\n"
        "   • ఏజెన్సీ కమిషన్: మొదటి నెల అద్దెపై ₹5,000 మాత్రమే.\n"
        "   • ₹8,000 లోపు అద్దె ఇళ్లకు: కమిషన్ ₹4,000 మాత్రమే.\n"
        "   • ఇళ్లు చూపించే ముందు ₹800 visiting fee 💸 (కమిషన్ నుండి తగ్గించబడుతుంది ✂️).\n"
        "4. తర్వాత: మిగిలిన ఇళ్లు (30+ ads) మా OLX profile లో చూడండి: " + OLX_LINK + "\n"
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


@app.post("/whatsapp")
async def whatsapp_webhook(From: str = Form(...), Body: str = Form(...)):
    phone = From.replace("whatsapp:", "")
    text = (Body or "").strip()

    if phone in opted_out or text.lower() in ("stop", "unsubscribe"):
        opted_out.add(phone)
        send(phone, "మీరు ఇకపై మా నుంచి messages పొందరు. ధన్యవాదాలు. 🙏")
        return Response(str(MessagingResponse()), media_type="text/xml")

    if phone == OWNER_WHATSAPP and text.lower().startswith("reply"):
        handle_owner_reply(phone, text)
        return Response(str(MessagingResponse()), media_type="text/xml")

    log_chat("IN", phone, text)

    reply = ask_gemini(phone, text)
    send(phone, reply if reply else FALLBACK)

    return Response(str(MessagingResponse()), media_type="text/xml")
