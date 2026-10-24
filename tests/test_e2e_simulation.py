"""Test de bout en bout EN SIMULATION : vrai serveur (uvicorn, port local) + vrai client (--dry-run).

Ce que ça prouve : le client et l'API parlent bien le même langage (Annexe A du CDC).
Ce que ça ne prouve PAS : WinPE, diskpart, DISM, PXE (voir CDC.md, « Ce qui est testé »).

Lancer :  python3 -m pytest tests -q      (depuis la racine du dépôt)
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import uvicorn  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """Démarre l'API sur un port libre avec un DATA_DIR jetable ; prépare une copie du client à pénalité courte."""
    data = tmp_path_factory.mktemp("data")
    (data / "images").mkdir()
    (data / "winpe").mkdir()
    shutil.copy(ROOT / "server" / "profiles.example.yaml", data / "profiles.yaml")
    for name in ("win11-physique.wim", "win10-polyvalent.wim"):
        (data / "images" / name).write_bytes(os.urandom(200_000))
    (data / "boot.ipxe").write_text("#!ipxe\nchain @@BASE_URL@@/winpe/wimboot\n")
    # une image Clonezilla (format réel, sans numéro de série) pour la salle ROOMAC de profiles.example.yaml
    cz = data / "images" / "W10-22H2-ROOMA"
    cz.mkdir()
    (cz / "disk").write_text("sdb\n")
    (cz / "parts").write_text("sdb1 sdb2 sdb3 sdb4\n")
    (cz / "Info-dmi.txt").write_text("System Information\n\tManufacturer: HP\n\tProduct Name: HP Exemple 600 G3 MT\n")
    (cz / "sdb-gpt.sgdisk").write_text("Disk /dev/sdb: 468862128 sectors, 223.6 GiB\nSector size (logical/physical): 512/512 bytes\n")
    (cz / "sdb-gpt.gdisk").write_bytes(os.urandom(17920))
    for name in ("sdb1.vfat-ptcl-img.zst", "sdb2.dd-ptcl-img.zst", "sdb3.ntfs-ptcl-img.zst", "sdb4.ntfs-ptcl-img.zst"):
        (cz / name).write_bytes(os.urandom(5000))

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    os.environ.update(
        DATA_DIR=str(data), PUBLIC_BASE_URL=base, DOMAIN_FQDN="example.local",
        DOMAIN_JOIN_USER="svc-test", DOMAIN_JOIN_PASSWORD="join&<pw>", LOCAL_ADMIN_USER="admin-test",
        LOCAL_ADMIN_PASSWORD="adm'\"pw", MAX_BAD_CODES="3", LOCKOUT_SECONDS="300",
    )
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "le serveur n'a pas démarré"

    # copie du client avec une pénalité de 1 s au lieu de 90 s
    client_dir = tmp_path_factory.mktemp("client")
    for f in (ROOT / "client").glob("*.py"):
        shutil.copy(f, client_dir / f.name)
    (client_dir / "client.json").write_text(json.dumps({"server_url": base, "penalty_seconds": 1, "ascii_only": False}))

    yield dict(base=base, data=data, client=client_dir / "deploy_client.py")
    server.should_exit = True
    thread.join(timeout=5)


def run_client(stack, hardware, answers):
    hw_args = ["--hardware-json", hardware] if str(hardware).endswith(".json") else ["--simulate", hardware]
    r = subprocess.run(
        [sys.executable, "-I", str(stack["client"]), *hw_args, "--dry-run"],
        input="\n".join(answers) + "\n", capture_output=True, text=True, timeout=60,
    )
    if os.environ.get("E2E_SHOW"):  # E2E_SHOW=1 pytest -s : affiche ce que verrait la personne devant le PC
        print("\n" + r.stdout + r.stderr)
    return r.returncode, r.stdout + r.stderr


def audit(stack):
    f = stack["data"] / "audit.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def test_pc_recent_passe_sans_avertissement(stack):
    code, out = run_client(stack, "modern", ["exemple-roomb", "ROOMB-05", "OUI"])
    assert code == 0, out
    assert "exemple-roomb" not in out and "adm'" not in out and "join&" not in out  # aucun secret affiché
    assert any(e["event"] == "deploy_start" and e["hostname"] == "ROOMB-05" for e in audit(stack))


