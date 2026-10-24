import hashlib
import json
import os
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import security
from app.config import load_settings
from app.main import create_app
from conftest import ADMIN_PASSWORD, JOIN_PASSWORD, OU_ROOMA, WIM_BYTES, bearer

NS = "{urn:schemas-microsoft-com:unattend}"
DOWNLOAD = "/api/v1/images/win11-test/download"


def test_full_flow(client, data_dir, login, hw):
    r = login("code-rooma", hw("modern_i5_12400"))
    assert r.status_code == 200
    body = r.json()
    token = body["token"]
    assert body["expires_in"] == 3600
    assert body["room"] == {"id": "ROOMA", "label": "Salle ROOMA", "name_prefix": "ROOMA-", "name_example": "ROOMA-01"}
    assert body["image"]["id"] == "win11-test" and body["image"]["wim_index"] == 2
    assert body["image"]["size_bytes"] == len(WIM_BYTES)
    assert body["image"]["sha256"] == hashlib.sha256(WIM_BYTES).hexdigest()
    assert body["assessment"]["level"] == "ok" and body["assessment"]["question"] is None

    r = client.post("/api/v1/deploy/start", headers=bearer(token), json={"hostname": "ROOMA-01", "disk": "0"})
    assert r.status_code == 200
    start = r.json()
    assert start["image_url"] == "http://srv.test:8080" + DOWNLOAD  # le "/" final de PUBLIC_BASE_URL est retiré
    assert start["sha256"] == body["image"]["sha256"] and start["wim_index"] == 2
    root = ET.fromstring(start["unattend_xml"])
    assert root.find(".//%sComputerName" % NS).text == "ROOMA-01"
    assert root.find(".//%sMachineObjectOU" % NS).text == OU_ROOMA
    assert start["setup_complete_cmd"].startswith("@echo off\r\n")

    r = client.get(DOWNLOAD, headers=bearer(token))
    assert r.status_code == 200 and r.content == WIM_BYTES
    assert r.headers["content-length"] == str(len(WIM_BYTES))

    r = client.post("/api/v1/deploy/report", headers=bearer(token),
                    json={"status": "success", "step": "apply-image", "message": "ok " + ADMIN_PASSWORD + " " + token})
    assert r.status_code == 204

    raw = (data_dir / "audit.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    assert [e["event"] for e in events] == ["session_ok", "deploy_start", "deploy_report"]
    assert events[2]["status"] == "success" and events[2]["hostname"] == "ROOMA-01"
    assert events[0]["room"] == "ROOMA" and events[0]["level"] == "ok"
    assert events[0]["model"] == "OptiPlex 3000" and events[0]["serial"] == "DLL0000004"
    for secret in ("code-rooma", JOIN_PASSWORD, ADMIN_PASSWORD, token):  # rien de sensible dans le journal
        assert secret not in raw


def test_wrong_codes_lock_the_ip_then_unlock(client, login, hw, monkeypatch):
    results = [login("faux", hw("modern_i5_12400")) for _ in range(3)]
    assert [r.status_code for r in results] == [401, 401, 401]
    assert [r.json()["attempts_left"] for r in results] == [2, 1, 0]
    assert [r.json()["penalty"] for r in results] == [False, False, True]

    r = login("code-rooma", hw("modern_i5_12400"))  # même le bon code est refusé pendant le blocage
    assert r.status_code == 429
    assert r.json()["detail"] == "locked" and r.json()["penalty"] is True and 0 < r.json()["retry_after"] <= 300
    assert r.headers["retry-after"] == str(r.json()["retry_after"])

    monkeypatch.setattr(security, "now", lambda: security.time.monotonic() + 301)  # 5 minutes plus tard
    assert login("code-rooma", hw("modern_i5_12400")).status_code == 200


def test_success_resets_the_failure_counter(login, hw):
    assert login("faux", hw("lenovo_old")).json()["attempts_left"] == 2
    assert login("faux", hw("lenovo_old")).json()["attempts_left"] == 1
    assert login("code-rooma", hw("lenovo_old")).status_code == 200
    assert login("faux", hw("lenovo_old")).json()["attempts_left"] == 2


@pytest.mark.parametrize("code", ["", "   ", None])
def test_empty_code_is_a_penalty_and_counts_as_a_failure(client, hw, code):
    for _ in range(3):
        r = client.post("/api/v1/session", json={"code": code, "hardware": hw("lenovo_old")})
        assert r.status_code == 400 and r.json() == {"detail": "empty_code", "penalty": True}
    r = client.post("/api/v1/session", json={"code": "code-rooma", "hardware": hw("lenovo_old")})
    assert r.status_code == 429


def test_download_range_for_resuming(client, login, hw):
    token = login("code-rooma", hw("modern_i5_12400")).json()["token"]
    r = client.get(DOWNLOAD, headers={**bearer(token), "Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == WIM_BYTES[100:200]
    assert r.headers["content-range"] == "bytes 100-199/4096" and r.headers["content-length"] == "100"
    r = client.get(DOWNLOAD, headers={**bearer(token), "Range": "bytes=4000-"})
    assert r.status_code == 206 and r.content == WIM_BYTES[4000:]
    assert client.get(DOWNLOAD, headers=bearer(token)).headers["accept-ranges"] == "bytes"


def test_token_rules(client, login, hw, monkeypatch):
    token = login("code-rooma", hw("modern_i5_12400")).json()["token"]
    assert client.get(DOWNLOAD).status_code == 401
    assert client.get(DOWNLOAD, headers=bearer("pas-un-vrai-jeton")).status_code == 401
    # le jeton de ROOMA ne donne pas droit à l'image d'une autre salle
    assert client.get("/api/v1/images/win10-test/download", headers=bearer(token)).status_code == 403
    assert client.post("/api/v1/deploy/start", json={"hostname": "PC-01"}).status_code == 401
    monkeypatch.setattr(security, "now", lambda: security.time.monotonic() + 3601)  # jeton expiré
    assert client.get(DOWNLOAD, headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("name", ["", "-abc", "12345", "a" * 16, "pc 01", "Éric", "pc_01"])
def test_bad_hostname(client, login, hw, name):
    token = login("code-rooma", hw("modern_i5_12400")).json()["token"]
    r = client.post("/api/v1/deploy/start", headers=bearer(token), json={"hostname": name})
    assert r.status_code == 400 and r.json()["detail"] == "bad_hostname" and r.json()["message"]


@pytest.mark.parametrize("name", ["ROOMA-01", "A", "1a", "x" * 15])
def test_good_hostname(client, login, hw, name):
    token = login("code-rooma", hw("modern_i5_12400")).json()["token"]
    assert client.post("/api/v1/deploy/start", headers=bearer(token), json={"hostname": name}).status_code == 200


@pytest.mark.parametrize("fixture", ["hp_i3_8100", "lenovo_old"])
def test_warning_must_be_confirmed(client, login, hw, fixture):
    token = login("code-rooma", hw(fixture)).json()["token"]
    start = lambda **extra: client.post("/api/v1/deploy/start", headers=bearer(token), json={"hostname": "PC-01", **extra})
    r = start()
    assert r.status_code == 409 and r.json() == {"detail": "warning_not_confirmed"}
    assert start(confirmed_warning=False).status_code == 409
    assert start(confirmed_warning=True).status_code == 200


def test_room_without_ou_has_no_ou_line(client, login, hw):
    token = login("code-roomb", hw("modern_i5_12400")).json()["token"]  # ROOMB : Windows 10, pas d'OU
    xml = client.post("/api/v1/deploy/start", headers=bearer(token), json={"hostname": "ROOMB-01"}).json()["unattend_xml"]
    assert "MachineObjectOU" not in xml and ET.fromstring(xml) is not None


def test_image_missing_gives_503(login, hw):
    r = login("code-vide", hw("modern_i5_12400"))
    assert r.status_code == 503 and r.json() == {"detail": "image_missing"}


def test_server_starts_without_secrets_but_refuses_deployments(env, hw):
    for name in ("DOMAIN_JOIN_PASSWORD", "LOCAL_ADMIN_PASSWORD"):
        del env[name]
    client = TestClient(create_app(load_settings(env)))
    assert client.get("/healthz").json() == {"status": "ok"}
    for r in (client.post("/api/v1/session", json={"code": "code-rooma", "hardware": hw("lenovo_old")}),
              client.post("/api/v1/deploy/start", headers=bearer("x"), json={"hostname": "PC-01"})):
        body = r.json()
        assert r.status_code == 503 and body["detail"] == "server_not_configured"
        assert "DOMAIN_JOIN_PASSWORD" in body["message"] and "LOCAL_ADMIN_PASSWORD" in body["message"]
        assert "DOMAIN_JOIN_USER" not in body["message"]  # seules les variables manquantes sont citées


def test_broken_profiles_do_not_crash_the_server(env, data_dir, hw):
    (data_dir / "profiles.yaml").write_text("images: [oups")
    client = TestClient(create_app(load_settings(env)))
    assert client.get("/healthz").status_code == 200
    r = client.post("/api/v1/session", json={"code": "code-rooma", "hardware": hw("lenovo_old")})
    assert r.status_code == 503 and r.json()["detail"] == "server_not_configured"


def test_boot_ipxe_gets_the_base_url(client):
    r = client.get("/boot.ipxe")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.text == "#!ipxe\nchain http://srv.test:8080/winpe/wimboot\n"


@pytest.mark.parametrize("folder, name, content", [("winpe", "wimboot", b"WIMBOOT"), ("live", "vmlinuz", b"VMLINUZ")])
def test_boot_files_are_served_but_never_leave_their_folder(client, data_dir, folder, name, content):
    (data_dir / folder).mkdir(exist_ok=True)
    (data_dir / folder / name).write_bytes(content)
    assert client.get("/%s/%s" % (folder, name)).content == content
    os.symlink(data_dir / "profiles.yaml", data_dir / folder / "lien")
    assert (data_dir / folder / ".." / "profiles.yaml").exists()  # la cible existe : seul le refus nous protège
    for path in ("..%2fprofiles.yaml", "%2e%2e%2fprofiles.yaml", "/etc/passwd", "lien", "absent", ""):
        assert client.get("/%s/%s" % (folder, path)).status_code == 404, path


def test_live_files_support_range(client, data_dir):
    (data_dir / "live").mkdir()
    (data_dir / "live" / "filesystem.squashfs").write_bytes(bytes(range(256)) * 4)
    r = client.get("/live/filesystem.squashfs", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20))


def test_no_interactive_docs(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
