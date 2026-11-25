"""The voices a reply can be read in (BL-53).

One voice is one MuAPI task plus the voice name it takes. This module imports nothing from the app so
the settings registry can check a saved choice against it without pulling in the speech service.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    model: str
    voice_id: str
    max_chars: int
    # MuAPI's list price for a short reply, shown beside the choice. The real quote is shown after
    # each reply, because MuAPI's prices change.
    price_hint: str


VOICES: dict[str, Voice] = {voice.id: voice for voice in (
    Voice("eleven-daniel", "Daniel (ElevenLabs)", "elevenlabs-v4-turbo", "voice_daniel", 5000, "from about $0.04"),
    Voice("eleven-liam", "Liam (ElevenLabs)", "elevenlabs-v4-turbo", "voice_liam", 5000, "from about $0.04"),
    Voice("fish-narrator", "Narrator (Fish Audio)", "fish-s2-1-pro", "voice_fish_narrator", 10000, "from about $0.015"),
)}
DEFAULT_VOICE = "eleven-daniel"
VOICE_IDS = tuple(VOICES)
