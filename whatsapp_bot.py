# whatsapp_bot.py — Shiva House Rental Agency (v6: strict sequential questions)
#
# CHANGES IN v5 (compared to v4):
#   1. WHATSAPP BUTTONS (quick replies): the 5 questions are now asked as
#      clickable buttons via the Twilio Content API:
#        (1) Language:  తెలుగు / English / हिंदी
#        (2) Budget:    ₹3,000-5,000 / ₹5,000-8,000 / ₹8,000-12,000
#        (3) Family or Bachelors
#        (4) Members:   1 / 2 / 3
#        (5) Name:     optional (free text or Skip button)
#      Clients can still type or send a voice note in any language —
#      Gemini extraction fills the same fields, buttons are just easier.
#   2. Numbered text fallback: if Content API templates cannot be created
#      or sending fails, the same questions go out as numbered menus.
#   3. Language is fixed at the start via buttons (multi-language bug fix).
#   4. Recommended: set GEMINI_MODEL=gemini-3.8-flash in Render env.
#   NOTE: requires twilio>=9 in requirements.txt (for Content API).
#
# WhatsApp bot with guided conversation flow, Gemini-powered understanding,
# voice note support, bachelors filtering, tiered fees, email alerts,
# duplicate-message protection, owner REPLY command, and opt-out.
#
# CHANGES IN v4 (compared to v3):
#   1. MULTI-LANGUAGE: bot now replies in Telugu, English, Hindi, or Kannada
#      (text + voice note). Gemini detects the client's language from each
#      message (e.g. "I don't understand Telugu" -> switches to English).
#   2. REPLY command fixed: 10-digit Indian numbers are now correctly sent
#      to +91XXXXXXXXXX (before, "9704231053" became +970... = Palestine!).
#      Spaces in the number are allowed. Owner gets success/failure feedback.
#   3. NO REPEATS: full package (houses + fees + links) is sent only ONCE
#      per client. Later messages get a short reply with contact numbers.
#      If they ask for houses again, only the house list is re-sent
#      (never the fees/links again).
#   4. OWNER_WHATSAPP2 env var: a second owner number can also use REPLY.
#   5. Sessions saved to sessions.json (best-effort) to survive restarts.
#
# Required environment variables (set in Render -> Environment):
#   TWILIO_ACCOUNT_SID
#   TWILIO_AUTH_TOKEN
#   TWILIO_WHATSAPP_FROM       (e.g. +14155238886)
#   OWNER_WHATSAPP             (e.g. +918074915644)
#   OWNER_WHATSAPP2            (optional second owner number, e.g. +918500701521)
#   GEMINI_API_KEY
#   GEMINI_MODEL               (recommended: gemini-2.5-flash)
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
OWNER_WHATSAPP2 = os.getenv("OWNER_WHATSAPP2", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
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

LANGS = ("te", "en", "hi", "kn")


def _gemini_model_list():
    """Models to try, in order: configured model first, then fallbacks."""
    models = []
    for m in [GEMINI_MODEL, "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]:
        if m and m not in models:
            models.append(m)
    return models


# ─── Multi-language message texts ─────────────────────────────────────

CONTACT_NUMBERS = "Shiva 8500701521, 8074915644"

GREETING = {
    "te": (
        "నమస్కారం! శివ హౌస్ రెంటల్ ఏజెన్సీ 🏡\n"
        "దయచేసి కింది వివరాలు పంపండి:\n"
        "1. మీ పేరు\n"
        "2. ఎంతమంది ఉంటారు\n"
        "3. ఎంత రెంట్లో చూస్తున్నారు\n"
        "4. ఫ్యామిలీనా / బ్యాచిలర్స్?\n\n"
        "voice note ద్వారా కూడా నాతో మాట్లాడవచ్చు మీ భాషలో 🎙️"
    ),
    "en": (
        "Hello! Shiva House Rental Agency 🏡\n"
        "Please send these details:\n"
        "1. Your name\n"
        "2. How many people will stay\n"
        "3. Your budget (rent)\n"
        "4. Family or Bachelors?\n\n"
        "You can also talk to me by voice note in your language 🎙️"
    ),
    "hi": (
        "नमस्ते! शिवा हाउस रेंटल एजेंसी 🏡\n"
        "कृपया ये जानकारी भेजें:\n"
        "1. आपका नाम\n"
        "2. कितने लोग रहेंगे\n"
        "3. आपका बजट (किराया)\n"
        "4. फैमिली या बैचलर्स?\n\n"
        "आप अपनी भाषा में वॉइस नोट भी भेज सकते हैं 🎙️"
    ),
    "kn": (
        "ನಮಸ್ಕಾರ! ಶಿವ ಹೌಸ್ ರೆಂಟಲ್ ಏಜೆನ್ಸಿ 🏡\n"
        "ದಯವಿಟ್ಟು ಈ ಮಾಹಿತಿಯನ್ನು ಕಳುಹಿಸಿ:\n"
        "1. ನಿಮ್ಮ ಹೆಸರು\n"
        "2. ಎಷ್ಟು ಜನ ಇರುತ್ತಾರೆ\n"
        "3. ನಿಮ್ಮ ಬಜೆಟ್ (ಬಾಡಿಗೆ)\n"
        "4. ಫ್ಯಾಮಿಲಿ ಅಥವಾ ಬ್ಯಾಚುಲರ್ಸ್?\n\n"
        "ನಿಮ್ಮ ಭಾಷೆಯಲ್ಲಿ ವಾಯ್ಸ್ ನೋಟ್ ಕಳುಹಿಸಬಹುದು 🎙️"
    ),
}

FOOTER = {
    "te": (
        f"\n\nWebsite: {WEBSITE_LINK}\n"
        f"WhatsApp Channel: {CHANNEL_LINK}\n"
        f"OLX Ads: {OLX_LINK}\n"
        f"YouTube: {YOUTUBE_LINK}\n\n"
        f"📞Contact : {CONTACT_NUMBERS} "
        "(OLX links open కావాలంటే ఈ నంబర్లను మీ phone contacts లో save చేసుకోండి) ✅"
    ),
    "en": (
        f"\n\nWebsite: {WEBSITE_LINK}\n"
        f"WhatsApp Channel: {CHANNEL_LINK}\n"
        f"OLX Ads: {OLX_LINK}\n"
        f"YouTube: {YOUTUBE_LINK}\n\n"
        f"📞Contact: {CONTACT_NUMBERS} "
        "(please save these numbers in your phone contacts to open the OLX links) ✅"
    ),
    "hi": (
        f"\n\nWebsite: {WEBSITE_LINK}\n"
        f"WhatsApp Channel: {CHANNEL_LINK}\n"
        f"OLX Ads: {OLX_LINK}\n"
        f"YouTube: {YOUTUBE_LINK}\n\n"
        f"📞संपर्क: {CONTACT_NUMBERS} "
        "(OLX लिंक खोलने के लिए इन नंबरों को अपने फोन कॉन्टैक्ट में सेव करें) ✅"
    ),
    "kn": (
        f"\n\nWebsite: {WEBSITE_LINK}\n"
        f"WhatsApp Channel: {CHANNEL_LINK}\n"
        f"OLX Ads: {OLX_LINK}\n"
        f"YouTube: {YOUTUBE_LINK}\n\n"
        f"📞ಸಂಪರ್ಕ: {CONTACT_NUMBERS} "
        "(OLX ಲಿಂಕ್ ತೆರೆಯಲು ಈ ಸಂಖ್ಯೆಗಳನ್ನು ನಿಮ್ಮ ಫೋನ್ ಸಂಪರ್ಕಗಳಲ್ಲಿ ಉಳಿಸಿ) ✅"
    ),
}

OPTED_OUT_MSG = {
    "te": "మీను మా సర్వీస్ నుంచి opt-out అయ్యారు. ✅ మళ్ళీ ఏమైనా అడగాలంటే 'hi' అని మెసేజ్ పంపండి.",
    "en": "You have been opted out of our service. ✅ To talk again, just send 'hi'.",
    "hi": "आप हमारी सेवा से बाहर हो गए हैं। ✅ फिर बात करने के लिए 'hi' भेजें।",
    "kn": "ನೀವು ನಮ್ಮ ಸೇವೆಯಿಂದ ಹೊರಗಿದ್ದೀರಿ. ✅ ಮತ್ತೆ ಮಾತನಾಡಲು 'hi' ಕಳುಹಿಸಿ.",
}

SHORT_REPLY = {
    "te": (
        "మీ వివరాలు నమోదయ్యాయి ✅\n"
        "ఫీజు వివరాలు మరియు లింక్స్ ముందే పంపాం.\n"
        "ఇంకేదైనా సమాచారం కావాలంటే అడగండి. "
        f"డైరెక్ట్ మాట కావాలంటే: {CONTACT_NUMBERS}"
    ),
    "en": (
        "Your details are saved ✅\n"
        "Fees details and links were already sent earlier.\n"
        "Ask me if you need anything else. "
        f"To talk directly: {CONTACT_NUMBERS}"
    ),
    "hi": (
        "आपकी जानकारी दर्ज हो गई है ✅\n"
        "फीस और लिंक पहले ही भेज दिए गए हैं।\n"
        "कुछ और चाहिए तो पूछें। "
        f"सीधे बात करने के लिए: {CONTACT_NUMBERS}"
    ),
    "kn": (
        "ನಿಮ್ಮ ವಿವರಗಳು ದಾಖಲಾಗಿವೆ ✅\n"
        "ಶುಲ್ಕ ವಿವರ ಮತ್ತು ಲಿಂಕ್‌ಗಳನ್ನು ಈಗಾಗಲೇ ಕಳುಹಿಸಲಾಗಿದೆ.\n"
        "ಬೇರೇನಾದರೂ ಬೇಕಿದ್ದರೆ ಕೇಳಿ. "
        f"ನೇರವಾಗಿ ಮಾತನಾಡಲು: {CONTACT_NUMBERS}"
    ),
}

HOUSE_HEADER = {
    "te": "మీ బడ్జెట్ ప్రకారం కింది ఇళ్లు అందుబాటులో ఉన్నాయి:\n\n",
    "en": "According to your budget, these houses are available:\n\n",
    "hi": "आपके बजट के अनुसार ये घर उपलब्ध हैं:\n\n",
    "kn": "ನಿಮ್ಮ ಬಜೆಟ್‌ಗೆ ಅನುಗುಣವಾಗಿ ಈ ಮನೆಗಳು ಲಭ್ಯವಿವೆ:\n\n",
}

HOUSE_NONE = {
    "te": "మీ బడ్జెట్ ప్రకారం ప్రస్తుతం ఇళ్లు అందుబాటులో లేవు. త్వరలో అప్డేట్ చేస్తాం.",
    "en": "No houses available in your budget right now. We will update soon.",
    "hi": "आपके बजट में अभी कोई घर उपलब्ध नहीं है। हम जल्द अपडेट करेंगे।",
    "kn": "ನಿಮ್ಮ ಬಜೆಟ್‌ನಲ್ಲಿ ಸದ್ಯಕ್ಕೆ ಮನೆಗಳಿಲ್ಲ. ಶೀಘ್ರದಲ್ಲೇ ಅಪ್‌ಡೇಟ್ ಮಾಡುತ್ತೇವೆ.",
}

GREET_LINE = {
    "te": "నమస్కారం {name}! 🏡\n\n",
    "en": "Hello {name}! 🏡\n\n",
    "hi": "नमस्ते {name}! 🏡\n\n",
    "kn": "ನಮಸ್ಕಾರ {name}! 🏡\n\n",
}

MISSING_LABELS = {
    "te": {"name": "మీ పేరు", "count": "ఎంతమంది ఉంటారు", "budget": "ఎంత రెంట్లో చూస్తున్నారు", "ftype": "ఫ్యామిలీనా / బ్యాచిలర్స్", "ask": "దయచేసి ఇంకా ఈ వివరాలు పంపండి:\n"},
    "en": {"name": "Your name", "count": "How many people will stay", "budget": "Your budget (rent)", "ftype": "Family or Bachelors?", "ask": "Please also send these details:\n"},
    "hi": {"name": "आपका नाम", "count": "कितने लोग रहेंगे", "budget": "आपका बजट (किराया)", "ftype": "फैमिली या बैचलर्स?", "ask": "कृपया ये जानकारी भी भेजें:\n"},
    "kn": {"name": "ನಿಮ್ಮ ಹೆಸರು", "count": "ಎಷ್ಟು ಜನ ಇರುತ್ತಾರೆ", "budget": "ನಿಮ್ಮ ಬಜೆಟ್ (ಬಾಡಿಗೆ)", "ftype": "ಫ್ಯಾಮಿಲಿ ಅಥವಾ ಬ್ಯಾಚುಲರ್ಸ್?", "ask": "ದಯವಿಟ್ಟು ಈ ಮಾಹಿತಿಯನ್ನು ಸಹ ಕಳುಹಿಸಿ:\n"},
}

MISSING_SINGLE = {
    "te": {"name": "మీ పేరు ఏమిటి?", "count": "ఎంతమంది ఉంటారు?", "budget": "ఎంత రెంట్లో చూస్తున్నారు?", "ftype": "ఫ్యామిలీనా / బ్యాచిలర్స్?"},
    "en": {"name": "What is your name?", "count": "How many people will stay?", "budget": "What is your budget?", "ftype": "Family or Bachelors?"},
    "hi": {"name": "आपका नाम क्या है?", "count": "कितने लोग रहेंगे?", "budget": "आपका बजट कितना है?", "ftype": "फैमिली या बैचलर्स?"},
    "kn": {"name": "ನಿಮ್ಮ ಹೆಸರು ಏನು?", "count": "ಎಷ್ಟು ಜನ ಇರುತ್ತಾರೆ?", "budget": "ನಿಮ್ಮ ಬಜೆಟ್ ಎಷ್ಟು?", "ftype": "ಫ್ಯಾಮಿಲಿ ಅಥವಾ ಬ್ಯಾಚುಲರ್ಸ್?"},
}


def get_fees_message(budget, lang="te"):
    """Return the fees message based on budget threshold, in the client's language."""
    if budget >= 8000:
        fees = {
            "te": (
                "మేము ఓనర్స్ కాదు, రెంటల్ ఏజెన్సీ. మాది service ఉంటుంది. "
                "మీ బడ్జెట్ లో 3 or 5 houses చూపిస్తాము. "
                "ఇల్లు ఇప్పిస్తాము. ఇప్పించినందుకు ఫీజు ఛార్జ్ చేస్తాము. "
                "5000 ఉంటుంది. 5000 లో ఇనిషియల్ గా 800 తీసుకుంటాము. "
                "రూమ్స్ అన్నీ చూపిస్తాము. మీరు రూమ్ కి అడ్వాన్స్ ఇచ్చేటప్పుడు 4200 ఇవ్వాల్సి ఉంటుంది. "
                "Total 5000.\n"
                "Note: మీరు ఇచ్చే 800 కి 1 month validity ఉంటుంది. మీకు ఇల్లు దొరికేవరకు."
            ),
            "en": (
                "We are a rental agency, not owners. "
                "We will show you 3 or 5 houses within your budget. "
                "We charge a service fee for getting you the house: "
                "Rs.5000 total. Rs.800 is collected initially. "
                "We show you all the rooms. The remaining Rs.4200 is due "
                "when you pay the advance for the room. Total 5000.\n"
                "Note: The Rs.800 you pay has 1 month validity, until you find a house."
            ),
            "hi": (
                "हम मालिक नहीं, रेंटल एजेंसी हैं। "
                "आपके बजट में 3 या 5 घर दिखाएंगे। "
                "घर दिलाने के लिए सर्विस फी लगती है: "
                "कुल ₹5000। शुरू में ₹800 लेते हैं। "
                "सारे कमरे दिखाते हैं। बाकी ₹4200 कमरे का अग्रिम (एडवांस) "
                "देते समय देना होगा। कुल 5000।\n"
                "नोट: ₹800 की वैधता 1 महीने की है, तब तक जब तक आपको घर मिल जाए।"
            ),
            "kn": (
                "ನಾವು ಮಾಲೀಕರಲ್ಲ, ರೆಂಟಲ್ ಏಜೆನ್ಸಿ. "
                "ನಿಮ್ಮ ಬಜೆಟ್‌ನಲ್ಲಿ 3 ಅಥವಾ 5 ಮನೆಗಳನ್ನು ತೋರಿಸುತ್ತೇವೆ. "
                "ಮನೆ ದೊರಕಿಸಿದ್ದಕ್ಕೆ ಸರ್ವಿಸ್ ಶುಲ್ಕ ಇರುತ್ತದೆ: "
                "ಒಟ್ಟು ₹5000. ಆರಂಭದಲ್ಲಿ ₹800 ತೆಗೆದುಕೊಳ್ಳುತ್ತೇವೆ. "
                "ಎಲ್ಲಾ ಕೋಣೆಗಳನ್ನು ತೋರಿಸುತ್ತೇವೆ. ಉಳಿದ ₹4200 ಅನ್ನು ಕೋಣೆಯ ಅಡ್ವಾನ್ಸ್ "
                "ನೀಡುವಾಗ ನೀಡಬೇಕು. ಒಟ್ಟು 5000.\n"
                "ಗಮನಿಸಿ: ₹800 ಗೆ 1 ತಿಂಗಳ ಮಾನ್ಯತೆ ಇದೆ, ನಿಮಗೆ ಮನೆ ಸಿಗುವವರೆಗೆ."
            ),
        }
    else:
        fees = {
            "te": (
                "మేము ఓనర్స్ కాదు, రెంటల్ ఏజెన్సీ. మాది service ఉంటుంది. "
                "మీ బడ్జెట్ లో 3 or 5 houses చూపిస్తాము. "
                "ఇల్లు ఇప్పిస్తాము. ఇప్పించినందుకు ఫీజు ఛార్జ్ చేస్తాము. "
                "4000 ఉంటుంది. First 800 pay చెయ్యాలి. రూమ్స్ అన్నీ చూపిస్తాము. "
                "తర్వాత మీరు room కి advance ఇచ్చేటప్పుడు 3200.\n"
                "Note: మీరు ఇచ్చే 800 కి 1 month validity ఉంటుంది. మీకు ఇల్లు దొరికేవరకు."
            ),
            "en": (
                "We are a rental agency, not owners. "
                "We will show you 3 or 5 houses within your budget. "
                "We charge a service fee for getting you the house: "
                "Rs.4000 total. First you pay Rs.800. "
                "We show you all the rooms. Then, when you pay the advance "
                "for the room, you pay the remaining Rs.3200.\n"
                "Note: The Rs.800 you pay has 1 month validity, until you find a house."
            ),
            "hi": (
                "हम मालिक नहीं, रेंटल एजेंसी हैं। "
                "आपके बजट में 3 या 5 घर दिखाएंगे। "
                "घर दिलाने के लिए सर्विस फी लगती है: "
                "कुल ₹4000। पहले ₹800 देना है। "
                "सारे कमरे दिखाते हैं। फिर कमरे का अग्रिम देते समय बाकी ₹3200 दें।\n"
                "नोट: ₹800 की वैधता 1 महीने की है, तब तक जब तक आपको घर मिल जाए।"
            ),
            "kn": (
                "ನಾವು ಮಾಲೀಕರಲ್ಲ, ರೆಂಟಲ್ ಏಜೆನ್ಸಿ. "
                "ನಿಮ್ಮ ಬಜೆಟ್‌ನಲ್ಲಿ 3 ಅಥವಾ 5 ಮನೆಗಳನ್ನು ತೋರಿಸುತ್ತೇವೆ. "
                "ಮನೆ ದೊರಕಿಸಿದ್ದಕ್ಕೆ ಸರ್ವಿಸ್ ಶುಲ್ಕ ಇರುತ್ತದೆ: "
                "ಒಟ್ಟು ₹4000. ಮೊದಲು ₹800 ನೀಡಬೇಕು. "
                "ಎಲ್ಲಾ ಕೋಣೆಗಳನ್ನು ತೋರಿಸುತ್ತೇವೆ. ನಂತರ ಕೋಣೆಯ ಅಡ್ವಾನ್ಸ್ ನೀಡುವಾಗ ಉಳಿದ ₹3200 ನೀಡಿ.\n"
                "ಗಮನಿಸಿ: ₹800 ಗೆ 1 ತಿಂಗಳ ಮಾನ್ಯತೆ ಇದೆ, ನಿಮಗೆ ಮನೆ ಸಿಗುವವರೆಗೆ."
            ),
        }
    return fees.get(lang, fees["te"])


VOICE_FEES_HIGH = {
    "te": (
        "మా సర్వీస్ ఫీజు మొత్తం 5000 రూపాయలు. "
        "అందులో మొదట 800 రూపాయలు ఇవ్వాలి. "
        "మిగిలిన 4200 రూపాయలు ఇల్లు దొరికిన తర్వాత "
        "అడ్వాన్స్ ఇచ్చేటప్పుడు ఇవ్వాలి. "
        "ఈ 800 రూపాయలకి ఒక నెల వాలిడిటీ ఉంటుంది, "
        "మీకు ఇల్లు దొరికేవరకు."
    ),
    "en": (
        "Our service fee is 5000 rupees total. "
        "First you pay 800 rupees. "
        "The remaining 4200 rupees is due when you pay the advance "
        "after finding the house. "
        "The 800 rupees has one month validity, "
        "until you find a house."
    ),
    "hi": (
        "हमारी सर्विस फी कुल 5000 रुपये है। "
        "पहले 800 रुपये देना है। "
        "बाकी 4200 रुपये घर मिलने के बाद "
        "अग्रिम देते समय देना है। "
        "इन 800 रुपये की वैधता एक महीने की है, "
        "घर मिलने तक।"
    ),
    "kn": (
        "ನಮ್ಮ ಸರ್ವಿಸ್ ಶುಲ್ಕ ಒಟ್ಟು 5000 ರೂಪಾಯಿ. "
        "ಮೊದಲು 800 ರೂಪಾಯಿ ನೀಡಬೇಕು. "
        "ಉಳಿದ 4200 ರೂಪಾಯಿ ಮನೆ ಸಿಕ್ಕ ನಂತರ "
        "ಅಡ್ವಾನ್ಸ್ ನೀಡುವಾಗ ನೀಡಬೇಕು. "
        "ಈ 800 ರೂಪಾಯಿಗೆ ಒಂದು ತಿಂಗಳ ಮಾನ್ಯತೆ ಇದೆ, "
        "ಮನೆ ಸಿಗುವವರೆಗೆ."
    ),
}

VOICE_FEES_LOW = {
    "te": (
        "మా సర్వీస్ ఫీజు మొత్తం 4000 రూపాయలు. "
        "అందులో మొదట 800 రూపాయలు ఇవ్వాలి. "
        "మిగిలిన 3200 రూపాయలు ఇల్లు దొరికిన తర్వాత "
        "అడ్వాన్స్ ఇచ్చేటప్పుడు ఇవ్వాలి. "
        "ఈ 800 రూపాయలకి ఒక నెల వాలిడిటీ ఉంటుంది, "
        "మీకు ఇల్లు దొరికేవరకు."
    ),
    "en": (
        "Our service fee is 4000 rupees total. "
        "First you pay 800 rupees. "
        "The remaining 3200 rupees is due when you pay the advance "
        "after finding the house. "
        "The 800 rupees has one month validity, "
        "until you find a house."
    ),
    "hi": (
        "हमारी सर्विस फी कुल 4000 रुपये है। "
        "पहले 800 रुपये देना है। "
        "बाकी 3200 रुपये घर मिलने के बाद "
        "अग्रिम देते समय देना है। "
        "इन 800 रुपये की वैधता एक महीने की है, "
        "घर मिलने तक।"
    ),
    "kn": (
        "ನಮ್ಮ ಸರ್ವಿಸ್ ಶುಲ್ಕ ಒಟ್ಟು 4000 ರೂಪಾಯಿ. "
        "ಮೊದಲು 800 ರೂಪಾಯಿ ನೀಡಬೇಕು. "
        "ಉಳಿದ 3200 ರೂಪಾಯಿ ಮನೆ ಸಿಕ್ಕ ನಂತರ "
        "ಅಡ್ವಾನ್ಸ್ ನೀಡುವಾಗ ನೀಡಬೇಕು. "
        "ಈ 800 ರೂಪಾಯಿಗೆ ಒಂದು ತಿಂಗಳ ಮಾನ್ಯತೆ ಇದೆ, "
        "ಮನೆ ಸಿಗುವವರೆಗೆ."
    ),
}

# Keywords that mean "show me the houses/links again"
HOUSE_KEYWORDS = [
    "ఇల్లు", "ఇళ్లు", "లింక్", "లింక్స్", "చూపించు",
    "house", "houses", "link", "links", "room", "rooms", "show",
    "घर", "लिंक", "कमरा",
    "ಮನೆ", "ಲಿಂಕ್", "ಕೋಣೆ",
]


# ─── Session Management ──────────────────────────────────────────────

SESSIONS_FILE = "sessions.json"
sessions = {}          # phone -> {name, count, budget, family_type, completed, opted_out, notified, lang, full_sent}
recent_sids = {}       # phone -> last MessageSid (dedup)
audio_store = {}


def load_sessions():
    global sessions
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                sessions = json.load(f)
            print(f"Loaded {len(sessions)} sessions from disk")
    except Exception as e:
        print("Session load error:", e)


def save_sessions():
    try:
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False)
    except Exception:
        pass


load_sessions()


def get_session(phone):
    if phone not in sessions:
        sessions[phone] = {
            "name": None,
            "count": None,
            "budget": None,
            "family_type": None,
            "completed": False,
            "opted_out": False,
            "notified": False,
            "lang": "te",
            "full_sent": False,
        }
    # ensure new keys exist on old sessions
    s = sessions[phone]
    s.setdefault("lang", "te")
    s.setdefault("full_sent", False)
    s.setdefault("opted_out", False)
    s.setdefault("notified", False)
    return s


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
    """Call Gemini API for text understanding or audio transcription.

    Retries on failure and automatically falls back to other models.
    """
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

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
        },
    }

    last_err = None
    for model in _gemini_model_list():
        for attempt in (1, 2):
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model}:generateContent?key={GEMINI_API_KEY}"
            )
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as res:
                    result = json.loads(res.read().decode("utf-8"))

                text = (
                    result.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                if text.strip():
                    return text.strip()
                last_err = "empty response"
            except Exception as e:
                last_err = e
                print(f"Gemini error ({model}, attempt {attempt}): {e}")
                time.sleep(1)

    print(f"All Gemini models failed. Last error: {last_err}")
    return None


def extract_info_with_gemini(text, session):
    """Extract name, count, budget, accommodation_type AND language."""
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
        '  "accommodation_type": "family" or "bachelors"\n'
        '  "lang": "te" or "en" or "hi" or "kn" -- the language the client '
        "wants to converse in. te=Telugu, en=English, hi=Hindi, kn=Kannada. "
        "If the client asks to change language (for example 'I don't "
        "understand Telugu, speak English'), set lang to the requested "
        "language. Romanized Telugu (Telugu typed in English letters) "
        "should be \"te\".\n\n"
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
    if "bachelor" in lower:
        info["accommodation_type"] = "bachelors"
    elif "family" in lower:
        info["accommodation_type"] = "family"

    # Count: look for patterns like "3 members", "2 people", "4 మంది"
    count_match = re.search(r"(\d+)\s*(?:members?|people|మంದಿ|persons?|ಜನ)", lower)
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
        "Kannada, or English. Return ONLY the transcribed text, nothing else."
    )

    result = call_gemini(prompt, audio_b64=audio_b64, audio_mime=mime_type)
    return result if result else ""


