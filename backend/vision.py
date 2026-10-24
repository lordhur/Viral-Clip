"""Optional face/webcam locator. OpenCV is optional so base installs work without native wheels."""
from pathlib import Path
import math

def find_webcam_region(video_path:Path,start_seconds:float,duration:float):
    try:
        import cv2
    except ImportError:
        return None
    cascade_path=Path(cv2.data.haarcascades)/'haarcascade_frontalface_default.xml'
    detector=cv2.CascadeClassifier(str(cascade_path))
    if detector.empty(): return None
    cap=cv2.VideoCapture(str(video_path))
    if not cap.isOpened(): return None
    width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if width<2 or height<2: cap.release(); return None
    samples=[]
    for n in range(9):
        cap.set(cv2.CAP_PROP_POS_MSEC,(start_seconds+duration*(n+.5)/9)*1000)
        ok,frame=cap.read()
        if not ok: continue
        scale=min(1.0,960/width)
        small=cv2.resize(frame,(int(width*scale),int(height*scale))) if scale<1 else frame
        gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
        faces=detector.detectMultiScale(gray,scaleFactor=1.1,minNeighbors=5,minSize=(max(30,int(small.shape[1]*.035)),max(30,int(small.shape[0]*.035))))
        for x,y,w,h in faces:
            # A webcam face is usually a repeated, relatively small region of the recording.
            if w<small.shape[1]*.38 and h<small.shape[0]*.55:
                samples.append((x/scale,y/scale,w/scale,h/scale))
    cap.release()
    if len(samples)<3: return None
    clusters=[]; radius=math.hypot(width,height)*.12
    for face in samples:
        cx,cy=face[0]+face[2]/2,face[1]+face[3]/2
        cluster=next((c for c in clusters if math.hypot(cx-c['cx'],cy-c['cy'])<radius),None)
        if cluster is None: clusters.append({'cx':cx,'cy':cy,'faces':[face]})
        else:
            cluster['faces'].append(face); cluster['cx']=sum(f[0]+f[2]/2 for f in cluster['faces'])/len(cluster['faces']); cluster['cy']=sum(f[1]+f[3]/2 for f in cluster['faces'])/len(cluster['faces'])
    best=max(clusters,key=lambda c:len(c['faces']))
    if len(best['faces'])<3: return None
    faces=best['faces']; x0=min(f[0] for f in faces); y0=min(f[1] for f in faces); x1=max(f[0]+f[2] for f in faces); y1=max(f[1]+f[3] for f in faces)
    face_w=max(f[2] for f in faces); face_h=max(f[3] for f in faces)
    # Expand around the repeated face to retain the camera frame rather than a tight face crop.
    cw=min(width,max(x1-x0,face_w*2.8)); ch=min(height,max(y1-y0,face_h*2.8))
    cx=max(0,min(width-cw,(x0+x1-cw)/2)); cy=max(0,min(height-ch,(y0+y1-ch)/2))
    return tuple(int(round(v)) for v in (cx,cy,cw,ch))
