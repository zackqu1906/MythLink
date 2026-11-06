"""macOS arm64 CPU inference from the unchanged encrypted Android model.

Run directly to compare 512 tokens and all recurrent states with SDK fixtures.
For live inference, create one Receiver per stream, then call step() serially.
"""
from pathlib import Path
import ctypes as C
import hashlib
import json
import platform
import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parent
MODEL_SHA256 = "c16d77015ad37ec09a7b3b9d86055aab910096ce4595142c91238724eed3c8ab"
WIDTHS = [256, 256, 256, 256, 64, 64, 64]
SCALES = np.array([1/32, 1/64, 1/32, 1/32, 1/32, 1/64, 1/64, 1/32], dtype=np.float32)


def decode_features(raw: bytes) -> np.ndarray:
    """Use only for the 8 wire bytes; already decoded floats need no scaling."""
    if len(raw) != 8:
        raise ValueError("Expected 8 signed feature bytes")
    values = np.frombuffer(raw, dtype=np.int8)
    if np.any(values == -128):
        raise ValueError("Reserved feature value -128")
    return values.astype(np.float32) * SCALES


def load_model_bytes() -> bytes:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Use a native macOS arm64 Python process (not Rosetta)")
    lib = C.CDLL(str(ROOT / "native/libring_touchpad_model.dylib"))
    lib.ring_touchpad_model_open.argtypes = [C.c_void_p, C.c_size_t, C.POINTER(C.c_void_p)]
    lib.ring_touchpad_model_open.restype = C.c_int
    lib.ring_touchpad_model_data.argtypes = [C.c_void_p]
    lib.ring_touchpad_model_data.restype = C.c_void_p
    lib.ring_touchpad_model_size.argtypes = [C.c_void_p]
    lib.ring_touchpad_model_size.restype = C.c_size_t
    lib.ring_touchpad_model_close.argtypes = [C.c_void_p]
    lib.ring_touchpad_model_close.restype = None
    encrypted = (ROOT / "assets/ring_runtime.bin").read_bytes()
    buffer = C.create_string_buffer(encrypted)
    handle = C.c_void_p()
    code = lib.ring_touchpad_model_open(buffer, len(encrypted), C.byref(handle))
    if code:
        raise RuntimeError(f"Model authentication/loading failed: {code}")
    try:
        model = C.string_at(lib.ring_touchpad_model_data(handle), lib.ring_touchpad_model_size(handle))
        if hashlib.sha256(model).hexdigest() != MODEL_SHA256:
            raise RuntimeError("Model SHA-256 mismatch")
        return model
    finally:
        lib.ring_touchpad_model_close(handle)


class TouchpadModel:
    def __init__(self):
        if ort.__version__ != "1.21.0":
            raise RuntimeError("This delivery is validated with onnxruntime==1.21.0")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(load_model_bytes(), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.names = ["xy", "touch", "finger"] + [f"next_h{i}" for i in range(7)]
        expected_inputs = [('features', 8)] + [(f'h{i}', w) for i, w in enumerate(WIDTHS)]
        expected_outputs = [('xy', 4), ('touch', 2), ('finger', 2)] + [(f'next_h{i}', w) for i, w in enumerate(WIDTHS)]
        for nodes, expected in [(self.session.get_inputs(), expected_inputs), (self.session.get_outputs(), expected_outputs)]:
            if [(n.name, n.shape, n.type) for n in nodes] != [(n, [1,w], 'tensor(float)') for n,w in expected]:
                raise RuntimeError('Model input/output contract mismatch')
        self.reset_for_epoch()

    def reset(self):
        self.states = {f"h{i}": np.zeros((1, width), dtype=np.float32)
                       for i, width in enumerate(WIDTHS)}

    def reset_for_epoch(self):
        """Warm the backend with zero input, then discard all warmup state."""
        self.reset()
        self.step(np.zeros(8, dtype=np.float32))
        self.reset()

    def step(self, features):
        x = np.asarray(features, dtype=np.float32)
        if x.shape != (8,) or not np.isfinite(x).all():
            raise ValueError("Expected 8 finite, dequantized float features")
        values = self.session.run(self.names, {"features": x.reshape(1, 8), **self.states})
        if not all(np.isfinite(value).all() for value in values):
            raise RuntimeError("Nonfinite model output/state; end this stream")
        xy, touch, finger = (value.reshape(-1) for value in values[:3])
        self.states = {f"h{i}": values[i + 3] for i in range(7)}
        # Two chronological 5 ms frames, each [vx, vy, touch_logit, finger_logit].
        # Apply sigmoid to touch/finger in postprocessing, exactly once.
        return np.array([[xy[0], xy[1], touch[0], finger[0]],
                         [xy[2], xy[3], touch[1], finger[1]]], dtype=np.float32)

    def state_vector(self):
        return np.concatenate([self.states[f"h{i}"].reshape(-1) for i in range(7)])
