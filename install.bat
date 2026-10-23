@echo off
setlocal
cd /d "%~dp0"
echo === Viral Hit - installation ===
where node >nul 2>nul || (echo Installez Node.js 20+ puis relancez. & pause & exit /b 1)
where python >nul 2>nul || (echo Installez Python 3.10+ puis relancez. & pause & exit /b 1)
where ffmpeg >nul 2>nul || echo ATTENTION: FFmpeg absent du PATH. L'export logiciel ne sera pas disponible.
where yt-dlp >nul 2>nul || python -m pip install --user yt-dlp
python -m pip install -r requirements.txt || exit /b 1
call npm install || exit /b 1
echo Installation terminee. Lancez npm run dev dans ce dossier.
pause
