"""Differential checks against the unmodified Java algorithms shipped in 1.15.1."""
import io
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from mythring.health_sleep import BedtimeDetector, stage_features


HARNESS = r'''
import java.io.*;
import com.nexthci.ring_sdk.health.internal.BedtimeDetector;
import com.nexthci.ring_sdk.health.internal.StageFeatures;
public class HealthGolden {
 static DataInputStream in = new DataInputStream(System.in);
 static DataOutputStream out = new DataOutputStream(System.out);
 static int[] ints() throws Exception {int[] a=new int[in.readInt()];for(int i=0;i<a.length;i++)a[i]=in.readInt();return a;}
 static long[] longs() throws Exception {long[] a=new long[in.readInt()];for(int i=0;i<a.length;i++)a[i]=in.readLong();return a;}
 static float[] floats() throws Exception {float[] a=new float[in.readInt()];for(int i=0;i<a.length;i++)a[i]=in.readFloat();return a;}
 public static void main(String[] args)throws Exception {
  if(in.readInt()==0){
   BedtimeDetector.Result r=BedtimeDetector.detect(in.readLong(),ints(),ints());
   out.writeInt(r.periods.size());for(long[] a:r.periods){out.writeLong(a[0]);out.writeLong(a[1]);}
   out.writeInt(r.trace.size());for(long[] a:r.trace)for(long v:a)out.writeLong(v);
  }else{
   long[] t=longs();float[] f=floats();int width=in.readInt();float[] p=floats();int[] columns=ints();
   long[] bt=longs();float[] b=floats();long[] burstTimes=longs();float[] bursts=floats();long[] starts=longs();
   float[][] result=StageFeatures.build(t,f,width,p,columns,bt,b,burstTimes,bursts,starts);
   out.writeInt(result.length);for(float[] row:result)for(float v:row)out.writeFloat(v);
  }
  out.flush();
 }
}
'''


class HealthJavaParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2]
        source = root / "ring-android-sdk-1.15.1-touchpad-20261103-full-auto-reconnect/ring-sdk/src/main/java/com/nexthci/ring_sdk/health/internal"
        if not shutil.which("javac") or not source.exists():
            raise unittest.SkipTest("Original 1.15.1 sources and a JDK are required for differential verification")
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        harness = cls.directory / "HealthGolden.java"
        harness.write_text(HARNESS)
        subprocess.run(["javac", "-d", str(cls.directory), str(source / "BedtimeDetector.java"), str(source / "StageFeatures.java"), str(harness)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def java(self, data):
        return io.BytesIO(subprocess.run(["java", "-cp", str(self.directory), "HealthGolden"], input=data, check=True, capture_output=True).stdout)

    def test_bedtime_full_trace_matches_original_java(self):
        for size, seed in ((480, 1), (1440, 42), (300, 17)):
            rng = np.random.default_rng(seed)
            motion = rng.integers(0, 3, size*2).tolist()
            temperatures = rng.integers(3300, 3400, size).tolist()
            for i in range(min(120, size*2)):
                motion[i] = 10
            if size > 400:
                motion[500:502] = [-1, -1]
                motion[600:610] = [31]*10
            start = 1700000100000
            pack = lambda values: struct.pack(">i", len(values))+struct.pack(">"+"i"*len(values), *values)
            result = self.java(struct.pack(">iq", 0, start)+pack(motion)+pack(temperatures))
            count = struct.unpack(">i", result.read(4))[0]
            periods = [list(struct.unpack(">qq", result.read(16))) for _ in range(count)]
            count = struct.unpack(">i", result.read(4))[0]
            trace = [list(struct.unpack(">18q", result.read(144))) for _ in range(count)]
            python = BedtimeDetector.detect(start, motion, temperatures)
            self.assertEqual(python.periods, periods)
            self.assertEqual(python.trace, trace)

    def test_all_1883_stage_features_match_original_java(self):
        rng, start, n, width = np.random.default_rng(123), 1700000100000, 80, 200
        times = np.arange(n, dtype=np.int64)*30000+start-15000
        features = rng.normal(size=(n, width)).astype(np.float32)
        features[rng.random(features.shape) < .08] = np.nan
        probabilities = rng.random((n, 5)).astype(np.float32)
        columns = np.arange(171, dtype=np.int32)
        beat_times = np.arange(start-900000, start+2400000, 800, dtype=np.int64)
        ibi = np.column_stack((rng.normal(800, 30, len(beat_times)).astype(np.float32), np.full(len(beat_times), np.nan, np.float32), (rng.random(len(beat_times)) > .08).astype(np.float32)))
        burst_times = np.arange(start, start+2400000, 60000, dtype=np.int64)
        bursts = rng.normal(size=(len(burst_times), 18)).astype(np.float32)
        bursts[rng.random(bursts.shape) < .08] = np.nan
        starts = np.arange(8, dtype=np.int64)*300000+start
        def pack(values, dtype):
            values = np.asarray(values).reshape(-1)
            return struct.pack(">i", len(values))+values.astype(dtype).tobytes()
        data = struct.pack(">i", 1)+pack(times, ">i8")+pack(features, ">f4")+struct.pack(">i", width)+pack(probabilities, ">f4")+pack(columns, ">i4")+pack(beat_times, ">i8")+pack(ibi, ">f4")+pack(burst_times, ">i8")+pack(bursts, ">f4")+pack(starts, ">i8")
        result = self.java(data)
        count = struct.unpack(">i", result.read(4))[0]
        expected = np.frombuffer(result.read(), dtype=">f4").reshape(count, 1883)
        actual = stage_features(times, features, probabilities, columns, beat_times, ibi, burst_times, bursts, starts)
        np.testing.assert_allclose(actual, expected, rtol=2e-7, atol=2e-7, equal_nan=True)


if __name__ == "__main__":
    unittest.main()
