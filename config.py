"""
Configuration for the Burmese Dubbing Web Application.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Folders ---
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")
TEMP_FOLDER = os.path.join(BASE_DIR, "temp")

# --- Video Processing ---
CHUNK_DURATION_SECONDS = 300  # 5 minutes per chunk
MAX_FILE_SIZE_MB = 4096       # 4 GB max upload
ALLOWED_EXTENSIONS = {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv"}

# --- Audio Transcription ---
AUDIO_EXTENSIONS = {"mp3", "wav", "ogg", "flac", "m4a", "wma", "aac", "webm"}
TRANSCRIBE_CHUNK_SECONDS = 300  # 5 minutes per chunk for transcription

# --- Whisper STT ---
WHISPER_MODEL = "base"        # Options: tiny, base, small, medium, large-v3
WHISPER_DEVICE = "auto"       # "auto" will use GPU if available, else CPU
WHISPER_COMPUTE_TYPE = "int8" # "float16" for GPU, "int8" for CPU

# --- Translation ---
TRANSLATION_SOURCE = "auto"   # Auto-detect source language
TRANSLATION_TARGET = "my"     # Burmese (Myanmar)

# --- Edge TTS ---
# TTS_VOICE = "my-MM-ThihaNeural"  # Male Burmese neural voice
TTS_VOICE = "en-AU-WilliamMultilingualNeural"  # Male 2 neural voice
TTS_RATE = "+0%"                  # Speech rate adjustment
TTS_VOLUME = "+0%"                # Volume adjustment
TTS_MAX_SPEEDUP_PERCENT = 40      # Max speedup rate for TTS generation when text is too long
STRETCH_VIDEO_TO_FIT_AUDIO = True # Enable/disable video stretching (slowing down) to fit longer audio


# --- Flask ---
SECRET_KEY = os.urandom(24).hex()
MAX_CONTENT_LENGTH = MAX_FILE_SIZE_MB * 1024 * 1024

# Ensure directories exist
for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, TEMP_FOLDER]:
    os.makedirs(folder, exist_ok=True)
