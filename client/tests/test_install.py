import pytest

import api
import windows_install as wi
from ui import UI

START = {"image_url": "http://srv/api/v1/images/win11/download", "sha256": "ab" * 32, "size_bytes": 10_000_000_000,
         "wim_index": 2, "unattend_xml": '<?xml version="1.0"?><pw>MOT-DE-PASSE-SECRET</pw>',
         "setup_complete_cmd": "@echo off\r\ndel x\r\n"}
DISK = {"name": "0", "size_gb": 256, "kind": "ssd"}


def test_diskpart_uefi_gpt():
    lines = wi.diskpart_script("1", "uefi").splitlines()
    assert lines == ["select disk 1", "clean", "convert gpt", "create partition efi size=260",
                     'format quick fs=fat32 label="Systeme"', "assign letter=S", "create partition msr size=16",
                     "create partition primary", 'format quick fs=ntfs label="Windows"', "assign letter=W", "exit"]


def test_diskpart_bios_mbr():
    lines = wi.diskpart_script("0", "bios").splitlines()
    assert lines[:4] == ["select disk 0", "clean", "convert mbr", "create partition primary size=500"]
    assert "active" in lines and "assign letter=S" in lines and "assign letter=W" in lines
    assert not any("efi" in l or "gpt" in l or "msr" in l for l in lines)


def test_diskpart_refuse_numero_douteux_et_firmware_inconnu():
    with pytest.raises(ValueError):
        wi.diskpart_script("0\nclean", "uefi")
    with pytest.raises(wi.InstallError, match="UEFI ou en BIOS"):
        wi.diskpart_script("0", "unknown")


def test_commandes_dism_et_bcdboot():
    assert wi.dism_command(2) == ["dism", "/Apply-Image", "/ImageFile:W:\\_deploy\\install.wim", "/Index:2",
                                  "/ApplyDir:W:\\", "/Verify"]
    assert wi.bcdboot_command("uefi") == ["bcdboot", "W:\\Windows", "/s", "S:", "/f", "UEFI"]
    assert wi.bcdboot_command("bios")[-1] == "BIOS"


def test_espace_disque():
    wi.check_space(25e9, 10_000_000_000)
    with pytest.raises(wi.InstallError, match="insuffisant") as e:
        wi.check_space(24e9, 10_000_000_000)
    assert e.value.step == "space"


def test_depot_des_fichiers_encodage(tmp_path):
    wi.write_target_files("\ufeff<?xml?><a>é</a>", "@echo off\nREM œ\n", root=str(tmp_path))
    xml, cmd = wi.target_paths(str(tmp_path))
    raw_xml, raw_cmd = open(xml, "rb").read(), open(cmd, "rb").read()
    assert raw_xml == "<?xml?><a>é</a>".encode("utf-8") and not raw_xml.startswith(b"\xef\xbb\xbf")
    assert raw_cmd == b"@echo off\r\nREM \x9c\r\n"   # cp1252, fins de ligne Windows


def test_dry_run_rien_execute_et_aucun_mot_de_passe_affiche(monkeypatch, capsys):
    def boom(*a, **k):
        raise AssertionError("aucune commande ne doit être lancée en dry-run")
    monkeypatch.setattr(wi.subprocess, "run", boom)
    monkeypatch.setattr(wi.probe, "run_diskpart", boom)
    monkeypatch.setattr(api, "download", boom)
    wi.install(UI(dry_run=True), START, DISK, "uefi", "TOKEN", dry_run=True)
    out = capsys.readouterr().out
    assert "MOT-DE-PASSE-SECRET" not in out and "TOKEN" not in out
    for expected in ("convert gpt", "dism /Apply-Image", "bcdboot W:\\Windows /s S: /f UEFI", "Unattend.xml"):
        assert expected in out


def test_dry_run_disque_trop_petit_refuse_avant_d_effacer(capsys):
    with pytest.raises(wi.InstallError, match="insuffisant"):
        wi.install(UI(dry_run=True), START, {"name": "0", "size_gb": 20, "kind": "hdd"}, "bios", "T", dry_run=True)


def test_firmware_inconnu_refuse_sans_rien_toucher(capsys):
    with pytest.raises(wi.InstallError):
        wi.install(UI(dry_run=True), START, DISK, "unknown", "T", dry_run=True)
    assert capsys.readouterr().out == ""


def test_familles_prises_en_charge():
    assert wi.supported("windows10") and wi.supported("windows11")
    assert not wi.supported("linux") and not wi.supported(None)
