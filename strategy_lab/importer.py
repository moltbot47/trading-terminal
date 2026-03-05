"""YouTube strategy importer — transcribe video, extract strategy rules via Claude."""

import json
import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger(__name__)

# Claude API for strategy extraction
_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

EXTRACTION_PROMPT = """You are a trading strategy extraction engine. Given a transcript from a trading YouTube video, extract the strategy into a structured JSON format.

IMPORTANT: Extract ONLY the concrete, mechanical trading rules. Ignore opinions, stories, and non-actionable content.

Return ONLY valid JSON with this exact structure (no markdown, no explanation):

{
  "name": "Short descriptive strategy name",
  "description": "1-2 sentence summary of the strategy",
  "timeframe": "5m",
  "instruments": ["MNQ", "MYM", "MES", "MBT"],
  "entry_rules": [
    {
      "indicator": "INDICATOR_NAME",
      "params": {"period": 14},
      "condition": ">",
      "value": 25
    }
  ],
  "direction_rules": [
    {
      "indicator": "price",
      "condition": ">",
      "reference": {"indicator": "EMA", "params": {"period": 200}},
      "direction": "long"
    },
    {
      "indicator": "price",
      "condition": "<",
      "reference": {"indicator": "EMA", "params": {"period": 200}},
      "direction": "short"
    }
  ],
  "exit_rules": {
    "stop_loss": {"method": "atr_multiple", "multiplier": 1.5, "period": 14},
    "take_profit": {"method": "risk_reward", "ratio": 2.0}
  },
  "indicators_config": [
    {"indicator": "EMA", "params": {"period": 9}},
    {"indicator": "EMA", "params": {"period": 21}},
    {"indicator": "RSI", "params": {"period": 14}},
    {"indicator": "ADX", "params": {"period": 14}},
    {"indicator": "ATR", "params": {"period": 14}}
  ],
  "risk_reward_target": 2.0
}

Available indicators: EMA, SMA, RSI, MACD, ADX, ATR, BOLLINGER, STOCHASTIC, VWAP
Available conditions: >, <, >=, <=, ==, crosses_above, crosses_below
Available stop methods: atr_multiple, fixed_points, fixed_percent
Available TP methods: risk_reward, fixed_points, fixed_percent
Available instruments: MNQ (Micro Nasdaq), MYM (Micro Dow), MES (Micro S&P), MBT (Micro Bitcoin)

If the video discusses a specific instrument, only include that one. Otherwise include all.
If the video discusses a specific timeframe, use it. Otherwise default to 5m.

indicators_config MUST list every indicator referenced in entry_rules, direction_rules, and exit_rules.

TRANSCRIPT:
"""


def transcribe_youtube(url: str) -> str | None:
    """Download audio from YouTube URL and transcribe with Whisper.

    Returns transcript text or None on failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")

        # Download audio with yt-dlp
        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "-x",
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
            # yt-dlp sometimes adds extension
            for f in os.listdir(tmpdir):
                if f.endswith(".mp3"):
                    audio_path = os.path.join(tmpdir, f)
                    break
            else:
                logger.error("No audio file produced")
                return None

        # Transcribe with Whisper CLI (if available) or Python whisper
        transcript = _transcribe_with_whisper_cli(audio_path)
        if transcript is None:
            transcript = _transcribe_with_whisper_python(audio_path)

        return transcript


def _transcribe_with_whisper_cli(audio_path: str) -> str | None:
    """Try using whisper CLI."""
    try:
        result = subprocess.run(
            ["whisper", audio_path, "--model", "base", "--output_format", "txt", "--language", "en"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            # Whisper writes .txt next to the audio file
            txt_path = audio_path.rsplit(".", 1)[0] + ".txt"
            if os.path.exists(txt_path):
                with open(txt_path) as f:
                    return f.read().strip()
            return result.stdout.strip() if result.stdout.strip() else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _transcribe_with_whisper_python(audio_path: str) -> str | None:
    """Try using Python whisper package."""
    try:
        import whisper
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, language="en")
        return result.get("text", "").strip() or None
    except ImportError:
        logger.warning("Neither whisper CLI nor Python whisper available")
        return None
    except Exception as e:
        logger.error("Whisper transcription failed: %s", e)
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
            max_tokens=2000,
            messages=[
                {"role": "user", "content": EXTRACTION_PROMPT + transcript[:8000]}
            ],
        )
        response_text = message.content[0].text.strip()

        # Try to parse JSON from response (handle markdown code blocks)
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

    Returns dict with strategy data ready for models.create_strategy(),
    or None on failure.
    """
    logger.info("Importing strategy from: %s", url)

    transcript = transcribe_youtube(url)
    if not transcript:
        return {"error": "Failed to transcribe video"}

    strategy = extract_strategy_from_transcript(transcript)
    if not strategy:
        return {"error": "Failed to extract strategy from transcript"}

    strategy["source_url"] = url
    strategy["source_type"] = "youtube"
    strategy["transcript"] = transcript[:10000]  # Truncate for storage

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
