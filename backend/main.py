from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path
import sqlite3, uuid, subprocess, shutil, time, threading, json, sys, importlib.util

ROOT=Path(__file__).resolve().parent.parent
DATA=Path.home()/'.viral-hit'; INPUT=DATA/'sources'; OUTPUT=DATA/'clips'
for p in (INPUT,OUTPUT): p.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='Viral Hit local API')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
app.mount('/media',StaticFiles(directory=str(DATA)),name='media')
db=sqlite3.connect(DATA/'viralhit.db',check_same_thread=False); lock=threading.RLock()
db.executescript('''CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY,name TEXT,path TEXT,status TEXT,progress REAL DEFAULT 0,error TEXT); CREATE TABLE IF NOT EXISTS clips(id TEXT PRIMARY KEY,video_id TEXT,title TEXT,start REAL,end REAL,score INTEGER,category TEXT,path TEXT,rating INTEGER DEFAULT 0,feedback TEXT,created REAL); CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,clip_id TEXT,platforms TEXT,publish_at TEXT,status TEXT,title TEXT);''')
try: db.execute("ALTER TABLE clips ADD COLUMN output_format TEXT NOT NULL DEFAULT 'vertical'")
except sqlite3.OperationalError: pass
db.commit()
def rows(sql,args=()):
 with lock: return [dict(zip([c[0] for c in db.execute(sql,args).description],r)) for r in db.execute(sql,args).fetchall()]
def run(cmd, timeout=3600):
 try:return subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
 except FileNotFoundError as e: raise RuntimeError(f"Commande introuvable : {cmd[0]}. Installez FFmpeg et yt-dlp, puis relancez Viral Hit.") from e
 except subprocess.TimeoutExpired as e: raise RuntimeError(f"Délai dépassé pendant {cmd[0]}.") from e

def set_video(vid,status,progress,error=None,path=None):
 with lock:
  if path is None: db.execute('UPDATE videos SET status=?,progress=?,error=? WHERE id=?',(status,progress,error,vid))
  else: db.execute('UPDATE videos SET status=?,progress=?,error=?,path=? WHERE id=?',(status,progress,error,str(path),vid))
  db.commit()

