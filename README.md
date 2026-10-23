# Viral Hit

Application desktop locale de création de clips. L'interface utilise React, TypeScript, Vite et Framer Motion ; le service local utilise FastAPI, SQLite, FFmpeg et yt-dlp. Les médias sont conservés dans `%USERPROFILE%\.viral-hit` (Windows) ou `~/.viral-hit`.

## Démarrage Windows

1. Installer Node.js 20+, Python 3.10+ et FFmpeg (avec `ffprobe`) puis ajouter leurs commandes au PATH. `yt-dlp` est installé par le script s'il manque.
2. Exécuter `install.bat`.
3. Dans le dossier du projet : `npm run dev` pour lancer l'interface web, puis dans un second terminal `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765`.
4. `npm run electron:dev` lance l'interface Electron/Vite. Pour le moment, le backend se lance séparément en développement.

## Fonctions incluses

Import vidéo local (lecture en blocs), import yt-dlp, SQLite, traitement asynchrone, création de clips distincts, encodage NVENC avec repli x264, recadrage vertical, lecture intégrée, téléchargement, notes, activité, statistiques et stockage de créneaux de planification. La déduplication applique une limite stricte de recouvrement temporel à 30 %.

## Limites honnêtes

Cette première version est un socle exécutable, pas la réalisation intégrale du cahier des charges. La sélection des extraits est une segmentation heuristique uniforme : aucune inférence Whisper/YOLO/CLIP/Silero, détection webcam/HUD, sous-titrage, mèmes, apprentissage de modèle, vues calendrier avancées ou publication réseau ne sont prétendus actifs. La planification enregistre des créneaux mais ne publie pas. L'import peut accepter de gros fichiers, mais l'interface ne montre pas encore une progression réelle de transfert. NVENC est utilisé si FFmpeg est compilé avec le codec et si le pilote GPU le permet ; sinon l'export bascule sur x264. Les API de réseaux sociaux requièrent des comptes et autorisations de plateforme et ne sont pas contournées.

## Vérification

`GET http://127.0.0.1:8765/api/health` vérifie la présence de FFmpeg. Endpoints principaux : `/api/import`, `/api/link`, `/api/videos`, `/api/clips`, `/api/stats`, `/api/planning`.
