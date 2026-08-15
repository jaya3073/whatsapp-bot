import os
import re
import json
import urllib.request
import pandas as pd
from datetime import datetime
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
LEADS_FILE = os.getenv("LEADS_FILE", "whatsapp_leads.xlsx")
PROPERTIES_FILE = os.getenv("PROPERTIES_FILE", "properties.xlsx")
SHEET_WEBHOOK_URL = os.getenv("SHEET_WEBHOOK_URL", "")
OLX_LINK = os.getenv("OLX_LINK", "https://www.olx.in/profile/129751503")
YOUTUBE_LINK = os.getenv("YOUTUBE_LINK", "https://youtube.com/@shivahouserentalagency745/shorts")
CATALOGUE_LINK = os.getenv("CATALOGUE_LINK", "https://wa.me/c/918500701521")

sessions = {}
opted_out = set()

MENU = (
    "నమస్కారం! 🙏 శివ హౌస్ రెంటల్ ఏజెన్సీకి స్వాగతం. 🏡\n\n"
    "1️⃣ ఇల్లు వెతకడం ప్రారంభించండి\n"
    "2️⃣ అందుబాటులో ఉన్న ఇళ్లు చూడండి 🏘️\n"
    "3️⃣ మీటింగ్ బుక్ చేయండి 🚇\n"
    "4️⃣ శివ గారితో మాట్లాడాలి ☎️\n"
    "5️⃣ మా OLX / YouTube / Catalogue పేజీలు 🔗\n\n"
    "దయచేసి సంఖ్య టైప్ చేయండి (1-5)"
)


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

    s = sessions.get(phone)
    if s is None:
        s = {"state": "MENU", "data": {}}
        sessions[phone] = s

    route(phone, s, text)

    return Response(str(MessagingResponse()), media_type="text/xml")


