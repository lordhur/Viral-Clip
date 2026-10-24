# Viral Hit

Application desktop locale en développement. Interface React/TypeScript avec Electron ; API FastAPI, base SQLite et média stockés localement dans `%USERPROFILE%\.viral-hit` (Windows) ou `~/.viral-hit`.

## Démarrage Windows

Prérequis : Node.js 20+, Python, FFmpeg avec `ffprobe`. Placez le dossier dans un emplacement permanent. `Lancer Viral Hit.bat` démarre Electron et son backend, installe les dépendances Python/Node si elles manquent et tente d'installer OpenCV (facultatif) pour la détection de visages. Pour créer un raccourci : clic droit sur le fichier `.bat` → Créer un raccourci.

Pour une copie Git, `Mettre a jour Viral Hit.bat` récupère les nouveautés sans télécharger tout le dépôt en archive. Le script nécessite une copie Git et une connexion internet.

## Fonctions actuelles

Import vidéo avec progression réelle côté interface (pourcentage, vitesse, estimation), téléchargements yt-dlp avec progression, suivi des étapes d'analyse et de montage, arrêt d'un transfert, arrêt/suppression d'une tâche, suppression d'une analyse terminée avec ses clips et sa source, réglages du nombre/durée/format, encodage NVENC avec repli x264, lecteur des clips, notes et planification locale. En format vertical, la webcam occupe le panneau supérieur (environ 38 %) et le gameplay le panneau inférieur (environ 62 %), centré et recadré sur les côtés; le zoom dynamique vise le gameplay pour ne pas agrandir/couper la caméra. Des pics de niveau audio pilotent les zooms et les impacts sonores générés localement. La webcam peut être détectée automatiquement; un choix de coin permet aussi de cadrer une webcam incrustée si OpenCV ne la trouve pas.

La détection du visage de webcam est best-effort et optionnelle (OpenCV Haar). Si OpenCV ne s'installe pas, ou si aucun visage persistant n'est détecté dans le même emplacement, Viral Hit utilise le recadrage vertical central. Aucun système ne peut garantir l'extraction correcte de webcam pour toute vidéo sans un détecteur spécialisé et/ou une validation utilisateur.

## Limites à connaître

Les moments sélectionnés restent des fenêtres heuristiques, pas des moments sémantiquement détectés par Whisper, YOLO ou CLIP. L’autocut s’appuie sur la détection des silences et des pics d'énergie audio FFmpeg, pas sur une transcription mot à mot ni sur un modèle de reconnaissance de parole : une musique ou un jeu bruyant peut masquer une pause de voix, et un pic sonore n'identifie pas à lui seul une action intéressante. Le score est un classement heuristique, pas une prédiction virale évaluée. Les accroches et le son généré sont des effets originaux génériques, pas une bibliothèque de mèmes/samples téléchargés ni une compréhension du contexte de chaque clip. Il n'y a pas encore de sous-titres transcrits, de détection HUD/jeu, de publication réseau, ni de calendrier interactif complet. La planification enregistre des créneaux, mais ne publie pas. L'API de publication n'est pas contournée.

## Backend séparé (diagnostic)

`python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765` démarre l'API manuellement si nécessaire. `GET http://127.0.0.1:8765/api/health` indique la disponibilité de FFmpeg, yt-dlp et OpenCV.
