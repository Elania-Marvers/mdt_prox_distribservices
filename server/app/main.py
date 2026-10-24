"""API du serveur de déploiement (FastAPI). Routes et codes HTTP : voir l'annexe A.6 du CDC.

Conception et développement : Baptiste Renneson-Boutard (Elania-Marvers), concepteur principal.
Projet libre (licence MIT) : reprise et adaptation bienvenues, en conservant cette mention et la licence.

Démarrage en production, depuis le dossier server/ :
    uvicorn --factory app.main:create_app --host "$BIND_HOST" --port "$BIND_PORT"
"""
import logging
import re
from typing import Any, Dict, Literal, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import clonezilla, images
from .admin import add_admin_routes
from .config import ConfigError, Profiles, Settings, load_profiles, load_settings
from .hardware import assess
from .security import SESSION_SECONDS, Audit, Guard, Sessions, verify_code
from .unattend import build_setup_complete, build_unattend

log = logging.getLogger("mdtprox")


class ApiError(Exception):
    """Erreur renvoyée telle quelle au client : {"detail": ..., + champs en plus}."""

    def __init__(self, status: int, detail: str, headers: Optional[Dict[str, str]] = None, **extra: Any):
        self.status, self.detail, self.headers, self.extra = status, detail, headers, extra


class SessionRequest(BaseModel):
    code: Optional[str] = None
    hardware: Optional[Dict[str, Any]] = None


class StartRequest(BaseModel):
    hostname: str = ""
    disk: Any = None
    confirmed_warning: bool = False


class ReportRequest(BaseModel):
    status: Literal["success", "failed"]
    step: Optional[str] = None
    message: Optional[str] = None


def hostname_problem(name: str) -> Optional[str]:
    """None si le nom de PC est valable, sinon la raison (en français)."""
    if not 1 <= len(name) <= 15:
        return "Le nom du PC doit faire entre 1 et 15 caractères."
    if not re.fullmatch(r"[A-Za-z0-9-]+", name):
        return "Le nom du PC ne peut contenir que des lettres (sans accent), des chiffres et des tirets."
    if name.startswith("-") or name.endswith("-"):
        return "Le nom du PC ne peut pas commencer ni finir par un tiret."
    if name.isdigit():
        return "Le nom du PC ne peut pas être composé uniquement de chiffres."
    return None


