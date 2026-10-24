"""Images Clonezilla : lecture du dossier, contrôle modèle/disque, routes de l'API.

Le dossier de test reprend le format réel d'un `ocs-sr savedisk` (octobre 2026), sans numéro de série.
Un dernier test relit les VRAIES images d'un établissement si les disques sont branchés (lecture seule, métadonnées).
"""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.clonezilla import compare_models, disk_problem, read_image_dir
from app.config import ConfigError, load_profiles, load_settings
from app.main import create_app
from app.security import make_hash
from conftest import WIM_BYTES, bearer

IMAGE = "W10-22H2-ROOMA"
BIG = bytes(range(256)) * 16  # 4096 octets reconnaissables (Range)
DMI = """\
BIOS Information
\tVendor: HP
\tVersion: P02 Ver. 02.50

System Information
\tManufacturer: HP
\tProduct Name: HP Exemple 600 G3 MT
\tVersion:
\tSerial Number: XXXXXXXXXX

Base Board Information
\tManufacturer: HP
\tProduct Name: 829D
"""
SGDISK = """\
Disk /dev/sdb: 468862128 sectors, 223.6 GiB
Model: PNY 240GB SATA S
Sector size (logical/physical): 512/512 bytes
"""
SOURCE_BYTES = 468862128 * 512  # 240,06 Go


@pytest.fixture
def cz_dir(data_dir):
    d = data_dir / "images" / IMAGE
    d.mkdir()
    for name, content in {"disk": "sdb\n", "parts": "sdb1 sdb2 sdb3 sdb4\n", "Info-dmi.txt": DMI,
                          "sdb-gpt.sgdisk": SGDISK, "clonezilla-img": "x"}.items():
        (d / name).write_text(content)
    (d / "sdb3.ntfs-ptcl-img.zst").write_bytes(BIG)
    (d / "sdb1.vfat-ptcl-img.zst").write_bytes(b"EFI" * 100)
    (d / ".cache").write_text("fichier caché")
    (data_dir / "images" / "secret.txt").write_text("hors du dossier d'image")
    (data_dir / "profiles.yaml").write_text("""\
images:
  win10-cz: {{label: "Windows 10 ROOMA", kind: clonezilla, dir: {d}, os_family: windows10}}
  win10-cz-forced: {{kind: clonezilla, dir: {d}, os_family: windows10, source_model: "HP Exemple 400 G7 SFF"}}
  cz-absent: {{kind: clonezilla, dir: introuvable, os_family: windows10}}
  win11-test: {{label: "Windows 11 wim", file: win11-test.wim, os_family: windows11}}
rooms:
  - {{id: CZ03, label: "Salle ROOMA", code_hash: "{a}", image: win10-cz, name_prefix: "ROOMA-"}}
  - {{id: FORCE, code_hash: "{b}", image: win10-cz-forced}}
  - {{id: ABS, code_hash: "{c}", image: cz-absent}}
  - {{id: WIM, code_hash: "{w}", image: win11-test}}
""".format(d=IMAGE, a=make_hash("c-cz", n=1024), b=make_hash("c-force", n=1024),
           c=make_hash("c-abs", n=1024), w=make_hash("c-wim", n=1024)))
    return d


@pytest.fixture
def cz(env, cz_dir):
    return TestClient(create_app(load_settings(env)))


def pc(**over):
    base = {"environment": "linux", "manufacturer": "HP", "model": "HP Exemple 600 G3 MT", "firmware": "uefi",
            "cpu_name": "Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz", "ram_mb": 8192, "tpm": "2.0",
            "bios_date": "2017-05-01", "disks": [{"name": "sda", "size_gb": 240, "kind": "ssd"}]}
    base.update(over)
    return base


def login(cz, code, hardware):
    return cz.post("/api/v1/session", json={"code": code, "hardware": hardware})


# --------------------------------------------------------------------------- lecture du dossier

def test_read_image_dir(cz_dir):
    info = read_image_dir(cz_dir)
    assert info["name"] == IMAGE and info["disk"] == "sdb"
    assert info["parts"] == ["sdb1", "sdb2", "sdb3", "sdb4"]
    assert info["source_model"] == "HP Exemple 600 G3 MT"  # « System Information », pas « Base Board » (829D)
    assert info["source_manufacturer"] == "HP"
    assert info["source_disk_bytes"] == SOURCE_BYTES
    names = [f["name"] for f in info["files"]]
    assert ".cache" not in names and "sdb3.ntfs-ptcl-img.zst" in names
    assert info["size_bytes"] == sum(f["size"] for f in info["files"]) > len(BIG)


