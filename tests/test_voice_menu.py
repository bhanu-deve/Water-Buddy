import time
import unittest

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from water_assistant.speech_service import SpeechService
from water_assistant.voice_menu import TEST_SENTENCE, populate_voice_menu

from .fakes import DAVID, ZIRA, FakeBackend

app = QApplication.instance() or QApplication([])


class VoiceMenuTest(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.speech = SpeechService(self.backend.factory())
        self.addCleanup(self.speech.shutdown)
        self.spoken_through_the_character = []
        self.speech.start()
        self.assertTrue(self.wait_until(lambda: self.speech.voices))

    def wait_until(self, condition, timeout_ms=3000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if condition():
                return True
            QTest.qWait(5)
        return bool(condition())

    def voice_menu(self) -> QMenu:
        """Build the menu the way the character window does, and return the "Voice" submenu."""
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        populate_voice_menu(menu, self.speech, self.spoken_through_the_character.append)
        return next(action for action in menu.actions() if action.text() == "Voice").menu()

    @staticmethod
    def entry(menu: QMenu, text: str):
        return next(action for action in menu.actions() if action.text() == text)

    @staticmethod
    def submenu(menu: QMenu, title: str) -> QMenu:
        return VoiceMenuTest.entry(menu, title).menu()

    @staticmethod
    def titles(menu: QMenu) -> list[str]:
        return [action.text() for action in menu.actions() if not action.isSeparator()]

    def test_the_menu_has_all_the_voice_controls(self):
        menu = self.voice_menu()
        self.assertEqual(self.titles(menu), ["Speak aloud", "Test voice", "Stop speaking", "Speed", "Volume", "Voice"])

    def test_speak_aloud_can_be_switched_off_and_on(self):
        self.assertTrue(self.entry(self.voice_menu(), "Speak aloud").isChecked())
        self.entry(self.voice_menu(), "Speak aloud").trigger()
        self.assertFalse(self.speech.enabled)
        menu = self.voice_menu()
        self.assertFalse(self.entry(menu, "Speak aloud").isChecked())
        self.assertFalse(self.entry(menu, "Test voice").isEnabled())  # nothing to test while it is switched off
        self.entry(menu, "Speak aloud").trigger()
        self.assertTrue(self.speech.enabled)

    def test_test_voice_speaks_a_sample_sentence_through_the_character(self):
        self.entry(self.voice_menu(), "Test voice").trigger()
        self.assertEqual(self.spoken_through_the_character, [TEST_SENTENCE])

    def test_stop_speaking_is_only_available_while_it_speaks(self):
        self.assertFalse(self.entry(self.voice_menu(), "Stop speaking").isEnabled())
        sentence = self.speech.speak("x" * 800)  # about 3 s
        self.assertTrue(self.wait_until(lambda: self.speech.speaking))
        stop = self.entry(self.voice_menu(), "Stop speaking")
        self.assertTrue(stop.isEnabled())
        stop.trigger()
        self.assertTrue(self.wait_until(lambda: not self.speech.speaking, 1000))
        self.assertEqual(self.backend.cancelled_texts, ["x" * 800])
        self.assertGreater(sentence, 0)

    def test_the_speed_menu_shows_the_current_speed_and_changes_it(self):
        speed = self.submenu(self.voice_menu(), "Speed")
        self.assertEqual(self.titles(speed), ["Slow", "Normal", "Fast"])
        self.assertEqual([self.entry(speed, name).isChecked() for name in ("Slow", "Normal", "Fast")], [False, True, False])
        self.entry(speed, "Fast").trigger()
        self.assertEqual(self.speech.rate, 225)
        speed = self.submenu(self.voice_menu(), "Speed")
        self.assertEqual([self.entry(speed, name).isChecked() for name in ("Slow", "Normal", "Fast")], [False, False, True])

    def test_a_speed_that_is_not_a_preset_has_no_tick(self):
        self.speech.set_rate(190)
        speed = self.submenu(self.voice_menu(), "Speed")
        self.assertFalse(any(action.isChecked() for action in speed.actions()))

    def test_the_volume_menu_shows_the_current_volume_and_changes_it(self):
        volume = self.submenu(self.voice_menu(), "Volume")
        self.assertEqual(self.titles(volume), ["Quiet (40 %)", "Medium (70 %)", "Loud (100 %)"])
        self.assertTrue(self.entry(volume, "Medium (70 %)").isChecked())
        self.entry(volume, "Quiet (40 %)").trigger()
        self.assertEqual(self.speech.volume, 0.4)
        volume = self.submenu(self.voice_menu(), "Volume")
        self.assertTrue(self.entry(volume, "Quiet (40 %)").isChecked())
        self.assertFalse(self.entry(volume, "Medium (70 %)").isChecked())

    def test_the_voice_menu_lists_the_installed_voices_with_short_names(self):
        voices = self.submenu(self.voice_menu(), "Voice")
        self.assertEqual(self.titles(voices), ["Microsoft David Desktop (en-US)", "Microsoft Zira Desktop (en-US)"])
        self.assertTrue(self.entry(voices, "Microsoft David Desktop (en-US)").isChecked())  # the default voice
        self.entry(voices, "Microsoft Zira Desktop (en-US)").trigger()
        self.assertEqual(self.speech.voice_id, ZIRA.id)
        voices = self.submenu(self.voice_menu(), "Voice")
        self.assertTrue(self.entry(voices, "Microsoft Zira Desktop (en-US)").isChecked())
        self.assertFalse(self.entry(voices, "Microsoft David Desktop (en-US)").isChecked())
        self.assertNotEqual(self.speech.voice_id, DAVID.id)

    def test_only_one_choice_is_ticked_at_a_time(self):
        for title in ("Speed", "Volume", "Voice"):
            submenu = self.submenu(self.voice_menu(), title)
            self.assertEqual(sum(action.isChecked() for action in submenu.actions()), 1, title)

    def test_when_the_voice_is_not_available_the_menu_says_so(self):
        speech = SpeechService(FakeBackend().factory())  # never started
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        populate_voice_menu(menu, speech, lambda text: None)
        voice = next(action for action in menu.actions() if action.text() == "Voice").menu()
        self.assertEqual(len(voice.actions()), 1)
        self.assertFalse(voice.actions()[0].isEnabled())
        self.assertIn("not available", voice.actions()[0].text())


if __name__ == "__main__":
    unittest.main()
