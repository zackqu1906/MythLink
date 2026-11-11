import io
import struct
import unittest
import zipfile

import numpy as np
import torch

from mythring.health_sleep_ops import _biquad, _classifier, _indices, _peaks, _windows, register_sleep_ops
from mythring.protected_models import load_model_bytes, _unlock, _SPECS, ProtectedModelError
from importlib.resources import files


class SleepOpsTests(unittest.TestCase):
    def test_original_biquad_instruction_loop(self):
        # Execute the original ELF numerical loop, bypassing its ATen adapter.
        from unicorn.arm64_const import UC_ARM64_REG_X8,UC_ARM64_REG_X9,UC_ARM64_REG_X10,UC_ARM64_REG_S0,UC_ARM64_REG_CPACR_EL1
        runtime=_classifier();u=runtime.uc; rng=np.random.default_rng(20261104)
        coeff=rng.uniform(-.4,.4,(3,6)).astype(np.float32);coeff[:,3]=1
        raw=rng.uniform(-3,3,150).astype(np.float32)
        expected=[]
        with runtime.lock:
            u.mem_write(runtime.data,coeff.tobytes());u.mem_write(runtime.data+2048,bytes(24))
            u.reg_write(UC_ARM64_REG_CPACR_EL1,3<<20)
            for value in raw:
                u.reg_write(UC_ARM64_REG_X8,runtime.data+16)
                u.reg_write(UC_ARM64_REG_X9,runtime.data+2052)
                u.reg_write(UC_ARM64_REG_X10,3)
                u.reg_write(UC_ARM64_REG_S0,struct.unpack('<I',struct.pack('<f',value))[0])
                u.emu_start(runtime.base+0x1ae30,runtime.base+0x1aeac,count=10000)
                expected.append(struct.unpack('<f',struct.pack('<I',u.reg_read(UC_ARM64_REG_S0)))[0])
        actual=_biquad(torch.from_numpy(coeff),torch.from_numpy(raw)).numpy()
        np.testing.assert_array_equal(actual,np.array(expected,dtype=np.float32))

    def test_classifier_original_trees(self):
        actual=_classifier()(np.zeros((1,182),np.float32))
        np.testing.assert_allclose(actual,[[.035949279,.93701196,.00039144937,.026647341]],rtol=1e-6)
        np.testing.assert_allclose(actual.sum(axis=1),1,rtol=1e-6)
        with self.assertRaises(ValueError):_classifier()(np.zeros((1,181)))

    def test_peak_plateaus_and_interpolation_edges(self):
        self.assertEqual(_peaks(torch.tensor([0.,1,1,1,0,2,0])).tolist(),[2,5])
        self.assertEqual(_indices(torch.tensor([0.,1,1,3]),torch.tensor([-1.,0,1,2,3])).tolist(),[0,0,0,2,2])
        with self.assertRaises(ValueError):_indices(torch.tensor([0.,1]),torch.tensor([2.]))

    def test_windows_source_endpoint_and_warmup(self):
        times=torch.tensor([[0],[29999],[30000],[59999],[60000],[300000]],dtype=torch.int64)
        ends,indices=_windows(times,30.)
        self.assertEqual(ends[:2].tolist(),[[30000],[60000]])
        self.assertEqual(indices[0].tolist(),[-1,-1,-1,-1])
        self.assertEqual(indices[1].tolist(),[-1,0,2,3])
        self.assertEqual(indices[9,0],0)

    def test_original_protected_graph_loads(self):
        register_sleep_ops();register_sleep_ops()
        archive=zipfile.ZipFile(io.BytesIO(load_model_bytes('health')))
        model=torch.jit.load(io.BytesIO(archive.read('sleepstaging_2_6_0.pt')),map_location='cpu')
        self.assertIn('acm_values',str(model.forward.schema))

    def test_encrypted_model_authentication(self):
        assets=files('mythring').joinpath('assets')
        envelope_name,archive_name,entry=_SPECS['touchpad']
        envelope=bytearray(assets.joinpath(envelope_name).read_bytes());envelope[-1]^=1
        with self.assertRaisesRegex(ProtectedModelError,'authentication'):
            _unlock(bytes(envelope),assets.joinpath(archive_name).read_bytes(),entry)
