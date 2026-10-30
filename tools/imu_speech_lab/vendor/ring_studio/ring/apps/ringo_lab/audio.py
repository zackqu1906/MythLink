"""Optional system libopus decoder. Raw Opus is always preserved."""
import ctypes as c
import ctypes.util
from pathlib import Path
from ring.drivers.ringo_protocol import opus_frames


class AudioDecoder:
    def __init__(self):
        name = ctypes.util.find_library('opus')
        if not name:
            name = next((str(p) for p in (Path('/opt/homebrew/lib/libopus.dylib'), Path('/usr/local/lib/libopus.dylib')) if p.is_file()), None)
        if not name:
            raise OSError('未找到 libopus：仍录制原始 Opus；安装 libopus 后可显示 PCM 波形')
        self.lib = c.CDLL(name)
        self.lib.opus_decoder_create.argtypes = [c.c_int, c.c_int, c.POINTER(c.c_int)]
        self.lib.opus_decoder_create.restype = c.c_void_p
        self.lib.opus_decode.argtypes = [c.c_void_p, c.c_char_p, c.c_int, c.POINTER(c.c_int16), c.c_int, c.c_int]
        self.lib.opus_decoder_destroy.argtypes = [c.c_void_p]
        self.decoder = None
        self.reset()

    def reset(self):
        self.close()
        error = c.c_int()
        self.decoder = self.lib.opus_decoder_create(16000, 1, c.byref(error))
        if error.value or not self.decoder:
            raise OSError('Opus decoder initialization failed')

    def decode(self, block):
        expected, frames = opus_frames(block)
        result = []
        for frame in frames:
            pcm = (c.c_int16 * 1920)()
            count = self.lib.opus_decode(self.decoder, frame, len(frame), pcm, 1920, 0)
            if count < 0:
                raise ValueError(f'Opus decode error {count}')
            if count != 320:
                raise ValueError(f'Opus frame decoded {count} samples, expected 320')
            result.extend(pcm[:count])
        if len(result) != expected:
            raise ValueError('Opus sample count mismatch')
        return result

    def close(self):
        if self.decoder:
            self.lib.opus_decoder_destroy(self.decoder)
            self.decoder = None


class OrderedAudio:
    """Bounded sequence ordering before mutating the stateful Opus decoder."""
    def __init__(self, decoder):
        self.decoder = decoder
        self.pending = {}
        self.next_seq = 0

    def push(self, seq, uptime, block, now):
        if (seq - self.next_seq) % 65536 >= 32768 or seq in self.pending:
            return []
        self.pending[seq] = (uptime, block, now)
        return self.drain(now)

    def drain(self, now, force=False):
        results = []
        while self.pending:
            if self.next_seq not in self.pending:
                if not force and len(self.pending) < 16 and now - min(v[2] for v in self.pending.values()) < 2:
                    break
                seq = min(self.pending, key=lambda s: (s - self.next_seq) % 65536)
                missing = (seq - self.next_seq) % 65536
                self.decoder.reset()
                results.append((self.next_seq, 0, None, f'音频缺少 {missing} 个块，已重置解码器'))
                self.next_seq = seq
            seq = self.next_seq
            uptime, block, _ = self.pending.pop(seq)
            self.next_seq = (seq + 1) % 65536
            try:
                results.append((seq, uptime, self.decoder.decode(block), ''))
            except ValueError as exc:
                self.decoder.reset()
                results.append((seq, uptime, None, str(exc)))
        return results

    def close(self):
        self.decoder.close()
