"""Which frame of the GIF shows what, and the short frame sequences ("clips") built from them.

This file is pure data about YOUR artwork. If you ever replace the GIF (for example with a
real side-view walk cycle), this is the place to update.

Left/right below are from the CHARACTER's point of view.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Pose(IntEnum):
    """The 8 frames of assets/character/water_reminder.gif (the value is the frame number)."""

    STAND = 0  # standing, bottle held at the side - the resting pose
    STEP_RIGHT = 1  # mid-step, right foot forward, bottle at the chest
    POINT = 2  # pointing at the viewer, open-mouth smile, yellow emphasis marks
    STEP_LEFT = 3  # mid-step, left foot forward, bottle raised, palm-up gesture
    DRINK = 4  # drinking from the bottle
    THUMBS_UP = 5  # thumbs-up, eyes closed
    SIGN_DRINK_WATER = 6  # pointing at a "Drink Water!" bubble (the text is part of the artwork)
    SIGN_STAY_HYDRATED = 7  # wink + a "Stay Hydrated!" banner (the text is part of the artwork)


@dataclass(frozen=True)
class Clip:
    """A looping or one-shot sequence of poses, each shown for its own number of milliseconds."""

    frames: tuple[Pose, ...]
    durations_ms: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.frames or len(self.frames) != len(self.durations_ms):
            raise ValueError("a Clip needs one duration per frame (and at least one frame)")


# The GIF only has FRONT-FACING "walking toward the camera" poses, so this is a stepping cycle
# (right foot, passing, left foot, passing) - a real side-view walk would need new artwork.
WALK_CLIP = Clip((Pose.STEP_RIGHT, Pose.STAND, Pose.STEP_LEFT, Pose.STAND), (260, 260, 260, 260))

# While the speech bubble is shown: point, rest, gesture, rest (the open smile in the point/gesture
# poses vs. the closed smile in the rest pose also reads as a little mouth movement).
TALK_CLIP = Clip((Pose.POINT, Pose.STAND, Pose.STEP_LEFT, Pose.STAND), (750, 450, 750, 450))

# Reminder attention grabber: point at you, then the "Drink Water!" sign pops up, and so on.
ATTENTION_CLIP = Clip((Pose.POINT, Pose.SIGN_DRINK_WATER), (500, 900))

# After drinking water: thumbs-up, then the wink with the "Stay Hydrated!" banner.
CELEBRATE_CLIP = Clip((Pose.THUMBS_UP, Pose.SIGN_STAY_HYDRATED), (850, 850))
