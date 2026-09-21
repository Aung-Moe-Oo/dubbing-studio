"""
Flask web server for the Burmese Dubbing application.
Provides REST API endpoints and serves the web UI.
"""

import os
import uuid
import json
import time
import logging
import asyncio
import threading
import re
import unicodedata
from queue import Queue
from flask import (
    Flask, render_template, request, jsonify, send_file,
    Response, stream_with_context
)

import config
from pipeline import DubbingPipeline, JobStatus, ChunkStatus

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# --- Flask App ---
app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

# Disable browser caching for static files during development
@app.after_request
def add_no_cache_headers(response):
    if 'static' in request.path:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# --- Job Management ---
jobs = {}           # job_id -> JobStatus
pipelines = {}      # job_id -> DubbingPipeline
job_queues = {}     # job_id -> Queue (for SSE events)
job_threads = {}    # job_id -> Thread
transcriptions = {} # job_id -> str (completed transcription text)


def is_myanmar_combining(char):
    """Check if character is a Unicode combining mark (vowels, medials, etc.)."""
    return unicodedata.category(char).startswith('M')


def split_text_to_chunks(text, target_size=5000):
    """
    Splits Burmese/general text into chunks of approximately target_size characters.
    Respects sentence boundaries (။, ၊, \n, spaces) and avoids splitting inside syllables.
    """
    if not text:
        return []

    # Split text by sentence/clause boundary delimiters and whitespace
    parts = re.split(r'([။၊\n\r\t ]+)', text)
    chunks = []
    current_chunk = []
    current_length = 0

    for part in parts:
        if not part:
            continue

        if current_length + len(part) > target_size and current_chunk:
            chunks.append("".join(current_chunk))
            current_chunk = []
            current_length = 0

        if len(part) > target_size:
            # If a single part exceeds target_size, split character-by-character
            # but ensure we don't split before a combining diacritic
            sub_start = 0
            while sub_start < len(part):
                sub_end = min(sub_start + target_size, len(part))
                while sub_end < len(part) and is_myanmar_combining(part[sub_end]):
                    sub_end += 1
                current_chunk.append(part[sub_start:sub_end])
                chunks.append("".join(current_chunk))
                current_chunk = []
                current_length = 0
                sub_start = sub_end
        else:
            current_chunk.append(part)
            current_length += len(part)

    if current_chunk:
        chunks.append("".join(current_chunk))

    return chunks


def allowed_file(filename):
    """Check if the file extension is allowed."""
    return "." in filename and \
           filename.rsplit(".", 1)[1].lower() in config.ALLOWED_EXTENSIONS


def status_to_dict(status: JobStatus) -> dict:
    """Convert JobStatus to serializable dict."""
    elapsed = time.time() - status.start_time if status.start_time else 0
    return {
        "job_id": status.job_id,
        "filename": status.filename,
        "total_chunks": status.total_chunks,
        "current_chunk": status.current_chunk,
        "stage": status.stage,
        "overall_progress": round(status.overall_progress, 1),
        "message": status.message,
        "error": status.error,
        "output_file": os.path.basename(status.output_file) if status.output_file else "",
        "original_output_file": os.path.basename(status.original_output_file) if status.original_output_file else "",
        "elapsed_seconds": round(elapsed),
        "chunks": [
            {
                "index": c.index,
                "stage": c.stage,
                "progress": round(c.progress, 1),
                "message": c.message,
                "error": c.error,
            }
            for c in status.chunks
        ]
    }


