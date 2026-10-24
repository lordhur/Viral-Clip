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
from backend.vision import find_webcam_region, webcam_supported

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


def run(cmd, timeout=3600, job_id=None, download=False, capture_all=False):
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
                if not capture_all and len(output) > 80:
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


def detect_audio_activity(source, duration, vid):
    """Measure silence and audio-energy peaks; this is not speech recognition."""
    probe = run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=index', '-of', 'csv=p=0', str(source)])
    if not probe.stdout.strip():
        return [], []
    metrics_path = DATA / f'audio-levels-{uuid.uuid4().hex}.txt'
    escaped_metrics_path = metrics_path.as_posix().replace(':', r'\:')
    audio_filter = (f"silencedetect=noise=-35dB:d=0.30,ebur128=metadata=1,"
                    f"ametadata=print:file='{escaped_metrics_path}'")
    result = run(['ffmpeg', '-hide_banner', '-loglevel', 'info', '-nostats', '-i', str(source), '-vn',
                  '-af', audio_filter, '-f', 'null', '-'], job_id=vid, capture_all=True)
    if vid in CANCELLED:
        metrics_path.unlink(missing_ok=True)
        return [], []
    silence_starts = [float(m) for m in re.findall(r'silence_start:\s*(-?\d+(?:\.\d+)?)', result.stdout)]
    silence_ends = [float(m) for m in re.findall(r'silence_end:\s*(-?\d+(?:\.\d+)?)', result.stdout)]
    silent = []
    for i, start in enumerate(silence_starts):
        end = silence_ends[i] if i < len(silence_ends) else duration
        silent.append((max(0.0, start), min(duration, end)))
    active = []
    cursor = 0.0
    for start, end in sorted(silent):
        if start > cursor + .08:
            active.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor + .08:
        active.append((cursor, duration))
    if not active:
        active = [(0.0, duration)]

    # Read EBU R128's 400 ms momentary loudness from a metadata sidecar; don't
    # depend on a truncated FFmpeg console log for long recordings.
    try:
        metrics_text = metrics_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        metrics_text = ''
    finally:
        metrics_path.unlink(missing_ok=True)
    levels = [(float(t), float(v)) for t, v in re.findall(
        r'pts_time:([\d.]+)\s+lavfi\.r128\.M=(-?[\d.]+)', metrics_text)]
    peaks = []
    if len(levels) >= 3:
        ordered = sorted(v for _, v in levels if v > -90)
        if ordered:
            threshold = max(-38.0, ordered[len(ordered) // 2] + 4.0)
            candidates = []
            for i in range(1, len(levels) - 1):
                t, level = levels[i]
                if level >= threshold and level >= levels[i-1][1] and level > levels[i+1][1]:
                    candidates.append((level, t))
            chosen = []
            for level, t in sorted(candidates, reverse=True):
                if all(abs(t - prior_t) >= 1.6 for _, prior_t in chosen):
                    chosen.append((level, t))
            peaks = sorted(t for _, t in chosen)
    return active, peaks


def fallback_webcam_region(source, position='bottom_left'):
    if position in ('none', 'auto'):
        position = 'bottom_left'
    probe = run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', str(source)])
    try:
        stream = json.loads(probe.stdout)['streams'][0]
        width, height = int(stream['width']), int(stream['height'])
    except Exception:
        return None
    aspect = 1080 / 730
    crop_w = min(width, int(width * .34))
    crop_h = min(height, int(crop_w / aspect))
    x = width - crop_w if position.endswith('right') else 0
    y = 0 if position.startswith('top') else height - crop_h
    return x, y, crop_w, crop_h


def analyze(vid, min_duration=12, max_duration=35, clip_count=0, output_format='vertical', camera_position='auto'):
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

        set_video(vid, 'Analyse en cours', 44, stage='Analyse audio · détection des silences et pics sonores')
        audio_activity, audio_peaks = detect_audio_activity(source, duration, vid)
        if vid in CANCELLED:
            return

        # Timeline sampling is biased toward audible activity, but is not speech transcription.
        window = min(max_duration, duration)
        if clip_count > 0:
            window = min(window, max(min_duration, duration / (1 + .72 * (clip_count - 1))))
        stride = max(min_duration, int(window * .72))
        candidates = []
        for start in range(0, max(1, int(duration - min_duration) + 1), stride):
            end = min(duration, start + window)
            if end - start >= min_duration:
                active_seconds = sum(max(0.0, min(end, b) - max(start, a)) for a, b in audio_activity)
                active_ratio = active_seconds / max(1.0, end - start)
                peak_count = sum(start <= peak <= end for peak in audio_peaks)
                # Heuristic ranking: retain audible passages and favor energetic moments.
                score = min(98, 45 + int((end - start) / 4) + int(active_ratio * 20) + min(20, peak_count * 3))
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
            # Keep audible phrases and collapse pauses longer than 0.35 s into real jump cuts.
            keep = []
            for active_start, active_end in audio_activity:
                left, right = max(start, active_start - .18), min(end, active_end + .18)
                if right <= left:
                    continue
                if keep and left - keep[-1][1] <= .35:
                    keep[-1] = (keep[-1][0], right)
                else:
                    keep.append((left, right))
            kept_seconds = sum(b-a for a,b in keep)
            if not keep or kept_seconds < max(5, min_duration * .45):
                keep = [(start, end)]
                kept_seconds = end-start
            face_box = find_webcam_region(source, start, end - start) if output_format == 'vertical' and camera_position == 'auto' else None
            face_detected = bool(face_box)
            if output_format == 'vertical' and not face_box and camera_position != 'none':
                fallback_position = 'bottom_left' if camera_position == 'auto' else camera_position
                face_box = fallback_webcam_region(source, fallback_position)
                if face_box:
                    camera_note = f'Webcam : recadrage de secours {fallback_position.replace("_", " ")}'
            cut_graph_parts = []
            for n,(a,b) in enumerate(keep):
                rel_a,rel_b = max(0,a-start),max(0,b-start)
                cut_graph_parts.append(f'[0:v]trim=start={rel_a:.3f}:end={rel_b:.3f},setpts=PTS-STARTPTS[v{n}]')
                if audio_activity:
                    cut_graph_parts.append(f'[0:a]atrim=start={rel_a:.3f}:end={rel_b:.3f},asetpts=PTS-STARTPTS[a{n}]')
            if len(keep)>1:
                if audio_activity:
                    concat_inputs=''.join(f'[v{n}][a{n}]' for n in range(len(keep)))
                    cut_graph_parts.append(f'{concat_inputs}concat=n={len(keep)}:v=1:a=1[cutv][cuta]')
                else:
                    concat_inputs=''.join(f'[v{n}]' for n in range(len(keep)))
                    cut_graph_parts.append(f'{concat_inputs}concat=n={len(keep)}:v=1:a=0[cutv]')
            else:
                cut_graph_parts.append('[v0]null[cutv]')
                if audio_activity:
                    cut_graph_parts.append('[a0]anull[cuta]')
            cut_graph=';'.join(cut_graph_parts)
            export_start = keep[0][0]
            # Translate source-time energy peaks into the concatenated output timeline.
            event_times = []
            output_offset = 0.0
            for a, b in keep:
                event_times.extend(output_offset + peak - a for peak in audio_peaks if a <= peak <= b)
                output_offset += b - a
            if not event_times:
                cursor = 0.0
                for a, b in keep:
                    event_times.append(cursor + (b - a) / 2)
                    cursor += b - a
            event_times = sorted(set(max(0.0, min(kept_seconds - .05, t)) for t in event_times))[:6]
            pulses = [f'max(0,1-abs(on-{round(t * 30)})/48)' for t in event_times]
            pulse = pulses[0]
            for next_pulse in pulses[1:]:
                pulse = f'max({pulse},{next_pulse})'
            zoom = f'1+0.30*({pulse})'
            camera_layout = bool(face_box)
            if camera_layout:
                x, y, cw, ch = face_box
                graph = (f'[cutv]split=2[cam][game];[cam]crop={cw}:{ch}:{x}:{y},'
                         'scale=1080:730:force_original_aspect_ratio=increase,crop=1080:730[camtop];'
                         '[game]scale=1080:1190:force_original_aspect_ratio=increase,crop=1080:1190[gamebase]')
                base_graph = graph + ';[camtop][gamebase]vstack=inputs=2,setsar=1[baseout]'
                zoom_graph = (graph + f";[gamebase]fps=30,zoompan=z='{zoom}':d=1:s=1080x1190:fps=30[gamezoom];"
                              '[camtop][gamezoom]vstack=inputs=2,setsar=1[renderbase]')
                if face_detected:
                    camera_note = 'Webcam détectée · cadrage supérieur (38%)'
            else:
                if output_format == 'horizontal':
                    graph = '[cutv]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1[layout]'
                    camera_note = 'Format horizontal · cadrage source'
                    width, height = 1920, 1080
                else:
                    graph = '[cutv]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[layout]'
                    camera_note = 'Webcam non détectée · cadrage vertical centré'
                    width, height = 1080, 1920
                base_graph = graph + ';[layout]null[baseout]'
                zoom_graph = (graph + f";[layout]fps=30,zoompan=z='{zoom}':d=1:s={width}x{height}:fps=30[renderbase]")
            caption = 'EPIC !' if score >= 80 else ('WOW ?!' if score >= 65 else 'MOMENT FORT')
            zoom_only_video = (f"{zoom_graph};[renderbase]eq=saturation=1.08:contrast=1.03,"
                               "drawbox=x=0:y=0:w=iw:h=ih:color=white@0.22:t=fill:enable='between(t,0.20,0.34)'[vout]")
            advanced_video = (f"{zoom_graph};[renderbase]eq=saturation=1.10:contrast=1.05,"
                              f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.22:t=fill:enable='between(t,0.20,0.34)',drawtext=text='{caption}':fontcolor=white:fontsize=88:"
                              "borderw=6:bordercolor=black:box=1:boxcolor=black@0.40:boxborderw=18:"
                              "x=(w-text_w)/2:y=h*0.26:enable='between(t,0.25,2.8)'[vout]")
            set_video(vid, f'Montage {index + 1}/{len(selected)}', 58 + int(index / max(1, len(selected)) * 38), stage=camera_note)
            sfx_path = DATA / ('impact_heavy_v2.wav' if score >= 75 else 'impact_soft_v2.wav')
            make_impact_sound(sfx_path, low=score < 75)
            delay_ms = int(min(max(0.0, kept_seconds - .1), event_times[0]) * 1000)
            if audio_activity:
                audio_filter = (f"[cuta]loudnorm=I=-16:TP=-1.5:LRA=11[a0];[1:a]volume=1.0,"
                                f"adelay={delay_ms}|{delay_ms}[fx];[a0][fx]amix=inputs=2:duration=first:"
                                "dropout_transition=0:weights='1 1.65':normalize=0,alimiter=limit=0.96[aout]")
            else:
                audio_filter = (f'[1:a]volume=1.0,adelay={delay_ms}|{delay_ms},apad=pad_dur={kept_seconds:.3f},'
                                f'atrim=duration={kept_seconds:.3f}[aout]')
            common = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(source),
                      '-i', str(sfx_path), '-t', f'{kept_seconds:.3f}', '-filter_complex', cut_graph + ';' + advanced_video + ';' + audio_filter,
                      '-map', '[vout]', '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart']
            cmd = common + ['-c:v', 'h264_nvenc', '-preset', 'p5', '-cq', '21', str(output)]
            result = run(cmd, job_id=vid)
            if vid in CANCELLED:
                output.unlink(missing_ok=True); return
            if result.returncode:
                cmd = common + ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', str(output)]
                result = run(cmd, job_id=vid)
            if result.returncode:
                # If drawtext is unavailable, retain the moving zoom and audio effect.
                zoom_common = common.copy()
                zoom_common[zoom_common.index('-filter_complex') + 1] = cut_graph + ';' + zoom_only_video + ';' + audio_filter
                zoom_fallback = zoom_common + ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', str(output)]
                result = run(zoom_fallback, job_id=vid)
            if result.returncode:
                # Last resort only: preserve a playable vertical/horizontal render.
                simple_graph = base_graph + ';[baseout]fps=30[vout];' + audio_filter
                fallback = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{start:.3f}', '-i', str(source),
                            '-i', str(sfx_path), '-t', f'{kept_seconds:.3f}', '-filter_complex', cut_graph + ';' + simple_graph,
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
                           (clip_id, vid, title, export_start, export_start + kept_seconds, score, 'Action', str(output), 0, '', time.time(), output_format))
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


