"""Test doubles. FakeBackend is a speech engine that makes no sound: "speaking" just takes a little time."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Callable

from water_assistant.speech_backend import VoiceInfo

DAVID = VoiceInfo("voice-david", "Microsoft David Desktop - English (United States)", ("en-US",), "Male")
ZIRA = VoiceInfo("voice-zira", "Microsoft Zira Desktop - English (United States)", ("en-US",), "Female")


class FakeClock:
    """A clock the test moves by hand: calling it returns the pretend 'now' (like datetime.now)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **timedelta_arguments) -> None:
        self.now += timedelta(**timedelta_arguments)


class FakeBackend:
    """Implements the SpeechBackend interface without any audio.

    * speaking a sentence takes `seconds_per_character` * len(text) seconds and honours cancellation
    * every call is recorded (and on which thread it happened), so tests can check what the service asked for
    * `fail_on_open` / `fail_on_text` make the engine break; `ignore_cancel` makes it deaf to cancellation until
      `release()` is called (a hung engine)
    """

    def __init__(
        self,
        *,
        voices: list[VoiceInfo] | None = None,
        seconds_per_character: float = 0.004,
        fail_on_open: Exception | None = None,
        wait_before_open: threading.Event | None = None,
        fail_on_text: str | None = None,
        ignore_cancel: bool = False,
    ) -> None:
        self._voices = [DAVID, ZIRA] if voices is None else voices
        self.seconds_per_character = seconds_per_character
        self.fail_on_open = fail_on_open
        self.wait_before_open = wait_before_open
        self.fail_on_text = fail_on_text
        self.ignore_cancel = ignore_cancel
        self._released = threading.Event()

        self.calls: list[dict] = []  # one entry per speak(): text, rate, volume, voice_id
        self.finished_texts: list[str] = []  # sentences that were spoken to the end
        self.cancelled_texts: list[str] = []
        self.warm_ups: list[str | None] = []  # the voice id of every warm_up() call
        self.threads: dict[str, set[str]] = {"open": set(), "voices": set(), "speak": set(), "close": set(), "warm_up": set()}
        self.opened = False
        self.closed = False

    def release(self) -> None:
        """Let a hung engine (ignore_cancel=True) finish what it was doing."""
        self._released.set()

    def factory(self) -> Callable[[], "FakeBackend"]:
        return lambda: self

    # ------------------------------------------------- SpeechBackend interface
    def open(self) -> None:
        self.threads["open"].add(threading.current_thread().name)
        if self.wait_before_open is not None:
            self.wait_before_open.wait(5)
        if self.fail_on_open is not None:
            raise self.fail_on_open
        self.opened = True

    def voices(self) -> list[VoiceInfo]:
        self.threads["voices"].add(threading.current_thread().name)
        return list(self._voices)

    def warm_up(self, voice_id: str | None) -> None:
        self.threads["warm_up"].add(threading.current_thread().name)
        self.warm_ups.append(voice_id)

    def speak(self, text: str, *, rate: int, volume: float, voice_id: str | None, cancelled: Callable[[], bool]) -> bool:
        self.threads["speak"].add(threading.current_thread().name)
        self.calls.append({"text": text, "rate": rate, "volume": volume, "voice_id": voice_id})
        if self.fail_on_text is not None and self.fail_on_text in text:
            raise RuntimeError("the fake engine broke")
        end = time.monotonic() + len(text) * self.seconds_per_character
        while time.monotonic() < end:
            if self.ignore_cancel:
                if self._released.is_set():
                    return False
            elif cancelled():
                self.cancelled_texts.append(text)
                return False
            time.sleep(0.003)
        if self.ignore_cancel:
            while not self._released.is_set():  # a hung engine never returns by itself
                time.sleep(0.003)
            return False
        self.finished_texts.append(text)
        return True

    def close(self) -> None:
        self.threads["close"].add(threading.current_thread().name)
        self.closed = True
