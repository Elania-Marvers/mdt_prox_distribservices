"""Pages d'administration (/admin/…) : état des images et codes de salle.

Accès : fenêtre d'identifiants du navigateur (HTTP Basic), vérifiée par le SERVEUR à chaque requête.
Pour l'instant les identifiants viennent du fichier d'environnement (ADMIN_USER / ADMIN_PASSWORD) ;
à terme ils seront vérifiés auprès de l'Active Directory (sans jamais en conserver les mots de passe).

Rien n'est affiché avant l'authentification, les réponses ne sont jamais mises en cache, et le journal
d'audit note QUI a ouvert QUELLE page, jamais un code ni un mot de passe.

Pourquoi « pikachu » ? J'avais une Nintendo DS avec Pikachu devant moi au moment de la rédaction.
Ces identifiants de démonstration ne fonctionnent qu'en mode banc d'essai (LAB_MODE=1) : en exploitation,
les pages refusent de s'ouvrir tant que le mot de passe est faible.
"""
import base64
import binascii
import hmac
from html import escape
from typing import Callable, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .config import Profiles, Settings
from .security import Audit, Guard

WEAK_PASSWORDS = {"pikachu", "admin", "password", "motdepasse", "changeme", "changez-moi", "123456", "azerty"}
MIN_LENGTH = 10  # hors banc d'essai

HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
}


CSS = """
body{font:16px/1.5 system-ui,sans-serif;margin:0;color:#1f2933;background:#f4f6f8}
header{background:#1f3a5f;color:#fff;padding:14px 24px}
header b{font-size:18px} nav{margin-top:6px} nav a{color:#cfe0f5;margin-right:18px;text-decoration:none}
nav a.on{color:#fff;border-bottom:2px solid #fff} main{max-width:1000px;margin:24px auto;padding:0 24px}
table{border-collapse:collapse;width:100%;background:#fff} th,td{border:1px solid #d5dbe1;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#e8edf2} code{background:#eef1f4;padding:2px 6px;border-radius:4px;font-size:15px}
summary{cursor:pointer;color:#1f3a5f} .ok{color:#1b7f3b} .ko{color:#b3261e} .note{background:#fff7e0;border:1px solid #e6c860;padding:10px 14px;margin:0 0 16px}
.alert{background:#fde8e6;border:1px solid #e3908a;padding:10px 14px;margin:0 0 16px} small{color:#5b6670}
"""


def _page(title: str, body: str, active: str = "", notes: str = "") -> str:
    links = [("images", "Images et codes")]
    nav = "".join('<a href="/admin/%s"%s>%s</a>' % (k, ' class="on"' if k == active else "", escape(name)) for k, name in links)
    return ("<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"robots\" content=\"noindex\"><title>%s</title><style>%s</style></head><body>"
            "<header><b>Administration du déploiement</b><nav>%s</nav></header><main>%s<h1>%s</h1>%s</main></body></html>"
            % (escape(title), CSS, nav, notes, escape(title), body))


def _reply(html: str, status: int = 200, extra: Optional[dict] = None) -> HTMLResponse:
    return HTMLResponse(html, status_code=status, headers={**HEADERS, **(extra or {})})


def weak_reason(password: str, lab_mode: bool) -> Optional[str]:
    """Pourquoi ce mot de passe est refusé hors banc d'essai (None s'il est accepté)."""
    if lab_mode:
        return None
    if password.lower() in WEAK_PASSWORDS:
        return "Le mot de passe est un mot de passe de démonstration."
    if len(password) < MIN_LENGTH:
        return "Le mot de passe fait moins de %d caractères." % MIN_LENGTH
    return None


