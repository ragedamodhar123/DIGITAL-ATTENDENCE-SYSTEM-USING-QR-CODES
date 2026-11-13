"""Building head-turn checks for tests: frames, and what the stand-in camera 'sees'."""
from tests.face_helpers import FakeEngine, encode, near, photo, unit

DIRECTION = {"L": +1, "R": -1}  # nose offset sign for the student's left / right


def frames(count, start):
    """Distinct encoded frames (different noise seeds)."""
    return [encode(photo(seed=start + i)) for i in range(count)]


def steps():
    """2 straight frames, 4 for the first turn, 4 for the second."""
    return [frames(2, 0), frames(4, 10), frames(4, 20)]


def offsets(challenge, straight=0.0, first=0.2, second=0.2):
    """What the camera sees: first straight frame, then 4 frames per turn.

    straight: the student's natural "straight" pose (real faces are rarely exactly 0).
    first/second: how far the head moved FROM that pose in the direction asked.
    """
    return (
        [straight]
        + [straight + first * DIRECTION[challenge[0]]] * 4
        + [straight + second * DIRECTION[challenge[1]]] * 4
    )


def live_person(vector, challenge, **turns):
    """A real student following the arrows: same face in every step, head really turning."""
    return FakeEngine(embeddings=[near(vector, seed=s) for s in (11, 12, 13)], offsets=offsets(challenge, **turns))


def photo_held_up(vector, challenge, straight=-0.07):
    """A photo or phone screen of the student: right face, but it cannot turn in 3D.
    Tilting it moves the measurement only a little (at most ~0.04 with the real detector)."""
    return FakeEngine(
        embeddings=[near(vector, seed=s) for s in (11, 12, 13)],
        offsets=offsets(challenge, straight=straight, first=0.04, second=0.04),
    )