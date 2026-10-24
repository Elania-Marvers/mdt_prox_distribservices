"""Dialogue avec l'API du serveur (annexe A.6 du CDC). urllib uniquement.

Toutes les erreurs du serveur arrivent sous la forme d'une ApiError (code HTTP + corps JSON),
avec un message en français prêt à afficher : user_message().
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request

CHUNK = 1024 * 1024  # on lit le .wim par blocs de 1 Mo

# Pas de proxy : le client tourne sur un réseau local isolé (et WinPE n'en a pas).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ApiError(Exception):
    """Réponse d'erreur du serveur (status 0 = serveur injoignable)."""

    def __init__(self, status: int, body=None):
        self.status = status
        self.body = body if isinstance(body, dict) else {}
        self.detail = str(self.body.get("detail", ""))
        self.message = self.body.get("message")  # texte français du serveur (ex. bad_hostname)
        self.attempts_left = self.body.get("attempts_left")
        self.retry_after = self.body.get("retry_after")
        # 429 = IP bloquée : pénalité même si le serveur a oublié le drapeau
        self.penalty = bool(self.body.get("penalty")) or status == 429
        super().__init__(self.user_message())

    def user_message(self) -> str:
        if self.status == 0:
            return "Impossible de joindre le serveur. Vérifiez le câble réseau, puis réessayez."
        if self.status == 400 and self.message:
            return str(self.message)
        if self.status == 401:
            return "Code incorrect." if self.detail == "bad_code" else "Session expirée ou refusée : recommencez depuis le début."
        if self.status == 409:
            return "Le serveur demande de confirmer l'avertissement sur le matériel."
        if self.status == 429:
            return f"Trop d'essais : ce poste est bloqué pendant {self.retry_after or 'quelques'} secondes."
        if self.status == 503:
            return "Le serveur est momentanément indisponible. Réessayez dans un instant."
        return f"Réponse inattendue du serveur (HTTP {self.status} {self.detail})."


class DownloadError(Exception):
    """Téléchargement impossible ou fichier corrompu (message en français)."""


def _request(method: str, url: str, body=None, token: str | None = None,
             headers: dict | None = None, timeout: float = 30):
    """Envoie une requête ; renvoie la réponse ouverte, ou lève ApiError."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        return _OPENER.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode("utf-8"))
        except (ValueError, OSError):
            parsed = None
        raise ApiError(e.code, parsed) from None
    except (OSError, http.client.HTTPException):  # URLError, délai dépassé, connexion coupée…
        raise ApiError(0) from None


def _json(resp) -> dict:
    with resp:
        try:
            return json.loads(resp.read().decode("utf-8"))
        except (ValueError, OSError, http.client.HTTPException):
            raise ApiError(0, {"detail": "bad_response"}) from None


def wait_ready(base: str, attempts: int = 30, delay: float = 2, on_wait=None) -> bool:
    """Attend que le réseau (DHCP) et le serveur répondent : GET /healthz."""
    for i in range(attempts):
        try:
            _request("GET", base + "/healthz", timeout=5).close()
            return True
        except ApiError:
            if i < attempts - 1:
                if on_wait:
                    on_wait(i)
                time.sleep(delay)
    return False


def session(base: str, code: str, hardware: dict) -> dict:
    """POST /api/v1/session -> {token, room, image, assessment}."""
    data = _json(_request("POST", base + "/api/v1/session", {"code": code, "hardware": hardware}))
    if not all(k in data for k in ("token", "room", "image", "assessment")):
        raise ApiError(0, {"detail": "bad_response"})
    return data


def deploy_start(base: str, token: str, hostname: str, disk: str, confirmed_warning: bool) -> dict:
    """POST /api/v1/deploy/start -> image_url, sha256, size_bytes, wim_index, unattend_xml, setup_complete_cmd."""
    body = {"hostname": hostname, "disk": disk, "confirmed_warning": confirmed_warning}
    return _json(_request("POST", base + "/api/v1/deploy/start", body, token=token))


def report(base: str, token: str, status: str, step: str | None = None, message: str | None = None) -> bool:
    """POST /api/v1/deploy/report. Au mieux : ne lève JAMAIS (ne doit pas masquer l'erreur d'origine)."""
    try:
        body = {"status": status, "step": step, "message": (message or "")[:500]}
        _request("POST", base + "/api/v1/deploy/report", body, token=token, timeout=10).close()
        return True
    except Exception:
        return False


class SinkClosed(Exception):
    """Le programme qui reçoit le flux (décompresseur, partclone) s'est arrêté : inutile de réessayer."""


