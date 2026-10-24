"""Best-effort face locator. Any absent/broken optional OpenCV build safely falls back to the normal crop."""
from pathlib import Path
import math
import logging

log = logging.getLogger(__name__)

def webcam_supported():
    try:
        import cv2
        if not all(hasattr(cv2, name) for name in ('CascadeClassifier', 'VideoCapture', 'cvtColor')):
            return False
        cascade_path = Path(cv2.data.haarcascades) / 'haarcascade_frontalface_default.xml'
        return cascade_path.is_file()
    except Exception:
        return False

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
            faces = detector.detectMultiScale(gray, scaleFactor=1.06, minNeighbors=3,
                minSize=(max(20, int(small.shape[1] * .022)), max(20, int(small.shape[0] * .022))))
            for x, y, w, h in faces:
                if w < small.shape[1] * .38 and h < small.shape[0] * .55:
                    samples.append((x / scale, y / scale, w / scale, h / scale))
        if len(samples) < 2:
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
        if len(best['faces']) < 2:
            return None
        faces = best['faces']
        x0, y0 = min(f[0] for f in faces), min(f[1] for f in faces)
        x1, y1 = max(f[0] + f[2] for f in faces), max(f[1] + f[3] for f in faces)
        face_w, face_h = max(f[2] for f in faces), max(f[3] for f in faces)
        aspect = 1080 / 520
        center_x, center_y = (x0+x1)/2, (y0+y1)/2
        cw = max((x1-x0) + face_w*2.0, face_w*4.2)
        ch = max((y1-y0) + face_h*1.8, face_h*2.6)
        cw = max(cw, ch*aspect)
        ch = cw/aspect
        if cw > width:
            cw = width; ch = cw/aspect
        if ch > height:
            ch = height; cw = ch*aspect
        cx = max(0, min(width-cw, center_x-cw/2))
        cy = max(0, min(height-ch, center_y-ch*.38))
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
