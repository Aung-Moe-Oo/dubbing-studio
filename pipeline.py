"""
Core dubbing pipeline for converting video audio to Burmese.

Pipeline stages:
1. Split video into chunks (FFmpeg)
2. Extract audio from each chunk (FFmpeg)
3. Transcribe audio (faster-whisper)
4. Translate text to Burmese (deep-translator)
5. Generate Burmese TTS audio (edge-tts)
6. Align TTS audio timing (FFmpeg)
7. Replace original audio with Burmese (FFmpeg)
8. Combine all dubbed chunks (FFmpeg)
"""

import os
import re
import json
import subprocess
import asyncio
import time
import math
import shutil
import logging
from dataclasses import dataclass, field
from typing import List, Callable, Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class Segment:
    """A single transcribed/translated speech segment."""
    start: float
    end: float
    text: str
    translated: str = ""
    tts_file: str = ""


@dataclass
class ChunkStatus:
    """Status of a single chunk being processed."""
    index: int
    total: int
    stage: str = "pending"  # pending, extracting, transcribing, translating, tts, merging, done, error
    progress: float = 0.0
    message: str = ""
    error: str = ""


@dataclass
class JobStatus:
    """Overall job status."""
    job_id: str
    filename: str
    total_chunks: int = 0
    current_chunk: int = 0
    stage: str = "initializing"  # initializing, splitting, processing, combining, done, error, cancelled
    overall_progress: float = 0.0
    message: str = ""
    error: str = ""
    output_file: str = ""
    original_output_file: str = ""
    chunks: List[ChunkStatus] = field(default_factory=list)
    cancelled: bool = False
    start_time: float = 0.0