def run_pipeline(job_id: str, input_file: str, source_lang: str, srt_file: str = None, output_mode: str = "video", tts_voice: str = None, skip_translate: bool = False):
    """Run the dubbing pipeline in a background thread."""
    queue = job_queues.get(job_id)

    def progress_callback(status: JobStatus):
        """Push status updates to SSE queue."""
        if queue:
            queue.put(status_to_dict(status))

    pipeline = DubbingPipeline(
        job_id=job_id,
        input_file=input_file,
        source_lang=source_lang,
        progress_callback=progress_callback,
        srt_file=srt_file,
        output_mode=output_mode,
        tts_voice=tts_voice,
        skip_translate=skip_translate
    )
    pipelines[job_id] = pipeline

    try:
        output_path = pipeline.run()
        jobs[job_id] = pipeline.status
        # Send final status
        if queue:
            queue.put(status_to_dict(pipeline.status))
            queue.put("DONE")
    except Exception as e:
        jobs[job_id] = pipeline.status
        if queue:
            queue.put(status_to_dict(pipeline.status))
            queue.put("DONE")
        logger.error(f"Job {job_id} failed: {e}")


# ============================================================================
# Routes
# ============================================================================

@app.route("/")
def index():
    """Serve the main web UI."""
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload_video():
    """Handle SRT file upload and start audio dubbing pipeline."""
    if "subtitle" not in request.files:
        return jsonify({"error": "No subtitle (SRT) file provided"}), 400

    file = request.files["subtitle"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not file.filename.lower().endswith(".srt"):
        return jsonify({"error": "Invalid file type. Only SRT files are allowed."}), 400

    source_lang = request.form.get("source_lang", "auto")
    if source_lang not in ("auto", "en", "zh"):
        source_lang = "auto"

    tts_voice = request.form.get("tts_voice", "").strip() or None
    # Validate voice against voices.json
    if tts_voice:
        voices_path = os.path.join(config.BASE_DIR, "voices.json")
        try:
            with open(voices_path, "r", encoding="utf-8") as f:
                voices_data = json.load(f)
            valid_names = [v["ShortName"] for v in voices_data.get("voices", [])]
            if tts_voice not in valid_names:
                tts_voice = None  # Fall back to default
        except Exception:
            tts_voice = None

    output_mode = "audio"

    skip_translate = request.form.get("skip_translate", "").strip().lower() == "true"

    # Generate job ID and save file
    job_id = str(uuid.uuid4())[:8]
    saved_filename = f"{job_id}.srt"
    srt_path = os.path.join(config.UPLOAD_FOLDER, saved_filename)
    file.save(srt_path)

    logger.info(f"Uploaded SRT: {file.filename} -> {srt_path} (lang={source_lang}, voice={tts_voice or 'default'})")

    # Create SSE queue and start pipeline
    job_queues[job_id] = Queue()
    thread = threading.Thread(
        target=run_pipeline,
        args=(job_id, None, source_lang, srt_path, output_mode, tts_voice, skip_translate),
        daemon=True
    )
    job_threads[job_id] = thread
    thread.start()

    return jsonify({
        "job_id": job_id,
        "filename": file.filename,
        "message": "Audio generation started",
        "has_subtitle": True,
        "output_mode": output_mode
    })


@app.route("/api/status/<job_id>")
def job_status_sse(job_id):
    """Server-Sent Events endpoint for real-time progress updates."""
    queue = job_queues.get(job_id)
    if not queue:
        # Job might already be done, check jobs dict
        if job_id in jobs:
            status = jobs[job_id]
            data = json.dumps(status_to_dict(status))

            def single():
                yield f"data: {data}\n\n"

            return Response(single(), mimetype="text/event-stream")
        return jsonify({"error": "Job not found"}), 404

    def generate():
        while True:
            try:
                msg = queue.get(timeout=30)
                if msg == "DONE":
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break
                yield f"data: {json.dumps(msg)}\n\n"
            except Exception:
                # Send heartbeat to keep connection alive
                yield f": heartbeat\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


@app.route("/api/download/<job_id>")
def download_video(job_id):
    """Download the dubbed video."""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404

    status = jobs[job_id]
    if status.stage != "done" or not status.output_file:
        return jsonify({"error": "Video not ready yet"}), 400

    # Determine correct filename and mimetype based on output file extension
    output_ext = os.path.splitext(status.output_file)[1].lower()
    if output_ext == ".mp3":
        download_name = f"burmese_dubbed_{os.path.splitext(status.filename)[0]}.mp3"
        mimetype = "audio/mpeg"
    else:
        download_name = f"burmese_dubbed_{status.filename}"
        mimetype = "video/mp4"

    is_download = request.args.get("download") == "true"

    if is_download:
        if not os.path.exists(status.output_file):
            return jsonify({"error": "Output file not found (might be already downloaded/cleaned up)"}), 404

        import io
        try:
            with open(status.output_file, "rb") as f:
                file_data = io.BytesIO(f.read())
        except Exception as e:
            logger.error(f"Failed to read output file into memory: {e}")
            return jsonify({"error": f"Failed to read file: {str(e)}"}), 500

        # Perform cleanup
        cleanup_job_files(job_id)

        # Serve from memory buffer
        return send_file(
            file_data,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype
        )
    else:
        if not os.path.exists(status.output_file):
            return jsonify({"error": "Output file not found"}), 404

        return send_file(
            status.output_file,
            as_attachment=False,
            mimetype=mimetype
        )


@app.route("/api/adjust/<job_id>", methods=["POST"])
def adjust_audio(job_id):
    """Adjust speed and pitch of generated audio."""
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404

    status = jobs[job_id]
    if status.stage != "done" or not status.original_output_file:
        return jsonify({"error": "Original audio not ready yet"}), 400

    if not os.path.exists(status.original_output_file):
        return jsonify({"error": "Original audio file not found"}), 404

    data = request.get_json()
    speed = float(data.get("speed", 1.0))
    pitch = float(data.get("pitch", 1.0))

    if speed == 1.0 and pitch == 1.0:
        # Revert to original
        status.output_file = status.original_output_file
        jobs[job_id] = status
        return jsonify({"success": True, "file": os.path.basename(status.output_file)})

    # Generate new filename
    import time as builtin_time
    output_name = f"adjusted_{job_id}_{int(builtin_time.time())}.mp3"
    output_path = os.path.join(config.OUTPUT_FOLDER, output_name)

    # Build FFmpeg filters
    # asetrate: change sample rate to affect pitch (and tempo)
    # atempo: change tempo (compensating for asetrate's tempo change + user's speed change)
    sample_rate = 44100
    new_sample_rate = int(sample_rate * pitch)
    
    # We must restrict atempo to its valid range (0.5 to 100.0)
    # The total tempo adjustment needed is (1/pitch) * speed
    total_tempo = (1.0 / pitch) * speed
    
    # Multiple atempos might be needed if total_tempo < 0.5 or > 100, but for typical use (0.5-2.0) it's fine.
    atempo_str = f"atempo={total_tempo:.4f}"
    
    # If the user adjusts only speed: "atempo=SPEED"
    # If the user adjusts both: "asetrate=44100*PITCH,atempo=(1/PITCH)*SPEED"
    af_filter = f"asetrate={new_sample_rate},{atempo_str}"

    import subprocess
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", status.original_output_file,
        "-af", af_filter,
        "-acodec", "libmp3lame",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        output_path
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            logger.error(f"FFmpeg adjust error: {result.stderr.strip()}")
            return jsonify({"error": f"Audio adjustment failed: {result.stderr.strip()}"}), 500

        status.output_file = output_path
        jobs[job_id] = status
        return jsonify({"success": True, "file": os.path.basename(output_path)})
    except Exception as e:
        logger.error(f"Adjustment failed: {e}")
        return jsonify({"error": f"Adjustment failed: {str(e)}"}), 500


@app.route("/api/cancel/<job_id>", methods=["POST"])
def cancel_job(job_id):
    """Cancel a running dubbing job."""
    pipeline = pipelines.get(job_id)
    if not pipeline:
        return jsonify({"error": "Job not found"}), 404

    pipeline.cancel()
    return jsonify({"message": "Cancellation requested"})


@app.route("/api/jobs")
def list_jobs():
    """List all jobs with their current status."""
    result = []
    for jid, status in jobs.items():
        result.append(status_to_dict(status))
    return jsonify(result)


@app.route("/api/cleanup/<job_id>", methods=["POST"])
def cleanup_job(job_id):
    """Clean up temporary files for a completed job."""
    cleanup_job_files(job_id)
    return jsonify({"message": "Cleaned up"})


def cleanup_job_files(job_id):
    """Delete all temporary, output, and uploaded files associated with a job ID."""
    logger.info(f"Cleaning up files for job {job_id}...")
    import shutil

    # 1. Clean up temp folder
    temp_dir = os.path.join(config.TEMP_FOLDER, job_id)
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
            logger.info(f"Deleted temp directory: {temp_dir}")
        except Exception as e:
            logger.error(f"Failed to delete temp directory {temp_dir}: {e}")

    # 2. Clean up output files
    if os.path.exists(config.OUTPUT_FOLDER):
        try:
            for filename in os.listdir(config.OUTPUT_FOLDER):
                if job_id in filename:
                    file_path = os.path.join(config.OUTPUT_FOLDER, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            logger.info(f"Deleted output file: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to delete output file {file_path}: {e}")
        except Exception as e:
            logger.error(f"Failed to scan output folder: {e}")

    # 3. Clean up uploaded files
    if os.path.exists(config.UPLOAD_FOLDER):
        try:
            for filename in os.listdir(config.UPLOAD_FOLDER):
                if job_id in filename:
                    file_path = os.path.join(config.UPLOAD_FOLDER, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            logger.info(f"Deleted uploaded file: {file_path}")
                    except Exception as e:
                        logger.error(f"Failed to delete uploaded file {file_path}: {e}")
        except Exception as e:
            logger.error(f"Failed to scan upload folder: {e}")


@app.route("/api/voices")
def get_voices():
    """Return available TTS voices from voices.json."""
    voices_path = os.path.join(config.BASE_DIR, "voices.json")
    try:
        with open(voices_path, "r", encoding="utf-8") as f:
            voices_data = json.load(f)
        return jsonify(voices_data)
    except FileNotFoundError:
        return jsonify({"error": "voices.json not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/preview-voice", methods=["POST"])
def preview_voice():
    """Generate a short TTS preview for the selected voice."""
    data = request.get_json()
    if not data or "voice" not in data:
        return jsonify({"error": "No voice specified"}), 400

    voice_name = data["voice"]
    preview_text = data.get("text", "မင်္ဂလာပါ။ ဒီအသံနဲ့ နားဆင်ကြည့်ပါ။")

    # Validate voice
    voices_path = os.path.join(config.BASE_DIR, "voices.json")
    try:
        with open(voices_path, "r", encoding="utf-8") as f:
            voices_data = json.load(f)
        valid_names = [v["ShortName"] for v in voices_data.get("voices", [])]
        if voice_name not in valid_names:
            return jsonify({"error": "Invalid voice"}), 400
    except Exception:
        return jsonify({"error": "Could not validate voice"}), 500

    # Generate preview audio
    preview_dir = os.path.join(config.TEMP_FOLDER, "previews")
    os.makedirs(preview_dir, exist_ok=True)
    preview_file = os.path.join(preview_dir, f"preview_{voice_name}.mp3")

    try:
        import edge_tts
        communicate = edge_tts.Communicate(
            preview_text,
            voice_name,
            rate=config.TTS_RATE,
            volume=config.TTS_VOLUME
        )
        asyncio.run(communicate.save(preview_file))

        return send_file(
            preview_file,
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name=f"preview_{voice_name}.mp3"
        )
    except Exception as e:
        logger.error(f"Preview generation failed: {e}")
        return jsonify({"error": f"Preview generation failed: {str(e)}"}), 500


@app.route("/api/text-to-speech", methods=["POST"])
def text_to_speech():
    """Generate TTS audio directly from typed text (no SRT timing)."""
    text = request.form.get("text", "").strip()
    if not text:
        return jsonify({"error": "No text provided"}), 400

    tts_voice = request.form.get("tts_voice", "").strip() or config.TTS_VOICE

    # Validate voice
    voices_path = os.path.join(config.BASE_DIR, "voices.json")
    try:
        with open(voices_path, "r", encoding="utf-8") as f:
            voices_data = json.load(f)
        valid_names = [v["ShortName"] for v in voices_data.get("voices", [])]
        if tts_voice not in valid_names:
            tts_voice = config.TTS_VOICE
    except Exception:
        tts_voice = config.TTS_VOICE

    job_id = str(uuid.uuid4())[:8]
    logger.info(f"Text-to-speech job {job_id}: {len(text)} chars, voice={tts_voice}")

    # Create SSE queue and start pipeline in background
    job_queues[job_id] = Queue()

    def run_tts_job():
        queue = job_queues.get(job_id)
        try:
            # Split text into chunks
            chunks_text = split_text_to_chunks(text, 5000)
            total_chunks = len(chunks_text)
            if total_chunks == 0:
                raise Exception("No text to generate speech from")

            status = JobStatus(
                job_id=job_id,
                filename="text_input.txt",
                total_chunks=total_chunks,
                start_time=time.time()
            )
            status.chunks = [ChunkStatus(index=i, total=total_chunks) for i in range(total_chunks)]
            jobs[job_id] = status

            # Push initial status
            status.stage = "processing"
            status.message = f"Splitting text into {total_chunks} chunk(s) and starting generation..."
            status.overall_progress = 5.0
            if queue:
                queue.put(status_to_dict(status))

            # Generate TTS
            import edge_tts

            tts_dir = os.path.join(config.TEMP_FOLDER, job_id)
            os.makedirs(tts_dir, exist_ok=True)
            
            chunk_paths = []
            for i, chunk_txt in enumerate(chunks_text):
                status.current_chunk = i
                status.chunks[i].stage = "tts"
                status.chunks[i].message = "Generating TTS..."
                status.chunks[i].progress = 10.0
                
                overall_pct = 5.0 + (i / total_chunks) * 65.0
                status.overall_progress = round(overall_pct, 1)
                status.message = f"Generating speech for chunk {i+1} of {total_chunks}..."
                if queue:
                    queue.put(status_to_dict(status))

                chunk_path = os.path.join(tts_dir, f"chunk_{i:03d}.mp3")
                chunk_paths.append(chunk_path)

                communicate = edge_tts.Communicate(
                    chunk_txt,
                    tts_voice,
                    rate=config.TTS_RATE,
                    volume=config.TTS_VOLUME
                )
                asyncio.run(communicate.save(chunk_path))

                status.chunks[i].stage = "done"
                status.chunks[i].progress = 100.0
                status.chunks[i].message = "TTS generated"

                if status.cancelled:
                    raise Exception("Job cancelled by user")

            status.overall_progress = 70.0
            status.stage = "combining"
            status.message = "Merging audio chunks..."
            if queue:
                queue.put(status_to_dict(status))

            # Concat demuxer list
            concat_file_path = os.path.join(tts_dir, "concat.txt")
            with open(concat_file_path, "w", encoding="utf-8") as f:
                for path in chunk_paths:
                    escaped_path = path.replace("\\", "/")
                    f.write(f"file '{escaped_path}'\n")

            merged_path = os.path.join(tts_dir, "merged_raw.mp3")

            import subprocess
            cmd_concat = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", concat_file_path,
                "-c", "copy",
                merged_path
            ]
            result = subprocess.run(cmd_concat, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                raise Exception(f"FFmpeg concat error: {result.stderr.strip()}")

            status.overall_progress = 85.0
            status.message = "Normalizing and converting audio to final format..."
            if queue:
                queue.put(status_to_dict(status))

            # Convert to final MP3 with normalization
            output_name = f"speech_{job_id}.mp3"
            output_path = os.path.join(config.OUTPUT_FOLDER, output_name)

            cmd_norm = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", merged_path,
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-acodec", "libmp3lame",
                "-b:a", "192k",
                "-ar", "44100",
                "-ac", "2",
                output_path
            ]
            result = subprocess.run(cmd_norm, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                raise Exception(f"FFmpeg normalization error: {result.stderr.strip()}")

            status.stage = "done"
            status.overall_progress = 100.0
            status.message = "Speech generation complete!"
            status.output_file = output_path
            status.original_output_file = output_path
            jobs[job_id] = status

            if queue:
                queue.put(status_to_dict(status))
                queue.put("DONE")

        except Exception as e:
            logger.error(f"Text-to-speech job {job_id} failed: {e}")
            if job_id in jobs:
                status = jobs[job_id]
            else:
                status = JobStatus(job_id=job_id, filename="text_input.txt")
                jobs[job_id] = status
            status.stage = "error"
            status.error = str(e)
            status.message = f"Error: {str(e)}"
            if queue:
                queue.put(status_to_dict(status))
                queue.put("DONE")

    thread = threading.Thread(target=run_tts_job, daemon=True)
    job_threads[job_id] = thread
    thread.start()

    return jsonify({
        "job_id": job_id,
        "filename": "text_input.txt",
        "message": "Speech generation started",
        "output_mode": "audio"
    })


# ============================================================================
# Audio Transcription
# ============================================================================

@app.route("/api/transcribe", methods=["POST"])
def transcribe_audio():
    """Upload an audio file and transcribe Burmese speech to text."""
    if "audio" not in request.files:
        return jsonify({"error": "No audio file provided"}), 400

    file = request.files["audio"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in config.AUDIO_EXTENSIONS:
        return jsonify({"error": f"Unsupported audio format '.{ext}'. Supported: {', '.join(sorted(config.AUDIO_EXTENSIONS))}"}), 400

    job_id = str(uuid.uuid4())[:8]
    saved_filename = f"{job_id}.{ext}"
    audio_path = os.path.join(config.UPLOAD_FOLDER, saved_filename)
    file.save(audio_path)

    logger.info(f"Transcription upload: {file.filename} -> {audio_path}")

    # Create SSE queue and start background thread
    job_queues[job_id] = Queue()

    def run_transcription_job():
        queue = job_queues.get(job_id)
        try:
            import subprocess
            from pydub import AudioSegment

            # --- Detect duration ---
            audio_seg = AudioSegment.from_file(audio_path)
            duration_sec = len(audio_seg) / 1000.0
            chunk_duration = config.TRANSCRIBE_CHUNK_SECONDS

            # Determine chunks
            if duration_sec > chunk_duration:
                num_chunks = int(duration_sec // chunk_duration) + (1 if duration_sec % chunk_duration > 0 else 0)
            else:
                num_chunks = 1

            status = JobStatus(
                job_id=job_id,
                filename=file.filename,
                total_chunks=num_chunks,
                start_time=time.time()
            )
            status.chunks = [ChunkStatus(index=i, total=num_chunks) for i in range(num_chunks)]
            jobs[job_id] = status

            status.stage = "processing"
            status.message = f"Audio duration: {int(duration_sec)}s — splitting into {num_chunks} chunk(s)..."
            status.overall_progress = 5.0
            if queue:
                queue.put(status_to_dict(status))

            # --- Split audio into chunks if needed ---
            chunk_dir = os.path.join(config.TEMP_FOLDER, job_id)
            os.makedirs(chunk_dir, exist_ok=True)
            chunk_paths = []

            if num_chunks == 1:
                chunk_paths.append(audio_path)
            else:
                for i in range(num_chunks):
                    start_ms = i * chunk_duration * 1000
                    end_ms = min((i + 1) * chunk_duration * 1000, len(audio_seg))
                    chunk_seg = audio_seg[start_ms:end_ms]

                    chunk_path = os.path.join(chunk_dir, f"chunk_{i:03d}.wav")
                    chunk_seg.export(chunk_path, format="wav")
                    chunk_paths.append(chunk_path)

                    status.message = f"Exported chunk {i+1}/{num_chunks}..."
                    status.overall_progress = 5.0 + (i / num_chunks) * 10.0
                    if queue:
                        queue.put(status_to_dict(status))

            status.overall_progress = 15.0
            status.message = "Loading Whisper model..."
            if queue:
                queue.put(status_to_dict(status))

            # --- Load Whisper model ---
            from faster_whisper import WhisperModel

            device = config.WHISPER_DEVICE
            compute_type = config.WHISPER_COMPUTE_TYPE
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            if device == "cpu":
                compute_type = "int8"

            model = WhisperModel(
                config.WHISPER_MODEL,
                device=device,
                compute_type=compute_type
            )

            status.overall_progress = 20.0
            status.message = "Transcribing audio..."
            if queue:
                queue.put(status_to_dict(status))

            # --- Transcribe each chunk ---
            all_text_segments = []
            for i, chunk_path in enumerate(chunk_paths):
                if getattr(status, 'cancelled', False):
                    raise Exception("Job cancelled by user")

                status.current_chunk = i
                status.chunks[i].stage = "transcribing"
                status.chunks[i].message = "Transcribing..."
                status.chunks[i].progress = 10.0

                overall_pct = 20.0 + (i / num_chunks) * 70.0
                status.overall_progress = round(overall_pct, 1)
                status.message = f"Transcribing chunk {i+1} of {num_chunks}..."
                if queue:
                    queue.put(status_to_dict(status))

                segments, info = model.transcribe(
                    chunk_path,
                    language="my",
                    beam_size=5,
                    vad_filter=True
                )

                chunk_text_parts = []
                for segment in segments:
                    chunk_text_parts.append(segment.text.strip())

                chunk_text = " ".join(chunk_text_parts)
                all_text_segments.append(chunk_text)

                status.chunks[i].stage = "done"
                status.chunks[i].progress = 100.0
                status.chunks[i].message = f"Transcribed ({len(chunk_text)} chars)"
                if queue:
                    queue.put(status_to_dict(status))

            # --- Combine all transcription text ---
            full_text = "\n".join(all_text_segments).strip()
            transcriptions[job_id] = full_text

            # Save transcription to a text file
            output_name = f"transcription_{job_id}.txt"
            output_path = os.path.join(config.OUTPUT_FOLDER, output_name)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(full_text)

            status.stage = "done"
            status.overall_progress = 100.0
            status.message = f"Transcription complete! ({len(full_text)} characters)"
            status.output_file = output_path
            status.original_output_file = output_path
            jobs[job_id] = status

            if queue:
                queue.put(status_to_dict(status))
                queue.put("DONE")

            logger.info(f"Transcription job {job_id} complete: {len(full_text)} chars")

        except Exception as e:
            logger.error(f"Transcription job {job_id} failed: {e}")
            if job_id in jobs:
                status = jobs[job_id]
            else:
                status = JobStatus(job_id=job_id, filename=file.filename)
                jobs[job_id] = status
            status.stage = "error"
            status.error = str(e)
            status.message = f"Error: {str(e)}"
            if queue:
                queue.put(status_to_dict(status))
                queue.put("DONE")

    thread = threading.Thread(target=run_transcription_job, daemon=True)
    job_threads[job_id] = thread
    thread.start()

    return jsonify({
        "job_id": job_id,
        "filename": file.filename,
        "message": "Transcription started",
        "output_mode": "transcription"
    })


@app.route("/api/transcription/<job_id>")
def get_transcription(job_id):
    """Return the transcribed text for a completed job."""
    if job_id in transcriptions:
        return jsonify({
            "job_id": job_id,
            "text": transcriptions[job_id]
        })

    # Check if the text file exists on disk
    output_path = os.path.join(config.OUTPUT_FOLDER, f"transcription_{job_id}.txt")
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            text = f.read()
        transcriptions[job_id] = text
        return jsonify({
            "job_id": job_id,
            "text": text
        })

    return jsonify({"error": "Transcription not found"}), 404


# ============================================================================

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("  Burmese Dubbing Web Application")
    logger.info(f"  Whisper model: {config.WHISPER_MODEL}")
    logger.info(f"  TTS voice: {config.TTS_VOICE}")
    logger.info(f"  Chunk duration: {config.CHUNK_DURATION_SECONDS}s")
    logger.info("=" * 60)

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        threaded=True
    )
