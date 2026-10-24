from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path
import importlib.util
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from backend.vision import find_webcam_region

ROOT = Path(__file__).resolve().parent.parent
DATA = Path.home() / '.viral-hit'
INPUT = DATA / 'sources'
OUTPUT = DATA / 'clips'
for folder in (INPUT, OUTPUT):
    folder.mkdir(parents=True, exist_ok=True)

app = FastAPI(title='Viral Hit local API')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
app.mount('/media', StaticFiles(directory=str(DATA)), name='media')
db = sqlite3.connect(DATA / 'viralhit.db', check_same_thread=False)
lock = threading.RLock()
db.executescript('''
CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY,name TEXT,path TEXT,status TEXT,progress REAL DEFAULT 0,error TEXT);
CREATE TABLE IF NOT EXISTS clips(id TEXT PRIMARY KEY,video_id TEXT,title TEXT,start REAL,end REAL,score INTEGER,category TEXT,path TEXT,rating INTEGER DEFAULT 0,feedback TEXT,created REAL);
CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,clip_id TEXT,platforms TEXT,publish_at TEXT,status TEXT,title TEXT);
''')
for column, definition in [('stage', "TEXT NOT NULL DEFAULT 'En attente'"), ('speed_mbps', 'REAL NOT NULL DEFAULT 0'), ('eta_seconds', 'INTEGER NOT NULL DEFAULT 0')]:
    try:
        db.execute(f'ALTER TABLE videos ADD COLUMN {column} {definition}')
    except sqlite3.OperationalError:
        pass
try:
    db.execute("ALTER TABLE clips ADD COLUMN output_format TEXT NOT NULL DEFAULT 'vertical'")
except sqlite3.OperationalError:
    pass
db.commit()
ACTIVE_PROCESSES = {}
PROCESS_LOCK = threading.Lock()
CANCELLED = set()


def rows(sql, args=()):
    with lock:
        cursor = db.execute(sql, args)
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, r)) for r in cursor.fetchall()]


def set_video(vid, status, progress, error=None, path=None, stage=None, speed=None, eta=None):
    with lock:
        previous = db.execute('SELECT progress FROM videos WHERE id=?', (vid,)).fetchone()
        if previous and status not in ('Erreur', 'Annulé'):
            progress = max(float(previous[0] or 0), float(progress))
        assignments = ['status=?', 'progress=?', 'error=?']
        values = [status, progress, error]
        if path is not None:
            assignments.append('path=?'); values.append(str(path))
        if stage is not None:
            assignments.append('stage=?'); values.append(stage)
        if speed is not None:
            assignments.append('speed_mbps=?'); values.append(speed)
        if eta is not None:
            assignments.append('eta_seconds=?'); values.append(eta)
        values.append(vid)
        db.execute(f"UPDATE videos SET {','.join(assignments)} WHERE id=?", values)
        db.commit()


def run(cmd, timeout=3600, job_id=None, download=False):
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace', bufsize=1)
        if job_id:
            with PROCESS_LOCK:
                ACTIVE_PROCESSES[job_id] = proc
        output = []
        started = time.time()
        if proc.stdout:
            for line in proc.stdout:
                output.append(line)
                if len(output) > 80:
                    output.pop(0)
                if download and job_id:
                    match = re.search(r'(\d+(?:\.\d+)?)%', line)
                    if match:
                        rate = re.search(r'at\s+([\d.]+)\s*(KiB|MiB|GiB)/s', line)
                        eta_match = re.search(r'ETA\s+(\d{1,2}:\d{2}(?::\d{2})?)', line)
                        speed = float(rate.group(1)) * 1024 ** ({'KiB': 1, 'MiB': 2, 'GiB': 3}[rate.group(2)]) / 1024 ** 2 if rate else None
                        parts = [int(x) for x in eta_match.group(1).split(':')] if eta_match else []
                        eta = sum(v * 60 ** i for i, v in enumerate(reversed(parts))) if parts else None
                        set_video(job_id, 'Téléchargement', min(34, 3 + float(match.group(1)) * .31),
                                  stage='Téléchargement yt-dlp', speed=speed, eta=eta)
                if job_id in CANCELLED:
                    proc.terminate()
                    break
                if time.time() - started > timeout:
                    proc.kill()
                    raise RuntimeError(f'Délai dépassé pendant {cmd[0]}.')
        code = proc.wait(timeout=10)
        return subprocess.CompletedProcess(cmd, code, ''.join(output), '')
    except FileNotFoundError as exc:
        raise RuntimeError(f"Commande introuvable : {cmd[0]}. Installez FFmpeg et yt-dlp, puis relancez Viral Hit.") from exc
    finally:
        if job_id:
            with PROCESS_LOCK:
                ACTIVE_PROCESSES.pop(job_id, None)


