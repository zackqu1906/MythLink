"""Physical ink measurements before DTW resampling; units are SDK displacement."""
import math


def measure_stroke(points, duration_ms):
    """Use confirmed contact frame duration, never recognizer computation time."""
    length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    if not math.isfinite(length) or length <= 0:
        return None
    if (not isinstance(duration_ms, (int, float)) or isinstance(duration_ms, bool)
            or not math.isfinite(duration_ms) or duration_ms <= 0):
        return None
    return dict(length=length, duration_ms=duration_ms, speed=length*1000/duration_ms,
                samples=len(points), units='sdk', timing='confirmed_contact_frames')
