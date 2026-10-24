"""Outils communs aux tests : un DATA_DIR temporaire avec un faux .wim de 4 Ko et un profiles.yaml de test."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from app.security import make_hash

FIXTURES = Path(__file__).parent / "fixtures"
WIM_BYTES = bytes(range(256)) * 16  # 4096 octets reconnaissables (pour tester les Range)
# Mots de passe pleins de caractères qui cassent le XML s'ils ne sont pas échappés.
JOIN_PASSWORD = "J0in&<pass>'\"é"
ADMIN_PASSWORD = "Adm&n<1>'\"x"
OU_ROOMA = "OU=ROOMA,OU=Ordinateurs,DC=example,DC=local"


def _hash(code):
    return make_hash(code, n=1024)  # paramètres légers : les tests restent rapides


PROFILES = """\
images:
  win11-test: {{label: "Windows 11 de test", file: win11-test.wim, wim_index: 2, os_family: windows11}}
  win10-test: {{label: "Windows 10 de test", file: win10-test.wim, wim_index: 1, os_family: windows10}}
  win11-absent: {{label: "Image absente", file: absent.wim, os_family: windows11}}
rooms:
  - {{id: ROOMA, label: "Salle ROOMA", code_hash: "{rooma}", image: win11-test, name_prefix: "ROOMA-", ou: "{ou}"}}
  - {{id: ROOMB, label: "Salle ROOMB", code_hash: "{roomb}", image: win10-test, name_prefix: "ROOMB-"}}
  - {{id: VIDE, label: "Salle sans image", code_hash: "{vide}", image: win11-absent}}
"""


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "win11-test.wim").write_bytes(WIM_BYTES)
    (tmp_path / "images" / "win10-test.wim").write_bytes(WIM_BYTES[::-1])
    (tmp_path / "winpe").mkdir()
    (tmp_path / "winpe" / "wimboot").write_bytes(b"WIMBOOT")
    (tmp_path / "boot.ipxe").write_text("#!ipxe\nchain @@BASE_URL@@/winpe/wimboot\n")
    (tmp_path / "profiles.yaml").write_text(PROFILES.format(
        rooma=_hash("code-rooma"), roomb=_hash("code-roomb"), vide=_hash("code-vide"), ou=OU_ROOMA), encoding="utf-8")
    return tmp_path


@pytest.fixture
def env(data_dir):
    """Variables d'environnement complètes (un dict : on peut en retirer pour tester les manques)."""
    return {
        "PUBLIC_BASE_URL": "http://srv.test:8080/",
        "DATA_DIR": str(data_dir),
        "DOMAIN_FQDN": "example.local",
        "DOMAIN_JOIN_USER": "svc-joindomaine",
        "DOMAIN_JOIN_PASSWORD": JOIN_PASSWORD,
        "LOCAL_ADMIN_USER": "admin-local",
        "LOCAL_ADMIN_PASSWORD": ADMIN_PASSWORD,
    }


@pytest.fixture
def client(env):
    return TestClient(create_app(load_settings(env)))


@pytest.fixture
def hw():
    """hw("modern_i5_12400") -> le matériel du fichier fixtures/hw_modern_i5_12400.json."""
    return lambda name: json.loads((FIXTURES / ("hw_%s.json" % name)).read_text())


@pytest.fixture
def login(client):
    """login(code, matériel) -> réponse de POST /api/v1/session."""
    return lambda code, hardware: client.post("/api/v1/session", json={"code": code, "hardware": hardware})


def bearer(token):
    return {"Authorization": "Bearer " + token}
