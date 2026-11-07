"""Tests with the REAL Windows speech engine (SAPI5 through pyttsx3).

They make no sound: speech is either played at volume 0 or rendered into a WAV file, which is then examined.
They are skipped on a computer without a Windows speech voice, or when the environment variable
WATER_ASSISTANT_NO_REAL_SPEECH is "1" (handy for a quick run; in PowerShell:
$env:WATER_ASSISTANT_NO_REAL_SPEECH = "1"  and then run the tests).

Each of the problems found in pyttsx3 while building this (see the text at the top of speech_backend.py) has a
test here, so it cannot come back unnoticed.
"""

import array
import math
import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from water_assistant.speech_backend import Pyttsx3Backend
from water_assistant.speech_service import SpeechService

app = QApplication.instance() or QApplication([])

SENTENCE = "Time to drink some water."
LONG = "Your body needs water. Let's drink! Come on, take a small water break, and don't forget to stay hydrated."


def on_worker(function, timeout=90):
    """Run function(backend) on a worker thread with an opened real backend - as the program itself does - and
    return its result. (Windows speech objects must be used on the thread that created them.)"""
    outcome = {}

    def run():
        backend = Pyttsx3Backend()
        try:
            backend.open()
            outcome["value"] = function(backend)
        except BaseException as error:  # noqa: BLE001 - handed on to the test thread below
            outcome["error"] = error
        finally:
            try:
                backend.close()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=run, daemon=True, name="test-speech-worker")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("the real speech engine hung")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _speech_possible() -> bool:
    if sys.platform != "win32" or os.environ.get("WATER_ASSISTANT_NO_REAL_SPEECH") == "1":
        return False
    try:
        return bool(on_worker(lambda backend: backend.voices(), timeout=30))
    except Exception:  # noqa: BLE001 - no engine, no voice, or it hung: just skip the real tests
        return False


SPEECH_POSSIBLE = _speech_possible()
needs_speech = unittest.skipUnless(SPEECH_POSSIBLE, "no Windows speech voice (or WATER_ASSISTANT_NO_REAL_SPEECH=1)")


def examine_wav(path):
    """Length, loudness (RMS), peak and a fingerprint of a 16-bit mono WAV file."""
    with wave.open(str(path)) as wav:
        frames = wav.readframes(wav.getnframes())
        seconds = wav.getnframes() / wav.getframerate()
    samples = array.array("h")
    samples.frombytes(frames)
    rms = math.sqrt(sum(sample * sample for sample in samples) / max(1, len(samples)))
    return {"seconds": seconds, "rms": rms, "peak": max((abs(s) for s in samples), default=0), "fingerprint": hash(frames)}


@needs_speech
class RealBackendTest(unittest.TestCase):
    def render(self, backend, name, text=SENTENCE, *, rate=165, volume=1.0, voice_id=None):
        path = Path(tempfile.mkdtemp()) / f"{name}.wav"
        backend.render_to_file(text, path, rate=rate, volume=volume, voice_id=voice_id)
        return examine_wav(path)

    def test_the_installed_voices_are_listed(self):
        voices = on_worker(lambda backend: backend.voices())
        self.assertGreaterEqual(len(voices), 1)
        self.assertTrue(all(voice.id and voice.name for voice in voices))

    def test_speaking_takes_realistic_time_and_reports_completion(self):
        def job(backend):
            started = time.perf_counter()
            done = backend.speak(SENTENCE, rate=165, volume=0.0, voice_id=None, cancelled=lambda: False)
            return done, time.perf_counter() - started

        done, seconds = on_worker(job)
        self.assertTrue(done)
        self.assertTrue(1.2 < seconds < 12, f"{seconds:.2f} s is not what real speech of this sentence takes")

    def test_cancelling_mid_sentence_is_quick_and_the_next_sentence_is_complete(self):
        # pyttsx3 trap 2: a stale "end of speech" event used to end the NEXT sentence after a fraction of a second.
        def job(backend):
            started = time.perf_counter()
            backend.speak(SENTENCE, rate=165, volume=0.0, voice_id=None, cancelled=lambda: False)
            full = time.perf_counter() - started

            began = time.monotonic()
            first = backend.speak(LONG, rate=165, volume=0.0, voice_id=None, cancelled=lambda: time.monotonic() - began > 0.6)
            cancel_took = time.monotonic() - began

            started = time.perf_counter()
            second = backend.speak(SENTENCE, rate=165, volume=0.0, voice_id=None, cancelled=lambda: False)
            return full, first, cancel_took, second, time.perf_counter() - started

        full, first, cancel_took, second, second_seconds = on_worker(job)
        self.assertFalse(first, "the cancelled sentence must report that it was not spoken to the end")
        self.assertLess(cancel_took, 3.0)
        self.assertTrue(second)
        self.assertGreater(second_seconds, 0.7 * full, f"the sentence after the cancel took {second_seconds:.2f} s, normally {full:.2f} s")

    def test_cancelling_before_the_sentence_starts_returns_at_once(self):
        # pyttsx3 trap 3: no "finished" event ever came, so a loop waiting for it never ended.
        def job(backend):
            started = time.perf_counter()
            done = backend.speak(LONG, rate=165, volume=0.0, voice_id=None, cancelled=lambda: True)
            return done, time.perf_counter() - started

        done, seconds = on_worker(job)
        self.assertFalse(done)
        self.assertLess(seconds, 0.5)

    def test_several_cancel_and_speak_cycles_in_a_row(self):
        def job(backend):
            started = time.perf_counter()
            backend.speak(SENTENCE, rate=165, volume=0.0, voice_id=None, cancelled=lambda: False)
            full = time.perf_counter() - started
            results = []
            for cancel_after in (0.15, 0.7):
                began = time.monotonic()
                first = backend.speak(LONG, rate=165, volume=0.0, voice_id=None, cancelled=lambda: time.monotonic() - began > cancel_after)
                started = time.perf_counter()
                second = backend.speak(SENTENCE, rate=165, volume=0.0, voice_id=None, cancelled=lambda: False)
                results.append((first, second, time.perf_counter() - started))
            return full, results

        full, results = on_worker(job)
        for first, second, seconds in results:
            self.assertFalse(first)
            self.assertTrue(second)
            self.assertGreater(seconds, 0.7 * full)

    def test_rendered_speech_is_real_audio(self):
        wav = on_worker(lambda backend: self.render(backend, "audio"))
        self.assertTrue(1.0 < wav["seconds"] < 8.0, wav)
        self.assertGreater(wav["rms"], 500, "the audio is practically silent")
        self.assertGreater(wav["peak"], 3000)

    def test_the_volume_setting_changes_how_loud_the_audio_is(self):
        def job(backend):
            return self.render(backend, "loud", volume=1.0), self.render(backend, "quiet", volume=0.3)

        loud, quiet = on_worker(job)
        self.assertTrue(0.02 < quiet["rms"] / loud["rms"] < 0.5, f"quiet/loud loudness = {quiet['rms'] / loud['rms']:.2f}")

    def test_the_speed_setting_changes_how_long_the_audio_is(self):
        def job(backend):
            return self.render(backend, "slow", rate=120), self.render(backend, "fast", rate=225)

        slow, fast = on_worker(job)
        self.assertGreater(slow["seconds"], 1.3 * fast["seconds"])

    def test_the_speed_presets_are_clearly_different_in_real_audio(self):
        def job(backend):
            return [self.render(backend, name, rate=rate)["seconds"] for name, rate in SpeechService.RATE_PRESETS]

        slow, normal, fast = on_worker(job)
        self.assertGreater(slow, 1.08 * normal)
        self.assertGreater(normal, 1.08 * fast)

    def test_different_voices_really_sound_different(self):
        def job(backend):
            return [self.render(backend, f"voice{i}", voice_id=voice.id) for i, voice in enumerate(backend.voices())]

        renders = on_worker(job)
        if len(renders) < 2:
            self.skipTest("only one Windows voice is installed")
        self.assertEqual(len({render["fingerprint"] for render in renders}), len(renders))


