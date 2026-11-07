"""The "Voice" entry of the right-click menu: speak aloud on/off, test, stop, speed, volume and voice.

It is rebuilt every time the menu opens, so the check marks always show the current settings.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QMenu

from .speech_service import SpeechService

TEST_SENTENCE = "Hi! This is how I sound. Stay hydrated!"


def populate_voice_menu(menu: QMenu, speech: SpeechService, say: Callable[[str], None]) -> None:
    """Add a "Voice" submenu to `menu`. `say(text)` shows the text in the speech bubble AND speaks it."""
    voice_menu = menu.addMenu("Voice")
    if not speech.available:
        voice_menu.addAction("Voice not available on this computer").setEnabled(False)
        return

    speak_aloud = voice_menu.addAction("Speak aloud")
    speak_aloud.setCheckable(True)
    speak_aloud.setChecked(speech.enabled)
    speak_aloud.toggled.connect(speech.set_enabled)

    test = voice_menu.addAction("Test voice")
    test.setEnabled(speech.enabled)
    test.triggered.connect(lambda _checked=False: say(TEST_SENTENCE))

    stop = voice_menu.addAction("Stop speaking")
    stop.setEnabled(speech.speaking)
    stop.triggered.connect(lambda _checked=False: speech.stop())

    voice_menu.addSeparator()
    _choice_menu(
        voice_menu,
        "Speed",
        [(name, rate) for name, rate in SpeechService.RATE_PRESETS],
        is_current=lambda rate: speech.rate == rate,
        apply=speech.set_rate,
    )
    _choice_menu(
        voice_menu,
        "Volume",
        [(f"{name} ({round(volume * 100)} %)", volume) for name, volume in SpeechService.VOLUME_PRESETS],
        is_current=lambda volume: abs(speech.volume - volume) < 0.005,
        apply=speech.set_volume,
    )
    if speech.voices:
        _choice_menu(
            voice_menu,
            "Voice",
            [(_short_voice_name(voice), voice.id) for voice in speech.voices],
            is_current=lambda voice_id: speech.voice_id == voice_id,
            apply=speech.set_voice,
        )


def _short_voice_name(voice) -> str:
    """'Microsoft David Desktop - English (United States)' -> 'Microsoft David Desktop (en-US)'."""
    name = voice.name.split(" - ")[0]
    return f"{name} ({voice.languages[0]})" if voice.languages else name


def _choice_menu(parent: QMenu, title: str, choices: list, *, is_current: Callable, apply: Callable) -> None:
    """A submenu of mutually exclusive choices (a tick shows the current one)."""
    submenu = parent.addMenu(title)
    group = QActionGroup(submenu)
    group.setExclusive(True)
    for label, value in choices:
        action = submenu.addAction(label)
        action.setCheckable(True)
        action.setChecked(bool(is_current(value)))
        group.addAction(action)
        action.triggered.connect(lambda _checked=False, value=value: apply(value))
