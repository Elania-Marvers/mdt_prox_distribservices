"""Pages d'administration : accès, codes, journal."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.security import make_hash
from conftest import WIM_BYTES

STRONG = "Un-mot-de-passe-solide-2026"


def basic(user="chef", password=STRONG):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def admin_env(env, data_dir):
    (data_dir / "profiles.yaml").write_text(f"""\
images:
  win10: {{label: "Windows 10 <b>test</b>", file: win10-test.wim, os_family: windows10}}
  absente: {{label: "Image absente", file: introuvable.wim, os_family: windows10}}
rooms:
  - {{id: ROOMA, label: "Salle <script>A</script>", image: win10, code: "code-affichable-A"}}
  - {{id: ROOMB, label: "Salle B", image: win10, code_hash: "{make_hash('code-secret-B', n=1024)}"}}
  - {{id: ROOMC, label: "Salle C", image: absente, code: "code-C-absent"}}
""", encoding="utf-8")
    return {**env, "ADMIN_USER": "chef", "ADMIN_PASSWORD": STRONG}


def app_for(env):
    return TestClient(create_app(load_settings(env)))


@pytest.fixture
def admin(admin_env):
    return app_for(admin_env)


def audit_lines(data_dir):
    f = data_dir / "audit.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def test_admin_is_disabled_without_an_account(env):
    r = app_for(env).get("/admin/images", headers=basic())
    assert r.status_code == 503 and "ADMIN_USER" in r.text


def test_the_browser_is_asked_for_credentials_and_nothing_leaks_before(admin):
    r = admin.get("/admin/images")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic realm=")
    for secret in ("code-affichable-A", "code-secret-B", "ROOMA", "Windows 10"):
        assert secret not in r.text


def test_right_credentials_show_the_codes_hidden_in_a_closed_fold(admin):
    r = admin.get("/admin/images", headers=basic())
    assert r.status_code == 200
    assert "<details><summary>Afficher le code</summary><code>code-affichable-A</code></details>" in r.text
    assert "code-secret-B" not in r.text and "seule l'empreinte est enregistrée" in r.text      # code_hash seul : non affichable
    assert "image absente du serveur" in r.text and "disponible" in r.text


def test_wrong_or_malformed_credentials_are_refused(admin):
    for headers in (basic("chef", "mauvais"), basic("autre", STRONG), {"Authorization": "Basic !!pas-du-base64"},
                    {"Authorization": "Bearer abc"}, {"Authorization": "Basic " + base64.b64encode(b"sans-deux-points").decode()}):
        assert admin.get("/admin/images", headers=headers).status_code == 401, headers


def test_responses_are_never_cached_and_are_not_indexable(admin):
    h = admin.get("/admin/images", headers=basic()).headers
    assert "no-store" in h["cache-control"] and "noindex" in h["x-robots-tag"] and h["x-frame-options"] == "DENY"
    assert "default-src 'none'" in h["content-security-policy"] and h["referrer-policy"] == "no-referrer"


def test_everything_shown_is_escaped(admin):
    text = admin.get("/admin/images", headers=basic()).text
    assert "<script>A</script>" not in text and "&lt;script&gt;A&lt;/script&gt;" in text
    assert "<b>test</b>" not in text and "&lt;b&gt;test&lt;/b&gt;" in text


def test_too_many_wrong_attempts_lock_the_ip(admin_env):
    app = app_for({**admin_env, "MAX_BAD_CODES": "2"})
    assert app.get("/admin/images", headers=basic(password="x")).status_code == 401
    assert app.get("/admin/images", headers=basic(password="y")).status_code == 401
    r = app.get("/admin/images", headers=basic())                       # même le BON mot de passe est refusé pendant le blocage
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_demo_password_is_refused_outside_lab_mode(admin_env):
    env = {**admin_env, "ADMIN_USER": "admin", "ADMIN_PASSWORD": "pikachu"}
    r = app_for(env).get("/admin/images", headers=basic("admin", "pikachu"))
    assert r.status_code == 503 and "démonstration" in r.text and "LAB_MODE" in r.text
    ok = app_for({**env, "LAB_MODE": "1"}).get("/admin/images", headers=basic("admin", "pikachu"))
    assert ok.status_code == 200 and "Mode banc d'essai" in ok.text


@pytest.mark.parametrize("password", ["court", "123456", "azerty", "Password"])
def test_weak_passwords_are_refused_outside_lab_mode(admin_env, password):
    r = app_for({**admin_env, "ADMIN_PASSWORD": password}).get("/admin/images", headers=basic(password=password))
    assert r.status_code == 503


def test_plain_http_is_flagged(admin):
    assert "Connexion non chiffrée" in admin.get("/admin/images", headers=basic()).text
    assert "Connexion non chiffrée" not in admin.get("/admin/images", headers={**basic(), "X-Forwarded-Proto": "https"}).text




def test_admin_root_redirects(admin):
    r = admin.get("/admin", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/admin/images"


def test_the_audit_log_names_who_looked_but_never_a_code(admin, data_dir):
    admin.get("/admin/images", headers=basic(password="mauvais"))
    admin.get("/admin/images", headers=basic())
    admin.get("/admin/images", headers=basic())
    log = (data_dir / "audit.jsonl").read_text()
    events = [e["event"] for e in audit_lines(data_dir)]
    assert events == ["admin_denied", "admin_view", "admin_view"]
    assert [e.get("page") for e in audit_lines(data_dir)] == ["images", "images", "images"]
    for secret in ("code-affichable-A", "code-secret-B", STRONG, "mauvais"):
        assert secret not in log


def test_a_room_declared_with_a_plain_code_still_works_for_the_deployment_flow(admin_env, data_dir):
    app = app_for(admin_env)
    ok = app.post("/api/v1/session", json={"code": "code-affichable-A", "hardware": {}})
    bad = app.post("/api/v1/session", json={"code": "code-secret-A", "hardware": {}})
    assert ok.status_code == 200 and ok.json()["room"]["id"] == "ROOMA" and bad.status_code == 401
    assert app.post("/api/v1/session", json={"code": "code-secret-B", "hardware": {}}).status_code == 200  # empreinte seule
    assert WIM_BYTES  # le faux .wim de la fixture existe bien