def enqueue(path, name, min_duration=12, max_duration=35, clip_count=0, output_format='vertical', camera_position='auto', vid=None):
    vid = vid or str(uuid.uuid4())
    CANCELLED.discard(vid)
    with lock:
        db.execute("INSERT OR REPLACE INTO videos(id,name,path,status,progress,error,stage) VALUES(?,?,?,?,?,NULL,?)",
                   (vid, name, str(path), 'Analyse en cours', 40, 'Lecture des métadonnées'))
        db.commit()
    threading.Thread(target=analyze, args=(vid, min_duration, max_duration, clip_count, output_format, camera_position), daemon=True).start()
    return vid


@app.post('/api/import')
async def upload(file: UploadFile = File(...), min_duration: int = Form(12), max_duration: int = Form(35),
                 clip_count: int = Form(0), output_format: str = Form('vertical'), camera_position: str = Form('auto')):
    if min_duration < 5 or max_duration < min_duration or max_duration > 180 or clip_count < 0 or output_format not in ('vertical', 'horizontal') or camera_position not in ('auto','bottom_left','bottom_right','top_left','top_right','none'):
        raise HTTPException(400, 'Paramètres de clips invalides.')
    name = Path(file.filename or 'video.mp4').name
    target = INPUT / f'{uuid.uuid4()}_{name}'
    try:
        with target.open('wb') as output:
            while chunk := await file.read(8 * 1024 * 1024):
                output.write(chunk)
        return {'id': enqueue(target, name, min_duration, max_duration, clip_count, output_format, camera_position), 'name': name}
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, str(exc))


