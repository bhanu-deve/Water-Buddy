"""The animation controller: decides WHAT the character does and drives everything that moves.

It owns the six states from the project brief:

    IDLE         stands still, breathes and sways gently; now and then strolls across the screen
    WALKING      steps across the desktop (smooth movement + stepping frames + bounce)
    REMINDER     hops, points at you, the "Drink Water!" sign blinks
    TALKING      speech bubble above the character, gesturing frames
    DRINKING     drinks from the bottle
    CELEBRATING  thumbs-up and the "Stay Hydrated!" wink (after you drank water)

plus SHOWCASE, which just plays the original GIF (menu: "Preview animation").

Qt classes used:
  * QVariantAnimation - smooth walking and hopping (updates about 60 times per second)
  * QTimer            - switching GIF frames, and the small breathing / bouncing motion

The CharacterWindow only draws, the SpeechBubble only shows text, the SpeechService only talks. Everything else
is decided here - including keeping the speech bubble (and the talking pose) up until the voice has finished.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from PySide6.QtCore import QObject, QTimer, QVariantAnimation, Qt, Signal

from . import messages
from .character_window import CharacterWindow
from .poses import ATTENTION_CLIP, CELEBRATE_CLIP, TALK_CLIP, WALK_CLIP, Clip, Pose
from .speech_bubble import SpeechBubble
from .speech_service import SpeechService


class State(Enum):
    IDLE = "idle"
    WALKING = "walking"
    REMINDER = "reminder"
    TALKING = "talking"
    DRINKING = "drinking"
    CELEBRATING = "celebrating"
    SHOWCASE = "showcase"  # only used by the "Play the original GIF" preview


@dataclass(frozen=True)
class Timing:
    """Every duration in one place. `scale` shrinks them all (the automated tests use a tiny value)."""

    scale: float = 1.0
    walk_speed: float = 275.0  # pixels per second for a character at 100 % size (grows with the size)
    first_wander_s: float = 7.0  # first stroll this long after start-up, so you quickly see it move
    wander_gap_s: tuple[float, float] = (25.0, 60.0)  # afterwards: a random pause in this range
    attention_s: float = 2.6  # how long the reminder grabs your attention before the speech bubble
    drink_s: float = 2.6
    celebrate_s: float = 3.4
    hop_s: float = 0.4  # one hop
    hop_height: float = 0.07  # fraction of the character's height
    speech_pause_s: float = 0.7  # the bubble stays this long after the voice has finished
    speech_timeout_s: float = 25.0  # if the voice has not reported back this long after the minimum time, carry on

    def ms(self, seconds: float) -> int:
        return max(1, round(seconds * 1000 * self.scale))


# How often the small breathing / bouncing motion is redrawn. Slow motion needs fewer redraws, and every
# redraw costs CPU (measured with exact CPU cycles: about 2.5 ms of CPU per redraw, so 10 redraws a second
# cost ~2.5 % of one core). Idle breathing changes by less than one pixel per step, so ~7 per second is plenty.
TICK_MS = {State.IDLE: 150, State.WALKING: 33}
DEFAULT_TICK_MS = 50  # talking, drinking, reminder, celebrating

# Small procedural motions drawn on top of the GIF frames (around the feet, so they stay planted).
BREATH_PERIOD_S = 3.8
SWAY_PERIOD_S = 6.1
SWAY_DEGREES = 0.5
WALK_LEAN_DEGREES = 2.0
MIN_WALK_PX = 24


def _trapezoid(t: float, ramp: float = 0.12) -> float:
    """Walking position (0..1) over time (0..1): speeds up for the first `ramp` of the walk,
    cruises at constant speed, then slows down for the last `ramp` - so it never starts/stops abruptly."""
    t = min(max(t, 0.0), 1.0)
    norm = 1.0 - ramp
    if t < ramp:
        return (t * t / (2 * ramp)) / norm
    if t > 1.0 - ramp:
        return 1.0 - (((1.0 - t) ** 2) / (2 * ramp)) / norm
    return (ramp / 2 + (t - ramp)) / norm


class AnimationController(QObject):
    stateChanged = Signal(object)  # emits the new State

    def __init__(
        self,
        window: CharacterWindow,
        bubble: SpeechBubble,
        timing: Timing | None = None,
        rng: random.Random | None = None,
        speech: SpeechService | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._bubble = bubble
        self._speech = speech  # None = the character never speaks aloud (it still shows its speech bubble)
        self._timing = timing or Timing()
        self._rng = rng or random.Random()
        self._reminders = messages.MessageRotator(messages.REMINDER_MESSAGES)

        self._state = State.IDLE
        self._state_started = time.monotonic()
        self._interacting = False  # the user is dragging the character or has its menu open
        self._wandered = False  # has it already taken its first stroll?
        self._deferred_reminder: str | None = None  # a reminder that came due while the user had hold of the character
        self._interaction_start_pos = None  # where the window was when the user grabbed it (or opened its menu)
        self._interrupted_hop_base = None  # ...and where it stands on the ground, if it was in the air in a hop then

        self._anim: QVariantAnimation | None = None  # the running walk / hop
        self._hop_base = None  # where to put the character back after a hop
        self._walk_direction = 0
        self._walk_progress = 0.0

        self._clip: Clip | None = None  # the running frame sequence
        self._clip_index = 0
        self._clip_loop = True
        self._clip_then: Callable[[], None] | None = None
        self._clip_started = 0.0
        self._end_action: Callable[[], None] | None = None
        # How a timed state ends (see _finish_after): the minimum time must be over AND the voice must be done.
        self._utterance = 0  # the sentence being spoken that the state still waits for (0 = none)
        self._min_time_over = True
        self._pause_over = True

        self._frame_timer = self._make_timer(self._next_clip_frame)
        self._end_timer = self._make_timer(self._on_min_time_over)
        self._pause_timer = self._make_timer(self._on_pause_over)
        self._safety_timer = self._make_timer(self._on_speech_timeout)
        self._wander_timer = self._make_timer(self._wander)
        self._tick = QTimer(self)  # redraws the breathing / bouncing
        self._tick.setTimerType(Qt.TimerType.PreciseTimer)
        self._tick.timeout.connect(self._on_tick)

        window.interactionStarted.connect(self._on_interaction_started)
        window.interactionFinished.connect(self._on_interaction_finished)
        window.scaleChanged.connect(self._on_scale_changed)
        window.geometryChanged.connect(self._on_geometry_changed)
        window.previewRequested.connect(self._on_preview)
        window.clicked.connect(self._on_clicked)
        window.wanderingToggled.connect(self.set_wandering_enabled)
        if speech is not None:
            speech.utteranceFinished.connect(self._on_utterance_finished)

    def _make_timer(self, slot: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(slot)
        return timer

    # ------------------------------------------------------------- lifecycle
    @property
    def state(self) -> State:
        return self._state

    def start(self, greet: bool = True) -> None:
        """Begin animating. With `greet`, the character says hello first."""
        if greet:
            self.say(messages.GREETING)
        else:
            self.go_idle()
        self._tick.start()

    def stop(self) -> None:
        """Stop everything (used when the program exits)."""
        self._tick.stop()
        self._deferred_reminder = None
        self._stop_motion(restore_hop=False)
        for timer in self._state_timers():
            timer.stop()
        if self._speech is not None:
            self._speech.stop()
        self._bubble.hide_bubble(animated=False)

    def set_wandering_enabled(self, enabled: bool) -> None:
        """Allow / forbid the character to stroll around on its own."""
        self._window.set_wandering_enabled(enabled)
        if not enabled:
            self._wander_timer.stop()
        elif self._state is State.IDLE:
            self._schedule_wander()

    # ---------------------------------------------------------------- states
    def go_idle(self) -> None:
        self._begin(State.IDLE)
        self._window.set_frame(Pose.STAND)
        self._schedule_wander()

    def walk_to(self, target_x: int) -> None:
        """Stroll horizontally to window x position `target_x` (kept inside the screen)."""
        left, right = self._walk_limits()
        target_x = min(max(int(target_x), left), right)
        start = self._window.pos()
        distance = abs(target_x - start.x())
        if distance < MIN_WALK_PX:
            self.go_idle()
            return
        self._begin(State.WALKING)
        self._walk_direction = 1 if target_x > start.x() else -1
        seconds = distance / (self._timing.walk_speed * self._window.scale)
        self._play_clip(WALK_CLIP)

        def step(t: float) -> None:
            self._walk_progress = t
            self._window.move(round(start.x() + (target_x - start.x()) * _trapezoid(t)), start.y())

        self._run_animation(self._timing.ms(seconds), step, self.go_idle)

    def walk_across(self) -> None:
        """Walk to the far side of the screen (the side the character is NOT on)."""
        left, right = self._walk_limits()
        centre = self._window.available_area().center().x()
        self.walk_to(left if self._window.geometry().center().x() > centre else right)

    def remind(self, text: str | None = None) -> None:
        """Reminder: hop + point + blinking "Drink Water!" sign, then say the reminder in the bubble (and aloud).

        A hidden character is shown first. If you are holding the character or have its menu open, the reminder
        waits until you let go - it would be rude to make the character hop out from under your mouse.
        """
        message = text if text is not None else self._reminders.next()
        if self._interacting:
            self._deferred_reminder = message
            return
        if not self._window.isVisible():
            self._window.show()
        self._begin(State.REMINDER)
        self._play_clip(ATTENTION_CLIP)
        self._hop(count=2)
        self._after(self._timing.attention_s, lambda: self.say(message))

    def say(self, text: str, seconds: float | None = None) -> None:
        """Show `text` in the speech bubble and say it aloud (when the voice is on).

        The bubble stays for at least `seconds` (default: long enough to read the text) and until the voice has
        finished, plus a short pause.
        """
        self._begin(State.TALKING, keep_bubble=True)
        self._play_clip(TALK_CLIP)
        self._bubble.show_text(text, self._window.geometry())
        minimum = messages.reading_time_seconds(text) if seconds is None else seconds
        self._finish_after(minimum, self.go_idle, utterance=self._speak(text))

    def drink(self, then: Callable[[], None] | None = None) -> None:
        """Drink from the bottle, then do `then` (by default: go back to idle)."""
        self._begin(State.DRINKING)
        self._window.set_frame(Pose.DRINK)
        self._after(self._timing.drink_s, then or self.go_idle)

    def celebrate(self, text: str | None = None) -> None:
        """Thumbs-up + "Stay Hydrated!" wink with two happy hops (optionally with a message)."""
        self._begin(State.CELEBRATING, keep_bubble=text is not None)
        self._play_clip(CELEBRATE_CLIP)
        self._hop(count=2)
        utterance = 0
        if text is not None:
            self._bubble.show_text(text, self._window.geometry())
            utterance = self._speak(text)
        self._finish_after(self._timing.celebrate_s, self.go_idle, utterance=utterance)

    def play_original_gif(self) -> None:
        """Play all 8 frames exactly like the original GIF (its own timing), twice."""
        sprites = self._window.sprites
        frames = tuple(range(sprites.frame_count))
        self._begin(State.SHOWCASE)
        self._play_clip(Clip(frames * 2, sprites.durations_ms * 2), loop=False, then=self.go_idle)

    def _begin(self, state: State, *, keep_bubble: bool = False) -> None:
        """Cancel everything the previous state was doing (including its speech) and enter `state`."""
        self._stop_motion(restore_hop=True)
        for timer in self._state_timers():
            timer.stop()
        if self._speech is not None:
            self._speech.stop()  # whatever the previous state was saying must not talk over the new one
        self._clip = None
        self._end_action = None
        self._utterance = 0
        self._walk_progress = 0.0
        if not keep_bubble:
            self._bubble.hide_bubble()
        self._state = state
        self._state_started = time.monotonic()
        self._tick.setInterval(TICK_MS.get(state, DEFAULT_TICK_MS))
        self.stateChanged.emit(state)

    def _state_timers(self) -> tuple[QTimer, ...]:
        """Every timer that belongs to the state that is running (all are stopped when the state changes)."""
        return (self._frame_timer, self._end_timer, self._pause_timer, self._safety_timer, self._wander_timer)

    # --------------------------------------------------- ending a timed state
    def _speak(self, text: str) -> int:
        """Start saying `text` aloud. Returns the utterance id, or 0 when nothing will be spoken."""
        return self._speech.speak(text) if self._speech is not None else 0

    def _after(self, seconds: float, action: Callable[[], None]) -> None:
        """Run `action` after `seconds` (ends timed states that have nothing to say)."""
        self._finish_after(seconds, action)

    def _finish_after(self, minimum_seconds: float, action: Callable[[], None], utterance: int = 0) -> None:
        """End the running state with `action`.

        It happens once `minimum_seconds` have passed AND - if `utterance` is being spoken - the voice has finished
        and a short pause has followed. If the voice never reports back, a safety timer carries on anyway.
        """
        self._end_action = action
        self._utterance = utterance
        self._min_time_over = False
        self._pause_over = utterance == 0  # nothing spoken: no pause needed
        self._end_timer.start(self._timing.ms(minimum_seconds))
        if utterance:
            self._safety_timer.start(self._timing.ms(minimum_seconds + self._timing.speech_timeout_s))

    def _on_min_time_over(self) -> None:
        self._min_time_over = True
        self._maybe_finish()

    def _on_utterance_finished(self, utterance_id: int, _completed: bool) -> None:
        if utterance_id != self._utterance:
            return  # an old sentence that was cancelled: not the one we are waiting for
        self._utterance = 0
        self._pause_timer.start(self._timing.ms(self._timing.speech_pause_s))

    def _on_pause_over(self) -> None:
        self._pause_over = True
        self._maybe_finish()

    def _on_speech_timeout(self) -> None:
        if self._speech is not None:
            self._speech.stop()
        self._utterance = 0
        self._min_time_over = self._pause_over = True
        self._maybe_finish()

    def _maybe_finish(self) -> None:
        if not (self._min_time_over and self._pause_over):
            return
        self._safety_timer.stop()
        self._pause_timer.stop()
        action, self._end_action = self._end_action, None
        if action is not None:
            action()

    # ---------------------------------------------------------------- motion
    def _run_animation(
        self, milliseconds: int, on_value: Callable[[float], None], on_finished: Callable[[], None], loops: int = 1
    ) -> None:
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(milliseconds)
        anim.setLoopCount(loops)
        anim.valueChanged.connect(lambda value: on_value(float(value)))

        def finished() -> None:
            if anim is self._anim:  # ignore animations that were cancelled meanwhile
                self._anim = None
                anim.deleteLater()
                on_finished()

        anim.finished.connect(finished)
        self._anim = anim
        anim.start()

    def _hop(self, count: int) -> None:
        """`count` little jumps on the spot (the character is put back exactly where it was)."""
        base = self._window.pos()
        height = round(self._window.height() * self._timing.hop_height)
        self._hop_base = base

        def step(t: float) -> None:
            self._window.move(base.x(), base.y() - round(height * 4 * t * (1 - t)))  # a parabola

        def done() -> None:
            self._window.move(base)
            self._hop_base = None

        self._run_animation(self._timing.ms(self._timing.hop_s), step, done, loops=count)

    def _stop_motion(self, *, restore_hop: bool) -> None:
        anim, self._anim = self._anim, None
        if anim is not None:
            anim.stop()
            anim.deleteLater()
        if restore_hop and self._hop_base is not None:
            self._window.move(self._hop_base)
        self._hop_base = None

    def _walk_limits(self) -> tuple[int, int]:
        """Smallest / largest window x that keeps the whole character on the screen."""
        area = self._window.available_area()
        left = area.left() + 4
        right = max(left, area.right() - self._window.width() - 3)
        return left, right

    # ------------------------------------------------------- frame sequences
    def _play_clip(self, clip: Clip, *, loop: bool = True, then: Callable[[], None] | None = None) -> None:
        self._clip, self._clip_index, self._clip_loop, self._clip_then = clip, 0, loop, then
        self._show_clip_frame()

    def _frame_ms(self, index: int) -> int:
        return max(1, round(self._clip.durations_ms[index] * self._timing.scale))

    def _show_clip_frame(self) -> None:
        self._window.set_frame(int(self._clip.frames[self._clip_index]))
        self._clip_started = time.monotonic()
        self._frame_timer.start(self._frame_ms(self._clip_index))

    def _next_clip_frame(self) -> None:
        if self._clip is None:
            return
        self._clip_index += 1
        if self._clip_index >= len(self._clip.frames):
            if not self._clip_loop:
                then, self._clip_then = self._clip_then, None
                if then is not None:
                    then()
                return
            self._clip_index = 0
        self._show_clip_frame()

    def _clip_cycle_phase(self) -> float:
        """Where we are in the running clip: 0.0 (first frame starts) ... 1.0 (last frame ends)."""
        if self._clip is None:
            return 0.0
        elapsed_ms = (time.monotonic() - self._clip_started) * 1000
        within_frame = min(1.0, elapsed_ms / self._frame_ms(self._clip_index))
        return (self._clip_index + within_frame) / len(self._clip.frames)

    # ----------------------------------------------- breathing / bouncing tick
    def _on_tick(self) -> None:
        self._window.set_pose(*self._procedural_pose(time.monotonic()))

    def _procedural_pose(self, now: float) -> tuple[float, float, float]:
        """(squash x, squash y, lean degrees) for this instant - small life-like motion on top of the frames."""
        if self._interacting:
            return 1.0, 1.0, 0.0  # hold still while the user is dragging / using the menu
        t = now - self._state_started
        state = self._state
        if state is State.IDLE:
            breath = 0.5 - 0.5 * math.cos(2 * math.pi * t / BREATH_PERIOD_S)  # 0 .. 1
            sway = SWAY_DEGREES * math.sin(2 * math.pi * t / SWAY_PERIOD_S)
            return 1.0 + 0.004 * breath, 1.0 - 0.014 * breath, sway
        if state is State.WALKING:
            contact = 0.5 + 0.5 * math.cos(4 * math.pi * self._clip_cycle_phase())  # 1 at each footfall
            progress = self._walk_progress
            ease = max(0.0, min(1.0, progress / 0.1, (1.0 - progress) / 0.1))  # lean in / out gently
            return 1.0 + 0.012 * contact, 1.0 - 0.032 * contact, self._walk_direction * WALK_LEAN_DEGREES * ease
        if state is State.TALKING:
            pulse = abs(math.sin(2 * math.pi * 2.3 * t)) * (0.65 + 0.35 * math.sin(2 * math.pi * 0.43 * t))
            return 1.0, 1.0 - 0.012 * pulse, 0.0
        if state is State.DRINKING:
            gulp = max(0.0, math.sin(2 * math.pi * t / 0.9)) ** 2
            return 1.0, 1.0 - 0.02 * gulp, 0.0
        if state in (State.REMINDER, State.CELEBRATING):
            return 1.0, 1.0 - 0.02 * abs(math.sin(2 * math.pi * 1.1 * t)), 0.0
        return 1.0, 1.0, 0.0  # SHOWCASE: the original frames, untouched

    # ------------------------------------------------------------- wandering
    def _schedule_wander(self) -> None:
        if self._interacting or not self._window.wandering_enabled:
            return
        low, high = self._timing.wander_gap_s
        seconds = self._timing.first_wander_s if not self._wandered else self._rng.uniform(low, high)
        self._wander_timer.start(self._timing.ms(seconds))

    def _wander(self) -> None:
        if self._state is not State.IDLE or self._interacting or not self._window.wandering_enabled:
            return
        self._wandered = True
        target = self._pick_wander_target()
        if target is None:
            self._schedule_wander()
        else:
            self.walk_to(target)

    def _pick_wander_target(self) -> int | None:
        left, right = self._walk_limits()
        here = self._window.x()
        minimum = max(int(self._window.width() * 0.9), 120)  # no pointless 10-pixel strolls
        for _ in range(8):
            candidate = self._rng.randint(left, right)
            if abs(candidate - here) >= minimum:
                return candidate
        far = left if (here - left) > (right - here) else right
        return far if abs(far - here) >= minimum else None

    # ------------------------------------------------- reactions to the window
    def _on_interaction_started(self) -> None:
        self._interacting = True
        self._interaction_start_pos = self._window.pos()
        self._interrupted_hop_base = self._hop_base  # if it was in the air, a click or a menu should land it again
        self._stop_motion(restore_hop=False)  # the user is in charge now: never fight the mouse
        self._wander_timer.stop()
        if self._state is State.WALKING:
            self.go_idle()

    def _on_clicked(self, _global_pos) -> None:
        self._land_interrupted_hop()  # a click is not a drag: put the character back on the ground

    def _on_interaction_finished(self) -> None:
        self._interacting = False
        if self._window.pos() == self._interaction_start_pos:  # not dragged anywhere (a menu was used, for example)
            self._land_interrupted_hop()
        self._interrupted_hop_base = None
        if self._deferred_reminder is not None:
            message, self._deferred_reminder = self._deferred_reminder, None
            self.remind(message)  # the reminder that came due while the user had hold of the character
        elif self._state is State.IDLE:
            self._schedule_wander()

    def _land_interrupted_hop(self) -> None:
        base, self._interrupted_hop_base = self._interrupted_hop_base, None
        if base is not None:
            self._window.move(base)

    def _on_scale_changed(self, _scale: float) -> None:
        self._stop_motion(restore_hop=False)  # the animation's start/end positions are out of date
        if self._state is State.WALKING:
            self.go_idle()

    def _on_geometry_changed(self) -> None:
        self._bubble.follow(self._window.geometry())  # the bubble stays attached to the character

    def _on_preview(self, key: str) -> None:
        actions: dict[str, Callable[[], None]] = {
            "idle": self.go_idle,
            "walk": self.walk_across,
            "reminder": self.remind,
            "talk": lambda: self.say(self._reminders.next()),
            "drink": lambda: self.drink(then=lambda: self.celebrate(messages.CELEBRATION_MESSAGES[0])),
            "celebrate": lambda: self.celebrate(messages.CELEBRATION_MESSAGES[0]),
            "original_gif": self.play_original_gif,
        }
        action = actions.get(key)
        if action is not None:
            action()
