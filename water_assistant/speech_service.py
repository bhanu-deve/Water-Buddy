"""Text-to-speech service: lets the rest of the program say things aloud without ever freezing the screen.

How it works (for the curious):
  * Windows' offline speech engine (SAPI5) is driven by a *backend* (speech_backend.py).
  * Speaking takes seconds and blocks, so it happens on a worker thread of its own. Windows speech objects must
    only be used by the thread that created them, so the worker creates the engine, speaks, and is the only one
    that ever touches it.
  * The rest of the program talks to the worker through a queue, and hears back (started / finished / error) as Qt
    signals that are always delivered on the main thread, so the handlers may safely touch widgets.
  * stop() needs no locks: it just raises a counter ("everything numbered below N is cancelled") which the worker
    looks at about 100 times a second while it speaks.

Use it from the main (GUI) thread only.
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot

from .speech_backend import Pyttsx3Backend, SpeechBackend, VoiceInfo

log = logging.getLogger(__name__)

MIN_RATE, MAX_RATE = 60, 400  # words per minute that the settings may ask for

_NEW, _STARTING, _READY, _FAILED, _STOPPED = "new", "starting", "ready", "failed", "stopped"
_SHUTDOWN = object()  # put on the queue to tell the worker to finish


@dataclass(frozen=True)
class _Job:
    id: int
    text: str
    rate: int
    volume: float
    voice_id: str | None


def pick_default_voice(voices: list[VoiceInfo]) -> str | None:
    """The voice used when the user has not chosen one: English first, and a male voice for this male character."""

    def score(voice: VoiceInfo) -> int:
        points = 0
        if any(language.lower().startswith("en") for language in voice.languages):
            points += 2
        if (voice.gender or "").lower() == "male":
            points += 1
        return points

    # max() keeps the FIRST voice among equals, so Windows' own ordering decides ties.
    return max(voices, key=score).id if voices else None


class SpeechService(QObject):
    """Speaks text aloud on a background thread. All the settings take effect from the next sentence."""

    utteranceStarted = Signal(int)  # the engine began speaking this utterance (id from speak())
    utteranceFinished = Signal(int, bool)  # utterance id, True if it was spoken to the end (False: cancelled / failed)
    availabilityChanged = Signal(bool)  # the engine became ready (True) or could not be started (False)
    errorOccurred = Signal(str)  # something went wrong (already logged); the text is safe to show to the user

    _fromWorker = Signal(str, object)  # private: worker thread -> main thread

    RATE_PRESETS = (("Slow", 120), ("Normal", 165), ("Fast", 225))  # words per minute; see README on SAPI's 11 % steps
    VOLUME_PRESETS = (("Quiet", 0.4), ("Medium", 0.7), ("Loud", 1.0))
    DEFAULT_RATE = 165
    DEFAULT_VOLUME = 0.7

    def __init__(
        self,
        backend_factory: Callable[[], SpeechBackend] = Pyttsx3Backend,
        *,
        enabled: bool = True,
        rate: int = DEFAULT_RATE,
        volume: float = DEFAULT_VOLUME,
        voice_id: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend_factory = backend_factory
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._state = _NEW
        self._voices: list[VoiceInfo] = []
        self._next_id = 1
        self._cancel_before = 1  # utterances numbered below this are cancelled (the worker thread reads it)
        self._current_id = 0  # utterance being spoken right now, as far as the main thread knows (0 = none)

        self._enabled = bool(enabled)
        self._rate = self._clamp_rate(rate)
        self._volume = self._clamp_volume(volume)
        self._voice_id = voice_id

        # QueuedConnection: the slot always runs on the main thread, whichever thread emits.
        self._fromWorker.connect(self._on_worker_event, Qt.ConnectionType.QueuedConnection)

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Start the worker thread and warm the engine up in the background (so the first sentence is quick)."""
        if self._thread is not None:
            return
        self._state = _STARTING
        self._thread = threading.Thread(target=self._run, name="water-assistant-speech", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop speaking and end the worker thread. Call when the program exits."""
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self.stop()
        self._jobs.put(_SHUTDOWN)
        thread.join(timeout)
        if thread.is_alive():  # a daemon thread cannot keep the program alive, so just say so
            log.warning("The speech worker did not stop within %.1f s; leaving it behind.", timeout)
        self._state = _STOPPED
        self._current_id = 0

    # ----------------------------------------------------------- information
    @property
    def available(self) -> bool:
        """True while the voice can speak (it is starting up or ready); False if it failed or was never started."""
        return self._state in (_STARTING, _READY)

    @property
    def voices(self) -> list[VoiceInfo]:
        """The installed Windows voices (empty until the engine is ready)."""
        return list(self._voices)

    @property
    def speaking(self) -> bool:
        return self._current_id != 0

    # -------------------------------------------------------------- settings
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.stop()

    @property
    def rate(self) -> int:
        """Speed in words per minute (approximate: Windows uses steps of about 11 %)."""
        return self._rate

    def set_rate(self, words_per_minute: int) -> None:
        self._rate = self._clamp_rate(words_per_minute)

    @property
    def volume(self) -> float:
        """0.0 (silent) ... 1.0 (full)."""
        return self._volume

    def set_volume(self, volume: float) -> None:
        self._volume = self._clamp_volume(volume)

    @property
    def voice_id(self) -> str | None:
        return self._voice_id

    def set_voice(self, voice_id: str) -> None:
        """Choose one of `voices`. An unknown id is ignored (once the list is known)."""
        if self._voices and voice_id not in {voice.id for voice in self._voices}:
            log.warning("Ignoring unknown voice id %r", voice_id)
            return
        self._voice_id = voice_id

    @staticmethod
    def _clamp_rate(rate: float) -> int:
        return int(min(MAX_RATE, max(MIN_RATE, round(rate))))

    @staticmethod
    def _clamp_volume(volume: float) -> float:
        return round(min(1.0, max(0.0, float(volume))), 3)

    # --------------------------------------------------------------- speaking
    def speak(self, text: str, *, interrupt: bool = True) -> int:
        """Say `text` aloud and return at once with an id for this utterance.

        Returns 0 (and says nothing) when the text is empty, speech is switched off, or the engine is unavailable.
        With `interrupt` (the default) anything still being said or waiting is cancelled first.
        """
        text = " ".join(str(text).split())  # one line, single spaces
        if not text or not self._enabled or not self.available:
            return 0
        if interrupt:
            self.stop()
        utterance_id = self._next_id
        self._next_id += 1
        self._jobs.put(_Job(utterance_id, text, self._rate, self._volume, self._voice_id))
        return utterance_id

    def stop(self) -> None:
        """Cancel the utterance being spoken and everything still waiting (each one reports finished, False)."""
        self._cancel_before = self._next_id

    # ------------------------------------------------------ the worker thread
    def _run(self) -> None:
        backend: SpeechBackend | None = None
        try:
            backend = self._backend_factory()
            backend.open()
            voices = backend.voices()
        except Exception as error:  # noqa: BLE001 - whatever is wrong with the engine must only silence the voice
            log.warning("The speech engine could not be started: %s", error, exc_info=True)
            self._fromWorker.emit("failed", f"{type(error).__name__}: {error}")
            self._report_unspoken_until_shutdown()
            self._close(backend)
            return

        self._fromWorker.emit("ready", voices)
        self._warm_up_if_idle(backend, voices)
        while True:
            job = self._jobs.get()
            if job is _SHUTDOWN:
                break
            self._speak_one(backend, job)
            self._warm_up_if_idle(backend, voices)
        self._close(backend)

    def _warm_up_if_idle(self, backend: SpeechBackend, voices: list[VoiceInfo]) -> None:
        """While nothing is waiting, get the engine ready for the next sentence (saves about a second of delay)."""
        if not self._jobs.empty():
            return
        try:
            backend.warm_up(self._voice_id or pick_default_voice(voices))
        except Exception as error:  # noqa: BLE001 - only an optimisation
            log.debug("Warm-up failed (ignored): %s", error)

    def _speak_one(self, backend: SpeechBackend, job: _Job) -> None:
        if job.id < self._cancel_before:  # cancelled while it was waiting in the queue
            self._fromWorker.emit("finished", (job.id, False))
            return
        self._fromWorker.emit("started", job.id)
        completed = False
        try:
            completed = bool(
                backend.speak(
                    job.text,
                    rate=job.rate,
                    volume=job.volume,
                    voice_id=job.voice_id,
                    cancelled=lambda: job.id < self._cancel_before,
                )
            )
        except Exception as error:  # noqa: BLE001 - one failed sentence must not end the voice
            log.warning("Speaking failed: %s", error, exc_info=True)
            self._fromWorker.emit("error", f"{type(error).__name__}: {error}")
        self._fromWorker.emit("finished", (job.id, completed))

    def _report_unspoken_until_shutdown(self) -> None:
        """The engine is dead: answer every queued sentence with "not spoken" so nobody waits for it forever."""
        while True:
            job = self._jobs.get()
            if job is _SHUTDOWN:
                return
            self._fromWorker.emit("finished", (job.id, False))

    @staticmethod
    def _close(backend: SpeechBackend | None) -> None:
        if backend is None:
            return
        try:
            backend.close()
        except Exception as error:  # noqa: BLE001
            log.warning("Closing the speech engine failed: %s", error)

    # ------------------------------------------- back on the main thread
    @Slot(str, object)
    def _on_worker_event(self, kind: str, payload: object) -> None:
        if self._state == _STOPPED:
            return
        if kind == "ready":
            self._voices = list(payload)  # type: ignore[arg-type]
            self._state = _READY
            if self._voice_id is None:
                self._voice_id = pick_default_voice(self._voices)
            self.availabilityChanged.emit(True)
        elif kind == "failed":
            self._state = _FAILED
            self._current_id = 0
            self.errorOccurred.emit(str(payload))
            self.availabilityChanged.emit(False)
        elif kind == "started":
            self._current_id = int(payload)  # type: ignore[arg-type]
            self.utteranceStarted.emit(self._current_id)
        elif kind == "finished":
            utterance_id, completed = payload  # type: ignore[misc]
            if self._current_id == utterance_id:
                self._current_id = 0
            self.utteranceFinished.emit(int(utterance_id), bool(completed))
        elif kind == "error":
            self.errorOccurred.emit(str(payload))
