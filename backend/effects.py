"""Original, locally synthesized transition/impact sounds; no third-party samples."""
from pathlib import Path
import math
import struct
import wave


def make_impact_sound(path: Path, low: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    rate = 48000
    seconds = .62
    frames = int(rate * seconds)
    start_hz = 82 if low else 118
    end_hz = 35 if low else 48
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        buf = bytearray()
        for i in range(frames):
            t = i / rate
            phase = 2 * math.pi * (start_hz*t + (end_hz-start_hz)*t*t/(2*seconds))
            bass = .72 * math.exp(-5.2*t) * math.sin(phase)
            transient = .28 * math.exp(-34*t) * math.sin(2*math.pi*(760 if low else 1050)*t)
            sample = int(max(-1, min(1, bass + transient)) * 32767)
            buf.extend(struct.pack('<h', sample))
        out.writeframes(buf)
    return path
