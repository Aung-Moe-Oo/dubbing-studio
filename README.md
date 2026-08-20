# 🎙️ Myanmar Dubbing Studio — AI Video & Audio Dubbing

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.0+-000000?style=flat&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-Required-0078D7?style=flat&logo=ffmpeg&logoColor=white)](https://ffmpeg.org/)
[![Faster--Whisper](https://img.shields.io/badge/Faster--Whisper-STT-orange?style=flat)](https://github.com/SYSTRAN/faster-whisper)
[![Edge--TTS](https://img.shields.io/badge/Edge--TTS-Neural-blue?style=flat)](https://github.com/rany2/edge-tts)

**Myanmar Dubbing Studio** is an AI-powered web application and pipeline for automatic speech-to-text transcription, translation, text-to-speech synthesis, and synchronized video/audio dubbing into Burmese (Myanmar).

---

## ✨ Features

- ✍️ **Text-to-Speech (Type Text)**: Type or paste Burmese or foreign text to generate high-quality neural voice audio instantly.
- 📜 **SRT Subtitle Dubbing**: Upload `.srt` subtitle files to automatically generate timed Burmese voice tracks and sync with audio/video.
- 🎧 **Audio & Video Transcription**: Upload audio or video files to automatically transcribe speech to text using OpenAI Whisper models via `faster-whisper`.
- 🎬 **Automated Dubbing Pipeline**:
  - **FFmpeg Video/Audio Extraction**: Splits long media into manageable chunks.
  - **Speech Recognition (STT)**: Transcribes source speech using `faster-whisper`.
  - **Machine Translation**: Translates text into natural Burmese via `deep-translator`.
  - **Neural TTS Generation**: Synthesizes speech using `edge-tts` with adjustable voices and pitch/rate adjustments.
  - **Audio Sync & Alignment**: Automatically stretches or adjusts audio speed to match video timing seamlessly (`pydub` + `FFmpeg`).
- 🌐 **Modern Web Interface**: Clean, dark-themed responsive UI built with Flask, providing real-time Server-Sent Events (SSE) progress tracking, audio player preview, and voice selection.

---

## 📁 Project Structure

```
audio dubbing/
├── app.py              # Flask Web Server & API endpoints
├── pipeline.py         # Multi-stage dubbing pipeline processing engine
├── dubber.py           # Subtitle SRT parsing & audio timing alignment logic
├── config.py           # Application configuration (models, paths, thresholds)
├── voices.json         # Available Neural TTS voices configuration
├── requirements.txt    # Python dependencies list
├── run.bat             # One-click setup & launcher script for Windows
├── .gitignore          # Git ignore configuration
├── templates/          # HTML templates
│   └── index.html      # Main application interface
├── static/             # Frontend static assets
│   ├── style.css       # Custom UI styles (Glassmorphism / Dark Mode)
│   └── app.js          # Interactive frontend logic & SSE streaming
├── uploads/            # Directory for uploaded user files
├── temp/               # Temporary workspace directory for chunking & audio cache
└── outputs/            # Final generated audio, video, and transcription outputs
```

---

## ⚙️ Prerequisites & System Requirements

1. **Python**: Python 3.10 or higher.
2. **FFmpeg**: Must be installed and available in system `PATH`.
   - **Windows**: Download from [FFmpeg Official Website](https://ffmpeg.org/download.html) or install via `winget install ffmpeg` / `choco install ffmpeg`. Verify with `ffmpeg -version`.
   - **Linux**: `sudo apt update && sudo apt install ffmpeg`
   - **macOS**: `brew install ffmpeg`

---

## 🚀 Quick Start & Installation

### Option 1: One-Click Launcher (Windows)
Double-click `run.bat` or execute in command prompt:
```cmd
run.bat
```
This script will automatically check Python & FFmpeg, create a virtual environment (`venv`), install dependencies, and start the web server at `http://localhost:5000`.

### Option 2: Manual Setup

1. **Clone the Repository**:
   ```bash
   git clone <repository-url>
   cd "audio dubbing"
   ```

2. **Create and Activate Virtual Environment**:
   - **Windows**:
     ```cmd
     python -m venv venv
     venv\Scripts\activate
     ```
   - **Linux / macOS**:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch Application**:
   ```bash
   python app.py
   ```
   Open your browser and navigate to `http://localhost:5000`.

---

## ⚙️ Configuration Options (`config.py`)

You can customize models and processing settings in `config.py`:

| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| `WHISPER_MODEL` | `"base"` | Whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`) |
| `WHISPER_DEVICE` | `"auto"` | Hardware compute device (`"auto"`, `"cuda"`, `"cpu"`) |
| `WHISPER_COMPUTE_TYPE` | `"int8"` | Model quantization type (`"float16"` for GPU, `"int8"` for CPU) |
| `TRANSLATION_TARGET` | `"my"` | Target translation language (Burmese / Myanmar) |
| `TTS_VOICE` | `"en-AU-WilliamMultilingualNeural"` | Default TTS voice model |
| `CHUNK_DURATION_SECONDS`| `300` (5 mins) | Duration per video processing chunk |
| `MAX_FILE_SIZE_MB` | `4096` (4 GB) | Maximum file upload size |

---

## 🧹 File Cleanup & Output Management

- Input uploads are saved in `uploads/`.
- Intermediate audio processing and temporary files are stored in `temp/`.
- Final output videos, synthesized audio, and transcriptions are saved in `outputs/`.
- Temporary files and media outputs are excluded from version control via `.gitignore`.

---

## 📄 License

Distributed under the MIT License. See `LICENSE` for details.