# ─── Language detection ──────────────────────────────────────────────

def detect_language(text):
    """Script-based language detection (fallback when Gemini is unavailable).

    Returns "te", "hi", "kn" based on script, or None if unclear
    (latin text -> let the session language stay as-is).
    """
    telugu_chars = sum(1 for c in text if "\u0c00" <= c <= "\u0c7f")
    hindi_chars = sum(1 for c in text if "\u0900" <= c <= "\u097f")
    kannada_chars = sum(1 for c in text if "\u0c80" <= c <= "\u0cff")
    if telugu_chars > 3:
        return "te"
    if hindi_chars > 3:
        return "hi"
    if kannada_chars > 3:
        return "kn"
    return None


# ─── WhatsApp Send ────────────────────────────────────────────────────

def send(to, body, media_url=None):
    """Send a WhatsApp message. Returns True on success, False on failure."""
    if not client:
        print("Twilio client is not configured.")
        return False
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
        return True
    except Exception as e:
        print(f"Send error to {to}: {e}")
        return False


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


# ─── Owner helpers ───────────────────────────────────────────────────

def _digits(number):
    return re.sub(r"\D", "", number or "")


def is_owner(number):
    """Check if the sender is one of the owner numbers (last 10 digits match)."""
    n = _digits(number)
    if len(n) < 10:
        return False
    for candidate in (OWNER_WHATSAPP, OWNER_WHATSAPP2):
        c = _digits(candidate)
        if len(c) >= 10 and n[-10:] == c[-10:]:
            return True
    return False