@needs_speech
class RealServiceTest(unittest.TestCase):
    """The whole chain - SpeechService, worker thread, real engine - at volume 0 (silent, but really 'speaking')."""

    def setUp(self):
        self.service = SpeechService(volume=0.0)
        self.events = []
        self.service.utteranceStarted.connect(lambda uid: self.events.append(("started", uid, time.monotonic())))
        self.service.utteranceFinished.connect(lambda uid, ok: self.events.append(("finished", uid, ok, time.monotonic())))
        self.addCleanup(self.service.shutdown)
        self.service.start()
        self.assertTrue(self.wait_until(lambda: self.service.voices or not self.service.available, 15000), "engine never became ready")
        self.assertTrue(self.service.available)

    def wait_until(self, condition, timeout_ms=15000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if condition():
                return True
            QTest.qWait(10)
        return bool(condition())

    def kinds(self):
        return [event[0] for event in self.events]

    def test_the_voices_are_listed_and_a_default_voice_is_chosen(self):
        self.assertGreaterEqual(len(self.service.voices), 1)
        self.assertIn(self.service.voice_id, {voice.id for voice in self.service.voices})

    def test_a_sentence_is_reported_as_started_and_then_finished_after_realistic_time(self):
        self.service.speak(SENTENCE)
        self.assertTrue(self.wait_until(lambda: "finished" in self.kinds()))
        self.assertEqual(self.kinds(), ["started", "finished"])
        started, finished = self.events
        self.assertTrue(finished[2], "the sentence should have been spoken to the end")
        self.assertTrue(1.2 < finished[3] - started[2] < 12, f"{finished[3] - started[2]:.2f} s")

    def test_stop_interrupts_a_real_sentence_quickly(self):
        self.service.speak(LONG)
        self.assertTrue(self.wait_until(lambda: "started" in self.kinds()))
        QTest.qWait(500)
        stopped_at = time.monotonic()
        self.service.stop()
        self.assertTrue(self.wait_until(lambda: "finished" in self.kinds(), 5000))
        finished = self.events[-1]
        self.assertFalse(finished[2])
        self.assertLess(finished[3] - stopped_at, 2.5)

    def test_a_sentence_requested_right_after_another_replaces_it_and_is_spoken_completely(self):
        self.service.speak(LONG)
        self.assertTrue(self.wait_until(lambda: "started" in self.kinds()))
        QTest.qWait(400)
        second = self.service.speak(SENTENCE)  # interrupts the long one
        self.assertTrue(self.wait_until(lambda: any(e[0] == "finished" and e[1] == second for e in self.events), 20000))
        first_finish = next(e for e in self.events if e[0] == "finished" and e[1] == 1)
        second_finish = next(e for e in self.events if e[0] == "finished" and e[1] == second)
        self.assertFalse(first_finish[2])
        self.assertTrue(second_finish[2])
        second_start = next(e for e in self.events if e[0] == "started" and e[1] == second)
        self.assertGreater(second_finish[3] - second_start[2], 1.0, "the second sentence was cut short")

    def test_shutting_down_in_the_middle_of_a_sentence_is_prompt_and_clean(self):
        self.service.speak(LONG)
        self.assertTrue(self.wait_until(lambda: "started" in self.kinds()))
        QTest.qWait(500)
        worker = self.service._thread
        started = time.monotonic()
        self.service.shutdown()
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertFalse(worker.is_alive(), "the speech thread did not end")


if __name__ == "__main__":
    unittest.main()
