"""Best-effort face locator. Any absent/broken optional OpenCV build safely falls back to the normal crop."""
from pathlib import Path
import math
import logging

log = logging.getLogger(__name__)

def find_webcam_region(video_path: Path, start_seconds: float, duration: float):
    cap = None
    try:
        import cv2
        # Some Python/OpenCV distributions expose a partial cv2 module without the Haar API.
        if not all(hasattr(cv2, name) for name in ('CascadeClassifier', 'VideoCapture', 'cvtColor')):
            log.warning('OpenCV lacks Haar face-detection APIs; using center crop.')
            return None
        cascade_path = Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml'
        detector = cv2.CascadeClassifier(str(cascade_path))
        if detector.empty():
            log.warning('OpenCV face cascade is missing; using center crop.')
            return None
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width < 2 or height < 2:
            return None
        samples = []
        for n in range(9):
            cap.set(cv2.CAP_PROP_POS_MSEC, (start_seconds + duration * (n + .5) / 9) * 1000)
            ok, frame = cap.read()
            if not ok:
                continue
            scale = min(1.0, 960 / width)
            small = cv2.resize(frame, (int(width * scale), int(height * scale))) if scale < 1 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                minSize=(max(30, int(small.shape[1] * .035)), max(30, int(small.shape[0] * .035))))
            for x, y, w, h in faces:
                if w < small.shape[1] * .38 and h < small.shape[0] * .55:
                    samples.append((x / scale, y / scale, w / scale, h / scale))
        if len(samples) < 3:
            return None
        clusters = []
        radius = math.hypot(width, height) * .12
        for face in samples:
            cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
            cluster = next((c for c in clusters if math.hypot(cx-c['cx'], cy-c['cy']) < radius), None)
            if cluster is None:
                clusters.append({'cx': cx, 'cy': cy, 'faces': [face]})
            else:
                cluster['faces'].append(face)
                cluster['cx'] = sum(f[0] + f[2]/2 for f in cluster['faces']) / len(cluster['faces'])
                cluster['cy'] = sum(f[1] + f[3]/2 for f in cluster['faces']) / len(cluster['faces'])
        best = max(clusters, key=lambda c: len(c['faces']))
        if len(best['faces']) < 3:
            return None
        faces = best['faces']
        x0, y0 = min(f[0] for f in faces), min(f[1] for f in faces)
        x1, y1 = max(f[0] + f[2] for f in faces), max(f[1] + f[3] for f in faces)
        face_w, face_h = max(f[2] for f in faces), max(f[3] for f in faces)
        cw, ch = min(width, max(x1-x0, face_w*2.8)), min(height, max(y1-y0, face_h*2.8))
        cx, cy = max(0, min(width-cw, (x0+x1-cw)/2)), max(0, min(height-ch, (y0+y1-ch)/2))
        return tuple(int(round(v)) for v in (cx, cy, cw, ch))
    except Exception:
        log.exception('Webcam/face detection failed; falling back to a center crop.')
        return None
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
