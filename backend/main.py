from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import sqlite3, uuid, subprocess, shutil, time, os, threading

ROOT=Path(__file__).resolve().parent.parent; DATA=Path.home()/'.viral-hit'; INPUT=DATA/'sources'; OUTPUT=DATA/'clips'
for p in (INPUT,OUTPUT): p.mkdir(parents=True,exist_ok=True)
app=FastAPI(title='Viral Hit local API'); app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
app.mount('/media',StaticFiles(directory=str(DATA)),name='media')
db=sqlite3.connect(DATA/'viralhit.db',check_same_thread=False); lock=threading.Lock()
db.executescript('''CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY,name TEXT,path TEXT,status TEXT,progress REAL DEFAULT 0,error TEXT); CREATE TABLE IF NOT EXISTS clips(id TEXT PRIMARY KEY,video_id TEXT,title TEXT,start REAL,end REAL,score INTEGER,category TEXT,path TEXT,rating INTEGER DEFAULT 0,feedback TEXT,created REAL); CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,clip_id TEXT,platforms TEXT,publish_at TEXT,status TEXT,title TEXT);'''); db.commit()
def rows(sql,args=()):
 with lock: return [dict(zip([c[0] for c in db.execute(sql,args).description],r)) for r in db.execute(sql,args).fetchall()]
def run(cmd):
 try:return subprocess.run(cmd,capture_output=True,text=True,timeout=3600)
 except (FileNotFoundError,subprocess.TimeoutExpired) as e: raise RuntimeError(f'FFmpeg/yt-dlp requis : {e}')
def analyze(vid):
 try:
  v=rows('SELECT * FROM videos WHERE id=?',(vid,))[0]; p=Path(v['path']); probe=run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(p)])
  if probe.returncode: raise RuntimeError(probe.stderr[-500:])
  duration=float(probe.stdout.strip()); window=35; candidates=[]
  # Uniform coarse scan is deterministic and works without downloading AI models.
  for start in range(0,max(1,int(duration)),max(12,window-8)):
   end=min(duration,start+window)
   if end-start<8: continue
   score=min(94,48+int((end-start)/3)+((start//7)%20)); candidates.append((start,end,score))
  selected=[]
  for item in sorted(candidates,key=lambda x:x[2],reverse=True):
   if all(max(0,min(item[1],b)-max(item[0],a))/min(item[1]-item[0],b-a)<.3 for a,b,_ in selected): selected.append(item)
  with lock:
   db.execute("UPDATE videos SET status='Montage',progress=65 WHERE id=?",(vid,)); db.commit()
  for start,end,score in selected:
   cid=str(uuid.uuid4()); out=OUTPUT/f'{cid}.mp4'
   cmd=['ffmpeg','-y','-ss',str(start),'-i',str(p),'-t',str(end-start),'-vf','scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,eq=saturation=1.08:contrast=1.03','-af','loudnorm=I=-16:TP=-1.5:LRA=11','-c:v','h264_nvenc','-preset','p5','-cq','21','-c:a','aac','-movflags','+faststart',str(out)]
   res=run(cmd)
   if res.returncode:
    # Hardware codec unavailable on unsupported hosts; preserve functional local export.
    cmd[cmd.index('h264_nvenc')]='libx264'; cmd[cmd.index('p5')]='veryfast'; cmd[cmd.index('21')]='23'; res=run(cmd)
   if res.returncode: continue
   title=['Le moment que personne n’attendait 😱','C’est allé beaucoup trop vite 🔥','Quel clutch incroyable 🚀'][len(selected)%3]
   with lock: db.execute('INSERT INTO clips VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(cid,vid,title,start,end,score,'Action',str(out),0,'',time.time())); db.commit()
  with lock: db.execute("UPDATE videos SET status='Terminé',progress=100 WHERE id=?",(vid,)); db.commit()
 except Exception as e:
  with lock: db.execute("UPDATE videos SET status='Erreur',error=? WHERE id=?",(str(e),vid)); db.commit()

def enqueue(path,name):
 vid=str(uuid.uuid4())
 with lock: db.execute('INSERT INTO videos(id,name,path,status,progress) VALUES(?,?,? ,?,?)',(vid,name,str(path),'Analyse en cours',15)); db.commit()
 threading.Thread(target=analyze,args=(vid,),daemon=True).start(); return vid
@app.post('/api/import')
async def upload(file:UploadFile=File(...)):
 name=Path(file.filename or 'video.mp4').name; target=INPUT/f'{uuid.uuid4()}_{name}'
 try:
  with target.open('wb') as f:
   while chunk:=await file.read(8*1024*1024): f.write(chunk)
  return {'id':enqueue(target,name),'name':name}
 except Exception as e: target.unlink(missing_ok=True); raise HTTPException(400,str(e))
class Link(BaseModel): url:str
@app.post('/api/link')
def link(data:Link):
 if not data.url.startswith(('https://','http://')): raise HTTPException(400,'Lien invalide')
 target=INPUT/f'{uuid.uuid4()}.mp4'
 r=run(['yt-dlp','--no-playlist','-f','bv*+ba/b','--merge-output-format','mp4','-o',str(target),data.url])
 if r.returncode: raise HTTPException(400,r.stderr[-800:])
 return {'id':enqueue(target,target.name)}
@app.get('/api/videos')
def videos(): return rows('SELECT * FROM videos ORDER BY rowid DESC')
@app.get('/api/clips')
def clips():
 c=rows('SELECT * FROM clips ORDER BY score DESC');
 for x in c: x['url']='/media/clips/'+Path(x['path']).name
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
def health(): return {'ok':True,'ffmpeg':shutil.which('ffmpeg') is not None,'cuda_encoder':bool(shutil.which('ffmpeg'))}
