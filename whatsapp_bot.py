from elevenlabs import generate, set_api_key

# Add at top after imports
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel voice

def make_voice_url(text):
    text = short_for_tts(text)
    
    if ELEVENLABS_API_KEY:
        try:
            set_api_key(ELEVENLABS_API_KEY)
            audio = generate(
                text=text,
                voice=ELEVENLABS_VOICE_ID,
                model="eleven_multilingual_v2",
                stability=0.75,
                similarity_boost=0.75,
                style=0.0,
                use_speaker_boost=True
            )
            
            uid = uuid.uuid4().hex
            audio_bytes = b"".join(audio)
            audio_store[uid] = (audio_bytes, "audio/mpeg")
            print("ElevenLabs TTS success")
            return f"{BASE_URL}/audio/{uid}"
            
        except Exception as e:
            print(f"ElevenLabs failed: {e}")
    
    # Fallback to gTTS
    try:
        buf = io.BytesIO()
        gTTS(text=text, lang='te', tld='co.in', slow=False).write_to_fp(buf)
        uid = uuid.uuid4().hex
        audio_store[uid] = (buf.getvalue(), "audio/mpeg")
        print("gTTS fallback success")
        return f"{BASE_URL}/audio/{uid}"
    except Exception as e:
        print(f"gTTS also failed: {e}")
        raise e
