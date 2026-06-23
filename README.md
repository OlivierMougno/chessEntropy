# lc0-policy-api

Petit backend FastAPI qui expose la **policy** du réseau de neurones de
[Lc0](https://github.com/LeelaChessZero/lc0) pour une position FEN donnée :
pour chaque coup légal, la probabilité a priori `P` (et accessoirement
`N` visites MCTS et `Q` valeur).

## Endpoints

### `GET /health`
```json
{ "status": "ok", "lc0_version": "Lc0 v0.31.2" }
```

### `POST /policy`
Requête :
```json
{ "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1" }
```
Réponse (triée par `p` décroissant) :
```json
{
  "fen": "...",
  "moves": [
    { "uci": "e2e4", "san": "e4", "p": 0.187, "n": 0, "q": 0.02 },
    { "uci": "d2d4", "san": "d4", "p": 0.164, "n": 0, "q": 0.02 }
  ]
}
```

Erreurs : `400` FEN invalide, `409` partie terminée, `502` parsing échoué.

## Lancer en local (sans Docker)

Prérequis : un binaire `lc0` dans le dossier (ou pointé par `LC0_PATH`)
et un fichier de poids `weights.pb.gz` (ou pointé par `LC0_WEIGHTS`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

## Lancer avec Docker (recommandé)

```bash
docker build -t lc0-policy-api .
docker run --rm -p 8000:8000 lc0-policy-api
```

Le `Dockerfile` :
- compile Lc0 (`v0.31.2`) avec le backend CPU `eigen` (portable, pas de GPU)
- télécharge le réseau **Maia 1900** (~25 Mo, style humain niveau 1900 Elo)

Pour utiliser un autre réseau, change `WEIGHTS_URL` dans le `Dockerfile`
ou monte un fichier :
```bash
docker run -p 8000:8000 -v $PWD/mon-reseau.pb.gz:/app/weights.pb.gz lc0-policy-api
```

## Variables d'environnement

| Variable        | Défaut             | Description                              |
|-----------------|--------------------|------------------------------------------|
| `LC0_PATH`      | `./lc0`            | Chemin du binaire Lc0                    |
| `LC0_WEIGHTS`   | `./weights.pb.gz`  | Chemin du réseau                         |
| `LC0_BACKEND`   | `eigen`            | Backend Lc0 (`eigen`, `blas`, `cuda`…)   |
| `LC0_NODES`     | `1`                | Nombre de nodes MCTS par requête         |
| `CORS_ORIGINS`  | `*`                | Origines autorisées (CSV)                |

## Connexion depuis le frontend Lovable

Dans le projet Lovable, définir la variable `VITE_LC0_API_URL` pointant
vers cette API (ex. `http://localhost:8000` en local, ou l'URL publique
de ton déploiement).
