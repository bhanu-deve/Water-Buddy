"""The part that really talks: Windows' built-in offline speech engine (SAPI5), driven through pyttsx3.

Everything in here is called from ONE worker thread (see speech_service.py): Windows speech objects must only be
used by the thread that created them.

Three traps in pyttsx3 2.99 on Windows were found by experiment (tests/test_speech_real.py keeps them from coming
back), and this module is built around them:

  1. engine.runAndWait() hangs forever when it is called while the engine is idle (it queues its own "end loop"
     command, which runs BEFORE the loop starts, so nothing ever ends the loop afterwards).
     -> only the "external loop" is used: startLoop(False) + iterate(), where we decide when to stop.
  2. engine.stop() leaves a stale "end of speech" event behind. If the next sentence has already started when it
     arrives, that sentence is reported as finished after a fraction of a second (the voice keeps talking, unseen).
     -> after every cancelled sentence the engine is replaced by a brand-new one, which cannot hold stale events.
  3. Calling stop() before the engine has begun the sentence leaves no "finished" event at all, so a loop waiting
     for one never ends. -> cancellation is checked before starting, and every wait has a time limit.

Also: the first sentence of a new engine starts about a second late (the voice data has to be loaded), so while the
program is idle a silent one-word warm-up sentence is spoken (warm_up()).
"""

from __future__ import annotations

import gc
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger(__name__)

POLL_SECONDS = 0.01  # how often the loop looks for events and for a cancellation
STOP_GRACE_SECONDS = 1.5  # after stop(), how long to wait for the engine to confirm before giving up on it
WARM_UP_TEXT = "Hello"  # spoken at volume 0 (silent) right after an engine is created


@dataclass(frozen=True)
class VoiceInfo:
    id: str  # what the engine needs to select the voice
    name: str  # what to show the user, e.g. "Microsoft David Desktop - English (United States)"
    languages: tuple[str, ...] = ()  # e.g. ("en-US",)
    gender: str | None = None  # "Male" / "Female" / None


class SpeechBackend(Protocol):
    """What SpeechService needs from a speech engine. All methods are called from the same worker thread."""

    def open(self) -> None:
        """Create the engine. Raises if speech is not possible on this computer."""

    def voices(self) -> list[VoiceInfo]: ...

    def warm_up(self, voice_id: str | None) -> None:
        """Called while idle: get the engine ready so that the next sentence starts without delay (may do nothing)."""

    def speak(
        self, text: str, *, rate: int, volume: float, voice_id: str | None, cancelled: Callable[[], bool]
    ) -> bool:
        """Say `text` and return when it is over: True if it was spoken to the end, False if `cancelled()` stopped it.
        `cancelled` must be polled often. Raises if the engine fails."""

    def close(self) -> None: ...