class DubbingPipeline:
    """Orchestrates the full dubbing pipeline."""

    def __init__(self, job_id: str, input_file: str, source_lang: str,
                 progress_callback: Optional[Callable] = None,
                 srt_file: Optional[str] = None,
                 output_mode: str = "video",
                 tts_voice: Optional[str] = None):
        self.job_id = job_id
        self.input_file = input_file
        self.source_lang = source_lang  # "en", "zh", or "auto"
        self.progress_callback = progress_callback
        self.srt_file = srt_file  # Optional SRT subtitle file path
        self.output_mode = output_mode  # "video" or "audio"
        self.tts_voice = tts_voice or config.TTS_VOICE  # User-selected voice or default

        self.job_dir = os.path.join(config.TEMP_FOLDER, job_id)
        self.chunks_dir = os.path.join(self.job_dir, "chunks")
        self.audio_dir = os.path.join(self.job_dir, "audio")
        self.tts_dir = os.path.join(self.job_dir, "tts")
        self.dubbed_dir = os.path.join(self.job_dir, "dubbed")

        for d in [self.job_dir, self.chunks_dir, self.audio_dir, self.tts_dir, self.dubbed_dir]:
            os.makedirs(d, exist_ok=True)

        self.status = JobStatus(
            job_id=job_id,
            filename=os.path.basename(input_file) if input_file else (os.path.basename(srt_file) if srt_file else "audio.mp3"),
            start_time=time.time()
        )

        # Lazy-loaded model
        self._whisper_model = None

        # Parsed SRT segments (populated once if srt_file is provided)
        self._srt_segments: Optional[List[Segment]] = None

    def _update_status(self, **kwargs):
        """Update job status and notify via callback."""
        for key, value in kwargs.items():
            setattr(self.status, key, value)
        if self.progress_callback:
            self.progress_callback(self.status)

    def _update_chunk(self, chunk_idx: int, **kwargs):
        """Update a specific chunk's status."""
        if chunk_idx < len(self.status.chunks):
            chunk = self.status.chunks[chunk_idx]
            for key, value in kwargs.items():
                setattr(chunk, key, value)
        if self.progress_callback:
            self.progress_callback(self.status)

    def _check_cancelled(self):
        """Raise exception if job was cancelled."""
        if self.status.cancelled:
            raise Exception("Job cancelled by user")

    def _run_ffmpeg(self, args: list, desc: str = ""):
        """Run an FFmpeg command and check for errors."""
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args
        logger.info(f"FFmpeg ({desc}): {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else f"FFmpeg failed with code {result.returncode}"
            raise Exception(f"FFmpeg error ({desc}): {error_msg}")
        return result

    def _get_duration(self, filepath: str) -> float:
        """Get duration of a media file in seconds."""
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            filepath
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise Exception(f"ffprobe failed: {result.stderr}")
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])

    # ========================================================================
    # Stage 1: Split video into chunks
    # ========================================================================
    def split_video(self) -> List[str]:
        """Split the input video into chunks using FFmpeg segment muxer."""
        self._update_status(stage="splitting", message="Splitting video into chunks...")
        self._check_cancelled()

        duration = self._get_duration(self.input_file)
        chunk_dur = config.CHUNK_DURATION_SECONDS
        num_chunks = math.ceil(duration / chunk_dur)

        self.status.total_chunks = num_chunks
        self.status.chunks = [
            ChunkStatus(index=i, total=num_chunks) for i in range(num_chunks)
        ]

        self._update_status(
            message=f"Splitting {duration:.0f}s video into {num_chunks} chunks ({chunk_dur}s each)..."
        )

        # Use FFmpeg segment to split
        chunk_pattern = os.path.join(self.chunks_dir, "chunk_%03d.mp4")
        self._run_ffmpeg([
            "-i", self.input_file,
            "-c", "copy",
            "-map", "0",
            "-segment_time", str(chunk_dur),
            "-f", "segment",
            "-reset_timestamps", "1",
            chunk_pattern
        ], desc="split video")

        # Collect chunk files
        chunks = sorted([
            os.path.join(self.chunks_dir, f)
            for f in os.listdir(self.chunks_dir)
            if f.startswith("chunk_") and f.endswith(".mp4")
        ])

        actual_count = len(chunks)
        if actual_count != num_chunks:
            self.status.total_chunks = actual_count
            self.status.chunks = [
                ChunkStatus(index=i, total=actual_count) for i in range(actual_count)
            ]

        self._update_status(
            message=f"Split into {actual_count} chunks",
            overall_progress=5.0
        )
        return chunks

    # ========================================================================
    # Stage 2: Extract audio
    # ========================================================================
    def extract_audio(self, video_path: str, chunk_idx: int) -> str:
        """Extract audio from a video chunk as WAV."""
        self._check_cancelled()
        self._update_chunk(chunk_idx, stage="extracting", message="Extracting audio...")

        audio_path = os.path.join(
            self.audio_dir, f"audio_{chunk_idx:03d}.wav"
        )
        self._run_ffmpeg([
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            audio_path
        ], desc=f"extract audio chunk {chunk_idx}")

        self._update_chunk(chunk_idx, progress=10.0, message="Audio extracted")
        return audio_path

    # ========================================================================
    # Stage 3a: Parse SRT subtitle file
    # ========================================================================
    def parse_srt(self, srt_path: str) -> List[Segment]:
        """Parse an SRT subtitle file into a list of Segments."""
        logger.info(f"Parsing SRT file: {srt_path}")
        segments = []

        with open(srt_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        # Split into subtitle blocks (separated by blank lines)
        blocks = re.split(r"\n\s*\n", content.strip())

        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 2:
                continue

            # Find the timestamp line (format: 00:00:01,000 --> 00:00:04,000)
            ts_match = None
            text_start_idx = 0
            for i, line in enumerate(lines):
                ts_match = re.match(
                    r"(\d{1,2}(?::\d{2}){1,2}(?:[,.]\d{3})?)\s*-->\s*(\d{1,2}(?::\d{2}){1,2}(?:[,.]\d{3})?)",
                    line.strip()
                )
                if ts_match:
                    text_start_idx = i + 1
                    break

            if not ts_match or text_start_idx >= len(lines):
                continue

            start_str, end_str = ts_match.group(1), ts_match.group(2)
            start = self._srt_time_to_seconds(start_str)
            end = self._srt_time_to_seconds(end_str)

            # Join remaining lines as subtitle text, strip HTML tags
            text = " ".join(lines[text_start_idx:]).strip()
            text = re.sub(r"<[^>]+>", "", text)  # Remove HTML tags like <i>, <b>
            text = text.strip()

            if text and end > start:
                segments.append(Segment(start=start, end=end, text=text))

        logger.info(f"Parsed {len(segments)} segments from SRT")
        return segments

    @staticmethod
    def _srt_time_to_seconds(time_str: str) -> float:
        """Convert SRT timestamp (HH:MM:SS,mmm or MM:SS, etc.) to seconds."""
        time_str = time_str.replace(",", ".").replace(";", ".").strip()
        
        # Split millisecond part
        if "." in time_str:
            time_part, ms_part = time_str.split(".", 1)
            ms = float("0." + ms_part)
        else:
            time_part = time_str
            ms = 0.0
            
        parts = time_part.split(":")
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = int(parts[2])
            return hours * 3600 + minutes * 60 + seconds + ms
        elif len(parts) == 2:
            minutes = int(parts[0])
            seconds = int(parts[1])
            return minutes * 60 + seconds + ms
        elif len(parts) == 1:
            seconds = int(parts[0])
            return seconds + ms
        else:
            return ms

    def get_segments_for_chunk(self, all_segments: List[Segment],
                               chunk_idx: int, chunk_start: float,
                               chunk_end: float) -> List[Segment]:
        """
        Filter and re-time SRT segments for a specific chunk.
        Returns segments with timestamps offset to chunk-local time.
        """
        chunk_segments = []
        for seg in all_segments:
            # Segment overlaps with this chunk's time window
            if seg.end > chunk_start and seg.start < chunk_end:
                local_start = max(0.0, seg.start - chunk_start)
                local_end = min(chunk_end - chunk_start, seg.end - chunk_start)
                if local_end > local_start:
                    chunk_segments.append(Segment(
                        start=local_start,
                        end=local_end,
                        text=seg.text
                    ))
        return chunk_segments

    # ========================================================================
    # Stage 3b: Transcribe with faster-whisper
    # ========================================================================
    def transcribe(self, audio_path: str, chunk_idx: int) -> List[Segment]:
        """Transcribe audio using faster-whisper."""
        self._check_cancelled()
        self._update_chunk(chunk_idx, stage="transcribing", message="Transcribing speech...")

        if self._whisper_model is None:
            self._update_chunk(chunk_idx, message="Loading Whisper model (first time)...")
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
            self._whisper_model = WhisperModel(
                config.WHISPER_MODEL,
                device=device,
                compute_type=compute_type
            )

        lang = self.source_lang if self.source_lang != "auto" else None
        segments_iter, info = self._whisper_model.transcribe(
            audio_path,
            language=lang,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )

        detected_lang = info.language
        logger.info(f"Chunk {chunk_idx}: Detected language = {detected_lang} (prob={info.language_probability:.2f})")

        segments = []
        for seg in segments_iter:
            self._check_cancelled()
            segments.append(Segment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip()
            ))

        self._update_chunk(
            chunk_idx,
            progress=35.0,
            message=f"Transcribed {len(segments)} segments (lang: {detected_lang})"
        )
        return segments

    # ========================================================================
    # Stage 4: Translate to Burmese
    # ========================================================================
    def _colloquialize_burmese(self, text: str) -> str:
        """Convert formal/literary Burmese text to daily spoken (colloquial) style."""
        if not text:
            return text

        import re

        # Pronouns, demonstratives, and common formal words
        replacements = [
            ("ကျွန်ုပ်တို့", "ကျွန်တော်တို့"),
            ("ကျွန်ုပ်", "ကျွန်တော်"),
            ("မည်သည့်", "ဘယ်"),
            ("၎င်း၏", "သူ့ရဲ့"),
            ("၎င်း", "အဲဒါ"),
            ("ထို", "အဲဒီ"),
            ("ဤ", "ဒီ"),
            ("ပြောကြား", "ပြော"),
            ("ပြုလုပ်", "လုပ်"),
            ("ခေါ်ဆို", "ခေါ်"),
            ("စတင်", "စ"),
            ("ထို့နောက်", "ပြီးတော့"),
            ("ထို့ပြင်", "ဒါ့အပြင်"),
            ("ထို့အပြင်", "ဒါ့အပြင်"),
            ("ထိုအခါ", "အဲဒီတော့"),
            ("ယခု", "အခု"),
            ("နှင့်", "နဲ့"),
            ("၌", "မှာ"),
            ("သို့", "ကို"),
            ("၏", "ရဲ့"),
            ("သောကြောင့်", "ကြောင့်"),
            ("သဖြင့်", "လို့"),
            ("သော်လည်း", "ပေမယ့်"),
        ]

        for formal, colloquial in replacements:
            text = text.replace(formal, colloquial)

        # Verb endings & questions
        text = re.sub(r"ပါသလား", "လား", text)
        text = re.sub(r"သလား", "လား", text)
        text = re.sub(r"ပါသနည်း", "လဲ", text)
        text = re.sub(r"သနည်း", "လဲ", text)
        text = re.sub(r"ပါသည်", "ပါတယ်", text)
        text = re.sub(r"ခဲ့ပါသည်", "ခဲ့ပါတယ်", text)
        text = re.sub(r"ခဲ့သည်", "ခဲ့တယ်", text)
        text = re.sub(r"နေပါသည်", "နေပါတယ်", text)
        text = re.sub(r"နေသည်", "နေတယ်", text)
        text = re.sub(r"ရပါသည်", "ရပါတယ်", text)
        text = re.sub(r"ရသည်", "ရတယ်", text)
        text = re.sub(r"ခဲ့ဖူးသည်", "ခဲ့ဖူးတယ်", text)
        text = re.sub(r"ဖူးပါသည်", "ဖူးပါတယ်", text)
        text = re.sub(r"ဖူးသည်", "ဖူးတယ်", text)
        text = re.sub(r"ပါမည်", "ပါမယ်", text)
        text = re.sub(r"ခဲ့ပါမည်", "ခဲ့ပါမယ်", text)
        text = re.sub(r"ခဲ့မည်", "ခဲ့မယ်", text)
        text = re.sub(r"နေပါမည်", "နေပါမယ်", text)
        text = re.sub(r"နေမည်", "နေမယ်", text)
        text = re.sub(r"ရပါမည်", "ရပါမယ်", text)
        text = re.sub(r"ရမည်", "ရမယ်", text)
        text = re.sub(r"ဦးမည်", "ဦးမယ်", text)
        text = re.sub(r"လိမ့်မည်", "လိမ့်မယ်", text)
        text = re.sub(r"ပါရန်", "ဖို့", text)
        text = re.sub(r"ရန်", "ဖို့", text)
        text = re.sub(r"ပါက", "ရင်", text)
        text = re.sub(r"ပါလျှင်", "ရင်", text)
        text = re.sub(r"လျှင်", "ရင်", text)
        text = re.sub(r"သော်", "ရင်", text)

        # Standalone "သည်" and "မည်" at boundary positions (to avoid matching inside words like သည်း)
        text = re.sub(r"သည်(?![း])(?=[။၊\s]|$)", "တယ်", text)
        text = re.sub(r"မည်(?=[။၊\s]|$)", "မယ်", text)

        return text

    def translate(self, segments: List[Segment], chunk_idx: int) -> List[Segment]:
        """Translate transcribed text to Burmese."""
        self._check_cancelled()
        self._update_chunk(chunk_idx, stage="translating", message="Translating to Burmese...")

        from deep_translator import GoogleTranslator

        translator = GoogleTranslator(
            source=config.TRANSLATION_SOURCE,
            target=config.TRANSLATION_TARGET
        )

        for i, seg in enumerate(segments):
            self._check_cancelled()
            if not seg.text:
                seg.translated = ""
                continue
            try:
                raw_translation = translator.translate(seg.text)
                if raw_translation:
                    seg.translated = self._colloquialize_burmese(raw_translation)
                else:
                    seg.translated = ""
            except Exception as e:
                logger.warning(f"Translation failed for segment {i}: {e}")
                seg.translated = seg.text  # Fallback to original

            # Update progress within translation stage
            pct = 35.0 + (25.0 * (i + 1) / len(segments))
            self._update_chunk(chunk_idx, progress=pct,
                               message=f"Translated {i + 1}/{len(segments)} segments")

        self._update_chunk(chunk_idx, progress=60.0, message="Translation complete")
        return segments

    # ========================================================================
    # Stage 5: Generate Burmese TTS
    # ========================================================================
    def generate_tts(self, segments: List[Segment], chunk_idx: int) -> List[Segment]:
        """Generate Burmese TTS audio for each segment using edge-tts."""
        self._check_cancelled()
        self._update_chunk(chunk_idx, stage="tts", message="Generating Burmese speech...")

        async def _generate_all():
            import edge_tts
            for i, seg in enumerate(segments):
                self._check_cancelled()
                if not seg.translated:
                    continue

                tts_path = os.path.join(self.tts_dir, f"tts_{chunk_idx:03d}_{i:04d}.mp3")
                try:
                    # Generate at base rate first
                    communicate = edge_tts.Communicate(
                        seg.translated,
                        self.tts_voice,
                        rate=config.TTS_RATE,
                        volume=config.TTS_VOLUME
                    )
                    await communicate.save(tts_path)
                    seg.tts_file = tts_path

                    # Check duration and dynamically speed up if it overflows
                    target_dur = seg.end - seg.start
                    if target_dur > 0:
                        tts_dur = self._get_duration(tts_path)
                        if tts_dur > target_dur:
                            ratio = tts_dur / target_dur
                            if ratio > 1.05:
                                # Convert ratio to percentage speedup (e.g. 1.25 ratio -> +25%)
                                speedup = int((ratio - 1.0) * 100)
                                max_speedup = getattr(config, "TTS_MAX_SPEEDUP_PERCENT", 40)
                                speedup = min(max_speedup, speedup)
                                if speedup > 0:
                                    rate_str = f"+{speedup}%"
                                    logger.info(f"Regenerating TTS segment {i} at rate {rate_str} to fit target {target_dur:.2f}s (was {tts_dur:.2f}s)")
                                    communicate = edge_tts.Communicate(
                                        seg.translated,
                                        self.tts_voice,
                                        rate=rate_str,
                                        volume=config.TTS_VOLUME
                                    )
                                    await communicate.save(tts_path)
                except Exception as e:
                    logger.warning(f"TTS failed for segment {i}: {e}")
                    seg.tts_file = ""

                pct = 60.0 + (20.0 * (i + 1) / len(segments))
                self._update_chunk(chunk_idx, progress=pct,
                                   message=f"Generated TTS {i + 1}/{len(segments)}")

        # Run async TTS in sync context
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_generate_all())).result()
            else:
                loop.run_until_complete(_generate_all())
        except RuntimeError:
            asyncio.run(_generate_all())

        self._update_chunk(chunk_idx, progress=80.0, message="TTS generation complete")
        return segments

    # ========================================================================
    # Stage 6: Build aligned Burmese audio track
    # ========================================================================
    def _clean_tts_clip(self, input_path: str, output_path: str):
        """
        Clean a single TTS clip: remove MP3 encoder padding, apply noise
        reduction, trim trailing silence, and normalize volume.
        """
        self._run_ffmpeg([
            "-i", input_path,
            "-af", (
                "highpass=f=80,"           # Remove low-frequency rumble
                "lowpass=f=12000,"         # Remove high-frequency hiss
                "afftdn=nf=-25,"           # FFT-based noise reduction
                "silenceremove="            # Trim trailing silence/click artifacts
                "stop_periods=1:"
                "stop_duration=0.05:"
                "stop_threshold=-50dB,"
                "volume=1.8"               # Boost TTS volume (edge-tts tends to be quiet)
            ),
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            output_path
        ], desc=f"clean TTS clip {os.path.basename(input_path)}")

    def build_aligned_audio(self, segments: List[Segment], chunk_duration: float,
                            chunk_idx: int) -> str:
        """
        Build a single audio track with TTS segments time-aligned to original timestamps.
        Uses pydub to align segments sequentially without overlap.
        Applies noise reduction and volume normalization.
        """
        self._check_cancelled()
        self._update_chunk(chunk_idx, stage="merging", message="Cleaning & aligning audio...")

        raw_audio = os.path.join(self.audio_dir, f"burmese_raw_{chunk_idx:03d}.wav")
        output_audio = os.path.join(self.audio_dir, f"burmese_{chunk_idx:03d}.wav")

        # Filter segments that have TTS files
        valid_segments = [s for s in segments if s.tts_file and os.path.exists(s.tts_file)]

        if not valid_segments:
            # Generate silence for the entire chunk
            self._run_ffmpeg([
                "-f", "lavfi",
                "-i", "anullsrc=r=16000:cl=mono",
                "-t", str(chunk_duration),
                "-acodec", "pcm_s16le",
                output_audio
            ], desc=f"silence for chunk {chunk_idx}")
            return output_audio

        # Step 1: Clean each TTS clip (noise reduction + volume)
        cleaned_dir = os.path.join(self.tts_dir, f"cleaned_{chunk_idx:03d}")
        os.makedirs(cleaned_dir, exist_ok=True)

        for i, seg in enumerate(valid_segments):
            self._check_cancelled()
            cleaned_path = os.path.join(cleaned_dir, f"clean_{i:04d}.wav")
            try:
                self._clean_tts_clip(seg.tts_file, cleaned_path)
                seg.tts_file = cleaned_path  # Point to cleaned version
            except Exception as e:
                logger.warning(f"TTS clip cleaning failed for segment {i}, using original: {e}")

        self._update_chunk(chunk_idx, progress=83.0, message="Aligning timing...")

        # Step 2: Assemble sequentially using pydub to prevent overlapping
        from pydub import AudioSegment

        try:
            # Calculate the total duration needed (in milliseconds)
            # We want to pre-allocate a silent background AudioSegment
            total_duration_ms = int(chunk_duration * 1000)
            
            # Pre-load clips and estimate timing to avoid truncating
            clips_with_timing = []
            current_cursor_ms = 0
            
            # Fade durations in milliseconds to prevent click/pop artifacts
            fade_in_ms = 15
            fade_out_ms = 30

            for i, seg in enumerate(valid_segments):
                self._check_cancelled()
                try:
                    clip = AudioSegment.from_file(seg.tts_file)
                    # Force mono, 16000Hz, 16-bit to match pipeline standard
                    clip = clip.set_frame_rate(16000).set_channels(1).set_sample_width(2)
                except Exception as e:
                    logger.warning(f"Failed to load clip {seg.tts_file} with pydub: {e}")
                    continue

                # Apply fade-in and fade-out to eliminate abrupt waveform
                # discontinuities that produce audible clicks ("tap tap")
                if len(clip) > (fade_in_ms + fade_out_ms + 10):
                    clip = clip.fade_in(fade_in_ms).fade_out(fade_out_ms)
                elif len(clip) > 20:
                    # Very short clip: apply minimal fades
                    short_fade = min(5, len(clip) // 4)
                    clip = clip.fade_in(short_fade).fade_out(short_fade)

                target_start_ms = int(seg.start * 1000)
                # Position clip: start at target_start_ms or cursor if it would overlap
                start_ms = max(target_start_ms, current_cursor_ms)
                
                clip_len = len(clip)
                clips_with_timing.append((clip, start_ms))
                
                # Update cursor for the next segment (add a 150ms gap to avoid
                # back-to-back boundary artifacts)
                current_cursor_ms = start_ms + clip_len + 150
            
            # Extend total background length if the final cursor goes beyond chunk_duration
            total_duration_ms = max(total_duration_ms, current_cursor_ms)
            
            logger.info(f"Assembling audio chunk {chunk_idx}: duration = {total_duration_ms / 1000:.2f}s (original = {chunk_duration:.2f}s)")
            
            # Create background silent track
            combined_audio = AudioSegment.silent(duration=total_duration_ms, frame_rate=16000)
            combined_audio = combined_audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            
            # Overlay all clips
            for clip, start_ms in clips_with_timing:
                combined_audio = combined_audio.overlay(clip, position=start_ms)
            
            # Export to raw audio file
            combined_audio.export(raw_audio, format="wav")
            
        except Exception as e:
            logger.error(f"Failed to assemble audio using pydub: {e}")
            raise Exception(f"Sequential audio alignment failed: {e}")

        # Step 3: Normalize volume with loudnorm for consistent level across chunks
        self._update_chunk(chunk_idx, progress=88.0, message="Normalizing volume...")
        self._normalize_audio(raw_audio, output_audio)

        # Clean up raw file
        try:
            os.remove(raw_audio)
        except OSError:
            pass

        self._update_chunk(chunk_idx, progress=90.0, message="Audio aligned & normalized")
        return output_audio

    def _normalize_audio(self, input_path: str, output_path: str):
        """
        Apply EBU R128 loudness normalization to ensure consistent volume
        across all chunks. Target: -16 LUFS (standard for spoken content).
        """
        self._run_ffmpeg([
            "-i", input_path,
            "-af", (
                "loudnorm=I=-16:TP=-1.5:LRA=11:"
                "measured_I=-30:measured_TP=-1.5:measured_LRA=11:measured_thresh=-40:"
                "linear=true:print_format=none"
            ),
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            output_path
        ], desc=f"loudnorm {os.path.basename(input_path)}")

    # ========================================================================
    # Stage 7: Replace audio in video chunk
    # ========================================================================
    def replace_audio(self, video_path: str, audio_path: str, chunk_idx: int) -> str:
        """Replace original audio with Burmese audio in the video chunk."""
        self._check_cancelled()
        self._update_chunk(chunk_idx, message="Merging audio with video...")

        dubbed_path = os.path.join(self.dubbed_dir, f"dubbed_{chunk_idx:03d}.mp4")
        
        video_dur = self._get_duration(video_path)
        audio_dur = self._get_duration(audio_path)
        
        stretch_enabled = getattr(config, "STRETCH_VIDEO_TO_FIT_AUDIO", True)
        
        if stretch_enabled and audio_dur > video_dur + 0.1:
            pts_ratio = audio_dur / video_dur
            logger.info(f"Slowing down video chunk {chunk_idx} by factor {pts_ratio:.3f} to match audio ({audio_dur:.2f}s vs {video_dur:.2f}s)")
            self._run_ffmpeg([
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", f"[0:v]setpts={pts_ratio:.4f}*PTS[v]",
                "-map", "[v]",
                "-map", "1:a:0",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                dubbed_path
            ], desc=f"slow down video and replace audio chunk {chunk_idx}")
        else:
            self._run_ffmpeg([
                "-i", video_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-shortest",
                dubbed_path
            ], desc=f"replace audio chunk {chunk_idx}")

        self._update_chunk(chunk_idx, stage="done", progress=100.0, message="Chunk dubbed!")
        return dubbed_path

    # ========================================================================
    # Stage 8: Combine all dubbed chunks
    # ========================================================================
    def combine_chunks(self, dubbed_chunks: List[str]) -> str:
        """Concatenate all dubbed video chunks into the final output."""
        self._check_cancelled()
        self._update_status(stage="combining", message="Combining all dubbed chunks...")

        # Create concat list file
        concat_file = os.path.join(self.job_dir, "concat_list.txt")
        with open(concat_file, "w", encoding="utf-8") as f:
            for chunk_path in dubbed_chunks:
                # Use forward slashes for FFmpeg compatibility
                safe_path = chunk_path.replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        output_name = f"dubbed_{self.job_id}.mp4"
        output_path = os.path.join(config.OUTPUT_FOLDER, output_name)

        self._run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            output_path
        ], desc="combine chunks")

        self._update_status(
            stage="done",
            overall_progress=100.0,
            message="Dubbing complete!",
            output_file=output_path
        )
        return output_path

    # ========================================================================
    # Stage 8b: Combine all audio-only chunks into a single MP3
    # ========================================================================
    def combine_audio_chunks(self, audio_chunks: List[str]) -> str:
        """Concatenate all Burmese audio chunks into a single MP3 output."""
        self._check_cancelled()
        self._update_status(stage="combining", message="Combining all audio chunks...")

        # Create concat list file
        concat_file = os.path.join(self.job_dir, "audio_concat_list.txt")
        with open(concat_file, "w", encoding="utf-8") as f:
            for chunk_path in audio_chunks:
                safe_path = chunk_path.replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        output_name = f"dubbed_{self.job_id}.mp3"
        output_path = os.path.join(config.OUTPUT_FOLDER, output_name)

        # Concatenate audio chunks and encode as MP3
        self._run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-acodec", "libmp3lame",
            "-b:a", "192k",
            "-ar", "44100",
            "-ac", "2",
            output_path
        ], desc="combine audio chunks to MP3")

        self._update_status(
            stage="done",
            overall_progress=100.0,
            message="Audio dubbing complete!",
            output_file=output_path
        )
        return output_path

    # ========================================================================
    # Main pipeline execution
    # ========================================================================
    def run(self) -> str:
        """Execute the full dubbing pipeline."""
        try:
            self._update_status(stage="initializing", message="Starting dubbing pipeline...")
            self.status.start_time = time.time()

            # Parse SRT file once if provided
            use_srt = False
            all_srt_segments = []
            if self.srt_file and os.path.exists(self.srt_file):
                self._update_status(message="Parsing subtitle file...")
                all_srt_segments = self.parse_srt(self.srt_file)
                if all_srt_segments:
                    use_srt = True
                    logger.info(f"Using SRT subtitles ({len(all_srt_segments)} segments) — skipping transcription")
                else:
                    logger.warning("SRT file was empty or invalid, falling back to transcription")

            if not self.input_file:
                # SRT-only mode to generate MP3 from SRT
                if not use_srt:
                    raise Exception("No SRT file provided for dubbing in SRT-only mode")
                
                self.status.total_chunks = 1
                self.status.chunks = [ChunkStatus(index=0, total=1)]
                self._update_status(message="Processing SRT translation and speech generation...", overall_progress=10.0)
                
                # Get the duration from the last segment in SRT
                chunk_duration = all_srt_segments[-1].end if all_srt_segments else 0.0
                
                # Stage 4: Translate
                self._update_chunk(0, stage="translating", progress=10.0, message="Translating subtitle text...")
                translated_segments = self.translate(all_srt_segments, 0)
                
                # Stage 5: Generate TTS
                self._update_chunk(0, stage="tts", progress=40.0, message="Generating audio...")
                tts_segments = self.generate_tts(translated_segments, 0)
                
                # Stage 6: Build aligned audio
                self._update_chunk(0, stage="merging", progress=80.0, message="Assembling final audio...")
                burmese_audio = self.build_aligned_audio(tts_segments, chunk_duration, 0)
                
                # Combine (which converts the single WAV to MP3)
                output_name = f"dubbed_{self.job_id}.mp3"
                output_path = os.path.join(config.OUTPUT_FOLDER, output_name)
                
                self._update_status(stage="combining", message="Converting to MP3...")
                self._run_ffmpeg([
                    "-i", burmese_audio,
                    "-acodec", "libmp3lame",
                    "-b:a", "192k",
                    "-ar", "44100",
                    "-ac", "2",
                    output_path
                ], desc="convert single audio chunk to MP3")
                
                self._update_status(
                    stage="done",
                    overall_progress=100.0,
                    message="Audio generation complete!",
                    output_file=output_path,
                    original_output_file=output_path
                )
                self._update_chunk(0, stage="done", progress=100.0, message="Audio ready!")
                return output_path

            # Stage 1: Split video
            chunks = self.split_video()
            logger.info(f"Split into {len(chunks)} chunks")

            # Calculate chunk time boundaries for SRT segment filtering
            chunk_dur = config.CHUNK_DURATION_SECONDS

            # Process each chunk
            dubbed_chunks = []
            for i, chunk_path in enumerate(chunks):
                self._check_cancelled()
                self.status.current_chunk = i
                chunk_progress_base = 5.0 + (85.0 * i / len(chunks))
                self._update_status(
                    overall_progress=chunk_progress_base,
                    message=f"Processing chunk {i + 1}/{len(chunks)}..."
                )

                # Get chunk duration for audio alignment
                chunk_duration = self._get_duration(chunk_path)

                if use_srt:
                    # --- SRT mode: skip audio extraction & transcription ---
                    chunk_start = i * chunk_dur
                    chunk_end = chunk_start + chunk_duration

                    self._update_chunk(i, stage="transcribing", progress=10.0,
                                       message="Using subtitle file (skipping transcription)...")

                    segments = self.get_segments_for_chunk(
                        all_srt_segments, i, chunk_start, chunk_end
                    )

                    self._update_chunk(i, progress=35.0,
                                       message=f"Loaded {len(segments)} subtitle segments")
                else:
                    # --- Normal mode: extract audio & transcribe ---
                    # Stage 2: Extract audio
                    audio_path = self.extract_audio(chunk_path, i)

                    # Stage 3: Transcribe
                    segments = self.transcribe(audio_path, i)

                if not segments:
                    logger.warning(f"Chunk {i}: No speech/subtitles found, keeping original")
                    if self.output_mode == "audio":
                        # Audio-only mode: generate silence for this chunk
                        silence_path = os.path.join(self.audio_dir, f"silence_{i:03d}.wav")
                        self._run_ffmpeg([
                            "-f", "lavfi",
                            "-i", "anullsrc=r=16000:cl=mono",
                            "-t", str(chunk_duration),
                            "-acodec", "pcm_s16le",
                            silence_path
                        ], desc=f"silence for empty chunk {i}")
                        dubbed_chunks.append(silence_path)
                    else:
                        dubbed_chunks.append(chunk_path)
                    self._update_chunk(i, stage="done", progress=100.0,
                                       message="No speech/subtitles found — kept original")
                    continue

                # Stage 4: Translate
                segments = self.translate(segments, i)

                # Stage 5: Generate TTS
                segments = self.generate_tts(segments, i)

                # Stage 6: Build aligned audio
                burmese_audio = self.build_aligned_audio(segments, chunk_duration, i)

                if self.output_mode == "audio":
                    # Audio-only mode: just collect the aligned audio
                    dubbed_chunks.append(burmese_audio)
                    self._update_chunk(i, stage="done", progress=100.0,
                                       message="Audio chunk ready!")
                else:
                    # Video mode: replace audio in video chunk
                    dubbed_path = self.replace_audio(chunk_path, burmese_audio, i)
                    dubbed_chunks.append(dubbed_path)

                # Update overall progress
                overall_pct = 5.0 + (85.0 * (i + 1) / len(chunks))
                self._update_status(overall_progress=overall_pct)

            # Stage 8: Combine
            if self.output_mode == "audio":
                # Audio-only mode: combine audio chunks into MP3
                if len(dubbed_chunks) == 1:
                    output_name = f"dubbed_{self.job_id}.mp3"
                    output_path = os.path.join(config.OUTPUT_FOLDER, output_name)
                    # Convert single WAV chunk to MP3
                    self._run_ffmpeg([
                        "-i", dubbed_chunks[0],
                        "-acodec", "libmp3lame",
                        "-b:a", "192k",
                        "-ar", "44100",
                        "-ac", "2",
                        output_path
                    ], desc="convert single audio chunk to MP3")
                    self._update_status(
                        stage="done",
                        overall_progress=100.0,
                        message="Audio dubbing complete!",
                        output_file=output_path,
                        original_output_file=output_path
                    )
                else:
                    output_path = self.combine_audio_chunks(dubbed_chunks)
            else:
                # Video mode: combine video chunks
                if len(dubbed_chunks) == 1:
                    output_name = f"dubbed_{self.job_id}.mp4"
                    output_path = os.path.join(config.OUTPUT_FOLDER, output_name)
                    shutil.copy2(dubbed_chunks[0], output_path)
                    self._update_status(
                        stage="done",
                        overall_progress=100.0,
                        message="Dubbing complete!",
                        output_file=output_path,
                        original_output_file=output_path
                    )
                else:
                    output_path = self.combine_chunks(dubbed_chunks)

            logger.info(f"Pipeline complete: {output_path}")
            return output_path

        except Exception as e:
            if "cancelled" in str(e).lower():
                self._update_status(stage="cancelled", message="Job cancelled by user")
            else:
                logger.exception(f"Pipeline error: {e}")
                self._update_status(stage="error", error=str(e),
                                    message=f"Error: {str(e)}")
            raise

    def cleanup(self):
        """Remove temporary files for this job."""
        try:
            if os.path.exists(self.job_dir):
                shutil.rmtree(self.job_dir)
                logger.info(f"Cleaned up temp dir: {self.job_dir}")
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")

    def cancel(self):
        """Cancel the pipeline execution."""
        self.status.cancelled = True
        self._update_status(stage="cancelled", message="Cancelling...")