def parse_basic(header: str) -> Optional[Tuple[str, str]]:
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic" or not value.strip():
        return None
    try:
        user, sep, password = base64.b64decode(value.strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return (user, password) if sep else None


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def add_admin_routes(app: FastAPI, *, settings: Settings, get_profiles: Callable[[], Profiles],
                     image_info: Callable[[object], Optional[dict]], audit: Audit, guard: Guard,
                     client_ip: Callable[[Request], str]) -> None:
    """Ajoute les routes /admin/… à l'application."""

    def refuse(status: int, title: str, message: str, extra: Optional[dict] = None) -> HTMLResponse:
        return _reply(_page(title, "<p>%s</p>" % escape(message)), status, extra)

    def authorize(request: Request, page: str) -> Optional[HTMLResponse]:
        """None si l'accès est autorisé, sinon la réponse à renvoyer."""
        if not settings.admin_user or not settings.admin_password:
            return refuse(503, "Administration désactivée",
                          "Aucun compte d'administration n'est configuré (ADMIN_USER et ADMIN_PASSWORD dans le fichier d'environnement).")
        reason = weak_reason(settings.admin_password, settings.lab_mode)
        if reason:
            return refuse(503, "Administration refusée",
                          reason + " Choisissez un mot de passe d'au moins %d caractères, ou activez LAB_MODE=1 "
                          "uniquement sur un banc d'essai isolé." % MIN_LENGTH)
        ip = client_ip(request)
        wait = guard.retry_after(ip)
        if wait:
            return refuse(429, "Trop d'essais", "Trop d'identifiants erronés. Réessayez dans %d secondes." % wait,
                          {"Retry-After": str(wait)})
        creds = parse_basic(request.headers.get("authorization", ""))
        good_user = creds is not None and _same(creds[0], settings.admin_user)
        good_pass = creds is not None and _same(creds[1], settings.admin_password)  # les deux sont toujours évalués
        if not (good_user and good_pass):
            if creds is not None:  # un simple premier affichage de la fenêtre n'est pas un échec
                guard.fail(ip)
                audit.log("admin_denied", ip=ip, page=page)
            return _reply(_page("Identification requise", "<p>Identifiez-vous pour accéder à l'administration.</p>"), 401,
                          {"WWW-Authenticate": 'Basic realm="Administration du deploiement", charset="UTF-8"'})
        guard.success(ip)
        audit.log("admin_view", ip=ip, user=settings.admin_user, page=page)
        return None

    def notes(request: Request) -> str:
        out = ""
        if settings.lab_mode:
            out += "<p class=\"note\"><b>Mode banc d'essai</b> (LAB_MODE) : identifiants de démonstration autorisés. Ne pas utiliser en exploitation.</p>"
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
        if not secure:
            out += ("<p class=\"alert\"><b>Connexion non chiffrée (HTTP).</b> Les identifiants et les codes circulent en clair sur le réseau : "
                    "à réserver à un banc d'essai isolé. En exploitation, placer le service derrière HTTPS.</p>")
        return out

    @app.get("/admin", include_in_schema=False)
    def admin_root():
        return RedirectResponse("/admin/images", status_code=307)

    @app.get("/admin/images", include_in_schema=False)
    def admin_images(request: Request):
        denied = authorize(request, "images")
        if denied:
            return denied
        profiles = get_profiles()
        rows = []
        for room in profiles.rooms:
            image = profiles.images[room.image]
            info = image_info(image)
            if info:
                size = "%.1f Go" % (info["size_bytes"] / 1e9)
                model = info.get("source_model")
                detail = "%s · %s" % (image.kind, size) + (" · prévue pour : %s" % model if model else "")
                state = '<span class="ok">disponible</span>'
            else:
                detail, state = image.kind, '<span class="ko">image absente du serveur</span>'
            if room.code:
                code = "<details><summary>Afficher le code</summary><code>%s</code></details>" % escape(room.code)
            else:
                code = "<small>non affichable : seule l'empreinte est enregistrée</small>"
            rows.append("<tr><td><b>%s</b><br><small>%s</small></td><td>%s<br><small>%s</small></td><td>%s</td><td>%s</td></tr>"
                        % (escape(room.id), escape(room.label), escape(image.label), escape(detail), state, code))
        body = ("<p>Chaque salle possède un code qui désigne une image. Le code n'est jamais affiché tant que vous n'êtes pas identifié.</p>"
                "<table><tr><th>Salle</th><th>Image</th><th>État</th><th>Code</th></tr>%s</table>"
                "<p><small>Fermez le navigateur après consultation. Chaque ouverture de cette page est enregistrée dans le journal (sans les codes).</small></p>"
                % "".join(rows))
        return _reply(_page("Images et codes", body, "images", notes(request)))