def test_not_an_image(tmp_path):
    assert read_image_dir(tmp_path / "absent") is None
    (tmp_path / "vide").mkdir()
    assert read_image_dir(tmp_path / "vide") is None
    (tmp_path / "sans-parts").mkdir()
    (tmp_path / "sans-parts" / "disk").write_text("sda")
    (tmp_path / "sans-parts" / "sda1.ntfs-ptcl-img.gz").write_bytes(b"x")
    assert read_image_dir(tmp_path / "sans-parts") is None


def test_missing_metadata_does_not_crash(cz_dir):
    (cz_dir / "Info-dmi.txt").unlink()
    (cz_dir / "sdb-gpt.sgdisk").unlink()
    info = read_image_dir(cz_dir)
    assert info["source_model"] is None and info["source_disk_bytes"] is None


# --------------------------------------------------------------------------- comparaison des modèles

@pytest.mark.parametrize("src, src_maker, model, maker, expected", [
    ("HP Exemple 600 G3 MT", "HP", "HP Exemple 600 G3 MT", "HP", "same"),
    ("HP Exemple 400 G7 Small Form Factor PC", "HP", "HP Exemple 400 G7 SFF", "HP", "same"),
    ("HP Exemple 600 G3 MT", "Hewlett-Packard", "HP Exemple 600 G3 Microtower", "HP Inc.", "same"),
    ("HP Exemple 600 G3 MT", "HP", "HP Exemple 600 G3 SFF", "HP", "variant"),
    ("HP Exemple 600 G3 MT", "HP", "HP Exemple 400 G7 Small Form Factor PC", "HP", "different"),
    ("HP Exemple 600 G1 SFF", "HP", "HP Exemple 600 G2 SFF", "HP", "different"),
    ("HP Exemple 600 G3 MT", "HP", "ThinkCentre M72e", "LENOVO", "different"),
    ("HP Exemple 600 G3 MT", "HP", None, "HP", "unknown"),
    ("HP Exemple 600 G3 MT", "HP", "   ", "HP", "unknown"),
    ("OptiPlex 7090", "Dell Inc.", "OptiPlex 7090 Tower", "Dell Inc.", "unknown"),  # trop flou : on ne condamne pas
])
def test_compare_models(src, src_maker, model, maker, expected):
    assert compare_models(src, src_maker, model, maker) == expected


# --------------------------------------------------------------------------- taille du disque

IMG = {"source_disk_bytes": SOURCE_BYTES}


@pytest.mark.parametrize("disks, name, problem", [
    ([{"name": "sda", "size_gb": 240}], None, False),                       # même modèle de SSD : « 240 Go » arrondi vs 240,06 Go
    ([{"name": "sda", "size_gb": 239}], None, True),                        # 239 Go arrondis : même en arrondissant vers le haut, trop petit
    ([{"name": "sda", "size_gb": 256}], None, False),                       # plus grand : permis
    ([{"name": "sda", "size_gb": 128}], None, True),                        # trop petit
    ([{"name": "sda", "size_bytes": SOURCE_BYTES}], None, False),           # taille exacte identique : permis
    ([{"name": "sda", "size_bytes": SOURCE_BYTES - 10**6}], None, True),    # exact : 1 Mo de moins suffit à refuser
    ([{"name": "sda", "size_gb": 128}, {"name": "sdb", "size_gb": 500}], None, False),  # le plus grand disque suffit…
    ([{"name": "sda", "size_gb": 128}, {"name": "sdb", "size_gb": 500}], "sda", True),  # …sauf si on vise le petit
    ([], None, False),                                                      # inconnu : pas de blocage sur une incertitude
    ([{"name": "sda"}], None, False),
])
def test_disk_problem(disks, name, problem):
    assert bool(disk_problem({"disks": disks}, IMG, name)) is problem


# --------------------------------------------------------------------------- API

def test_matching_pc_is_ok(cz):
    r = login(cz, "c-cz", pc())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assessment"]["level"] == "ok" and body["assessment"]["blocked"] is False
    img = body["image"]
    assert img["kind"] == "clonezilla" and img["sha256"] is None and img["wim_index"] is None
    assert img["source_model"] == "HP Exemple 600 G3 MT" and img["source_disk_bytes"] == SOURCE_BYTES
    assert img["size_bytes"] > len(BIG)