def analyze(vid, min_duration=12, max_duration=35, clip_count=0, output_format='vertical'):
 try:
  v=rows('SELECT * FROM videos WHERE id=?',(vid,))[0]; p=Path(v['path'])
  probe=run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(p)])
  if probe.returncode: raise RuntimeError('FFprobe ne parvient pas à lire la vidéo : '+probe.stderr[-500:])
  duration=float(probe.stdout.strip())
  if duration < min_duration: raise RuntimeError(f'Vidéo trop courte : {duration:.1f}s (minimum demandé : {min_duration}s).')
  window=min(max_duration,duration)
  if clip_count>0:
   # Shorten windows when needed so the requested count fits in the source while staying above the chosen minimum.
   window=min(window,max(min_duration,duration/(1+.72*(clip_count-1))))
  stride=max(min_duration,int(window*.72)); candidates=[]
  for start in range(0,max(1,int(duration-min_duration)+1),stride):
   end=min(duration,start+window)
   if end-start < min_duration: continue
   score=min(94,48+int((end-start)/3)+((start//7)%20)); candidates.append((start,end,score))
  if not candidates: candidates=[(0,duration,min(90,55+int(duration/3)))]
  selected=[]
  for item in sorted(candidates,key=lambda x:x[2],reverse=True):
   if all(max(0,min(item[1],b)-max(item[0],a))/min(item[1]-item[0],b-a)<.3 for a,b,_ in selected): selected.append(item)
  if clip_count>0: selected=selected[:clip_count]
  set_video(vid,'Montage',65)
  created=0
  for index,(start,end,score) in enumerate(sorted(selected)):
   cid=str(uuid.uuid4()); out=OUTPUT/f'{cid}.mp4'
   if output_format=='horizontal':
    vf='scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,eq=saturation=1.08:contrast=1.03'
   else:
    vf='scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,eq=saturation=1.08:contrast=1.03'
   base=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(p),'-ss',f'{start:.3f}','-t',f'{end-start:.3f}','-vf',vf,'-af','loudnorm=I=-16:TP=-1.5:LRA=11','-c:a','aac','-b:a','192k','-movflags','+faststart']
   cmd=base+['-c:v','h264_nvenc','-preset','p5','-cq','21',str(out)]
   res=run(cmd)
   if res.returncode:
    cmd=base+['-c:v','libx264','-preset','veryfast','-crf','23',str(out)]
    res=run(cmd)
   if res.returncode or not out.exists() or out.stat().st_size<1024:
    detail=(res.stderr or 'Le fichier exporté est vide.').strip()[-700:]
    out.unlink(missing_ok=True)
    raise RuntimeError('Échec du montage FFmpeg : '+detail)
   check=run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=codec_name,width,height','-of','json',str(out)])
   try: stream=json.loads(check.stdout)['streams'][0]
   except Exception: raise RuntimeError('FFmpeg a créé un fichier sans piste vidéo lisible.')
   if not stream.get('width') or not stream.get('height'): raise RuntimeError('La vidéo exportée n’a pas de dimensions valides.')
   title=['Moment fort détecté','Séquence à partager','Extrait vidéo'][index%3]
   with lock:
    db.execute('INSERT INTO clips(id,video_id,title,start,end,score,category,path,rating,feedback,created,output_format) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(cid,vid,title,start,end,score,'Action',str(out),0,'',time.time(),output_format)); db.commit()
   created+=1; set_video(vid,'Montage',min(99,65+int(created/max(1,len(selected))*34)))
  set_video(vid,'Terminé',100)
  if not created: raise RuntimeError('Aucun extrait valide n’a pu être exporté.')
 except Exception as e: set_video(vid,'Erreur',100,str(e))

def enqueue(path,name,min_duration=12,max_duration=35,clip_count=0,output_format='vertical',vid=None):
 vid=vid or str(uuid.uuid4())
 with lock:
  db.execute('INSERT OR REPLACE INTO videos(id,name,path,status,progress,error) VALUES(?,?,?,?,?,NULL)',(vid,name,str(path),'Analyse en cours',15)); db.commit()
 threading.Thread(target=analyze,args=(vid,min_duration,max_duration,clip_count,output_format),daemon=True).start(); return vid

@app.post('/api/import')
async def upload(file:UploadFile=File(...),min_duration:int=Form(12),max_duration:int=Form(35),clip_count:int=Form(0),output_format:str=Form('vertical')):
 if min_duration<5 or max_duration<min_duration or max_duration>180 or clip_count<0 or output_format not in ('vertical','horizontal'): raise HTTPException(400,'Paramètres de clips invalides.')
 name=Path(file.filename or 'video.mp4').name; target=INPUT/f'{uuid.uuid4()}_{name}'
 try:
  with target.open('wb') as f:
   while chunk:=await file.read(8*1024*1024): f.write(chunk)
  return {'id':enqueue(target,name,min_duration,max_duration,clip_count,output_format),'name':name}
 except Exception as e: target.unlink(missing_ok=True); raise HTTPException(400,str(e))

class Link(BaseModel):
 url:str
 min_duration:int=Field(12,ge=5,le=180)
 max_duration:int=Field(35,ge=5,le=180)
 clip_count:int=Field(0,ge=0)
 output_format:str='vertical'

def download_link(vid,url,min_duration,max_duration,clip_count,output_format):
 stem=uuid.uuid4().hex; template=str(INPUT/(stem+'.%(ext)s'))
 try:
  r=run([sys.executable,'-m','yt_dlp','--no-playlist','--newline','-f','bv*+ba/b','--merge-output-format','mp4','-o',template,url])
  if r.returncode: raise RuntimeError((r.stderr or r.stdout or 'yt-dlp a échoué.')[-900:])
  files=sorted(INPUT.glob(stem+'.*'))
  files=[f for f in files if f.suffix.lower() not in ('.part','.ytdl')]
  if not files: raise RuntimeError('yt-dlp a terminé mais aucun fichier vidéo n’a été trouvé.')
  chosen=next((f for f in files if f.suffix.lower()=='.mp4'),files[0])
  set_video(vid,'Analyse en cours',20,path=chosen)
  analyze(vid,min_duration,max_duration,clip_count,output_format)
 except Exception as e: set_video(vid,'Erreur',100,str(e))

@app.post('/api/link')
def link(data:Link):
 if not data.url.startswith(('https://','http://')): raise HTTPException(400,'Lien invalide')
 if data.max_duration<data.min_duration or data.output_format not in ('vertical','horizontal'): raise HTTPException(400,'Paramètres de clips invalides.')
 vid=str(uuid.uuid4())
 with lock: db.execute('INSERT INTO videos(id,name,path,status,progress) VALUES(?,?,?,?,?)',(vid,data.url,'','Téléchargement',3)); db.commit()
 threading.Thread(target=download_link,args=(vid,data.url,data.min_duration,data.max_duration,data.clip_count,data.output_format),daemon=True).start()
 return {'id':vid,'status':'Téléchargement lancé'}

@app.get('/api/videos')
def videos(): return rows('SELECT * FROM videos ORDER BY rowid DESC')
@app.get('/api/clips')
def clips():
 c=rows('SELECT * FROM clips ORDER BY score DESC')
 for x in c: x['url']='http://127.0.0.1:8765/media/clips/'+Path(x['path']).name
 return c
class Rating(BaseModel): rating:int; feedback:str=''
@app.patch('/api/clips/{cid}/feedback')
def feedback(cid:str,d:Rating):
 if not 1<=d.rating<=5: raise HTTPException(400,'Note entre 1 et 5')
 with lock: db.execute('UPDATE clips SET rating=?,feedback=? WHERE id=?',(d.rating,d.feedback,cid)); db.commit()
 return {'ok':True}
@app.delete('/api/clips/{cid}')
def delete(cid:str):
 r=rows('SELECT path FROM clips WHERE id=?',(cid,))
 if r: Path(r[0]['path']).unlink(missing_ok=True)
 with lock: db.execute('DELETE FROM clips WHERE id=?',(cid,)); db.commit()
 return {'ok':True}
@app.get('/api/stats')
def stats(): return {'clips':rows('SELECT COUNT(*) n FROM clips')[0]['n'],'videos':rows('SELECT COUNT(*) n FROM videos')[0]['n'],'rated':rows('SELECT COUNT(*) n FROM clips WHERE rating>0')[0]['n']}
class Post(BaseModel): clip_id:str; platforms:list[str]; publish_at:str; title:str
@app.get('/api/planning')
def planning(): return rows('SELECT * FROM posts ORDER BY publish_at')
@app.post('/api/planning')
def schedule(p:Post):
 pid=str(uuid.uuid4())
 with lock: db.execute('INSERT INTO posts VALUES(?,?,?,?,?,?)',(pid,p.clip_id,','.join(p.platforms),p.publish_at,'Programmé',p.title)); db.commit()
 return {'id':pid}
@app.get('/api/health')
def health(): return {'ok':True,'ffmpeg':shutil.which('ffmpeg') is not None,'yt_dlp':importlib.util.find_spec('yt_dlp') is not None}