def route(phone, s, text):
    st = s["state"]
    d = s["data"]

    if text.lower() in ("menu", "hi", "hello", "start", "హాయ్", "మెనూ"):
        s["state"] = "MENU"
        s["data"] = {}
        send(phone, MENU)
        return

    if st == "MENU":
        if text == "1":
            s["state"] = "NAME"
            s["data"] = {}
            send(phone, "మీ పేరు ఏమిటి? 🙋")
        elif text == "2":
            send_listings(phone, d)
        elif text == "3":
            s["state"] = "MEETING"
            send(
                phone,
                "మీటింగ్ కోసం మీకు అనువైన రోజు & సమయం టైప్ చేయండి.\n"
                "(ఉదా: రేపు ఉదయం 11 గంటలకు)\n"
                "📍 కూకట్‌పల్లి మెట్రో స్టేషన్"
            )
        elif text == "4":
            send(
                phone,
                "శివ గారిని సంప్రదించండి:\n"
                "📞 8500701521 (కాల్/వాట్సాప్)\n"
                "రాకముందు 10 నిమిషాల ముందు కాల్ చేయండి. 🕒"
            )
        elif text == "5":
            send(
                phone,
                "🔗 మా పేజీలు చూడండి:\n\n"
                f"🛒 OLX Ads:\n{OLX_LINK}\n\n"
                f"🎬 YouTube Shorts (ఇళ్ల వీడియోలు):\n{YOUTUBE_LINK}\n\n"
                f"🛍️ WhatsApp Catalogue:\n{CATALOGUE_LINK}"
            )
        else:
            send(phone, MENU)

    elif st == "NAME":
        d["name"] = text
        s["state"] = "STAY"
        send(phone, f"ధన్యవాదాలు {text}! 🙏\nమీరు సింగిల్‌గా ఉంటారా లేదా కుటుంబంతో ఉంటారా?\n(సింగిల్ / ఫ్యామిలీ)")

    elif st == "STAY":
        d["stay_type"] = text
        s["state"] = "OCC"
        send(phone, "ఎంత మంది ఆక్యుపెంట్స్ ఉంటారు?\n(సంఖ్య టైప్ చేయండి)")

    elif st == "OCC":
        d["occupants"] = text
        s["state"] = "AREA"
        send(phone, "మీకు ఇష్టమైన ఏరియా ఏది?\n(ఉదా: కూకట్‌పల్లి, మియాపూర్, నిజాంపేట్, కేపీహెచ్‌బీ)")

    elif st == "AREA":
        d["area"] = text
        s["state"] = "BUDGET"
        send(phone, "మీ బడ్జెట్ ఎంత?\n(రూపాయులలో టైప్ చేయండి, ఉదా: 10000)")

    elif st == "BUDGET":
        d["budget"] = text
        s["state"] = "MOVEIN"
        send(phone, "ఎప్పుడు ఇల్లు కావాలి?\n(ఈ వారం / ఈ నెల / తర్వాత)")

    elif st == "MOVEIN":
        d["move_in"] = text
        s["state"] = "CONFIRM"
        send(phone, confirm_text(d))

    elif st == "CONFIRM":
        if text.lower() in ("అవును", "yes", "ok", "సరే"):
            save_lead(    row = {    row = {
        "sheet": "Leads",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "phone": phone,
        "sheet": "Leads",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "phone": phone,)
            notify_owner(d)
            s["state"] = "MENU"
            s["data"] = {}
            send(phone, done_text(d))
        else:
            s["state"] = "NAME"
            s["data"] = {}
            send(phone, "సరే, మళ్ళీ ప్రారంభిద్దాం. 😊\nమీ పేరు ఏమిటి?")

    elif st == "MEETING":
        d["meeting_time"] = text
        save_meeting(    row = {
        "sheet": "Leads",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "phone": phone,)
        notify_owner_meeting(phone, d)
        s["state"] = "MENU"
        send(
            phone,
            "✅ మీటింగ్ నమోదైంది!\n"
            "📍 కూకట్‌పల్లి మెట్రో స్టేషన్\n"
            "రాకముందు 10 నిమిషాల ముందు కాల్ చేయండి: 8500701521 🕒"
        )


def confirm_text(d):
    return (
        "✅ మీ వివరాలు:\n"
        f"పేరు: {d.get('name','')}\n"
        f"రకం: {d.get('stay_type','')}\n"
        f"ఆక్యుపెంట్స్: {d.get('occupants','')}\n"
        f"ఏరియా: {d.get('area','')}\n"
        f"బడ్జెట్: ₹{d.get('budget','')}\n"
        f"మూవ్-ఇన్: {d.get('move_in','')}\n\n"
        "సరైనవేనా? (అవును / కాదు)"
    )


def done_text(d):
    return (
        f"ధన్యవాదాలు {d.get('name','')}! 🙏\n"
        "మీ వివరాలు నమోదయ్యాయి. ✅\n"
        "మీ బడ్జెట్‌కు సరిపడే ఇల్లు దొరికిన వెంటనే మీకు తెలియజేస్తాము. 🏡\n\n"
        "📍 మీటింగ్ పాయింట్: కూకట్‌పల్లి మెట్రో స్టేషన్\n"
        "📞 8500701521 (కాల్/వాట్సాప్)"
    )


def save_lead(d):
    row = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "name": d.get("name", ""),
        "stay_type": d.get("stay_type", ""),
        "occupants": d.get("occupants", ""),
        "area": d.get("area", ""),
        "budget": d.get("budget", ""),
        "move_in": d.get("move_in", ""),
        "source": "whatsapp"
    }
    push_to_sheet(row)
    try:
        if os.path.exists(LEADS_FILE):
            df = pd.read_excel(LEADS_FILE)
        else:
            df = pd.DataFrame()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_excel(LEADS_FILE, index=False)
    except Exception as e:
        print("Save error:", e)


def save_meeting(phone, d):
    row = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "phone": phone,
        "name": d.get("name", ""),
        "meeting_time": d.get("meeting_time", ""),
        "type": "meeting"
    }
    push_to_sheet(row)
    try:
        if os.path.exists(LEADS_FILE):
            df = pd.read_excel(LEADS_FILE)
        else:
            df = pd.DataFrame()
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_excel(LEADS_FILE, index=False)
    except Exception as e:
        print("Save error:", e)


def notify_owner(d):
    msg = (
        "🆕 కొత్త WhatsApp లీడ్:\n"
        f"పేరు: {d.get('name','')}\n"
        f"రకం: {d.get('stay_type','')}\n"
        f"ఆక్యుపెంట్స్: {d.get('occupants','')}\n"
        f"ఏరియా: {d.get('area','')}\n"
        f"బడ్జెట్: ₹{d.get('budget','')}\n"
        f"మూవ్-ఇన్: {d.get('move_in','')}"
    )
    send(OWNER_WHATSAPP, msg)


def notify_owner_meeting(phone, d):
    msg = (
        "📅 కొత్త మీటింగ్ బుకింగ్:\n"
        f"Phone: {phone}\n"
        f"పేరు: {d.get('name','')}\n"
        f"సమయం: {d.get('meeting_time','')}\n"
        "📍 కూకట్‌పల్లి మెట్రో స్టేషన్"
    )
    send(OWNER_WHATSAPP, msg)


def send_listings(phone, d):
    try:
        df = pd.read_excel(PROPERTIES_FILE)
    except Exception:
        send(
            phone,
            "ప్రస్తుతం లిస్ట్ అందుబాటులో లేదు. 🙏\n"
            "ఆప్షన్ 1 ఎంచుకుని మీ వివరాలు పంపండి, "
            "లేదా 5 నొక్కి OLX లో చూడండి."
        )
        return

    top = df.head(5)
    if top.empty:
        send(phone, "ప్రస్తుతం లిస్టులు లేవు. 😔\nఆప్షన్ 1 తో మీ వివరాలు పంపండి.")
        return

    lines = ["🏘️ అందుబాటులో ఉన్న ఇళ్లు:\n"]
    for _, r in top.iterrows():
        line = f"• {r['title']} | {r['area']} | ₹{r['budget']}"
        if 'link' in df.columns and str(r.get('link', '')).startswith('http'):
            line += f"\n  🔗 {r['link']}"
        lines.append(line)
    lines.append("\nనచ్చిందా? మీటింగ్ బుక్ చేయాలంటే 3, మొత్తం లిస్ట్ కోసం 5 టైప్ చేయండి.")
    send(phone, "\n".join(lines))