class Pyttsx3Backend:
    """Windows SAPI5 voices through pyttsx3 (fully offline)."""

    def __init__(self) -> None:
        self._engine = None
        self._applied_voice: str | None = None  # the voice this engine was last told to use
        self._cold = True  # True until this engine has spoken a sentence: its first sentence starts late
        self._com_started = False

    # ------------------------------------------------------------- lifecycle
    def open(self) -> None:
        import pythoncom  # comes with pywin32, which pyttsx3 needs on Windows

        pythoncom.CoInitialize()  # COM must be started on the thread that will use the speech objects
        self._com_started = True
        self._engine = self._new_engine()

    def close(self) -> None:
        self._engine = None
        gc.collect()  # release the speech objects BEFORE COM is shut down on this thread
        if self._com_started:
            import pythoncom

            pythoncom.CoUninitialize()
            self._com_started = False

    def _new_engine(self):
        from pyttsx3.engine import Engine

        self._applied_voice = None
        self._cold = True
        return Engine("sapi5")  # always a brand-new engine (pyttsx3.init() would hand back a cached one)

    def _replace_engine(self) -> None:
        """Throw the engine away and build a new one (see trap 2 in the module text)."""
        self._engine = None
        gc.collect()  # frees the old engine now, together with its COM objects and their event connection
        self._engine = self._new_engine()

    def warm_up(self, voice_id: str | None) -> None:
        """Speak one silent word (volume 0) so that the next real sentence starts without the usual ~1 s delay."""
        if not self._cold or self._engine is None:
            return
        try:
            self.speak(WARM_UP_TEXT, rate=200, volume=0.0, voice_id=voice_id, cancelled=lambda: False)
        except Exception as error:  # noqa: BLE001 - the warm-up is only an optimisation
            log.debug("Speech warm-up failed (ignored): %s", error)
            self._cold = False  # do not retry over and over

    # ----------------------------------------------------------------- voices
    def voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo(id=voice.id, name=voice.name, languages=tuple(voice.languages or ()), gender=voice.gender)
            for voice in self._engine.getProperty("voices")
        ]

    # --------------------------------------------------------------- speaking
    def speak(
        self, text: str, *, rate: int, volume: float, voice_id: str | None, cancelled: Callable[[], bool]
    ) -> bool:
        if cancelled():
            return False  # nothing was started, so nothing can be left behind (trap 3)
        engine = self._engine
        state: dict = {"finished": None, "error": None}

        def on_finished(name=None, completed=None, **_ignored) -> None:  # must never raise: pyttsx3 swallows it
            state["finished"] = bool(completed)

        def on_error(name=None, exception=None, **_ignored) -> None:
            state["error"] = exception
            state["finished"] = False

        tokens = [engine.connect("finished-utterance", on_finished), engine.connect("error", on_error)]
        stopped = False
        try:
            self._configure(engine, rate, volume, voice_id)
            engine.say(text)
            engine.startLoop(False)  # the "external loop": we call iterate() ourselves (trap 1)
            try:
                started = stopped_at = time.monotonic()
                limit = self._time_limit(text)
                while state["finished"] is None:
                    now = time.monotonic()
                    if not stopped and cancelled():
                        engine.stop()
                        stopped, stopped_at = True, now
                    if now - started > limit or (stopped and now - stopped_at > STOP_GRACE_SECONDS):
                        state["error"] = TimeoutError("the speech engine did not finish in time")
                        break
                    engine.iterate()
                    time.sleep(POLL_SECONDS)
            finally:
                engine.endLoop()
        finally:
            for token in tokens:
                engine.disconnect(token)

        if stopped or state["error"] is not None:
            self._replace_engine()  # stale events may follow a stop or an error (trap 2)
        else:
            self._cold = False
        if state["error"] is not None:
            raise SpeechEngineError(f"{type(state['error']).__name__}: {state['error']}")
        return bool(state["finished"]) and not stopped

    @staticmethod
    def _time_limit(text: str) -> float:
        """The longest a sentence can reasonably take, even at the slowest speed. Beyond it the engine is considered stuck."""
        return 10.0 + 0.25 * len(text)

    def _configure(self, engine, rate: int, volume: float, voice_id: str | None) -> None:
        if voice_id and voice_id != self._applied_voice:  # changing the voice is slow, so only do it when needed
            engine.setProperty("voice", voice_id)
            self._applied_voice = voice_id
        engine.setProperty("rate", int(rate))
        engine.setProperty("volume", float(volume))

    # ------------------------------------------------------ for tests / tools
    def render_to_file(self, text: str, path: str | Path, *, rate: int, volume: float, voice_id: str | None = None) -> None:
        """Speak into a WAV file instead of the speakers - silent, so tests and tools can examine real speech."""
        engine = self._engine
        problems: list = []
        token = engine.connect("error", lambda name=None, exception=None, **_ignored: problems.append(exception))
        try:
            self._configure(engine, rate, volume, voice_id)
            engine.save_to_file(text, str(Path(path).resolve()))
            engine.startLoop(False)
            try:
                engine.iterate()  # runs the queued commands; the file is written during this call
            finally:
                engine.endLoop()
        finally:
            engine.disconnect(token)
        if problems:
            self._replace_engine()
            raise SpeechEngineError(f"{type(problems[0]).__name__}: {problems[0]}")


class SpeechEngineError(RuntimeError):
    """The speech engine failed (message is safe to show to the user)."""
