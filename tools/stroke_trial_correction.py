"""The standalone trial uses the app's whole-character repair worker."""
from proximic_ring.ui.touchpad.stroke_correction import (  # noqa: F401
    DEFAULT_CORRECTION_POLICY, LatestCorrectionWorker, StrokeCorrection as TrialCorrection, WholeCharacterCorrector)
