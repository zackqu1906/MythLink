"""Real MNN regression tests for recurrent output/input memory reuse."""
import pytest
pytest.importorskip("MNN")
import numpy as np
from ring_python_sdk.touchpad import TouchpadBackbone
from ring_python_sdk.touchpad.model import default_model_path

MODEL=default_model_path()


def test_step_preserves_all_outputs_before_any_input_overwrite():
    # Obtain every output from a pristine single native inference. Uploading
    # conv_0 prematurely corrupts next_ssm_0, so the old adapter fails here.
    ref=TouchpadBackbone(MODEL);runner=TouchpadBackbone(MODEL)
    token=np.array([.6,-.12,-.23,.8,.74,1.25],dtype=np.float32)
    ref.inputs['tokens'].copyFromHostTensor(ref._tensor((1,1,6),token))
    for name,host in ref.state_hosts.items():ref.inputs[name].copyFromHostTensor(host)
    assert ref.interpreter.runSession(ref.session)==0
    expected={}
    for name,out in ref.outputs.items():
        host=ref._tensor(out.getShape());out.copyToHostTensor(host)
        expected[name]=np.asarray(host.getData(),np.float32)
    actual=runner.step(token)
    np.testing.assert_array_equal(actual.ravel(),expected['velocities'])
    for name,host in runner.state_hosts.items():
        np.testing.assert_array_equal(host.getData(),expected['next_'+name],err_msg=name)
    # Explicitly reproduce the underlying alias hazard on this model/backend.
    ref.inputs['conv_0'].copyFromHostTensor(runner.state_hosts['conv_0'])
    changed=ref._tensor(ref.outputs['next_ssm_0'].getShape())
    ref.outputs['next_ssm_0'].copyToHostTensor(changed)
    assert np.max(np.abs(np.asarray(changed.getData())-expected['next_ssm_0']))>1e-3


def test_reset_clears_host_recurrent_states_and_replays_exactly():
    runner=TouchpadBackbone(MODEL)
    tokens=np.random.default_rng(42).uniform(-.3,.3,(240,6)).astype(np.float32)
    first=np.asarray([runner.step(t) for t in tokens])
    assert np.isfinite(first).all()
    runner.reset()
    assert all(not np.any(h.getData()) for h in runner.state_hosts.values())
    replay=np.asarray([runner.step(t) for t in tokens])
    np.testing.assert_array_equal(first,replay)
