"""Sécurité : empreintes de codes (scrypt), blocage anti-brute-force, sessions, journal d'audit.

Bibliothèque standard uniquement : ce module est aussi utilisé par tools/hash_code.py,
qui doit pouvoir tourner sans installer FastAPI.
"""
import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

SESSION_SECONDS = 3600  # durée de vie d'un jeton (1 h)


def now() -> float:
    """Horloge utilisée pour les blocages et les expirations (les tests la remplacent)."""
    return time.monotonic()


# --------------------------------------------------------------------------
# Empreintes de codes : "scrypt$N$r$p$sel_base64$empreinte_base64"
# --------------------------------------------------------------------------

def _scrypt(code: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(code.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=256 * n * r + 256 * r * p, dklen=length)


def make_hash(code: str, n: int = 16384, r: int = 8, p: int = 1) -> str:
    salt = os.urandom(16)
    digest = _scrypt(code, salt, n, r, p, 32)
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")
    return "$".join(["scrypt", str(n), str(r), str(p), b64(salt), b64(digest)])


def parse_hash(stored: str) -> Tuple[int, int, int, bytes, bytes]:
    """Découpe une empreinte. Lève ValueError si le format n'est pas le bon."""
    try:
        kind, n, r, p, salt, digest = str(stored).split("$")
        n, r, p = int(n), int(r), int(p)
        salt, digest = base64.b64decode(salt, validate=True), base64.b64decode(digest, validate=True)
    except ValueError:
        raise ValueError("format invalide") from None
    if kind != "scrypt" or n < 2 or n & (n - 1) or r < 1 or p < 1 or not salt or not digest:
        raise ValueError("format invalide")
    return n, r, p, salt, digest


def verify_code(code: str, stored: str) -> bool:
    n, r, p, salt, expected = parse_hash(stored)
    got = _scrypt(code, salt, n, r, p, len(expected))
    return hmac.compare_digest(got, expected)  # comparaison en temps constant


# --------------------------------------------------------------------------
# Anti-brute-force par adresse IP
# --------------------------------------------------------------------------

class Guard:
    """Compte les codes ratés par IP ; après `max_bad` échecs, l'IP est bloquée `lockout` secondes.

    Limite connue : tout est en mémoire, donc remis à zéro au redémarrage du serveur ;
    évolution : fichier ou SQLite si on veut que le blocage survive.
    """

    def __init__(self, max_bad: int, lockout: int):
        self.max_bad = max_bad
        self.lockout = lockout
        self.entries = {}  # type: Dict[str, dict]
        self.lock = threading.Lock()

    def retry_after(self, ip: str) -> int:
        """Secondes restantes de blocage (0 = pas bloqué)."""
        with self.lock:
            entry = self.entries.get(ip)
            left = entry["locked_until"] - now() if entry else 0
            return max(1, math.ceil(left)) if left > 0 else 0

    def fail(self, ip: str) -> int:
        """Enregistre un échec ; renvoie le nombre d'essais restants (0 = IP maintenant bloquée)."""
        with self.lock:
            t = now()
            # Les échecs de plus de `lockout` secondes sont oubliés (et les IP oubliées sont purgées).
            self.entries = {k: e for k, e in self.entries.items() if t - e["last"] <= self.lockout}
            entry = self.entries.setdefault(ip, {"count": 0, "last": t, "locked_until": 0.0})
            entry["count"] += 1
            entry["last"] = t
            left = max(0, self.max_bad - entry["count"])
            if left == 0:
                entry["locked_until"] = t + self.lockout
            return left

    def success(self, ip: str) -> None:
        with self.lock:
            self.entries.pop(ip, None)


# --------------------------------------------------------------------------
# Sessions (jeton -> infos), en mémoire
# --------------------------------------------------------------------------

class Sessions:
    """Limite connue : en mémoire, perdues au redémarrage (le client doit alors redemander un code)."""

    def __init__(self):
        self.items = {}  # type: Dict[str, dict]
        self.lock = threading.Lock()

    def create(self, **data) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            t = now()
            self.items = {k: s for k, s in self.items.items() if s["expires_at"] > t}
            self.items[token] = dict(data, expires_at=t + SESSION_SECONDS)
        return token

    def get(self, token: str) -> Optional[dict]:
        with self.lock:
            session = self.items.get(token)
            if session and session["expires_at"] <= now():
                del self.items[token]
                return None
            return session


# --------------------------------------------------------------------------
# Journal d'audit : une ligne JSON par événement
# --------------------------------------------------------------------------

class Audit:
    """Écrit DATA_DIR/audit.jsonl. Ne reçoit jamais le code saisi, un mot de passe ou un jeton.

    Limite connue : on rouvre le fichier à chaque ligne (quelques centaines d'événements par jour) ;
    si l'écriture échoue (disque plein, lecture seule) on écrit sur stderr plutôt que de planter.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def log(self, event: str, **fields) -> None:
        line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
        line.update({k: str(v)[:300] for k, v in fields.items() if v is not None})
        text = json.dumps(line, ensure_ascii=False)
        try:
            with self.lock, open(self.path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError as exc:
            print("audit: écriture impossible (%s) : %s" % (exc, text), file=sys.stderr)