def test_vieux_pc_windows11_est_deconseille_mais_passe_si_on_confirme(stack):
    code, out = run_client(stack, "old-lenovo", ["exemple-rooma", "oui", "ROOMA-01", "OUI"])
    assert code == 0, out
    ok = [e for e in audit(stack) if e["event"] == "session_ok" and e.get("level") == "discouraged"]
    assert ok, "le serveur aurait dû juger ce PC « discouraged »"
    assert any(e["event"] == "deploy_start" and e["hostname"] == "ROOMA-01" for e in audit(stack))


def test_vieux_pc_refus_de_confirmer_ne_demarre_rien(stack):
    before = len([e for e in audit(stack) if e["event"] == "deploy_start"])
    # refus du verdict -> retour à la saisie du code -> code vide -> pénalité
    code, out = run_client(stack, "old-lenovo", ["exemple-rooma", "non", ""])
    assert code == 0, out
    assert len([e for e in audit(stack) if e["event"] == "deploy_start"]) == before


def test_code_vide_declenche_la_penalite_oeuf(stack):
    code, out = run_client(stack, "modern", [""])
    assert code == 0, out
    assert "cuire un œuf" in out


# --------------------------------------------------------------------------- images Clonezilla (salle ROOMAC)

def test_clonezilla_matching_pc_shows_the_restore_plan(stack):
    code, out = run_client(stack, "exemple-600-g3-linux", ["exemple-roomac", "ROOMA-07", "OUI"])
    assert code == 0, out
    assert "Windows 10 22H2" in out and "Cette image n'a pas été préparée" not in out        # même modèle : aucun avertissement
    for expected in ("sgdisk --load-backup=", "partclone.vfat -r -s - -o /dev/sda1", "partclone.ntfs -r -s - -o /dev/sda3",
                     "dd of=/dev/sda2".split()[0], "Unattend.xml", "SetupComplete.cmd"):
        assert expected in out, expected
    assert "MOT-DE-PASSE" not in out and "adm'" not in out and "join&" not in out
    assert any(e["event"] == "deploy_start" and e["hostname"] == "ROOMA-07" and e.get("image") == "win10-rooma-clonezilla"
               for e in audit(stack))


def test_clonezilla_other_model_is_discouraged_but_goes_on_if_confirmed(stack):
    code, out = run_client(stack, "hp-i3", ["exemple-roomac", "oui", "ROOMA-08", "OUI"])      # un 800 G4 SFF
    assert code == 0, out
    assert "Cette image n'a pas été préparée pour ce poste." in out
    assert "HP Exemple 600 G3 MT" in out and "Exemple 800 G4" in out
    assert "partclone.ntfs" in out


def test_clonezilla_bios_pc_is_refused_before_anything_is_touched(stack):
    code, out = run_client(stack, "old-lenovo", ["exemple-roomac", "oui", "ROOMA-09", "OUI"])
    assert code == 1, out
    assert "démarre en BIOS classique" in out and "sgdisk" not in out


def test_clonezilla_disk_too_small_is_blocked_by_the_server(stack, tmp_path):
    hw = {
        "environment": "linux", "manufacturer": "HP", "model": "HP Exemple 600 G3 MT", "firmware": "uefi",
        "cpu_name": "Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz", "ram_mb": 8192, "tpm": "2.0", "bios_date": "2017-05-01",
        "disks": [{"name": "sda", "size_gb": 120, "size_bytes": 120034123776, "kind": "ssd"}]}
    path = tmp_path / "petit-disque.json"
    path.write_text(json.dumps(hw))
    code, out = run_client(stack, str(path), ["exemple-roomac", ""])                           # puis code vide -> pénalité
    assert "plus petit que le disque d'origine" in out and "Cette installation est impossible" in out
    assert "partclone" not in out and "cuire un œuf" in out


# Toujours EN DERNIER : il bloque l'adresse 127.0.0.1 (5 minutes) pour tous les tests qui suivraient.
def test_trois_mauvais_codes_puis_blocage_serveur(stack):
    code, out = run_client(stack, "modern", ["faux1", "faux2", "faux3"])
    assert code == 0 and "cuire un œuf" in out, out
    # l'IP est maintenant bloquée côté serveur : même le BON code est refusé (429 -> pénalité)
    code, out = run_client(stack, "modern", ["exemple-roomb"])
    assert code == 0 and "cuire un œuf" in out, out
    assert any(e["event"] == "locked" for e in audit(stack))