def test_other_model_is_discouraged_but_can_be_confirmed(cz):
    r = login(cz, "c-cz", pc(model="HP Exemple 400 G7 Small Form Factor PC"))
    a = r.json()["assessment"]
    assert a["level"] == "discouraged" and a["blocked"] is False
    assert a["title"] == "Cette image n'a pas été préparée pour ce poste."
    assert any("600 G3 MT" in x and "400 G7" in x for x in a["reasons"])
    assert a["question"]
    token = r.json()["token"]
    refused = cz.post("/api/v1/deploy/start", headers=bearer(token), json={"hostname": "ROOMA-01", "disk": "sda"})
    assert refused.status_code == 409 and refused.json() == {"detail": "warning_not_confirmed"}
    ok = cz.post("/api/v1/deploy/start", headers=bearer(token),
                 json={"hostname": "ROOMA-01", "disk": "sda", "confirmed_warning": True})
    assert ok.status_code == 200


def test_other_format_is_a_warning(cz):
    a = login(cz, "c-cz", pc(model="HP Exemple 600 G3 SFF")).json()["assessment"]
    assert a["level"] == "warn" and "autre format" in " ".join(a["reasons"])
    assert a["title"] == "Cette image est prévue pour un format de poste un peu différent."


def test_unreadable_model_is_only_uncertain(cz):
    a = login(cz, "c-cz", pc(model=None)).json()["assessment"]
    assert a["level"] == "warn" and a["title"].startswith("Ce poste n'a pas pu être vérifié")


def test_disk_too_small_is_blocked(cz):
    r = login(cz, "c-cz", pc(disks=[{"name": "sda", "size_gb": 128, "kind": "ssd"}]))
    a = r.json()["assessment"]
    assert a["level"] == "discouraged" and a["blocked"] is True and "plus petit" in " ".join(a["reasons"])
    start = cz.post("/api/v1/deploy/start", headers=bearer(r.json()["token"]),
                    json={"hostname": "ROOMA-02", "disk": "sda", "confirmed_warning": True})
    assert start.status_code == 409 and start.json()["detail"] == "disk_too_small"


def test_source_model_in_profiles_wins_over_dmi(cz):
    a = login(cz, "c-force", pc()).json()["assessment"]  # le PC est un 600 G3 MT, le profil dit « 400 G7 SFF »
    assert a["level"] == "discouraged" and "400 G7 SFF" in " ".join(a["reasons"])


def test_missing_image_folder(cz):
    r = login(cz, "c-abs", pc())
    assert r.status_code == 503 and r.json() == {"detail": "image_missing"}


