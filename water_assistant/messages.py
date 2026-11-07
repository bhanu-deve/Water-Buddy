"""The things the character says. Phase 4 (reminders) and Phase 5 (water tracking) reuse these."""

from __future__ import annotations

GREETING = "Hi! I'm your water buddy. I'll remind you to stay hydrated!"

# The first four are the example messages from the project brief; they are given in this order, round and round.
REMINDER_MESSAGES = (
    "Hey! It's time to drink some water.",
    "Don't forget to stay hydrated!",
    "Come on, take a small water break.",
    "Your body needs water. Let's drink!",
    "Time for a sip! Your water bottle is waiting.",
    "A glass of water now will keep the tiredness away.",
    "Quick hydration check! Have a little water.",
    "Let's take a water break together.",
)

# Said after you have drunk water (Phase 5). The brief's fifth example message is praise, so it belongs here.
CELEBRATION_MESSAGES = (
    "Good job! Keep drinking water regularly.",
    "Great! That's the way to stay hydrated.",
    "Nice one! Your body says thank you.",
)


class MessageRotator:
    """Hands out messages one after another and starts over at the end, so they rotate."""

    def __init__(self, messages: tuple[str, ...]) -> None:
        if not messages:
            raise ValueError("a MessageRotator needs at least one message")
        self._messages = messages
        self._next_index = 0

    def next(self) -> str:
        message = self._messages[self._next_index]
        self._next_index = (self._next_index + 1) % len(self._messages)
        return message


def reading_time_seconds(text: str) -> float:
    """How long a speech bubble should stay up so the text can be read comfortably."""
    return max(3.0, 1.6 + 0.06 * len(text))
