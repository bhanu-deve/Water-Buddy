import threading
import time
import unittest

from PySide6.QtCore import Slot
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from water_assistant.speech_backend import VoiceInfo
from water_assistant.speech_service import MAX_RATE, MIN_RATE, SpeechService, pick_default_voice

from .fakes import DAVID, ZIRA, FakeBackend

app = QApplication.instance() or QApplication([])


class ServiceTestCase(unittest.TestCase):
    """Builds a SpeechService on top of a FakeBackend (no sound) and records every signal it emits."""

    def make(self, backend=None, *, start=True, **settings):
        backend = backend or FakeBackend()
        service = SpeechService(backend.factory(), **settings)
        self.addCleanup(service.shutdown)
        self.events = []
        service.utteranceStarted.connect(lambda uid: self.events.append(("started", uid)))
        service.utteranceFinished.connect(lambda uid, ok: self.events.append(("finished", uid, ok)))
        service.errorOccurred.connect(lambda message: self.events.append(("error", message)))
        service.availabilityChanged.connect(lambda ok: self.events.append(("available", ok)))
        if start:
            service.start()
            self.assertTrue(self.wait_until(lambda: service.voices or not service.available), "engine never became ready")
        return service, backend

    def wait_until(self, condition, timeout_ms=4000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if condition():
                return True
            QTest.qWait(5)
        return bool(condition())

    def saw(self, event):
        return lambda: event in self.events

    def finished_events(self):
        return [event for event in self.events if event[0] == "finished"]


class PickDefaultVoiceTest(unittest.TestCase):
    def test_prefers_an_english_male_voice(self):
        voices = [
            VoiceInfo("a", "A", ("de-DE",), "Male"),
            VoiceInfo("b", "B", ("en-GB",), "Female"),
            VoiceInfo("c", "C", ("en-US",), "Male"),
        ]
        self.assertEqual(pick_default_voice(voices), "c")

    def test_english_matters_more_than_gender(self):
        voices = [VoiceInfo("a", "A", ("de-DE",), "Male"), VoiceInfo("b", "B", ("en-GB",), "Female")]
        self.assertEqual(pick_default_voice(voices), "b")

    def test_among_equals_the_first_voice_wins(self):
        voices = [VoiceInfo("a", "A", ("en-US",), "Male"), VoiceInfo("b", "B", ("en-US",), "Male")]
        self.assertEqual(pick_default_voice(voices), "a")

    def test_unknown_gender_and_language_still_gives_a_voice(self):
        self.assertEqual(pick_default_voice([VoiceInfo("x", "X")]), "x")

    def test_no_voices_gives_none(self):
        self.assertIsNone(pick_default_voice([]))


class StartupTest(ServiceTestCase):
    def test_is_not_available_before_it_is_started(self):
        service, backend = self.make(start=False)
        self.assertFalse(service.available)
        self.assertEqual(service.speak("hello"), 0)
        self.assertFalse(backend.opened)

    def test_becomes_available_and_lists_the_installed_voices(self):
        service, _ = self.make()
        self.assertTrue(service.available)
        self.assertEqual(service.voices, [DAVID, ZIRA])
        self.assertIn(("available", True), self.events)

    def test_the_default_voice_is_english_and_male(self):
        service, _ = self.make()
        self.assertEqual(service.voice_id, DAVID.id)

    def test_a_voice_chosen_beforehand_is_kept(self):
        service, _ = self.make(voice_id=ZIRA.id)
        self.assertEqual(service.voice_id, ZIRA.id)

    def test_starting_twice_still_creates_a_single_worker(self):
        service, _ = self.make()
        worker = service._thread
        service.start()
        self.assertIs(service._thread, worker)

    def test_the_engine_is_warmed_up_while_idle_with_the_voice_that_will_be_used(self):
        service, backend = self.make()
        self.assertTrue(self.wait_until(lambda: backend.warm_ups))
        self.assertEqual(backend.warm_ups[0], DAVID.id)

    def test_every_backend_call_happens_on_one_worker_thread_that_is_not_the_main_thread(self):
        service, backend = self.make()
        service.speak("hello there")
        self.assertTrue(self.wait_until(lambda: self.finished_events()))
        service.shutdown()
        names = set().union(*backend.threads.values())
        self.assertEqual(len(names), 1, backend.threads)
        self.assertNotEqual(names.pop(), threading.current_thread().name)
        self.assertTrue(backend.closed)


class SpeakingTest(ServiceTestCase):
    def test_reports_started_then_finished_for_each_sentence_in_order(self):
        service, backend = self.make()
        first = service.speak("Hello there")
        second = service.speak("Another sentence", interrupt=False)
        self.assertEqual((first, second), (1, 2))
        self.assertTrue(self.wait_until(lambda: len(self.finished_events()) == 2))
        progress = [event for event in self.events if event[0] in ("started", "finished")]
        self.assertEqual(progress, [("started", 1), ("finished", 1, True), ("started", 2), ("finished", 2, True)])
        self.assertEqual(backend.finished_texts, ["Hello there", "Another sentence"])

    def test_text_is_tidied_up_and_blank_text_is_ignored(self):
        service, backend = self.make()
        self.assertEqual(service.speak(""), 0)
        self.assertEqual(service.speak("   \n\t  "), 0)
        service.speak("  Hello\n  there   friend ")
        self.assertTrue(self.wait_until(lambda: self.finished_events()))
        self.assertEqual([call["text"] for call in backend.calls], ["Hello there friend"])

    def test_each_sentence_uses_the_settings_in_force_when_it_was_requested(self):
        service, backend = self.make()
        service.set_rate(120)
        service.set_volume(0.4)
        service.set_voice(ZIRA.id)
        service.speak("one two three")
        service.set_rate(225)  # later changes must not touch the sentence that is already queued
        service.set_volume(1.0)
        service.set_voice(DAVID.id)
        service.speak("four five six", interrupt=False)
        self.assertTrue(self.wait_until(lambda: len(self.finished_events()) == 2))
        self.assertEqual(
            [(call["rate"], call["volume"], call["voice_id"]) for call in backend.calls],
            [(120, 0.4, ZIRA.id), (225, 1.0, DAVID.id)],
        )

    def test_the_speaking_flag_follows_the_sentence(self):
        service, _ = self.make()
        self.assertFalse(service.speaking)
        service.speak("x" * 200)  # about 0.8 s
        self.assertTrue(self.wait_until(lambda: service.speaking))
        self.assertTrue(self.wait_until(lambda: not service.speaking))
        self.assertEqual(self.finished_events(), [("finished", 1, True)])


class SettingsTest(ServiceTestCase):
    def test_rate_and_volume_are_kept_within_sensible_limits(self):
        service, _ = self.make(start=False)
        service.set_rate(5)
        self.assertEqual(service.rate, MIN_RATE)
        service.set_rate(9999)
        self.assertEqual(service.rate, MAX_RATE)
        service.set_volume(-3)
        self.assertEqual(service.volume, 0.0)
        service.set_volume(7)
        self.assertEqual(service.volume, 1.0)

    def test_initial_settings_are_limited_too(self):
        service = SpeechService(FakeBackend().factory(), rate=1, volume=2.5)
        self.assertEqual((service.rate, service.volume), (MIN_RATE, 1.0))

    def test_an_unknown_voice_is_ignored(self):
        service, _ = self.make()
        with self.assertLogs("water_assistant.speech_service", "WARNING") as logged:
            service.set_voice("no-such-voice")
        self.assertIn("no-such-voice", logged.output[0])
        self.assertEqual(service.voice_id, DAVID.id)
        service.set_voice(ZIRA.id)
        self.assertEqual(service.voice_id, ZIRA.id)

    def test_a_switched_off_voice_is_silent_and_can_be_switched_on_again(self):
        service, backend = self.make(enabled=False)
        self.assertEqual(service.speak("hello"), 0)
        QTest.qWait(60)
        self.assertEqual(backend.calls, [])
        service.set_enabled(True)
        sentence = service.speak("hello")
        self.assertGreater(sentence, 0)
        self.assertTrue(self.wait_until(self.saw(("finished", sentence, True))))

    def test_the_presets_cover_the_defaults_and_are_clearly_different(self):
        rates = [rate for _name, rate in SpeechService.RATE_PRESETS]
        volumes = [volume for _name, volume in SpeechService.VOLUME_PRESETS]
        self.assertIn(SpeechService.DEFAULT_RATE, rates)
        self.assertIn(SpeechService.DEFAULT_VOLUME, volumes)
        self.assertEqual(rates, sorted(rates))
        self.assertEqual(volumes, sorted(volumes))
        self.assertTrue(all(b >= a * 1.15 for a, b in zip(rates, rates[1:])), "Windows only has ~11 % speed steps")


class InterruptionTest(ServiceTestCase):
    def test_a_new_sentence_interrupts_the_old_one(self):
        service, backend = self.make()
        old = service.speak("x" * 600)  # about 2.4 s
        self.assertTrue(self.wait_until(self.saw(("started", old))))
        new = service.speak("short one")
        self.assertTrue(self.wait_until(self.saw(("finished", new, True)), 2000))
        self.assertIn(("finished", old, False), self.events)
        self.assertLess(self.events.index(("finished", old, False)), self.events.index(("started", new)))
        self.assertEqual(backend.cancelled_texts, ["x" * 600])

    def test_stop_cancels_the_current_sentence_and_the_waiting_ones(self):
        service, backend = self.make()
        ids = [service.speak("y" * 300, interrupt=False) for _ in range(3)]
        service.stop()
        self.assertTrue(self.wait_until(lambda: len(self.finished_events()) == 3))
        self.assertEqual(sorted(event[1] for event in self.finished_events()), ids)
        self.assertTrue(all(event[2] is False for event in self.finished_events()))
        self.assertEqual(backend.finished_texts, [])

    def test_stop_while_idle_does_not_cancel_later_sentences(self):
        service, _ = self.make()
        service.stop()
        service.stop()
        sentence = service.speak("hello")
        self.assertTrue(self.wait_until(self.saw(("finished", sentence, True))))

    def test_switching_the_voice_off_stops_the_sentence_being_spoken(self):
        service, _ = self.make()
        sentence = service.speak("x" * 800)  # about 3.2 s
        self.assertTrue(self.wait_until(self.saw(("started", sentence))))
        started = time.monotonic()
        service.set_enabled(False)
        self.assertTrue(self.wait_until(self.saw(("finished", sentence, False)), 1000))
        self.assertLess(time.monotonic() - started, 1.0)


class FailureTest(ServiceTestCase):
    def test_an_engine_that_cannot_start_silences_the_voice_without_crashing(self):
        with self.assertLogs("water_assistant.speech_service", "WARNING") as logged:
            service, _ = self.make(FakeBackend(fail_on_open=RuntimeError("no voices installed")))
        self.assertIn("no voices installed", "\n".join(logged.output))  # the problem is logged, not swallowed silently
        self.assertFalse(service.available)
        self.assertIn(("available", False), self.events)
        self.assertTrue(any(event[0] == "error" and "no voices installed" in event[1] for event in self.events))
        self.assertEqual(service.speak("hello"), 0)
        self.assertEqual(service.voices, [])

    def test_sentences_accepted_while_the_engine_was_still_starting_are_reported_as_not_spoken(self):
        gate = threading.Event()
        service, _ = self.make(FakeBackend(fail_on_open=RuntimeError("boom"), wait_before_open=gate), start=False)
        service.start()
        sentence = service.speak("hello")  # the engine has not finished starting: this is accepted...
        self.assertGreater(sentence, 0)
        with self.assertLogs("water_assistant.speech_service", "WARNING"):
            gate.set()  # ...and then it turns out the engine cannot start
            self.assertTrue(
                self.wait_until(self.saw(("finished", sentence, False))), "the waiting sentence was never answered"
            )
        self.assertFalse(service.available)

    def test_one_failing_sentence_is_reported_and_the_voice_keeps_working(self):
        service, _ = self.make(FakeBackend(fail_on_text="boom"))
        with self.assertLogs("water_assistant.speech_service", "WARNING") as logged:
            bad = service.speak("this will boom")
            self.assertTrue(self.wait_until(self.saw(("finished", bad, False))))
        self.assertIn("the fake engine broke", "\n".join(logged.output))
        self.assertTrue(any(event[0] == "error" for event in self.events))
        good = service.speak("this is fine")
        self.assertTrue(self.wait_until(self.saw(("finished", good, True))))
        self.assertTrue(service.available)

    def test_shutdown_is_quick_even_in_the_middle_of_a_sentence(self):
        service, backend = self.make()
        sentence = service.speak("z" * 2000)  # about 8 s
        self.assertTrue(self.wait_until(self.saw(("started", sentence))))
        started = time.monotonic()
        service.shutdown()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(backend.closed)
        self.assertFalse(service.available)
        self.assertEqual(service.speak("hi"), 0)

    def test_a_hung_engine_cannot_block_the_program_from_exiting(self):
        backend = FakeBackend(ignore_cancel=True)
        service, _ = self.make(backend)
        sentence = service.speak("hang " * 50)
        self.assertTrue(self.wait_until(self.saw(("started", sentence))))
        started = time.monotonic()
        with self.assertLogs("water_assistant.speech_service", "WARNING") as logged:
            service.shutdown(timeout=0.3)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIn("did not stop", logged.output[0])
        backend.release()  # lets the abandoned (daemon) worker end

    def test_shutdown_before_start_and_twice_is_harmless(self):
        service, _ = self.make(start=False)
        service.shutdown()
        service.shutdown()


class ThreadingTest(ServiceTestCase):
    def test_signals_are_delivered_on_the_main_thread_so_handlers_may_touch_widgets(self):
        service, backend = self.make()
        delivered_on = []
        service.utteranceStarted.connect(lambda _uid: delivered_on.append(threading.current_thread()))
        service.utteranceFinished.connect(lambda _uid, _ok: delivered_on.append(threading.current_thread()))
        service.speak("hello")
        self.assertTrue(self.wait_until(lambda: len(delivered_on) == 2))
        self.assertTrue(all(thread is threading.main_thread() for thread in delivered_on))
        self.assertNotIn(threading.main_thread().name, backend.threads["speak"])

    def test_the_services_own_bookkeeping_runs_on_the_main_thread_too(self):
        # Not only the public signals: the state (available, voices, speaking, ...) must only ever be changed by the
        # main thread, otherwise the worker thread and the GUI could trample on each other.
        threads = []

        class Spy(SpeechService):
            @Slot(str, object)
            def _on_worker_event(self, kind, payload):
                threads.append(threading.current_thread())
                super()._on_worker_event(kind, payload)

        service = Spy(FakeBackend().factory())
        self.addCleanup(service.shutdown)
        service.start()
        self.assertTrue(self.wait_until(lambda: service.voices))
        service.speak("hello")
        self.assertTrue(self.wait_until(lambda: len(threads) >= 3))  # ready, started, finished
        self.assertTrue(all(thread is threading.main_thread() for thread in threads), threads)


if __name__ == "__main__":
    unittest.main()