def normalize_client_number(raw):
    """Normalize a client number typed in the REPLY command.

    10-digit Indian mobile (e.g. 9704231053) -> +919704231053
    """
    digits = _digits(raw)
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    if len(digits) == 11 and digits.startswith("0"):
        return "+91" + digits[1:]
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    if len(digits) >= 12:
        return "+" + digits
    # Too short / unclear — return as-is with +
    return "+" + digits


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
                return "NO_HOUSES_BACHELORS"

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


BACHELORS_NONE = {
    "te": "మీ అవసరానికి బ్యాచిలర్స్ అలవ్ అయ్యే ఇళ్లు ప్రస్తుతం అందుబాటులో లేవు. త్వరలో అప్డేట్ చేస్తాం.",
    "en": "No bachelor-friendly houses are available right now. We will update soon.",
    "hi": "बैचलर्स के लिए घर अभी उपलब्ध नहीं हैं। हम जल्द अपडेट करेंगे।",
    "kn": "ಬ್ಯಾಚುಲರ್‌ಗೆ ಮನೆಗಳು ಸದ್ಯಕ್ಕೆ ಲಭ್ಯವಿಲ್ಲ. ಶೀಘ್ರದಲ್ಲೇ ಅಪ್‌ಡೇಟ್ ಮಾಡುತ್ತೇವೆ.",
}