def client_ip(request: Request) -> str:
    # On n'utilise PAS X-Forwarded-For : un PC pourrait l'inventer pour échapper au blocage.
    return request.client.host if request.client else "inconnue"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="mdtprox", docs_url=None, redoc_url=None, openapi_url=None)  # pas de schéma exposé
    guard = Guard(settings.max_bad_codes, settings.lockout_seconds)
    sessions = Sessions()
    audit = Audit(settings.data_dir / "audit.jsonl")
    app.state.settings, app.state.guard, app.state.sessions, app.state.audit = settings, guard, sessions, audit
    images_dir = settings.data_dir / "images"

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse({"detail": exc.detail, **exc.extra}, status_code=exc.status, headers=exc.headers)

    # --- profiles.yaml : relu quand le fichier change (pas besoin de redémarrer le serveur)
    cache = {"mtime": "jamais lu", "value": None}  # value : Profiles ou ConfigError

    def get_profiles() -> Profiles:
        path = settings.data_dir / "profiles.yaml"
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if cache["mtime"] != mtime:
            try:
                cache["value"] = load_profiles(path)
            except ConfigError as exc:
                cache["value"] = exc
                log.error("profiles.yaml inutilisable : %s", exc)  # le détail reste dans le journal du serveur
            cache["mtime"] = mtime
        if isinstance(cache["value"], ConfigError):
            raise ApiError(503, "server_not_configured",
                           message="Le fichier profiles.yaml est absent ou invalide (voir le journal du serveur).")
        return cache["value"]

    if settings.missing:
        log.warning("Variables d'environnement manquantes : %s", ", ".join(settings.missing))
    try:
        get_profiles()
    except ApiError:
        pass  # déjà journalisé : le serveur démarre quand même (healthz, boot.ipxe, winpe restent servis)

    # --- dépendances des routes
    def require_config() -> None:
        if settings.missing:
            raise ApiError(503, "server_not_configured",
                           message="Variables d'environnement manquantes : %s." % ", ".join(settings.missing))

    def bearer_token(request: Request) -> Optional[str]:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        return token.strip() if scheme.lower() == "bearer" and token.strip() else None

    def current_session(request: Request, _: None = Depends(require_config)) -> dict:
        token = bearer_token(request)
        session = sessions.get(token) if token else None
        if session is None:
            raise ApiError(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
        return session

    def image_info(image) -> Optional[dict]:
        """Description de l'image sur le disque (wim : taille + empreinte ; clonezilla : dossier lu), None si absente."""
        if image.kind == "clonezilla":
            info = clonezilla.read_image_dir(images_dir / image.dir)
            if info and image.source_model:  # le modèle écrit dans profiles.yaml l'emporte sur Info-dmi.txt
                info["source_model"] = image.source_model
            return info
        return images.image_info(images_dir / image.file)

    def image_block(image, info: dict) -> dict:
        block = {"id": image.id, "label": image.label, "os_family": image.os_family, "kind": image.kind,
                 "size_bytes": info["size_bytes"]}
        if image.kind == "clonezilla":
            block.update(sha256=None, wim_index=None, source_model=info["source_model"],
                         source_disk_bytes=info["source_disk_bytes"])
        else:
            block.update(sha256=info["sha256"], wim_index=image.wim_index)
        return block

    def who(session: dict) -> dict:
        """Les champs d'audit d'une session (jamais de jeton ni de mot de passe)."""
        return dict(ip=session["ip"], room=session["room"].id, hostname=session.get("hostname"),
                    level=session["assessment"]["level"], model=session["hardware"].get("model"),
                    serial=session["hardware"].get("serial"))

    # --- pages d'administration (/admin/…) : identifiants vérifiés par le serveur, voir admin.py
    add_admin_routes(app, settings=settings, get_profiles=get_profiles, image_info=image_info, audit=audit,
                     guard=Guard(settings.max_bad_codes, settings.lockout_seconds), client_ip=client_ip)

    # --- routes sans authentification
    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/boot.ipxe")
    def boot_ipxe():
        if not settings.public_base_url:
            raise ApiError(503, "server_not_configured", message="Variable d'environnement manquante : PUBLIC_BASE_URL.")
        try:
            text = (settings.data_dir / "boot.ipxe").read_text(encoding="utf-8")
        except OSError:
            raise ApiError(404, "boot_ipxe_missing")
        return PlainTextResponse(text.replace("@@BASE_URL@@", settings.public_base_url))

    def static_file(folder: str, nom: str) -> FileResponse:
        """Un fichier de DATA_DIR/<folder>/ ; `..`, chemins absolus et liens sortants donnent 404."""
        base = (settings.data_dir / folder).resolve()
        try:
            target = (base / nom).resolve()  # suit les liens : un lien vers l'extérieur sera refusé
        except (OSError, ValueError, RuntimeError):
            raise ApiError(404, "not_found")
        if not target.is_relative_to(base) or not target.is_file():
            raise ApiError(404, "not_found")
        return FileResponse(target, media_type="application/octet-stream")

    @app.get("/winpe/{nom:path}")
    def winpe(nom: str):  # mini-système WinPE (wimboot, boot.wim…)
        return static_file("winpe", nom)

    @app.get("/live/{nom:path}")
    def live(nom: str):  # mini-système Linux (vmlinuz, initrd.img, filesystem.squashfs)
        return static_file("live", nom)

    @app.post("/api/v1/session", dependencies=[Depends(require_config)])
    def open_session(body: SessionRequest, request: Request):
        ip = client_ip(request)
        hardware = body.hardware or {}
        known = dict(ip=ip, model=hardware.get("model"), serial=hardware.get("serial"))

        wait = guard.retry_after(ip)
        if wait:
            audit.log("locked", **known)
            raise ApiError(429, "locked", headers={"Retry-After": str(wait)}, retry_after=wait, penalty=True)

        code = (body.code or "").strip()
        if not code:
            guard.fail(ip)  # un code vide compte comme un échec
            audit.log("empty_code", **known)
            raise ApiError(400, "empty_code", penalty=True)

        profiles = get_profiles()
        # Limite connue : un scrypt (~50 ms) par salle et par essai, sans s'arrêter à la première trouvée
        # (pour ne pas révéler par le temps de réponse quelle salle a répondu) ; OK jusqu'à ~50 salles.
        room = None
        for candidate in profiles.rooms:
            if verify_code(code, candidate.code_hash) and room is None:
                room = candidate
        if room is None:
            left = guard.fail(ip)
            audit.log("bad_code", **known)
            raise ApiError(401, "bad_code", attempts_left=left, penalty=left == 0)

        guard.success(ip)
        image = profiles.images[room.image]
        info = image_info(image)
        if info is None:
            audit.log("image_missing", room=room.id, **known)
            raise ApiError(503, "image_missing")
        assessment = assess(hardware, image.os_family, info if image.kind == "clonezilla" else None)
        token = sessions.create(room=room, image=image, hardware=hardware, ip=ip, assessment=assessment)
        audit.log("session_ok", room=room.id, level=assessment["level"], **known)
        example = (room.name_prefix + "01")[:15] if room.name_prefix else "PC-01"
        return {
            "token": token,
            "expires_in": SESSION_SECONDS,
            "room": {"id": room.id, "label": room.label, "name_prefix": room.name_prefix, "name_example": example},
            "image": image_block(image, info),
            "assessment": assessment,
        }

    # --- routes avec jeton (Authorization: Bearer ...)
    @app.post("/api/v1/deploy/start")
    def deploy_start(body: StartRequest, session: dict = Depends(current_session)):
        hostname = body.hostname.strip()
        problem = hostname_problem(hostname)
        if problem:
            raise ApiError(400, "bad_hostname", message=problem)
        if session["assessment"]["level"] != "ok" and not body.confirmed_warning:
            raise ApiError(409, "warning_not_confirmed")
        image, room = session["image"], session["room"]
        info = image_info(image)
        if info is None:
            raise ApiError(503, "image_missing")
        if image.kind == "clonezilla":  # le disque choisi (et pas seulement le plus grand) doit pouvoir recevoir l'image
            problem = clonezilla.disk_problem(session["hardware"], info, body.disk)
            if problem:
                raise ApiError(409, "disk_too_small", message=problem)

        session["hostname"] = hostname
        audit.log("deploy_start", image=image.id, **who(session))
        if image.kind == "clonezilla":
            where = {"kind": "clonezilla", "image_name": info["name"],
                     "files_url": "%s/api/v1/images/%s/files" % (settings.public_base_url, image.id),
                     "files": info["files"], "source_disk": info["disk"], "parts": info["parts"],
                     "source_disk_bytes": info["source_disk_bytes"], "size_bytes": info["size_bytes"]}
        else:
            where = {"kind": "wim", "image_url": "%s/api/v1/images/%s/download" % (settings.public_base_url, image.id),
                     "sha256": info["sha256"], "size_bytes": info["size_bytes"], "wim_index": image.wim_index}
        return {
            **where,
            "unattend_xml": build_unattend(
                hostname=hostname, domain=settings.domain_fqdn,
                join_user=settings.domain_join_user, join_password=settings.domain_join_password,
                admin_user=settings.local_admin_user, admin_password=settings.local_admin_password,
                timezone=settings.win_timezone, locale=settings.win_locale, ou=room.ou),
            "setup_complete_cmd": build_setup_complete(),
        }

    @app.get("/api/v1/images/{image_id}/download")
    def download(image_id: str, session: dict = Depends(current_session)):
        image = session["image"]
        if image_id != image.id:
            raise ApiError(403, "forbidden")
        if image.kind != "wim":
            raise ApiError(404, "not_found")
        path = images_dir / image.file
        if not path.is_file():
            raise ApiError(503, "image_missing")
        # FileResponse gère l'en-tête Range (reprise après coupure) et Content-Length.
        return FileResponse(path, media_type="application/octet-stream", filename=image.file)

    @app.get("/api/v1/images/{image_id}/files/{name}")
    def image_file(image_id: str, name: str, session: dict = Depends(current_session)):
        """Un fichier d'une image Clonezilla (Range géré : le client peut reprendre après une coupure)."""
        image = session["image"]
        if image_id != image.id:
            raise ApiError(403, "forbidden")
        if image.kind != "clonezilla":
            raise ApiError(404, "not_found")
        base = (images_dir / image.dir).resolve()
        try:
            target = (base / name).resolve()  # suit les liens : un lien vers l'extérieur sera refusé
        except (OSError, ValueError, RuntimeError):
            raise ApiError(404, "not_found")
        if name.startswith(".") or target.parent != base or not target.is_file():
            raise ApiError(404, "not_found")
        return FileResponse(target, media_type="application/octet-stream", filename=name)

    @app.post("/api/v1/deploy/report", status_code=204)
    def deploy_report(body: ReportRequest, request: Request, session: dict = Depends(current_session)):
        # Le message vient du client : on en retire les secrets au cas où il en contiendrait.
        message = body.message
        for secret in (bearer_token(request), settings.domain_join_password, settings.local_admin_password):
            if message and secret:
                message = message.replace(secret, "***")
        audit.log("deploy_report", status=body.status, step=body.step, message=message, **who(session))
        return Response(status_code=204)

    return app