def analyze(vid, min_duration=12, max_duration=35, clip_count=0, output_format='vertical'):
    try:
        if vid in CANCELLED:
            return
        set_video(vid, 'Analyse en cours', 40, stage='Lecture des métadonnées')
        video = rows('SELECT * FROM videos WHERE id=?', (vid,))[0]
        source = Path(video['path'])
        probe = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(source)], job_id=vid)
        if probe.returncode:
            raise RuntimeError('FFprobe ne parvient pas à lire la vidéo : ' + probe.stdout[-500:])
        duration = float(probe.stdout.strip())
        if duration < min_duration:
            raise RuntimeError(f'Vidéo trop courte : {duration:.1f}s (minimum demandé : {min_duration}s).')

        # A coarse, deterministic timeline sampler. It is not a semantic AI detector.
        window = min(max_duration, duration)
        if clip_count > 0:
            window = min(window, max(min_duration, duration / (1 + .72 * (clip_count - 1))))
        stride = max(min_duration, int(window * .72))
        candidates = []
        for start in range(0, max(1, int(duration - min_duration) + 1), stride):
            end = min(duration, start + window)
            if end - start >= min_duration:
                score = min(94, 48 + int((end - start) / 3) + ((start // 7) % 20))
                candidates.append((start, end, score))
        if not candidates:
            candidates = [(0, duration, min(90, 55 + int(duration / 3)))]
        selected = []
        for item in sorted(candidates, key=lambda x: x[2], reverse=True):
            if all(max(0, min(item[1], b) - max(item[0], a)) / min(item[1] - item[0], b - a) < .3 for a, b, _ in selected):
                selected.append(item)
        if clip_count > 0:
            selected = selected[:clip_count]
        if vid in CANCELLED:
            return
        set_video(vid, 'Analyse terminée · préparation des clips', 58,
                  stage=f'{len(selected)} segment(s) distinct(s) retenu(s)')
        created = 0
        render_started = time.time()
        from backend.effects import make_impact_sound
        for index, (start, end, score) in enumerate(sorted(selected)):
            if vid in CANCELLED:
                return
            set_video(vid, f'Montage {index + 1}/{len(selected)}', 58 + int(index / max(1, len(selected)) * 38),
                      stage=f'Export FFmpeg · extrait {index + 1}/{len(selected)}')
            clip_id = str(uuid.uuid4())
            output = OUTPUT / f'{clip_id}.mp4'
            face_box = find_webcam_region(source, start, end - start) if output_format == 'vertical' else None
            if face_box:
                x, y, cw, ch = face_box
                graph = (f'[0:v]split=2[cam][game];[cam]crop={cw}:{ch}:{x}:{y},'
                         'scale=1080:520:force_original_aspect_ratio=increase,crop=1080:520[camtop];'
                         ' [game]scale=1080:1400:force_original_aspect_ratio=increase,crop=1080:1400[gameplay];'
                         '[camtop][gameplay]vstack=inputs=2,setsar=1[layout]')
                basic_video = 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1'
                camera_note = 'Webcam détectée · caméra en haut'
            elif output_format == 'horizontal':
                graph = '[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[layout]'
                basic_video = 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1'
                camera_note = 'Format horizontal · cadrage source'
            else:
                graph = '[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[layout]'
                basic_video = 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1'
                camera_note = 'Webcam non détectée · cadrage vertical centré'
            width, height = (1920, 1080) if output_format == 'horizontal' else (1080, 1920)
            caption = 'EPIC !' if score >= 80 else ('WOW ?!' if score >= 65 else 'MOMENT FORT')
            zoom = "if(lte(on,45),1+0.0017*on,if(lte(on,105),1.076,1.076-0.0004*(on-105)))"
            advanced_video = (f"{graph};[layout]fps=30,zoompan=z='{zoom}':d=1:s={width}x{height}:fps=30,"
                              f"eq=saturation=1.08:contrast=1.03,drawtext=text='{caption}':fontcolor=white:fontsize=72:"
                              "borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.28:enable='between(t,0.45,1.65)'[vout]")
            set_video(vid, f'Montage {index + 1}/{len(selected)}', 58 + int(index / max(1, len(selected)) * 38), stage=camera_note)
            sfx_path = DATA / ('impact_heavy.wav' if score >= 75 else 'impact_soft.wav')
            make_impact_sound(sfx_path, low=score < 75)
            audio_probe = run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=index', '-of', 'csv=p=0', str(source)])
            if audio_probe.stdout.strip():
                audio_filter = '[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[a0];[1:a]volume=0.28,adelay=850|850[fx];[a0][fx]amix=inputs=2:duration=first:dropout_transition=0[aout]'
            else:
                audio_filter = f'[1:a]volume=0.28,adelay=850|850,apad=pad_dur={end-start:.3f},atrim=duration={end-start:.3f}[aout]'
            common = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(source),
                      '-i', str(sfx_path), '-t', f'{end-start:.3f}', '-filter_complex', advanced_video + ';' + audio_filter,
                      '-map', '[vout]', '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart']
            cmd = common + ['-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '21', str(output)]
            result = run(cmd, job_id=vid)
            if vid in CANCELLED:
                output.unlink(missing_ok=True); return
            if result.returncode:
                cmd = common + ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', str(output)]
                result = run(cmd, job_id=vid)
            if result.returncode:
                simple_graph = f'[0:v]{basic_video},fps=30[vout];{audio_filter}'
                fallback = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(source),
                            '-i', str(sfx_path), '-t', f'{end-start:.3f}', '-filter_complex', simple_graph,
                            '-map', '[vout]', '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart',
                            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', str(output)]
                result = run(fallback, job_id=vid)
            if vid in CANCELLED:
                output.unlink(missing_ok=True); return
            if result.returncode or not output.exists() or output.stat().st_size < 1024:
                detail = (result.stderr or result.stdout or 'Le fichier exporté est vide.').strip()[-700:]
                output.unlink(missing_ok=True)
                raise RuntimeError('Échec du montage FFmpeg : ' + detail)
            check = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_name,width,height', '-of', 'json', str(output)], job_id=vid)
            try:
                stream = json.loads(check.stdout)['streams'][0]
            except Exception as exc:
                raise RuntimeError('FFmpeg a créé un fichier sans piste vidéo lisible.') from exc
            if not stream.get('width') or not stream.get('height'):
                raise RuntimeError('La vidéo exportée n’a pas de dimensions valides.')
            if vid in CANCELLED:
                output.unlink(missing_ok=True); return
            title = ['Moment fort détecté', 'Séquence à partager', 'Extrait vidéo'][index % 3]
            with lock:
                db.execute('INSERT INTO clips(id,video_id,title,start,end,score,category,path,rating,feedback,created,output_format) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (clip_id, vid, title, start, end, score, 'Action', str(output), 0, '', time.time(), output_format))
                db.commit()
            created += 1
            elapsed = time.time() - render_started
            remaining = int(elapsed / created * max(0, len(selected) - created))
            set_video(vid, f'Montage {created}/{len(selected)}', min(99, 58 + int(created / max(1, len(selected)) * 41)),
                      stage=f'Export terminé · {created}/{len(selected)}', eta=remaining)
        if vid not in CANCELLED:
            set_video(vid, 'Terminé', 100, stage='Terminé', eta=0)
        if not created and vid not in CANCELLED:
            raise RuntimeError('Aucun extrait valide n’a pu être exporté.')
    except Exception as exc:
        if vid not in CANCELLED:
            set_video(vid, 'Erreur', 100, str(exc), stage='Erreur')


def enqueue(path, name, min_duration=12, max_duration=35, clip_count=0, output_format='vertical', vid=None):
    vid = vid or str(uuid.uuid4())
    CANCELLED.discard(vid)
    with lock:
        db.execute("INSERT OR REPLACE INTO videos(id,name,path,status,progress,error,stage) VALUES(?,?,?,?,?,NULL,?)",
                   (vid, name, str(path), 'Analyse en cours', 40, 'Lecture des métadonnées'))
        db.commit()
    threading.Thread(target=analyze, args=(vid, min_duration, max_duration, clip_count, output_format), daemon=True).start()
    return vid


@app.post('/api/import')
async def upload(file: UploadFile = File(...), min_duration: int = Form(12), max_duration: int = Form(35),
                 clip_count: int = Form(0), output_format: str = Form('vertical')):
    if min_duration < 5 or max_duration < min_duration or max_duration > 180 or clip_count < 0 or output_format not in ('vertical', 'horizontal'):
        raise HTTPException(400, 'Paramètres de clips invalides.')
    name = Path(file.filename or 'video.mp4').name
    target = INPUT / f'{uuid.uuid4()}_{name}'
    try:
        with target.open('wb') as output:
            while chunk := await file.read(8 * 1024 * 1024):
                output.write(chunk)
        return {'id': enqueue(target, name, min_duration, max_duration, clip_count, output_format), 'name': name}
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, str(exc))


class Link(BaseModel):
    url: str
    min_duration: int = Field(12, ge=5, le=180)
    max_duration: int = Field(35, ge=5, le=180)
    clip_count: int = Field(0, ge=0)
    output_format: str = 'vertical'


def download_link(vid, url, min_duration, max_duration, clip_count, output_format):
    stem = uuid.uuid4().hex
    template = str(INPUT / (stem + '.%(ext)s'))
    try:
        result = run([sys.executable, '-m', 'yt_dlp', '--no-playlist', '--newline', '-f', 'bv*+ba/b',
                      '--merge-output-format', 'mp4', '-o', template, url], job_id=vid, download=True)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or 'yt-dlp a échoué.')[-900:])
        files = [f for f in INPUT.glob(stem + '.*') if f.suffix.lower() not in ('.part', '.ytdl')]
        if not files:
            raise RuntimeError('yt-dlp a terminé mais aucun fichier vidéo n’a été trouvé.')
        chosen = next((f for f in files if f.suffix.lower() == '.mp4'), files[0])
        set_video(vid, 'Analyse en cours', 38, path=chosen, stage='Téléchargement terminé · analyse vidéo')
        if vid not in CANCELLED:
            analyze(vid, min_duration, max_duration, clip_count, output_format)
    except Exception as exc:
        if vid not in CANCELLED:
            set_video(vid, 'Erreur', 100, str(exc), stage='Erreur')


@app.post('/api/link')
def link(data: Link):
    if not data.url.startswith(('https://', 'http://')):
        raise HTTPException(400, 'Lien invalide')
    if data.max_duration < data.min_duration or data.output_format not in ('vertical', 'horizontal'):
        raise HTTPException(400, 'Paramètres de clips invalides.')
    vid = str(uuid.uuid4())
    CANCELLED.discard(vid)
    with lock:
        db.execute("INSERT INTO videos(id,name,path,status,progress,stage) VALUES(?,?,?,?,?,?)",
                   (vid, data.url, '', 'Téléchargement', 3, 'Démarrage du téléchargement'))
        db.commit()
    threading.Thread(target=download_link, args=(vid, data.url, data.min_duration, data.max_duration, data.clip_count, data.output_format), daemon=True).start()
    return {'id': vid, 'status': 'Téléchargement lancé'}


@app.get('/api/videos')
def videos():
    return rows('SELECT * FROM videos ORDER BY rowid DESC')


@app.delete('/api/videos/{vid}')
def delete_video(vid: str):
    entry = rows('SELECT path FROM videos WHERE id=?', (vid,))
    if not entry:
        raise HTTPException(404, 'Analyse introuvable.')
    CANCELLED.add(vid)
    with PROCESS_LOCK:
        proc = ACTIVE_PROCESSES.get(vid)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    with lock:
        clip_paths = [r[0] for r in db.execute('SELECT path FROM clips WHERE video_id=?', (vid,)).fetchall()]
        db.execute('DELETE FROM clips WHERE video_id=?', (vid,))
        db.execute('DELETE FROM videos WHERE id=?', (vid,))
        db.commit()
    for path in clip_paths + [entry[0]['path']]:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
    return {'ok': True}


@app.get('/api/clips')
def clips():
    result = rows('SELECT * FROM clips ORDER BY score DESC')
    for clip in result:
        clip['url'] = 'http://127.0.0.1:8765/media/clips/' + Path(clip['path']).name
    return result


class Rating(BaseModel):
    rating: int
    feedback: str = ''


@app.patch('/api/clips/{cid}/feedback')
def feedback(cid: str, data: Rating):
    if not 1 <= data.rating <= 5:
        raise HTTPException(400, 'Note entre 1 et 5')
    with lock:
        db.execute('UPDATE clips SET rating=?,feedback=? WHERE id=?', (data.rating, data.feedback, cid))
        db.commit()
    return {'ok': True}


@app.delete('/api/clips/{cid}')
def delete_clip(cid: str):
    found = rows('SELECT path FROM clips WHERE id=?', (cid,))
    if found:
        Path(found[0]['path']).unlink(missing_ok=True)
    with lock:
        db.execute('DELETE FROM clips WHERE id=?', (cid,))
        db.commit()
    return {'ok': True}


@app.get('/api/stats')
def stats():
    return {'clips': rows('SELECT COUNT(*) n FROM clips')[0]['n'],
            'videos': rows('SELECT COUNT(*) n FROM videos')[0]['n'],
            'rated': rows('SELECT COUNT(*) n FROM clips WHERE rating>0')[0]['n']}


class Post(BaseModel):
    clip_id: str
    platforms: list[str]
    publish_at: str
    title: str


@app.get('/api/planning')
def planning():
    return rows('SELECT * FROM posts ORDER BY publish_at')


@app.post('/api/planning')
def schedule(post: Post):
    post_id = str(uuid.uuid4())
    with lock:
        db.execute('INSERT INTO posts VALUES(?,?,?,?,?,?)', (post_id, post.clip_id, ','.join(post.platforms), post.publish_at, 'Programmé', post.title))
        db.commit()
    return {'id': post_id}


@app.get('/api/health')
def health():
    return {'ok': True, 'ffmpeg': shutil.which('ffmpeg') is not None,
            'yt_dlp': importlib.util.find_spec('yt_dlp') is not None,
            'webcam_detection': importlib.util.find_spec('cv2') is not None}
