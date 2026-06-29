#!/usr/bin/env python3
"""
Burmese Dubbing Audio
Provides functions and CLI tool to align a video with a Burmese SRT subtitle file
and a folder of corresponding Burmese TTS audio clips.
"""

import os
import sys
import re
import json
import subprocess
import argparse
import shutil
import tempfile
import logging
from pydub import AudioSegment

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("dubber")


class DubbingSyncError(Exception):
    """Custom exception class for dubbing sync errors."""
    pass


# ============================================================================
# 1. PARSE SRT
# ============================================================================
def parse_srt(srt_path: str) -> list:
    """
    Parse an SRT subtitle file into a list of dictionaries with timing and text.
    Returns:
        List of dicts: [
            {
                'index': int,
                'start': float (seconds),
                'end': float (seconds),
                'duration': float (seconds),
                'text': str
            }, ...
        ]
    """
    logger.info(f"Parsing SRT file: {srt_path}")
    if not os.path.exists(srt_path):
        raise FileNotFoundError(f"SRT file not found: {srt_path}")

    try:
        with open(srt_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except Exception as e:
        raise DubbingSyncError(f"Failed to read SRT file: {e}")

    # Normalize newlines
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    
    # Split into blocks by double newlines (or empty lines)
    blocks = re.split(r"\n\s*\n", content.strip())
    subtitles = []

    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue

        # Subtitle index is typically the first line
        try:
            index = int(lines[0])
            ts_line = lines[1]
            text_lines = lines[2:]
        except ValueError:
            # Fallback if index line is missing
            ts_line = lines[0]
            text_lines = lines[1:]
            index = len(subtitles) + 1

        # Match timestamps: e.g., 00:00:01,000 --> 00:00:04,000 or simplified
        ts_match = re.match(
            r"(\d{1,2}(?::\d{2}){1,2}(?:[,.]\d{3})?)\s*-->\s*(\d{1,2}(?::\d{2}){1,2}(?:[,.]\d{3})?)",
            ts_line
        )
        if not ts_match:
            # If the current line is not timestamps, check if any line in block matches timestamps
            found_ts = False
            for idx, line in enumerate(lines):
                ts_match = re.match(
                    r"(\d{1,2}(?::\d{2}){1,2}(?:[,.]\d{3})?)\s*-->\s*(\d{1,2}(?::\d{2}){1,2}(?:[,.]\d{3})?)",
                    line
                )
                if ts_match:
                    ts_line = line
                    index = int(lines[idx - 1]) if idx > 0 else len(subtitles) + 1
                    text_lines = lines[idx + 1:]
                    found_ts = True
                    break
            if not found_ts:
                logger.warning(f"Skipping malformed block (no timestamps found): {block}")
                continue

        start_str, end_str = ts_match.group(1), ts_match.group(2)
        
        try:
            start = _srt_time_to_seconds(start_str)
            end = _srt_time_to_seconds(end_str)
        except Exception as e:
            logger.warning(f"Error parsing timestamps '{start_str}' or '{end_str}': {e}")
            continue

        text = " ".join(text_lines).strip()
        # Clean HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        if start >= end:
            logger.warning(f"Skipping subtitle block {index}: start time >= end time ({start} >= {end})")
            continue

        subtitles.append({
            'index': index,
            'start': round(start, 3),
            'end': round(end, 3),
            'duration': round(end - start, 3),
            'text': text
        })

    # Sort subtitles by start time to make sure they are in chronological order
    subtitles.sort(key=lambda x: x['start'])
    
    logger.info(f"Successfully parsed {len(subtitles)} subtitle blocks.")
    return subtitles


def _srt_time_to_seconds(time_str: str) -> float:
    """Convert SRT timestamp format HH:MM:SS,mmm or MM:SS to seconds."""
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


# ============================================================================
# 2. MATCH AUDIO
# ============================================================================
def match_audio(audio_dir: str, sub_index: int) -> str:
    """
    Locates the corresponding Burmese TTS audio file for a given subtitle block index.
    Looks for exact match, zero-padded matches, or matching numeric tokens in filenames.
    
    Returns:
        str: Absolute path to the matching audio file.
    Raises:
        FileNotFoundError: If no matching file is found.
    """
    if not os.path.exists(audio_dir):
        raise FileNotFoundError(f"Audio directory not found: {audio_dir}")

    files = [f for f in os.listdir(audio_dir) if os.path.isfile(os.path.join(audio_dir, f))]
    allowed_extensions = {".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac", ".wma"}

    # Filter out files with unsupported formats
    audio_files = [f for f in files if os.path.splitext(f)[1].lower() in allowed_extensions]

    # Pattern 1: Exact matches or zero-padded index matches, e.g., "1.mp3", "01.mp3", "0001.wav"
    for name in [f"{sub_index}", f"{sub_index:02d}", f"{sub_index:03d}", f"{sub_index:04d}"]:
        for f in audio_files:
            base, ext = os.path.splitext(f)
            if base == name:
                return os.path.abspath(os.path.join(audio_dir, f))

    # Pattern 2: Numeric token matches, e.g., "segment_1.mp3", "tts_001.wav", "audio-1.m4a"
    # We split filenames by non-digits and look for an exact numerical token matching sub_index
    for f in audio_files:
        base, _ = os.path.splitext(f)
        # Find all sequences of digits in the filename
        digits = re.findall(r"\d+", base)
        for num_str in digits:
            if int(num_str) == sub_index:
                return os.path.abspath(os.path.join(audio_dir, f))

    # Pattern 3: Fallback match if the files are just named sequentially in sorted order
    # Verify if total audio files equals or exceeds index (only use if index is 1-based and small)
    # This is less safe, so we log a warning.
    sorted_files = sorted(audio_files, key=lambda x: [int(s) if s.isdigit() else s for s in re.split(r'(\d+)', x)])
    if 1 <= sub_index <= len(sorted_files):
        matched_file = sorted_files[sub_index - 1]
        logger.warning(f"Audio matching fallback: index {sub_index} matched sequentially to '{matched_file}'")
        return os.path.abspath(os.path.join(audio_dir, matched_file))

    raise FileNotFoundError(f"Could not find matching Burmese audio file for subtitle block index {sub_index} in directory: {audio_dir}")


# ============================================================================
# 3. GET MEDIA INFORMATION
# ============================================================================
def get_media_info(video_path: str) -> dict:
    """
    Query video details (duration, width, height, fps, sample_rate) using ffprobe.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Standard ffprobe command
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8", errors="replace")
        data = json.loads(result.stdout)
    except Exception as e:
        raise DubbingSyncError(f"Failed to query video info via ffprobe: {e}")

    info = {
        'duration': 0.0,
        'width': 1280,
        'height': 720,
        'fps': 30.0,
        'sample_rate': 44100,
        'has_audio': False
    }

    # Parse format duration
    if "format" in data and "duration" in data["format"]:
        info['duration'] = float(data["format"]["duration"])

    # Parse streams
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            info['width'] = int(stream.get("width", info['width']))
            info['height'] = int(stream.get("height", info['height']))
            
            # Parse frame rate (fraction e.g. "30000/1001" or "25/1")
            r_frame_rate = stream.get("r_frame_rate", "30/1")
            if "/" in r_frame_rate:
                num, den = map(int, r_frame_rate.split("/"))
                info['fps'] = num / den if den != 0 else 30.0
            else:
                info['fps'] = float(r_frame_rate)
        elif codec_type == "audio":
            info['has_audio'] = True
            info['sample_rate'] = int(stream.get("sample_rate", info['sample_rate']))

    return info


# ============================================================================
# 4. TIME-STRETCH AUDIO (PITCH-PRESERVING)
# ============================================================================
def stretch_audio(input_path: str, output_path: str, speed_factor: float) -> str:
    """
    Speed up or slow down an audio clip using FFmpeg's `atempo` filter to preserve pitch.
    Handles speeds from 0.5 to 2.0. If outside this range, chains multiple `atempo` filters.
    """
    # Guard against no-op
    if abs(speed_factor - 1.0) < 0.005:
        shutil.copy2(input_path, output_path)
        return output_path

    # Construct the filter chain
    # FFmpeg atempo filter works between 0.5 and 2.0
    filters = []
    temp_factor = speed_factor
    while temp_factor > 2.0:
        filters.append("atempo=2.0")
        temp_factor /= 2.0
    while temp_factor < 0.5:
        filters.append("atempo=0.5")
        temp_factor /= 0.5
    
    filters.append(f"atempo={temp_factor:.4f}")
    filter_str = ",".join(filters)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", input_path,
        "-af", filter_str,
        "-acodec", "pcm_s16le",  # Output to standard WAV format
        "-ar", "44100",
        "-ac", "2",
        output_path
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else "Unknown error"
        raise DubbingSyncError(f"Audio time-stretch failed: {error_msg}")

    return output_path


# ============================================================================
# 5. PROCESS SEGMENT (GAP OR SUBTITLE SLOT)
# ============================================================================
def process_segment(
    video_path: str,
    start: float,
    end: float,
    duration: float,
    segment_type: str,
    sub_index: int,
    audio_path: str,
    output_path: str,
    width: int,
    height: int,
    fps: float,
    stretch_mode: str,
    duck_db: float,
    has_video_audio: bool
) -> str:
    """
    Renders a single video+audio segment to a temporary .mp4 file.
    Standardizes output resolution, frame rate, audio channel count (2), and sample rate (44100).
    """
    # Standard format variables
    video_codec = "libx264"
    audio_codec = "aac"
    audio_rate = "44100"
    audio_channels = "2"
    
    # ------------------------------------------------------------------------
    # GAP SEGMENT: Keep original video & audio as-is
    # ------------------------------------------------------------------------
    if segment_type == "gap":
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", video_path,
            "-c:v", video_codec,
            "-r", f"{fps:.2f}",
            "-s", f"{width}x{height}",
            "-pix_fmt", "yuv420p"
        ]
        
        if has_video_audio:
            cmd += [
                "-c:a", audio_codec,
                "-ar", audio_rate,
                "-ac", audio_channels
            ]
        else:
            # Generate silent audio track if original video has no audio
            cmd += [
                "-f", "lavfi", "-i", f"anullsrc=r={audio_rate}:cl=stereo",
                "-c:a", audio_codec,
                "-shortest"
            ]
        
        cmd.append(output_path)
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else "Unknown error"
            raise DubbingSyncError(f"Failed to process gap segment {start:.2f}-{end:.2f}s: {error_msg}")
        
        return output_path

    # ------------------------------------------------------------------------
    # SUBTITLE SEGMENT (DUBBING + RUBBER BANDING)
    # ------------------------------------------------------------------------
    # 1. Parse Burmese TTS audio info using pydub
    try:
        burmese_audio = AudioSegment.from_file(audio_path)
    except Exception as e:
        raise DubbingSyncError(f"Failed to load Burmese audio clip {audio_path}: {e}")
        
    d_audio = burmese_audio.duration_seconds
    d_avail = duration
    ratio = d_audio / d_avail
    
    logger.info(f"Sub [{sub_index}]: Slot duration = {d_avail:.2f}s, Burmese Audio duration = {d_audio:.2f}s, Ratio R = {ratio:.3f}")

    # Determine processing parameters based on ratio R
    s_audio = 1.0
    s_video = 1.0
    freeze_duration = 0.0
    pad_silence_duration = 0.0

    if ratio <= 1.15:
        # Audio is shorter or only slightly longer: Speed up audio to match slot exactly
        if ratio > 1.0:
            s_audio = ratio
        elif ratio < 1.0:
            # Audio is shorter: keep audio speed at 1.0, pad with silence at the end
            pad_silence_duration = d_avail - d_audio
    elif 1.15 < ratio <= 1.4:
        # Audio is noticeably longer: slow down video or freeze last frame, no audio speedup
        s_audio = 1.0
        if stretch_mode == "slow":
            s_video = 1.0 / ratio  # Video slowed down to match audio duration
        else:
            freeze_duration = d_audio - d_avail  # Video plays normal, last frame is frozen
    else:
        # Audio is way too long: Speed up audio by 15% AND slow down/freeze video
        s_audio = 1.15
        d_audio_sped = d_audio / 1.15
        ratio_remaining = d_audio_sped / d_avail
        
        if stretch_mode == "slow":
            s_video = 1.0 / ratio_remaining
        else:
            freeze_duration = d_audio_sped - d_avail

    # Determine ducking multiplier
    duck_multiplier = 10 ** (-abs(duck_db) / 20.0) if duck_db > 0 else 0.0
    
    # Create absolute temporary path for intermediate processed audio
    temp_dir = os.path.dirname(output_path)
    temp_audio_path = os.path.join(temp_dir, f"temp_aud_{sub_index}.wav")

    # Step 1: Pre-process the Burmese audio (Speedup / Padding) using pydub & ffmpeg atempo
    try:
        # Apply pitch-preserving speedup if needed
        if abs(s_audio - 1.0) >= 0.005:
            # Save raw clip to temp, speed it up
            raw_temp = os.path.join(temp_dir, f"raw_temp_{sub_index}.wav")
            burmese_audio.export(raw_temp, format="wav")
            stretch_audio(raw_temp, temp_audio_path, s_audio)
            try:
                os.remove(raw_temp)
            except OSError:
                pass
            # Re-load the speed-adjusted audio
            processed_audio = AudioSegment.from_file(temp_audio_path)
        else:
            processed_audio = burmese_audio

        # Apply padding if Burmese audio is shorter than the target duration
        if pad_silence_duration > 0:
            silence_ms = int(pad_silence_duration * 1000)
            processed_audio = processed_audio + AudioSegment.silent(duration=silence_ms)
            processed_audio.export(temp_audio_path, format="wav")
        else:
            processed_audio.export(temp_audio_path, format="wav")

    except Exception as e:
        raise DubbingSyncError(f"Failed to prepare audio for subtitle block {sub_index}: {e}")

    # Step 2: Extract video and synthesize with the processed Burmese audio using FFmpeg
    # We construct a complex filtergraph to handle:
    # 1. Video setpts (speed reduction) or tpad (freeze frame)
    # 2. Audio mixing (ducking original video audio + mixing with Burmese TTS audio)
    # 3. Codec and format standardization
    
    filter_v_parts = []
    filter_a_parts = []
    
    # Handle video filters
    if abs(s_video - 1.0) >= 0.005 and stretch_mode == "slow":
        # Slow down video
        pts_factor = 1.0 / s_video
        filter_v_parts.append(f"[0:v]setpts={pts_factor:.6f}*PTS[v_out]")
        video_mapped = "[v_out]"
    elif freeze_duration > 0.0:
        # Freeze last frame using tpad clone mode
        filter_v_parts.append(f"[0:v]tpad=stop_duration={freeze_duration:.6f}:stop_mode=clone[v_out]")
        video_mapped = "[v_out]"
    else:
        # Normal speed video segment
        video_mapped = "0:v"

    # Handle audio mixing filters
    if has_video_audio and duck_multiplier > 0.0:
        # Duck original audio and mix
        # Step A: Duck original audio
        if abs(s_video - 1.0) >= 0.005 and stretch_mode == "slow":
            # If video is slowed down, original audio must also be slowed down (pitch-preserved)
            pts_factor = 1.0 / s_video
            filter_a_parts.append(f"[0:a]atempo={s_video:.6f},volume={duck_multiplier:.6f}[a_ducked]")
        elif freeze_duration > 0.0:
            # If video has freeze frame, pad original audio with silence so it has the same duration
            filter_a_parts.append(f"[0:a]volume={duck_multiplier:.6f},apad[a_ducked]")
        else:
            # Normal segment mixing
            filter_a_parts.append(f"[0:a]volume={duck_multiplier:.6f}[a_ducked]")
            
        # Step B: Mix ducked original audio with Burmese TTS audio
        filter_a_parts.append(f"[a_ducked][1:a]amix=inputs=2:duration=first:dropout_transition=0[a_out]")
        audio_mapped = "[a_out]"
    else:
        # Pure dubbing mode: ignore original audio completely, only use padded/sped Burmese audio
        audio_mapped = "1:a"

    # Assemble FFmpeg command
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", video_path,
        "-i", temp_audio_path
    ]

    # Combine video and audio filters
    filter_complex = "; ".join(filter_v_parts + filter_a_parts)
    if filter_complex:
        cmd += ["-filter_complex", filter_complex]
        cmd += ["-map", video_mapped, "-map", audio_mapped]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]

    # Add standard format encoding arguments
    cmd += [
        "-c:v", video_codec,
        "-r", f"{fps:.2f}",
        "-s", f"{width}x{height}",
        "-pix_fmt", "yuv420p",
        "-c:a", audio_codec,
        "-ar", audio_rate,
        "-ac", audio_channels,
        "-shortest",  # Clamp to shortest stream duration (matching the final video)
        output_path
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else "Unknown error"
        raise DubbingSyncError(f"Failed to render segment for subtitle block {sub_index}: {error_msg}")
    finally:
        # Clean up temp audio clip
        try:
            os.remove(temp_audio_path)
        except OSError:
            pass

    return output_path


# ============================================================================
# 6. ASSEMBLE FINAL VIDEO
# ============================================================================
def assemble_final_video(segment_paths: list, output_path: str) -> str:
    """
    Concatenate all processed segment .mp4 files into a single output video.
    Uses FFmpeg concat demuxer with a safe path list file.
    """
    logger.info(f"Assembling final video with {len(segment_paths)} segments...")
    temp_dir = tempfile.mkdtemp(prefix="dub_concat_")
    list_file_path = os.path.join(temp_dir, "concat_list.txt")

    try:
        # Write concat list file
        with open(list_file_path, "w", encoding="utf-8") as f:
            for path in segment_paths:
                # Normalize file path for FFmpeg concat format (forward slashes, escaped quotes)
                safe_path = os.path.abspath(path).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        # Concat command
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file_path,
            "-c", "copy",  # Direct stream copy since all segments were standardized
            output_path
        ]

        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8", errors="replace").strip() if e.stderr else "Unknown error"
        raise DubbingSyncError(f"Final video assembly failed: {error_msg}")
    finally:
        # Clean up concat list directory
        try:
            shutil.rmtree(temp_dir)
        except OSError:
            pass

    logger.info(f"Dubbed video successfully exported to: {output_path}")
    return output_path


# ============================================================================
# CORE WORKFLOW: SYNC DUBBED VIDEO
# ============================================================================
def sync_dubbed_video(
    video_path: str,
    srt_path: str,
    audio_dir: str,
    output_path: str,
    stretch_mode: str = "freeze",
    duck_db: float = 24.0,
    keep_temp_files: bool = False
) -> str:
    """
    Orchestrates the full rubber-banding dubbing sync workflow.
    
    Args:
        video_path (str): Path to input source video.
        srt_path (str): Path to Burmese SRT subtitle file.
        audio_dir (str): Directory containing generated Burmese audio clips.
        output_path (str): Export path for the final synced video.
        stretch_mode (str): Mode for adjusting longer audio. Options: 'slow' (slow down video) or 'freeze' (freeze last frame).
        duck_db (float): Amount of attenuation (dB) to apply to original background audio during speech slots. 0 for complete mute.
        keep_temp_files (bool): If True, does not delete intermediate segment clips.
        
    Returns:
        str: Path to the generated dubbed video.
    """
    if stretch_mode not in ("slow", "freeze"):
        raise ValueError("stretch_mode must be 'slow' or 'freeze'")

    # Step 1: Parse SRT and query video info
    subtitles = parse_srt(srt_path)
    video_info = get_media_info(video_path)
    
    video_dur = video_info['duration']
    width = video_info['width']
    height = video_info['height']
    fps = video_info['fps']
    has_audio = video_info['has_audio']

    logger.info(f"Source Video details: duration={video_dur:.2f}s, resolution={width}x{height}, fps={fps:.2f}, has_audio={has_audio}")

    # Step 2: Build Timeline Segments (Gaps & Subtitle slots)
    timeline = []
    current_time = 0.0

    for sub in subtitles:
        # Add gap segment if there is silence/space before the subtitle
        if sub['start'] > current_time + 0.005:
            timeline.append({
                'type': 'gap',
                'start': current_time,
                'end': sub['start'],
                'duration': sub['start'] - current_time,
                'sub_index': None
            })

        # Add subtitle segment
        sub_end = min(sub['end'], video_dur)
        if sub_end > sub['start']:
            timeline.append({
                'type': 'subtitle',
                'start': sub['start'],
                'end': sub_end,
                'duration': sub_end - sub['start'],
                'sub_index': sub['index']
            })
        current_time = sub_end

    # Add trailing gap if there is video remaining at the end
    if video_dur > current_time + 0.005:
        timeline.append({
            'type': 'gap',
            'start': current_time,
            'end': video_dur,
            'duration': video_dur - current_time,
            'sub_index': None
        })

    logger.info(f"Timeline constructed: {len(timeline)} total segments ({len(subtitles)} subtitle segments).")

    # Step 3: Render each segment
    temp_seg_dir = tempfile.mkdtemp(prefix="dub_segments_")
    segment_files = []

    try:
        for idx, seg in enumerate(timeline):
            seg_out_path = os.path.join(temp_seg_dir, f"segment_{idx:04d}.mp4")
            
            # Print segment-level progress log
            progress_pct = (idx / len(timeline)) * 100
            print(f"[{progress_pct:5.1f}%] Processing segment {idx + 1}/{len(timeline)}: Type={seg['type'].upper()} ({seg['start']:.2f}s - {seg['end']:.2f}s, Dur={seg['duration']:.2f}s)")

            if seg['type'] == 'gap':
                process_segment(
                    video_path=video_path,
                    start=seg['start'],
                    end=seg['end'],
                    duration=seg['duration'],
                    segment_type='gap',
                    sub_index=0,
                    audio_path="",
                    output_path=seg_out_path,
                    width=width,
                    height=height,
                    fps=fps,
                    stretch_mode=stretch_mode,
                    duck_db=duck_db,
                    has_video_audio=has_audio
                )
            else:
                # Match Burmese audio
                try:
                    matched_audio_path = match_audio(audio_dir, seg['sub_index'])
                except FileNotFoundError as e:
                    logger.warning(f"Audio clip matching failed for subtitle index {seg['sub_index']}: {e}. Falling back to silence.")
                    matched_audio_path = None

                if matched_audio_path:
                    process_segment(
                        video_path=video_path,
                        start=seg['start'],
                        end=seg['end'],
                        duration=seg['duration'],
                        segment_type='subtitle',
                        sub_index=seg['sub_index'],
                        audio_path=matched_audio_path,
                        output_path=seg_out_path,
                        width=width,
                        height=height,
                        fps=fps,
                        stretch_mode=stretch_mode,
                        duck_db=duck_db,
                        has_video_audio=has_audio
                    )
                else:
                    # No audio clip found: Treat as gap (original video audio preserved, no stretching)
                    process_segment(
                        video_path=video_path,
                        start=seg['start'],
                        end=seg['end'],
                        duration=seg['duration'],
                        segment_type='gap',
                        sub_index=0,
                        audio_path="",
                        output_path=seg_out_path,
                        width=width,
                        height=height,
                        fps=fps,
                        stretch_mode=stretch_mode,
                        duck_db=duck_db,
                        has_video_audio=has_audio
                    )

            segment_files.append(seg_out_path)

        print("[100.0%] Finished rendering individual segments. Concatenating final video...")
        
        # Step 4: Assemble into final video
        output_dir = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(output_dir, exist_ok=True)
        assemble_final_video(segment_files, output_path)

    finally:
        # Clean up temporary segments unless explicitly requested to keep them
        if not keep_temp_files:
            try:
                shutil.rmtree(temp_seg_dir)
                logger.info("Cleaned up temporary segment directory.")
            except OSError as e:
                logger.warning(f"Failed to remove temp segments directory: {e}")
        else:
            logger.info(f"Temporary segment files retained in: {temp_seg_dir}")

    return output_path


# ============================================================================
# CLI INTERFACE
# ============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Sync dubbed Burmese audio clips with a source video and SRT file using rubber-banding logic."
    )
    parser.add_argument("--video", required=True, help="Path to the source English/Chinese video file")
    parser.add_argument("--srt", required=True, help="Path to the Burmese SRT subtitle file")
    parser.add_argument("--audio-dir", required=True, help="Directory containing the Burmese TTS audio clips")
    parser.add_argument("--output", required=True, help="Path to export the final dubbed video file")
    parser.add_argument(
        "--mode", 
        choices=["slow", "freeze"], 
        default="freeze", 
        help="Dynamic stretching mode when audio is longer than slot. 'freeze' (freeze last frame, default) or 'slow' (slow down video segment)"
    )
    parser.add_argument(
        "--duck", 
        type=float, 
        default=24.0, 
        help="Amount of original background audio ducking in dB during speech (default: 24.0 dB). Set to 0 to mute original audio completely during speech."
    )
    parser.add_argument(
        "--keep-temp", 
        action="store_true", 
        help="Keep intermediate processed video/audio clips in the temp directory (useful for debugging)"
    )

    args = parser.parse_args()

    try:
        sync_dubbed_video(
            video_path=args.video,
            srt_path=args.srt,
            audio_dir=args.audio_dir,
            output_path=args.output,
            stretch_mode=args.mode,
            duck_db=args.duck,
            keep_temp_files=args.keep_temp
        )
        print(f"SUCCESS: Synced video exported successfully to: {args.output}")
        sys.exit(0)
    except Exception as e:
        logger.exception("Dubbing sync job failed")
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
