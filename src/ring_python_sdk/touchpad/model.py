"""Original AAR model with Android-compatible, non-aliasing state transfer."""
from pathlib import Path
import hashlib
import numpy as np

MODEL_SHA256 = "b7595998f7aa6088181c9c856efd96255aa093e36666a1bc67dc12eae7c58ff8"


def default_model_path() -> Path:
    path = Path(__file__).parent / "assets" / "touchpad_model.mnn"
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise RuntimeError("Bundled touchpad model missing or checksum mismatch")
    return path


class TouchpadBackbone:
    """One 6-float token -> raw (5, 3) output, carrying 12 recurrent states."""
    def __init__(self, model: Path | None = None):
        try:
            import MNN
        except ImportError as exc:
            raise RuntimeError("Touchpad inference requires MNN==3.6.1; install proximic-ring[touchpad]") from exc
        self._mnn = MNN
        model = default_model_path() if model is None else Path(model)
        self.interpreter = MNN.Interpreter(str(model))
        self.session = self.interpreter.createSession(
            {'backend': 'CPU', 'numThread': 1, 'precision': 'high'})
        self.inputs = self.interpreter.getSessionInputAll(self.session)
        self.outputs = self.interpreter.getSessionOutputAll(self.session)
        expected = {'tokens': (1, 1, 6)}
        expected.update({f'conv_{k}': (1, 256, 4) for k in range(6)})
        expected.update({f'ssm_{k}': (1, 3, 64, 32) for k in range(6)})
        if {n: t.getShape() for n, t in self.inputs.items()} != expected:
            raise ValueError('Unexpected model input contract')
        out_expected = {'next_' + n: shape for n, shape in expected.items() if n != 'tokens'}
        out_expected['velocities'] = (1, 5, 3)
        if {n: t.getShape() for n, t in self.outputs.items()} != out_expected:
            raise ValueError('Unexpected model output contract')
        self.state_names = tuple(f'{kind}_{k}' for k in range(6) for kind in ('conv', 'ssm'))
        self.velocity_host = self._tensor((1, 5, 3))
        self.reset()

    def _tensor(self, shape, values=None):
        # PyMNN tuple elements must be Python floats, not numpy.float32 objects.
        values = (0.0,) * int(np.prod(shape)) if values is None else tuple(float(x) for x in values)
        return self._mnn.Tensor(shape, self._mnn.Halide_Type_Float, values, self._mnn.Tensor_DimensionType_Caffe)

    def reset(self):
        # Match nativeReset: zero the independent host states, not just session
        # inputs. MNN may reuse session input/output memory between operations.
        self.state_hosts = {n: self._tensor(self.inputs[n].getShape()) for n in self.state_names}

    def step(self, token):
        token = np.asarray(token, dtype=np.float32)
        if token.shape != (6,) or not np.isfinite(token).all():
            raise ValueError('A token must contain exactly six finite floats')
        self.inputs['tokens'].copyFromHostTensor(self._tensor((1, 1, 6), token))
        for name, host in self.state_hosts.items():
            self.inputs[name].copyFromHostTensor(host)
        status = self.interpreter.runSession(self.session)
        if status != 0:
            raise RuntimeError(f'MNN inference failed: {status}')
        self.outputs['velocities'].copyToHostTensor(self.velocity_host)
        values = np.asarray(self.velocity_host.getData(), dtype=np.float32).reshape(5, 3)
        if not np.isfinite(values).all():
            self.reset()
            raise RuntimeError('Non-finite model output; recurrent state reset')
        for name, host in self.state_hosts.items():
            self.outputs['next_' + name].copyToHostTensor(host)
        # Do NOT write any input while reading these outputs. E.g. conv_0 input
        # aliases next_ssm_0 output in this model's CPU memory plan. Android JNI
        # snapshots all 12 outputs and only uploads them at the next step.
        return values