def stream_to(url: str, token: str, write, size: int | None = None, progress=None, attempts: int = 3) -> int:
    """Télécharge `url` et passe chaque bloc à `write(octets)` (sans rien stocker), avec reprise (Range).

    Le flux reste CONTINU : après une coupure, `write` reçoit exactement la suite des octets déjà envoyés.
    Limite connue : 3 essais fixes, une seule connexion ; le serveur doit savoir reprendre (Range -> 206),
    sinon on échoue plutôt que de renvoyer deux fois le début à un programme qui ne peut pas le rejouer.
    Renvoie le nombre d'octets reçus. progress(octets_reçus, octets_total) est appelée à chaque bloc.
    """
    done, last_error = 0, None
    for attempt in range(1, attempts + 1):
        try:
            resp = _request("GET", url, token=token, timeout=60,
                            headers={"Range": f"bytes={done}-"} if done else None)
            with resp:
                if done and resp.status != 206:
                    raise DownloadError("Le serveur ne sait pas reprendre un téléchargement interrompu.")
                if size is None:
                    size = done + int(resp.headers.get("Content-Length") or 0) or None
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    try:
                        write(chunk)
                    except (BrokenPipeError, ValueError) as e:  # ValueError : écriture sur un tube déjà fermé
                        raise SinkClosed(str(e)) from None
                    done += len(chunk)
                    if progress:
                        progress(done, size or 0)
            if size and done < size:
                raise OSError("téléchargement interrompu")
            return done
        except ApiError as e:
            if e.status not in (0, 503):  # 401, 404… : réessayer ne servirait à rien
                raise
            last_error = e
        except (OSError, http.client.HTTPException) as e:  # connexion coupée en cours de route
            last_error = e
        if attempt < attempts:
            time.sleep(2 * attempt)
    raise DownloadError(f"Le téléchargement a échoué après {attempts} essais ({last_error}).")


def download(url: str, token: str, dest: str, expected_sha256: str, size_bytes: int | None = None,
             progress=None, attempts: int = 3) -> str:
    """Télécharge `url` vers `dest`, avec reprise (en-tête Range) et SHA-256 calculé au fil de l'eau.

    Limite connue : 3 essais fixes, une seule connexion, pas de débit limité ; un .wim de plusieurs Go sur
    réseau local passe, à revoir (reprises plus nombreuses) si le réseau est instable.
    3 essais en tout ; chaque reprise repart à l'octet où l'on s'est arrêté.
    Empreinte différente : le fichier est supprimé et DownloadError est levée.
    progress(octets_reçus, octets_total) est appelée à chaque bloc.
    """
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    sha, done, total, last_error = hashlib.sha256(), 0, size_bytes or None, None
    with open(dest, "wb") as f:
        for attempt in range(1, attempts + 1):
            try:
                resp = _request("GET", url, token=token, timeout=60,
                                headers={"Range": f"bytes={done}-"} if done else None)
                with resp:
                    if done and resp.status != 206:  # le serveur ignore Range : on repart de zéro
                        f.seek(0)
                        f.truncate()
                        sha, done = hashlib.sha256(), 0
                    if total is None:
                        total = done + int(resp.headers.get("Content-Length") or 0) or None
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        sha.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total or 0)
                if total and done < total:
                    raise OSError("téléchargement interrompu")
                last_error = None
                break
            except ApiError as e:
                if e.status not in (0, 503):  # 401, 404… : réessayer ne servirait à rien
                    raise
                last_error = e
            except (OSError, http.client.HTTPException) as e:  # connexion coupée en cours de route
                last_error = e
            if attempt < attempts:
                time.sleep(2 * attempt)
    if last_error is not None:
        raise DownloadError(f"Le téléchargement de l'image a échoué après {attempts} essais ({last_error}).")
    if sha.hexdigest() != expected_sha256.lower():
        os.remove(dest)
        raise DownloadError("L'image téléchargée est corrompue (empreinte SHA-256 différente de celle du "
                            "serveur). Le fichier a été supprimé.")
    return sha.hexdigest()