# ─── Text-to-Speech ──────────────────────────────────────────────────

voice_map = {
    "te": "te-IN-ShrutiNeural",
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
    "kn": "kn-IN-SapnaNeural",
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

def ask_missing_fields(session, lang="te"):
    """Ask for whichever of the 4 fields are still missing."""
    labels = MISSING_LABELS.get(lang, MISSING_LABELS["te"])
    singles = MISSING_SINGLE.get(lang, MISSING_SINGLE["te"])

    missing = []
    if not session.get("name"):
        missing.append("name")
    if not session.get("count"):
        missing.append("count")
    if not session.get("budget"):
        missing.append("budget")
    if not session.get("family_type"):
        missing.append("ftype")

    if not missing:
        return None

    if len(missing) == 4:
        return GREETING.get(lang, GREETING["te"])

    if len(missing) == 1:
        return singles[missing[0]]

    parts = []
    for i, m in enumerate(missing, 1):
        parts.append(f"{i}. {labels[m]}")
    return labels["ask"] + "\n".join(parts)


def build_complete_response(session):
    """Build the single combined message with properties + fees + footer."""
    lang = session.get("lang", "te")
    budget = session.get("budget", 6000)
    family_type = session.get("family_type", "family")
    name = session.get("name", "")

    properties_text = quick_property_search(budget, family_type)
    fees_msg = get_fees_message(budget, lang)

    # Greeting with name
    greeting_line = GREET_LINE.get(lang, GREET_LINE["te"]).format(
        name=name if name else ""
    )

    # Property results
    if properties_text == "NO_HOUSES_BACHELORS":
        props_section = BACHELORS_NONE.get(lang, BACHELORS_NONE["te"]) + "\n\n"
    elif properties_text:
        props_section = (
            HOUSE_HEADER.get(lang, HOUSE_HEADER["te"])
            + f"{properties_text}\n"
        )
    else:
        props_section = HOUSE_NONE.get(lang, HOUSE_NONE["te"]) + "\n\n"

    # Combine everything into ONE message
    full_response = (
        greeting_line
        + props_section
        + fees_msg
        + FOOTER.get(lang, FOOTER["te"])
    )

    return full_response


def build_house_only_response(session):
    """Houses list WITHOUT fees and WITHOUT footer links (for repeat requests)."""
    lang = session.get("lang", "te")
    budget = session.get("budget", 6000)
    family_type = session.get("family_type", "family")

    properties_text = quick_property_search(budget, family_type)
    if properties_text == "NO_HOUSES_BACHELORS":
        return BACHELORS_NONE.get(lang, BACHELORS_NONE["te"])
    if properties_text:
        return (
            HOUSE_HEADER.get(lang, HOUSE_HEADER["te"])
            + f"{properties_text}\n"
        )
    return HOUSE_NONE.get(lang, HOUSE_NONE["te"])


def build_voice_reply(session):
    """Build a voice-friendly version (no links, titles, or phone numbers)."""
    lang = session.get("lang", "te")
    budget = session.get("budget", 6000)
    name = session.get("name", "")
    name_part = f" {name}" if name else ""

    fees_text = (VOICE_FEES_HIGH if budget >= 8000 else VOICE_FEES_LOW).get(
        lang, VOICE_FEES_HIGH["te"]
    )

    intro = {
        "te": (
            f"నమస్కారం{name_part}! మీ బడ్జెట్ ప్రకారం కొన్ని ఇళ్లు దొరికాయి. "
            "వివరాలు మీకు మెసేజ్ లో పంపాం, చూడండి. "
        ),
        "en": (
            f"Hello{name_part}! We found some houses as per your budget. "
            "Details are in the message, please check. "
        ),
        "hi": (
            f"नमस्ते{name_part}! आपके बजट के अनुसार कुछ घर मिल गए हैं। "
            "विवरण मैसेज में भेजा है, देख लीजिए। "
        ),
        "kn": (
            f"ನಮಸ್ಕಾರ{name_part}! ನಿಮ್ಮ ಬಜೆಟ್‌ಗೆ ಅನುಗುಣವಾಗಿ ಕೆಲವು ಮನೆಗಳು ಸಿಕ್ಕಿವೆ. "
            "ವಿವರಗಳನ್ನು ಮೆಸೇಜ್‌ನಲ್ಲಿ ಕಳುಹಿಸಿದ್ದೇವೆ, ನೋಡಿ. "
        ),
    }.get(lang, "")

    outro = {
        "te": " ఏవైనా ప్రశ్నలు ఉంటే అడగండి.",
        "en": " Any questions, just ask.",
        "hi": " कोई सवाल हो तो पूछिए।",
        "kn": " ಯಾವುದೇ ಪ್ರಶ್ನೆ ಇದ್ದರೆ ಕೇಳಿ.",
    }.get(lang, "")

    return intro + fees_text + outro


# ─── FastAPI Web Routes ──────────────────────────────────────────────

@app.get("/")
def health():
    return {"status": "ok", "service": "Shiva House Rental Agency Bot v4"}


@app.get("/audio/{uid}")
def get_audio(uid: str):
    item = audio_store.get(uid)
    if not item:
        return Response(status_code=404, content="Audio not found")
    return Response(content=item["data"], media_type=item["mime"])



# ─── v5 Button Flow (Twilio Content API quick replies) ──────────────
CONTENT_SIDS_FILE = "content_sids.json"

try:
    with open(CONTENT_SIDS_FILE, "r", encoding="utf-8") as f:
        _content_sids = json.load(f)
except Exception:
    _content_sids = {}


def _save_content_sids():
    try:
        with open(CONTENT_SIDS_FILE, "w", encoding="utf-8") as f:
            json.dump(_content_sids, f, ensure_ascii=False)
    except Exception:
        pass


def ensure_button_template(key, buttons):
    """Create (once) a quick-reply content template, return its SID."""
    if key in _content_sids:
        return _content_sids[key]
    if not (TWILIO_SID and TWILIO_TOKEN):
        return None
    try:
        payload = {
            "friendly_name": key,
            "language": "en",
            "variables": {"1": "Choose an option"},
            "types": {"twilio/quick-reply": {
                "body": "{{1}}",
                "actions": [{"title": b, "id": b} for b in buttons],
            }},
        }
        auth = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
        request = urllib.request.Request(
            "https://content.twilio.com/v1/Content",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            content_sid = json.load(response)["sid"]
        _content_sids[key] = content_sid
        _save_content_sids()
        print(f"Created content template {key}: {content_sid}")
        return content_sid
    except AttributeError as e:
        print(f"Content API unavailable ({key}) - twilio SDK too old: {e}")
        return None
    except Exception as e:
        print(f"Content template create failed ({key}): {e}")
        return None


def send_buttons(to, body_text, buttons, key=None):
    """Send a quick-reply button message; fall back to numbered text."""
    sid = ensure_button_template(key or f"btn_{abs(hash(tuple(buttons)))}", buttons)
    if sid:
        try:
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            client.messages.create(
                from_=f"whatsapp:{WHATSAPP_FROM}",
                to=f"whatsapp:{to}",
                content_sid=sid,
                content_variables=json.dumps({"1": body_text}),
            )
            return True
        except Exception as e:
            print(f"Button send failed, using text fallback: {e}")
    numbered = body_text + "\n" + "\n".join(
        f"{i + 1}. {b}" for i, b in enumerate(buttons)
    )
    numbered += "\n(నంబర్ టైప్ చేసి పంపండి / type the number)"
    return send(to, numbered)


# ── Button sets and question texts ──
LANG_BUTTONS = ["తెలుగు", "English", "हिंदी"]
WELCOME_TEXT = (
    "నమస్కారం! 🏠 శివ హౌస్ రెంటల్ ఏజెన్సీకి స్వాగతం!\n"
    "Hello! Welcome to Shiva House Rental Agency!\n"
    "नमस्ते! शिवा हाउस रेंटल एजेंसी में आपका स्वागत है!\n\n"
    "మీ భాష ఎంచుకోండి / Choose your language / अपनी भाषा चुनें:"
)
BUDGET_BUTTONS = ["₹3,000-5,000", "₹5,000-8,000", "₹8,000-12,000"]
BUDGET_MAP = {
    "₹3,000-5,000": 4000,
    "₹5,000-8,000": 6000,
    "₹8,000-12,000": 10000,
}
FAMILY_BUTTONS = ["Family", "Bachelors"]
MEMBERS_BUTTONS = ["1", "2", "3"]
NAME_BUTTONS = ["Skip"]

BUDGET_Q = {
    "te": "మీ బడ్జెట్ రేంజ్ ఎంత? (నెలకి అద్దె)",
    "en": "What is your budget range? (monthly rent)",
    "hi": "आपकी बजट रेंज क्या है? (मासिक किराया)",
    "kn": "ನಿಮ್ಮ ಬಜೆಟ್ ವ್ಯಾಪ್ತಿ? (ಮಾಸಿಕ ಬಾಡಿಗೆ)",
}
FAMILY_Q = {
    "te": "మీరు ఫ్యామిలీనా / బ్యాచిలర్స్?",
    "en": "Family or Bachelors?",
    "hi": "फैमिली या बैचलर्स?",
    "kn": "ಫ್ಯಾಮಿಲಿ ಅಥವಾ ಬ್ಯಾಚುಲರ್ಸ್?",
}
MEMBERS_Q = {
    "te": "ఎంతమంది ఉంటారు?",
    "en": "How many members will stay?",
    "hi": "कितने लोग रहेंगे?",
    "kn": "ಎಷ್ಟು ಜನ ಇರುತ್ತಾರೆ?",
}
NAME_Q = {
    "te": "మీ పేరు ఏమిటి? (optional — టైప్ చేయండి లేదా Skip నొక్కండి)",
    "en": "What is your name? (optional — type it or tap Skip)",
    "hi": "आपका नाम क्या है? (optional — लिखें या Skip दबाएँ)",
    "kn": "ನಿಮ್ಮ ಹೆಸರು? (optional)",
}

LANG_WORDS = {
    "తెలుగు": "te", "telugu": "te",
    "english": "en", "ఇంగ్లీష్": "en", "ఆంగ్లం": "en",
    "हिंदी": "hi", "hindi": "hi",
}


def _q(d, session):
    return d.get(session.get("lang", "te"), d["te"])


def start_flow(to, session):
    # Clear earlier answers so every restarted enquiry follows all five steps.
    for field in ("name", "count", "budget", "family_type", "budget_range"):
        session[field] = None
    for field in ("lang_chosen", "name_skipped", "completed", "full_sent", "notified"):
        session[field] = False
    session["flow_version"] = 6
    session["flow_started"] = True
    session["stage"] = "lang"
    send_buttons(to, WELCOME_TEXT, LANG_BUTTONS, key="shruti_lang_v6")


def ask_budget(to, session):
    session["stage"] = "budget"
    q = _q(BUDGET_Q, session)
    send_buttons(to, q, BUDGET_BUTTONS, key="shruti_budget_v6")
    send_voice_background(to, q, session.get("lang", "te"))


def ask_family(to, session):
    session["stage"] = "family"
    q = _q(FAMILY_Q, session)
    send_buttons(to, q, FAMILY_BUTTONS, key="shruti_family_v6")
    send_voice_background(to, q, session.get("lang", "te"))


def ask_members(to, session):
    session["stage"] = "members"
    q = _q(MEMBERS_Q, session)
    send_buttons(to, q, MEMBERS_BUTTONS, key="shruti_members_v6")
    send_voice_background(to, q, session.get("lang", "te"))


def ask_name(to, session):
    session["stage"] = "name"
    q = _q(NAME_Q, session)
    send_buttons(to, q, NAME_BUTTONS, key="shruti_name_v6")
    send_voice_background(to, q, session.get("lang", "te"))


def interpret_answer(text, session):
    """Only consume an answer for the currently displayed question."""
    t = text.strip()
    low = t.lower()
    stage = session.get("stage", "")
    if stage == "lang":
        if low in ("1", "2", "3"):
            low = LANG_BUTTONS[int(low) - 1].lower()
        clean = re.sub(r"^[\d\s.\-)(]+", "", low).strip()
        if clean in LANG_WORDS:
            session["lang"] = LANG_WORDS[clean]
            session["lang_chosen"] = True
            return True
    elif stage == "budget":
        if low in ("1", "2", "3"):
            t = BUDGET_BUTTONS[int(low) - 1]
        if t in BUDGET_MAP:
            session["budget"] = BUDGET_MAP[t]
            session["budget_range"] = t
            return True
        amount = re.fullmatch(r"(?:₹|rs\.?\s*)?([0-9,]+)", low)
        if amount and int(amount[1].replace(",", "")) >= 1000:
            session["budget"] = int(amount[1].replace(",", ""))
            return True
    elif stage == "family":
        if low in ("1", "family", "ఫ్యామిలీ", "ఫామిలీ", "फैमिली"):
            session["family_type"] = "family"
            return True
        if low in ("2", "bachelors", "bachelor", "బ్యాచిలర్స్", "బేచిలర్స్", "बैचलर्स", "बैचलर"):
            session["family_type"] = "bachelors"
            return True
    elif stage == "members":
        match = re.fullmatch(r"([1-9][0-9]?)\s*(?:members?|people|persons?|మంది|लोग)?", low)
        if match:
            session["count"] = int(match[1])
            return True
    elif stage == "name":
        if low in ("1", "skip", "skip చేయండి", "no name", "వద్దు", "later"):
            session["name_skipped"] = True
            return True
        if t and any(c.isalpha() for c in t):
            session["name"] = t
            return True
    return False


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
        lang = session.get("lang", "te")

        # ── Owner REPLY command: REPLY <number> <message> ──
        if is_owner(from_number):
            m = re.match(
                r"^\s*REPLY\s+([+\d][\d\s\-+]{8,22}?)\s+(.+)$",
                message_text,
                re.IGNORECASE | re.DOTALL,
            )
            if m:
                target = normalize_client_number(m.group(1))
                reply_body = m.group(2).strip()
                ok = send(target, reply_body)
                if ok:
                    send(from_number, f"✅ Sent to {target}")
                else:
                    send(
                        from_number,
                        f"❌ Could not send to {target}. "
                        "Please check the number. Note: WhatsApp only "
                        "allows replies within 24 hours of the client's "
                        "last message to us.",
                    )
                save_sessions()
                return Response(
                    content=str(MessagingResponse()),
                    media_type="text/xml",
                )

        # ── Opt-out: client sends "stop" ──
        if message_text.lower().strip() in (
            "stop", "unsubscribe", "స్టాప్",
        ):
            session["opted_out"] = True
            send(from_number, OPTED_OUT_MSG.get(lang, OPTED_OUT_MSG["te"]))
            save_sessions()
            return Response(
                content=str(MessagingResponse()),
                media_type="text/xml",
            )

        # If they opted out earlier but messaged again, re-activate
        if session.get("opted_out"):
            session["opted_out"] = False

        # ── Handle voice note (audio attachment) ──
        if num_media > 0:
            media_url = data.get("MediaUrl0", "")
            media_type = data.get("MediaContentType0", "")
            print(f"Media: {media_type} from {from_number}")

            if "audio" in media_type:
                audio_data = download_twilio_media(media_url)
                if audio_data:
                    transcribed = transcribe_audio(audio_data, media_type)
                    if transcribed:
                        message_text = transcribed
                        print(f"Transcribed: {transcribed[:80]}")
                        log_chat("IN", from_number, f"[VOICE] {transcribed}")
                        detected = detect_language(transcribed)
                        if detected and not session.get("lang_chosen"):
                            session["lang"] = detected
                            lang = detected

        # Start/restart before interpreting input; greetings are never field values.
        restart = message_text.casefold().strip(" !.,") in (
            "hi", "hello", "hey", "start", "restart", "నమస్కారం", "నమస్తే", "नमस्ते",
        )
        if (restart or not session.get("flow_started")
                or (session.get("flow_version") != 6 and not session.get("full_sent"))):
            start_flow(from_number, session)
            save_sessions()
            return Response(content=str(MessagingResponse()), media_type="text/xml")

        if not session.get("full_sent"):
            interpret_answer(message_text, session)
        lang = session.get("lang", "te")

        # ── v5 staged flow: ask the next missing question with buttons ──
        if not session.get("lang_chosen"):
            session["stage"] = "lang"
            send_buttons(from_number, WELCOME_TEXT, LANG_BUTTONS, key="shruti_lang_v6")
        elif not session.get("budget"):
            ask_budget(from_number, session)
        elif not session.get("family_type"):
            ask_family(from_number, session)
        elif not session.get("count"):
            ask_members(from_number, session)
        elif not session.get("name") and not session.get("name_skipped"):
            ask_name(from_number, session)
        elif not session.get("full_sent"):
            # All fields collected — send complete response ONCE
            response_text = build_complete_response(session)
            send(from_number, response_text)
            session["completed"] = True
            session["full_sent"] = True

            # Voice note is COMPULSORY with every reply
            voice_text = build_voice_reply(session)
            send_voice_background(from_number, voice_text, lang)
        else:
            # Full package already sent before — NO REPEATS.
            lower_msg = message_text.lower()
            if any(k in lower_msg for k in HOUSE_KEYWORDS):
                # Client asked for houses again — resend house list only
                house_reply = build_house_only_response(session)
                send(from_number, house_reply)
                send_voice_background(from_number, house_reply, lang)
            else:
                # Short polite reply with contacts only
                short = SHORT_REPLY.get(lang, SHORT_REPLY["te"])
                send(from_number, short)
                send_voice_background(from_number, short, lang)

        # ── Email notification to owner (once per conversation) ──
        if not session.get("notified"):
            notify_owner_new_lead(from_number, message_text, session)
            session["notified"] = True

        save_sessions()
        return Response(
            content=str(MessagingResponse()),
            media_type="text/xml",
        )
    except Exception as e:
        print(f"Webhook error: {e}")
        return Response(
            content=str(MessagingResponse()),
            media_type="text/xml",
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