class Link(BaseModel):
    url: str
    min_duration: int = Field(12, ge=5, le=180)
    max_duration: int = Field(35, ge=5, le=180)
    clip_count: int = Field(0, ge=0)
    output_format: str = 'vertical'
    camera_position: str = 'auto'


def download_link(vid, url, min_duration, max_duration, clip_count, output_format, camera_position):
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
            analyze(vid, min_duration, max_duration, clip_count, output_format, camera_position)
    except Exception as exc:
        if vid not in CANCELLED:
            set_video(vid, 'Erreur', 100, str(exc), stage='Erreur')


@app.post('/api/link')
def link(data: Link):
    if not data.url.startswith(('https://', 'http://')):
        raise HTTPException(400, 'Lien invalide')
    if data.max_duration < data.min_duration or data.output_format not in ('vertical', 'horizontal') or data.camera_position not in ('auto','bottom_left','bottom_right','top_left','top_right','none'):
        raise HTTPException(400, 'Paramètres de clips invalides.')
    vid = str(uuid.uuid4())
    CANCELLED.discard(vid)
    with lock:
        db.execute("INSERT INTO videos(id,name,path,status,progress,stage) VALUES(?,?,?,?,?,?)",
                   (vid, data.url, '', 'Téléchargement', 3, 'Démarrage du téléchargement'))
        db.commit()
    threading.Thread(target=download_link, args=(vid, data.url, data.min_duration, data.max_duration, data.clip_count, data.output_format, data.camera_position), daemon=True).start()
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
            'webcam_detection': webcam_supported()}
