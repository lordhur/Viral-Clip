"""Small original, locally synthesized impact effect used by the editor; no third-party samples."""
from pathlib import Path
import math, struct, wave

def make_impact_sound(path:Path, low=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): return path
    rate=48000; seconds=.48; frames=int(rate*seconds)
    with wave.open(str(path),'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
        buf=bytearray()
        for i in range(frames):
            t=i/rate; freq=(72 if low else 112)-38*t/seconds
            phase=2*math.pi*((72 if low else 112)*t-19*t*t/seconds)
            env=math.exp(-8*t)*(1-math.exp(-90*t))
            sample=int(max(-1,min(1,.72*env*math.sin(phase)))*32767)
            buf.extend(struct.pack('<h',sample))
        out.writeframes(buf)
    return path
