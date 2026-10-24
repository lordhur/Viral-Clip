"""Incremental GitHub updater for ZIP installs; uses only the Python standard library."""
from __future__ import annotations
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

OWNER = 'lordhur'
REPO = 'Viral-Clip'
BRANCH = 'arena/2e402653-viral-clip'
API = f'https://api.github.com/repos/{OWNER}/{REPO}'
ARCHIVE = f'https://codeload.github.com/{OWNER}/{REPO}/zip/refs/heads/{BRANCH}'
SKIP = {'.git', 'node_modules', 'dist', 'build', 'coverage', '.venv', '__pycache__', 'output', 'temp'}


def request(url: str) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'Viral-Hit-Updater/1.0', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read()


def current_sha() -> str:
    ref = urllib.parse.quote(BRANCH, safe='')
    data = json.loads(request(f'{API}/commits/{ref}'))
    return data['sha']


def state_file() -> Path:
    return Path(__file__).resolve().parent / '.viral-hit-version'


def safe_target(root: Path, name: str) -> Path:
    rel = PurePosixPath(name)
    if rel.is_absolute() or any(part in ('..', '') for part in rel.parts) or (rel.parts and rel.parts[0] in SKIP):
        raise ValueError(f'Chemin de mise à jour refusé : {name}')
    target = root.joinpath(*rel.parts).resolve()
    if root.resolve() not in target.parents:
        raise ValueError(f'Chemin de mise à jour hors du projet : {name}')
    return target


def download_archive(root: Path) -> None:
    print('Initialisation de la copie ZIP : téléchargement de la version courante…')
    archive = request(ARCHIVE)
    with tempfile.TemporaryDirectory(prefix='viralhit-update-') as tmp:
        zip_path = Path(tmp) / 'source.zip'
        zip_path.write_bytes(archive)
        with zipfile.ZipFile(zip_path) as zf:
            top = next((n.split('/')[0] for n in zf.namelist() if '/' in n), None)
            if not top:
                raise RuntimeError('Archive GitHub invalide.')
            for item in zf.infolist():
                name = item.filename
                if not name.startswith(top + '/') or item.is_dir():
                    continue
                relative = name[len(top) + 1:]
                if not relative or PurePosixPath(relative).parts[0] in SKIP:
                    continue
                target = safe_target(root, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(item) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)


def update_from_github(root: Path) -> None:
    state = state_file()
    latest = current_sha()
    base = state.read_text(encoding='utf-8').strip() if state.exists() else ''
    if not base:
        download_archive(root)
        state.write_text(latest, encoding='utf-8')
        print('Première mise à jour ZIP terminée; les prochaines seront différentielles.')
        return
    if base == latest:
        print('Viral Hit est déjà à jour.')
        return
    compare = json.loads(request(f'{API}/compare/{urllib.parse.quote(base, safe="")}...{latest}'))
    files = compare.get('files') or []
    if not files or len(files) >= 300:
        download_archive(root)
    else:
        print(f'{len(files)} fichier(s) modifié(s) à récupérer…')
        for entry in files:
            name = entry.get('filename', '')
            if entry.get('status') == 'removed':
                target = safe_target(root, name)
                target.unlink(missing_ok=True)
                continue
            if entry.get('status') == 'renamed' and entry.get('previous_filename'):
                safe_target(root, entry['previous_filename']).unlink(missing_ok=True)
            target = safe_target(root, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(request(entry['raw_url']))
    state.write_text(latest, encoding='utf-8')
    print('Mise à jour différentielle terminée.')


def main() -> int:
    root = Path(__file__).resolve().parent
    try:
        if '--record-current' in sys.argv:
            state_file().write_text(current_sha(), encoding='utf-8')
            print('Version de départ enregistrée.')
        else:
            update_from_github(root)
        return 0
    except (OSError, ValueError, KeyError, urllib.error.URLError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(f'Échec de la mise à jour : {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