def test_deploy_start_describes_the_files(cz):
    token = login(cz, "c-cz", pc()).json()["token"]
    r = cz.post("/api/v1/deploy/start", headers=bearer(token), json={"hostname": "ROOMA-05", "disk": "sda"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["kind"] == "clonezilla" and b["image_name"] == IMAGE
    assert b["files_url"] == "http://srv.test:8080/api/v1/images/win10-cz/files"
    assert b["source_disk"] == "sdb" and b["parts"] == ["sdb1", "sdb2", "sdb3", "sdb4"]
    assert {"name": "sdb3.ntfs-ptcl-img.zst", "size": len(BIG)} in b["files"]
    assert "sha256" not in b and "image_url" not in b
    assert "ROOMA-05" in b["unattend_xml"] and b["setup_complete_cmd"].startswith("@echo off")


def test_image_files_with_range_and_guards(cz):
    token = login(cz, "c-cz", pc()).json()["token"]
    h = bearer(token)
    url = "/api/v1/images/win10-cz/files/"
    full = cz.get(url + "sdb3.ntfs-ptcl-img.zst", headers=h)
    assert full.status_code == 200 and full.content == BIG
    part = cz.get(url + "sdb3.ntfs-ptcl-img.zst", headers={**h, "Range": "bytes=100-199"})
    assert part.status_code == 206 and part.content == BIG[100:200]
    assert cz.get(url + "sdb3.ntfs-ptcl-img.zst").status_code == 401                       # sans jeton
    assert cz.get("/api/v1/images/autre/files/disk", headers=h).status_code == 403          # autre image
    assert cz.get(url + ".cache", headers=h).status_code == 404                              # fichier caché
    assert cz.get(url + "absent", headers=h).status_code == 404
    for sneaky in ("..%2Fsecret.txt", "%2e%2e%2fsecret.txt", "..%5Csecret.txt", "../secret.txt"):
        assert cz.get(url + sneaky, headers=h).status_code == 404, sneaky
    assert cz.get("/api/v1/images/win10-cz/download", headers=h).status_code == 404         # pas un .wim


def test_wim_sessions_cannot_use_the_files_route(cz):
    token = login(cz, "c-wim", pc(model="Autre PC")).json()["token"]
    assert cz.get("/api/v1/images/win11-test/files/disk", headers=bearer(token)).status_code == 404
    r = cz.post("/api/v1/deploy/start", headers=bearer(token), json={"hostname": "PC-01", "disk": "0", "confirmed_warning": True})
    assert r.json()["kind"] == "wim" and r.json()["image_url"].endswith("/win11-test/download")


# --------------------------------------------------------------------------- profiles.yaml

def _profile(tmp_path, image_line):
    p = tmp_path / "profiles.yaml"
    p.write_text("images:\n  x: %s\nrooms:\n  - {id: A, code_hash: \"%s\", image: x}\n" % (image_line, make_hash("c", n=1024)))
    return p


@pytest.mark.parametrize("line, fragment", [
    ("{kind: clonezilla, os_family: windows10}", "« dir »"),
    ("{kind: clonezilla, dir: a/b, os_family: windows10}", "simple nom de dossier"),
    ("{kind: clonezilla, dir: .., os_family: windows10}", "simple nom de dossier"),
    ("{kind: clonezilla, dir: .caché, os_family: windows10}", "simple nom de dossier"),
    ("{kind: iso, file: a.iso, os_family: windows10}", "kind doit valoir"),
    ("{file: a.wim, os_family: linux}", "os_family"),
])
def test_invalid_clonezilla_profiles(tmp_path, line, fragment):
    with pytest.raises(ConfigError, match=fragment):
        load_profiles(_profile(tmp_path, line))


def test_clonezilla_profile_loads(tmp_path):
    image = load_profiles(_profile(tmp_path, "{kind: clonezilla, dir: 2026-ROOMA, os_family: windows10}")).images["x"]
    assert (image.kind, image.dir, image.file) == ("clonezilla", "2026-ROOMA", None)


# --------------------------------------------------------------------------- de vraies images (facultatif)

REAL_DIR = os.environ.get("MDTPROX_REAL_IMAGES")  # dossier qui contient des dossiers d'images Clonezilla réelles


@pytest.mark.skipif(not REAL_DIR or not Path(REAL_DIR).is_dir(),
                    reason="définir MDTPROX_REAL_IMAGES=/chemin/des/images pour relire de vraies images (lecture seule)")
def test_real_images_are_readable():
    infos = [read_image_dir(d) for d in sorted(Path(REAL_DIR).iterdir()) if d.is_dir()]
    images = [i for i in infos if i]
    assert images, "aucune image Clonezilla reconnue dans " + REAL_DIR
    for i in images:
        assert i["source_model"] and i["source_disk_bytes"], i["name"]
        assert compare_models(i["source_model"], i["source_manufacturer"], i["source_model"], i["source_manufacturer"]) == "same"


# --------------------------------------------------------------------------- outil d'inventaire

def test_scan_images_tool(cz_dir, data_dir, capsys):
    import sys
    import yaml
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import scan_images
    (data_dir / "images" / "W7-ANCIEN-PC").mkdir()  # pas une image : ignoré sans bruit
    assert scan_images.main(["x", str(data_dir / "images")]) == 0
    out = capsys.readouterr().out
    assert "HP Exemple 600 G3 MT" in out and "1 images Clonezilla trouvées" in out
    snippet = yaml.safe_load(out.split("---\n", 1)[1])
    entry = next(iter(snippet["images"].values()))
    assert entry == {"label": IMAGE, "kind": "clonezilla", "dir": IMAGE, "os_family": "windows10"}


def test_scan_images_guesses_os_family():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import scan_images
    assert scan_images.os_family("2026-W11-ROOME") == "windows11"
    assert scan_images.os_family("2026-W10-22H2-ROOMA") == "windows10"
    assert scan_images.os_family("W7-ROOMD-PC-250GO") is None
    assert scan_images.image_id("2023-02-14-SFF-SVT&PC-FULL") == "2023-02-14-sff-svt-pc-full"
