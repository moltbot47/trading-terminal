"""YouTube strategy importer — transcribe video, extract strategy rules via Claude."""

import json
import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

EXTRACTION_PROMPT = """You are a trading strategy extraction engine. Given a transcript from a trading YouTube video, extract the strategy into a structured JSON format.

IMPORTANT: Extract ONLY the concrete, mechanical trading rules. Ignore opinions, stories, and non-actionable content.

Return ONLY valid JSON with this exact structure (no markdown, no explanation):

{
  "name": "Short descriptive strategy name",
  "description": "1-2 sentence summary of the strategy",
  "timeframe": "5m",
  "instruments": ["MNQ", "MYM", "MES", "MBT"],
  "highlights": [
    "Key insight or rule #1 from the video",
    "Key insight or rule #2",
    "Key insight or rule #3"
  ],
  "entry_rules": [
    {
      "indicator": "INDICATOR_NAME",
      "params": {"period": 14},
      "condition": ">",
      "value": 25,
      "label": "ADX above 25 (trending market)"
    }
  ],
  "direction_rules": [
    {
      "indicator": "price",
      "condition": ">",
      "reference": {"indicator": "EMA", "params": {"period": 200}},
      "direction": "long",
      "label": "Price above 200 EMA = bullish"
    },
    {
      "indicator": "price",
      "condition": "<",
      "reference": {"indicator": "EMA", "params": {"period": 200}},
      "direction": "short",
      "label": "Price below 200 EMA = bearish"
    }
  ],
  "exit_rules": {
    "stop_loss": {"method": "atr_multiple", "multiplier": 1.5, "period": 14, "label": "1.5x ATR stop"},
    "take_profit": {"method": "risk_reward", "ratio": 2.0, "label": "2:1 reward-to-risk"}
  },
  "indicators_config": [
    {"indicator": "EMA", "params": {"period": 9}},
    {"indicator": "EMA", "params": {"period": 21}},
    {"indicator": "RSI", "params": {"period": 14}},
    {"indicator": "ADX", "params": {"period": 14}},
    {"indicator": "ATR", "params": {"period": 14}}
  ],
  "risk_reward_target": 2.0,
  "edge_summary": "What gives this strategy an edge, in one sentence"
}

Available indicators: EMA, SMA, RSI, MACD, ADX, ATR, BOLLINGER, STOCHASTIC, VWAP
Available conditions: >, <, >=, <=, ==, crosses_above, crosses_below
Available stop methods: atr_multiple, fixed_points, fixed_percent
Available TP methods: risk_reward, fixed_points, fixed_percent
Available instruments: MNQ (Micro Nasdaq), MYM (Micro Dow), MES (Micro S&P), MBT (Micro Bitcoin)

Rules:
- Each entry_rule and direction_rule MUST have a "label" field with a plain-English explanation.
- "highlights" should be the 3-7 most important takeaways or rules from the video.
- If the video discusses a specific instrument, only include that one. Otherwise include all.
- If the video discusses a specific timeframe, use it. Otherwise default to 5m.
- indicators_config MUST list every indicator referenced in entry_rules, direction_rules, and exit_rules.
- "edge_summary" is one sentence describing what makes this strategy work.

TRANSCRIPT:
"""


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:v=|/v/)([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"shorts/([a-zA-Z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _transcribe_via_captions(url: str) -> dict | None:
    """Grab YouTube's built-in captions (fastest, no audio download needed)."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.warning("youtube-transcript-api not installed")
        return None

    video_id = _extract_video_id(url)
    if not video_id:
        logger.warning("Could not extract video ID from URL: %s", url)
        return None

    try:
        ytt = YouTubeTranscriptApi()
        entries = ytt.fetch(video_id, languages=["en"])

        segments = []
        text_parts = []
        for entry in entries:
            seg_text = str(getattr(entry, "text", "")).strip()
            start = float(getattr(entry, "start", 0))
            duration = float(getattr(entry, "duration", 0))
            if seg_text:
                segments.append({
                    "start": round(start, 1),
                    "end": round(start + duration, 1),
                    "text": seg_text,
                })
                text_parts.append(seg_text)

        full_text = " ".join(text_parts)
        if not full_text:
            return None

        duration = segments[-1]["end"] if segments else 0
        logger.info("Got YouTube captions: %d chars, %d segments", len(full_text), len(segments))
        return {
            "text": full_text,
            "segments": segments,
            "language": "en",
            "duration": round(duration, 1),
        }
    except Exception as e:
        logger.warning("YouTube captions not available: %s", e)
        return None


def transcribe_youtube(url: str) -> dict | None:
    """Transcribe a YouTube video.

    Strategy:
    1. Try YouTube's built-in captions (instant, no download)
    2. Fall back to yt-dlp + faster-whisper/whisper CLI

    Returns dict with 'text' and 'segments', or None on failure.
    """
    title = _get_video_title(url)

    # Primary: YouTube captions (fast, no audio needed)
    transcript = _transcribe_via_captions(url)
    if transcript:
        if title:
            transcript["title"] = title
        return transcript

    logger.info("No captions available, falling back to audio transcription...")

    # Fallback: download audio + whisper
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")

        try:
            result = subprocess.run(
                [
                    "yt-dlp", "-x",
                    "--audio-format", "mp3",
                    "--audio-quality", "5",
                    "-o", audio_path,
                    "--no-playlist",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error("yt-dlp failed: %s", result.stderr)
                return None
        except FileNotFoundError:
            logger.error("yt-dlp not found — install with: brew install yt-dlp")
            return None
        except subprocess.TimeoutExpired:
            logger.error("yt-dlp timed out")
            return None

        if not os.path.exists(audio_path):
            for f in os.listdir(tmpdir):
                if f.endswith(".mp3"):
                    audio_path = os.path.join(tmpdir, f)
                    break
            else:
                logger.error("No audio file produced")
                return None

        transcript = _transcribe_faster_whisper(audio_path)
        if transcript is None:
            text = _transcribe_with_whisper_cli(audio_path)
            if text:
                transcript = {"text": text, "segments": []}

        if transcript and title:
            transcript["title"] = title

        return transcript


def _get_video_title(url: str) -> str | None:
    """Extract video title via yt-dlp."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--get-title", "--no-playlist", url],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _transcribe_faster_whisper(audio_path: str) -> dict | None:
    """Transcribe using faster-whisper (CTranslate2 backend)."""
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments_gen, info = model.transcribe(audio_path, language="en")

        segments = []
        full_text_parts = []
        for seg in segments_gen:
            segments.append({
                "start": round(seg.start, 1),
                "end": round(seg.end, 1),
                "text": seg.text.strip(),
            })
            full_text_parts.append(seg.text.strip())

        full_text = " ".join(full_text_parts)
        if not full_text:
            return None

        return {
            "text": full_text,
            "segments": segments,
            "language": info.language,
            "duration": round(info.duration, 1),
        }
    except ImportError:
        logger.warning("faster-whisper not installed — pip install faster-whisper")
        return None
    except Exception as e:
        logger.error("faster-whisper transcription failed: %s", e)
        return None


def _transcribe_with_whisper_cli(audio_path: str) -> str | None:
    """Fallback: try using whisper CLI."""
    try:
        result = subprocess.run(
            ["whisper", audio_path, "--model", "base", "--output_format", "txt", "--language", "en"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            txt_path = audio_path.rsplit(".", 1)[0] + ".txt"
            if os.path.exists(txt_path):
                with open(txt_path) as f:
                    return f.read().strip()
            return result.stdout.strip() if result.stdout.strip() else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def extract_strategy_from_transcript(transcript: str) -> dict | None:
    """Use Claude API to extract structured strategy rules from transcript."""
    if not _ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY not set — cannot extract strategy")
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-5-20250514",
            max_tokens=3000,
            messages=[
                {"role": "user", "content": EXTRACTION_PROMPT + transcript[:8000]}
            ],
        )
        response_text = message.content[0].text.strip()

        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return json.loads(response_text)

    except ImportError:
        logger.error("anthropic package not installed — pip install anthropic")
        return None
    except json.JSONDecodeError as e:
        logger.error("Failed to parse strategy JSON: %s", e)
        return None
    except Exception as e:
        logger.error("Claude API error: %s", e)
        return None


def import_from_youtube(url: str) -> dict | None:
    """Full pipeline: YouTube URL -> transcript -> strategy rules.

    Returns dict with strategy data + transcript metadata,
    or {"error": "..."} on failure.
    """
    logger.info("Importing strategy from: %s", url)

    result = transcribe_youtube(url)
    if not result or not result.get("text"):
        return {"error": "Failed to transcribe video"}

    transcript_text = result["text"]
    strategy = extract_strategy_from_transcript(transcript_text)
    if not strategy:
        return {"error": "Failed to extract strategy from transcript"}

    strategy["source_url"] = url
    strategy["source_type"] = "youtube"
    strategy["transcript"] = transcript_text[:10000]

    # Attach metadata
    if result.get("title"):
        strategy.setdefault("name", result["title"][:80])
    if result.get("duration"):
        strategy["video_duration"] = result["duration"]
    if result.get("segments"):
        strategy["transcript_segments"] = result["segments"][:200]

    return strategy


def import_from_transcript(transcript: str, source_url: str = "") -> dict | None:
    """Import from a raw transcript (skip YouTube download)."""
    strategy = extract_strategy_from_transcript(transcript)
    if not strategy:
        return {"error": "Failed to extract strategy from transcript"}

    strategy["source_url"] = source_url
    strategy["source_type"] = "transcript"
    strategy["transcript"] = transcript[:10000]
    return strategy
