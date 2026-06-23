"""
FastAPI wrapper autour de Lc0 exposant la policy (probabilités a priori)
du réseau de neurones pour chaque coup légal d'une position FEN.

Endpoints :
  GET  /health  -> { status, lc0_version }
  POST /policy  -> { fen, moves: [{ uci, san, p, n, q }, ...] } trié par p desc.

Lance un sous-processus Lc0 persistant en mode UCI avec VerboseMoveStats=true,
puis pour chaque requête envoie `position fen <FEN>` + `go nodes 1` et parse
les lignes `info string <move> ... (P: xx.xx%) ...`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

import chess
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("lc0-api")

LC0_PATH = os.environ.get("LC0_PATH", "./lc0")
WEIGHTS_PATH = os.environ.get("LC0_WEIGHTS", "./weights.pb.gz")
LC0_BACKEND = os.environ.get("LC0_BACKEND", "eigen")  # CPU par défaut
LC0_NODES = int(os.environ.get("LC0_NODES", "1"))
CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*").split(",")


class Lc0Engine:
    """Sous-processus Lc0 piloté en UCI, accès sérialisé par un asyncio.Lock."""

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.lock = asyncio.Lock()
        self.version = "unknown"

    async def start(self) -> None:
        log.info("Lancement de Lc0: %s (weights=%s, backend=%s)",
                 LC0_PATH, WEIGHTS_PATH, LC0_BACKEND)
        self.proc = await asyncio.create_subprocess_exec(
            LC0_PATH,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await self._send("uci")
        lines = await self._read_until("uciok")
        for ln in lines:
            if ln.startswith("id name"):
                self.version = ln[len("id name"):].strip()
        await self._send(f"setoption name WeightsFile value {WEIGHTS_PATH}")
        await self._send("setoption name VerboseMoveStats value true")
        await self._send("setoption name MultiPV value 256")
        await self._send(f"setoption name Backend value {LC0_BACKEND}")
        await self._send("isready")
        await self._read_until("readyok")
        log.info("Lc0 prêt: %s", self.version)

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                await self._send("quit")
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.proc.kill()

    async def _send(self, line: str) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((line + "\n").encode())
        await self.proc.stdin.drain()

    async def _read_until(self, marker: str, timeout: float = 30.0) -> list[str]:
        assert self.proc and self.proc.stdout
        out: list[str] = []
        async def reader() -> list[str]:
            while True:
                raw = await self.proc.stdout.readline()  # type: ignore[union-attr]
                if not raw:
                    raise RuntimeError("Lc0 stdout closed")
                line = raw.decode(errors="replace").rstrip("\n")
                out.append(line)
                if line == marker or line.startswith(marker + " "):
                    return out
        return await asyncio.wait_for(reader(), timeout=timeout)

    async def policy(self, fen: str, nodes: int = LC0_NODES) -> list[dict]:
        """Retourne la liste brute [{uci, p, n, q}] triée par p desc."""
        async with self.lock:
            await self._send("ucinewgame")
            await self._send(f"position fen {fen}")
            await self._send(f"go nodes {max(1, nodes)}")
            lines = await self._read_until("bestmove")
        return _parse_verbose(lines)


# Format typique d'une ligne VerboseMoveStats :
#   info string e2e4  (322 ) N:       0 (+ 0) (P:  9.86%) (WL: ...) (Q: ...) ...
# On extrait le coup en UCI, P (%), N et Q.
_MOVE_RE = re.compile(
    r"^info string\s+(?P<uci>[a-h][1-8][a-h][1-8][qrbn]?)\b.*?"
    r"N:\s*(?P<n>\d+).*?"
    r"\(P:\s*(?P<p>[\d.]+)%\).*?"
    r"\(Q:\s*(?P<q>-?[\d.]+)\)",
)


def _parse_verbose(lines: list[str]) -> list[dict]:
    rows: list[dict] = []
    for ln in lines:
        m = _MOVE_RE.search(ln)
        if not m:
            continue
        try:
            p = float(m.group("p")) / 100.0
            rows.append({
                "uci": m.group("uci"),
                "p": p,
                "n": int(m.group("n")),
                "q": float(m.group("q")),
            })
        except ValueError:
            continue
    # Normalisation au cas où la somme ne ferait pas 1
    total = sum(r["p"] for r in rows)
    if total > 0:
        for r in rows:
            r["p"] = r["p"] / total
    rows.sort(key=lambda r: r["p"], reverse=True)
    return rows


engine = Lc0Engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()
    try:
        yield
    finally:
        await engine.stop()


app = FastAPI(title="lc0-policy-api", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class PolicyRequest(BaseModel):
    fen: str = Field(..., description="Position au format FEN")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "lc0_version": engine.version}


@app.post("/policy")
async def policy(req: PolicyRequest) -> dict:
    # Validation FEN
    try:
        board = chess.Board(req.fen)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"FEN invalide: {e}") from e
    if board.is_game_over():
        raise HTTPException(status_code=409, detail="Position terminée")

    rows = await engine.policy(req.fen)
    # Filtre aux coups légaux (sécurité) + ajout du SAN
    legal_uci = {m.uci(): m for m in board.legal_moves}
    out: list[dict] = []
    for r in rows:
        mv = legal_uci.get(r["uci"])
        if mv is None:
            continue
        out.append({
            "uci": r["uci"],
            "san": board.san(mv),
            "p": r["p"],
            "n": r["n"],
            "q": r["q"],
        })
    if not out:
        raise HTTPException(status_code=502, detail="Lc0 n'a renvoyé aucun coup")
    return {"fen": req.fen, "moves": out}
