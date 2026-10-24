@echo off
setlocal
cd /d "%~dp0"
title Mise a jour Viral Hit

git rev-parse --is-inside-work-tree >nul 2>nul
if not errorlevel 1 goto git_update

where python >nul 2>nul
if errorlevel 1 (echo Python est introuvable dans le PATH.& pause& exit /b 1)
if not exist "update.py" call :bootstrap
if errorlevel 1 (
  echo ECHEC lors de l'initialisation ZIP. Le detail est indique ci-dessus.
  pause
  exit /b 1
)
python update.py
if errorlevel 1 (
  echo ECHEC lors de la mise a jour incrementale. Verifiez la connexion puis reessayez.
  pause
  exit /b 1
)
goto install_deps

:git_update
echo Mise a jour Git de Viral Hit...
git pull --ff-only origin arena/2e402653-viral-clip
if errorlevel 1 (echo La mise a jour Git a echoue.& pause& exit /b 1)
goto install_deps

:bootstrap
echo Dossier ZIP detecte. Initialisation automatique unique...
where curl.exe >nul 2>nul
if errorlevel 1 (echo ECHEC: curl.exe introuvable. Windows 10/11 ou ulterieur est requis.& exit /b 1)
set "WORK=%TEMP%\ViralHitBootstrap_%RANDOM%"
set "ZIP=%WORK%\source.zip"
mkdir "%WORK%" >nul 2>nul
curl.exe -fL --retry 3 --output "%ZIP%" "https://codeload.github.com/lordhur/Viral-Clip/zip/refs/heads/arena/2e402653-viral-clip"
if errorlevel 1 (echo ECHEC: impossible de telecharger l'archive GitHub.& exit /b 1)
echo Archive recue. Extraction avec Python...
python -c "import sys,zipfile,pathlib; z=zipfile.ZipFile(sys.argv[1]); root=pathlib.Path(sys.argv[2]); top=next(n.split('/')[0] for n in z.namelist() if '/' in n); skip={'.git','node_modules','dist','build','coverage','.venv','__pycache__','output','temp'}; [( (root/rel).parent.mkdir(parents=True,exist_ok=True), (root/rel).write_bytes(z.read(n))) for n in z.namelist() if n.startswith(top+'/') and not n.endswith('/') and (rel:=n[len(top)+1:]) and pathlib.PurePosixPath(rel).parts[0] not in skip]" "%ZIP%" "%~dp0"
if errorlevel 1 (echo ECHEC: Python n'a pas pu extraire les fichiers de l'archive.& exit /b 1)
if not exist "update.py" (echo ECHEC: update.py absent apres extraction.& exit /b 1)
python update.py --record-current
if errorlevel 1 (echo ECHEC: GitHub n'a pas repondu lors de la verification de version.& exit /b 1)
rmdir /s /q "%WORK%" >nul 2>nul
echo Initialisation terminee. Les prochaines mises a jour ne telechargeront que les fichiers modifies.
exit /b 0

:install_deps
echo Mise a jour des dependances...
call npm.cmd install
if errorlevel 1 (echo Echec de la mise a jour Node.js.& pause& exit /b 1)
python -m pip install -r requirements.txt
if errorlevel 1 (echo Echec de la mise a jour Python.& pause& exit /b 1)
echo Viral Hit est a jour. Lancez "Lancer Viral Hit.bat".
pause
endlocal
