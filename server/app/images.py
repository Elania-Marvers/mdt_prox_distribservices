"""Taille et empreinte SHA-256 d'une image .wim (cache en mémoire + fichier .sha256 à côté)."""
import hashlib
import re
import stat
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

# Limite connue : cache sans limite (une entrée par image et par version de fichier : une poignée) ;
# un gros .wim est haché une seule fois, sous un verrou global, à la première demande.
# Pour que cette première demande ne soit pas lente : `sha256sum x.wim > x.wim.sha256` à l'avance.
_cache = {}  # type: Dict[Tuple[str, int, int], str]
_lock = threading.Lock()


def image_info(path: Path) -> Optional[dict]:
    """{"size_bytes", "sha256"} de l'image, ou None si le fichier n'existe pas."""
    try:
        st = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    digest = _cache.get(key)
    if digest is None:
        with _lock:
            digest = _cache.get(key) or _read_sidecar(path, st) or _compute(path)
            _cache[key] = digest
    return {"size_bytes": st.st_size, "sha256": digest}


def _sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def _read_sidecar(path: Path, st) -> Optional[str]:
    """Empreinte écrite dans <image>.sha256, si ce fichier n'est pas plus ancien que l'image."""
    try:
        side = _sidecar(path)
        if side.stat().st_mtime_ns < st.st_mtime_ns:
            return None  # l'image a changé depuis : on recalcule
        token = side.read_text().split()[0].lower()  # accepte aussi le format de `sha256sum`
    except (OSError, IndexError):
        return None
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else None


def _compute(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    digest = h.hexdigest()
    try:
        _sidecar(path).write_text("%s  %s\n" % (digest, path.name))  # même format que `sha256sum`
    except OSError:
        pass  # dossier en lecture seule : l'empreinte reste seulement en mémoire
    return digest
