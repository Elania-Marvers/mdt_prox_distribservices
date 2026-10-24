"""Restauration Clonezilla : plan des partitions, dépôt de l'unattend, garde-fous, et tout le tuyau
HTTP -> décompresseur -> partclone avec de FAUX outils (petits scripts qui notent leurs appels)."""
import gzip
import http.server
import os
import shutil
import subprocess
import threading
import urllib.parse
from pathlib import Path

import pytest

import linux_restore as lr
from windows_install import InstallError

TOKEN = "TOKEN-SECRET"
UNATTEND = '<?xml version="1.0"?><unattend>MOT-DE-PASSE-SECRET &amp; é</unattend>'
SETUP_CMD = "@echo off\r\ndel unattend.xml\r\n"


class DummyUI:
    def __init__(self):
        self.lines, self.steps, self.progressed = [], [], []

    def say(self, text="", color=None):
        self.lines.append(text)

    error = say

    def step(self, n, total, text):
        self.steps.append((n, total, text))

    def progress(self, done, total):
        self.progressed.append((done, total))


# --------------------------------------------------------------------------- noms et plan

@pytest.mark.parametrize("name, expected", [
    ("sdb3.ntfs-ptcl-img.zst", {"part": "sdb3", "fs": "ntfs", "ext": "zst", "split": ""}),
    ("nvme0n1p2.dd-ptcl-img.gz", {"part": "nvme0n1p2", "fs": "dd", "ext": "gz", "split": ""}),
    ("sda1.vfat-ptcl-img.gz.aa", {"part": "sda1", "fs": "vfat", "ext": "gz", "split": "aa"}),
    ("sda1.ntfs-ptcl-img.ab", {"part": "sda1", "fs": "ntfs", "ext": "", "split": "ab"}),
    ("sda1.ntfs-ptcl-img", {"part": "sda1", "fs": "ntfs", "ext": "", "split": ""}),
])
def test_parse_image_file(name, expected):
    assert lr.parse_image_file(name) == expected


@pytest.mark.parametrize("name", ["parts", "disk", "sdb-gpt.gdisk", "Info-dmi.txt", "clonezilla-img",
                                  "sda1.ntfs-ptcl-img.zst.tmp.old", "sda1.ntfs-ptcl-img.ZST"])
def test_other_files_are_not_partitions(name):
    assert lr.parse_image_file(name) is None


FILES = [{"name": n, "size": s} for n, s in [
    ("sdb-gpt.gdisk", 17920), ("parts", 20), ("sdb4.ntfs-ptcl-img.zst", 459), ("sdb3.ntfs-ptcl-img.zst", 4705),
    ("sdb2.dd-ptcl-img.zst", 49), ("sdb1.vfat-ptcl-img.zst", 111)]]


def test_plan_follows_the_parts_order():
    plan = lr.plan_partitions(FILES, ["sdb1", "sdb2", "sdb3", "sdb4"])
    assert [(p["num"], p["fs"], p["ext"], p["total"]) for p in plan] == [
        (1, "vfat", "zst", 111), (2, "dd", "zst", 49), (3, "ntfs", "zst", 4705), (4, "ntfs", "zst", 459)]


def test_plan_orders_split_pieces_and_adds_their_sizes():
    files = [{"name": "sda1.ntfs-ptcl-img.gz.ab", "size": 5}, {"name": "sda1.ntfs-ptcl-img.gz.aa", "size": 7}]
    item = lr.plan_partitions(files, ["sda1"])[0]
    assert [p["name"] for p in item["pieces"]] == ["sda1.ntfs-ptcl-img.gz.aa", "sda1.ntfs-ptcl-img.gz.ab"] and item["total"] == 12


def test_plan_refuses_an_incomplete_or_inconsistent_image():
    with pytest.raises(InstallError, match="aucun fichier pour la partition « sdb9 »"):
        lr.plan_partitions(FILES, ["sdb1", "sdb9"])
    mixed = [{"name": "sda1.ntfs-ptcl-img.zst", "size": 1}, {"name": "sda1.ntfs-ptcl-img.gz", "size": 1}]
    with pytest.raises(InstallError, match="plusieurs formats"):
        lr.plan_partitions(mixed, ["sda1"])


@pytest.mark.parametrize("disk, num, expected", [("sda", 3, "/dev/sda3"), ("nvme0n1", 3, "/dev/nvme0n1p3"),
                                                 ("mmcblk0", 1, "/dev/mmcblk0p1")])
def test_part_device(disk, num, expected):
    assert lr.part_device("/dev", disk, num) == expected


# --------------------------------------------------------------------------- fichiers déposés dans Windows

def test_write_target_files_ignores_case_and_overwrites(tmp_path):
    (tmp_path / "WINDOWS" / "system32").mkdir(parents=True)
    old = tmp_path / "WINDOWS" / "panther" / "unattend"
    old.mkdir(parents=True)
    (old / "unattend.xml").write_text("ancien")
    written = lr.write_target_files(str(tmp_path), UNATTEND, SETUP_CMD)
    assert (old / "unattend.xml").read_bytes() == UNATTEND.encode("utf-8")      # même fichier, casse d'origine
    assert not (tmp_path / "WINDOWS" / "panther" / "unattend" / "Unattend.xml").exists() or True
    cmd = tmp_path / "WINDOWS" / "Setup" / "Scripts" / "SetupComplete.cmd"
    assert cmd.read_bytes() == b"@echo off\r\ndel unattend.xml\r\n" and len(written) == 2
    assert sorted(os.listdir(old)) == ["unattend.xml"]                           # pas de doublon « Unattend.xml »


def test_write_target_files_needs_windows(tmp_path):
    with pytest.raises(InstallError, match="Dossier Windows introuvable"):
        lr.write_target_files(str(tmp_path), UNATTEND, SETUP_CMD)


def test_efi_fallback_copy(tmp_path):
    boot = tmp_path / "EFI" / "Microsoft" / "Boot"
    boot.mkdir(parents=True)
    (boot / "bootmgfw.efi").write_bytes(b"MZ-bootmgr")
    assert lr.ensure_efi_fallback(str(tmp_path)) is True
    assert (tmp_path / "EFI" / "BOOT" / "BOOTX64.EFI").read_bytes() == b"MZ-bootmgr"
    (tmp_path / "EFI" / "BOOT" / "BOOTX64.EFI").write_bytes(b"autre")           # déjà présent : jamais écrasé
    assert lr.ensure_efi_fallback(str(tmp_path)) is True
    assert (tmp_path / "EFI" / "BOOT" / "BOOTX64.EFI").read_bytes() == b"autre"


def test_efi_fallback_without_windows(tmp_path):
    (tmp_path / "EFI").mkdir()
    assert lr.ensure_efi_fallback(str(tmp_path)) is False


# --------------------------------------------------------------------------- faux serveur HTTP et faux outils

class FileServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, files):
        super().__init__(("127.0.0.1", 0), self.Handler)
        self.files, self.requests, self.cut = files, [], {}   # cut : nom -> octets après lesquels couper UNE fois
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d/files" % self.server_address[1]

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            srv = self.server
            name = urllib.parse.unquote(self.path.rsplit("/", 1)[1])
            srv.requests.append((name, self.headers.get("Range")))
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
            blob = srv.files.get(name)
            if blob is None:
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            rng = self.headers.get("Range")
            start = int(rng.split("=")[1].split("-")[0]) if rng else 0
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(blob) - start))
            self.end_headers()
            cut = srv.cut.pop(name, None) if start == 0 else None
            if cut is not None:
                self.wfile.write(blob[:cut]); self.wfile.flush(); self.close_connection = True; return
            self.wfile.write(blob[start:])


def _bin(directory, name, body):
    path = Path(directory) / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


SINK = '''echo "$(basename "$0") $*" >> "$CALLS"
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
if [ -n "$FAKE_SINK_FAIL" ] && [ "$(basename "$0")" = "partclone.$FAKE_SINK_FAIL" ]; then
  cat > /dev/null; echo "Erreur partclone simulee" >&2; exit 1; fi
cat > "$out"
'''
LOGGED = 'echo "$(basename "$0") $*" >> "$CALLS"\n'
MOUNT = '''echo "mount $*" >> "$CALLS"
opts=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && opts="$a"; prev="$a"; done
n=$#; eval "mnt=\\${$n}"; eval "dev=\\${$((n-1))}"
if [ "$opts" = "rw" ] && [ -n "$FAKE_MOUNT_FAIL_RW" ]; then echo "Windows is hibernated" >&2; exit 14; fi
case "$dev" in *1) tree="$FAKE_ESP_TREE";; *3) tree="$FAKE_WIN_TREE";; *4) tree="$FAKE_REC_TREE";; esac
[ -n "$tree" ] && cp -R "$tree"/. "$mnt"/
exit 0
'''
UMOUNT = 'echo "umount $*" >> "$CALLS"; cp -R "$1" "$FAKE_SNAP/$(basename "$1")"\n'


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Faux outils dans le PATH + dossiers /dev, arbres NTFS/EFI, et journal des appels."""
    binary = tmp_path / "bin"
    binary.mkdir()
    for name in ("sgdisk", "wipefs", "partprobe", "udevadm", "efibootmgr", "mount.ntfs-3g"):
        _bin(binary, name, LOGGED)
    _bin(binary, "mount", MOUNT)
    _bin(binary, "umount", UMOUNT)
    _bin(binary, "blockdev", 'echo "$FAKE_DISK_BYTES"\n')
    _bin(binary, "zstd", 'cat\n')                                   # « décompression » = identité
    _bin(binary, "dd", LOGGED + 'out=""; for a in "$@"; do case "$a" in of=*) out="${a#of=}";; esac; done; cat > "$out"\n')
    for fs in ("ntfs", "vfat", "dd"):
        _bin(binary, "partclone." + fs, SINK)
    dev, snap = tmp_path / "dev", tmp_path / "snap"
    dev.mkdir(); snap.mkdir()
    win = tmp_path / "win_tree"; (win / "windows" / "System32").mkdir(parents=True)
    esp = tmp_path / "esp_tree"; (esp / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
    (esp / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi").write_bytes(b"MZ")
    rec = tmp_path / "rec_tree"; (rec / "Recovery").mkdir(parents=True)
    calls = tmp_path / "calls.log"; calls.write_text("")
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    for key, value in dict(CALLS=calls, FAKE_SNAP=snap, FAKE_WIN_TREE=win, FAKE_ESP_TREE=esp, FAKE_REC_TREE=rec,
                           FAKE_DISK_BYTES="256060514304").items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(lr, "MOUNT_ROOT", str(tmp_path))
    return dict(dev=str(dev), snap=snap, calls=calls, bin=binary, tmp=tmp_path)


def image_files():
    data = {
        "sdb-gpt.gdisk": b"GPT-BACKUP" * 100,
        "sdb1.vfat-ptcl-img.zst": b"partclone-image\x00EFI" + bytes(range(256)) * 50,
        "sdb2.dd-ptcl-img.gz": gzip.compress(bytes(16384)),                   # partition « dd » : données brutes, sans en-tête partclone
        "sdb3.ntfs-ptcl-img.zst.aa": b"partclone-image\x00NTFS-A" + os.urandom(300_000),
        "sdb3.ntfs-ptcl-img.zst.ab": os.urandom(200_000),
        "sdb4.ntfs-ptcl-img.zst": b"partclone-image\x00RECOVERY" + os.urandom(5000),
    }
    return data


def start_for(server, files, **over):
    listing = [{"name": n, "size": len(b)} for n, b in files.items()] + [{"name": "parts", "size": 20}]
    start = {"kind": "clonezilla", "image_name": "demo", "files_url": server.url, "files": listing,
             "source_disk": "sdb", "parts": ["sdb1", "sdb2", "sdb3", "sdb4"], "source_disk_bytes": 256060514304,
             "size_bytes": sum(len(b) for b in files.values()), "unattend_xml": UNATTEND, "setup_complete_cmd": SETUP_CMD}
    start.update(over)
    return start


def run_restore(lab, start, disk=None, firmware="uefi", **kw):
    ui = DummyUI()
    disk = disk or {"name": "sda", "size_gb": 256, "size_bytes": 256060514304}
    lr.restore(ui, start, disk, firmware, TOKEN, dev_root=lab["dev"], check_device=False, **kw)
    return ui


def calls(lab):
    return lab["calls"].read_text().splitlines()


# --------------------------------------------------------------------------- le tuyau complet

def test_full_restore(lab):
    files = image_files()
    server = FileServer(files)
    server.cut["sdb3.ntfs-ptcl-img.zst.aa"] = 120_000            # coupure en plein milieu : le client doit reprendre
    ui = run_restore(lab, start_for(server, files))
    dev = Path(lab["dev"])

    assert (dev / "sda1").read_bytes() == files["sdb1.vfat-ptcl-img.zst"]
    assert (dev / "sda2").read_bytes() == bytes(16384)                         # partition dd : données brutes via dd, gunzip réel
    assert (dev / "sda3").read_bytes() == files["sdb3.ntfs-ptcl-img.zst.aa"] + files["sdb3.ntfs-ptcl-img.zst.ab"]
    assert (dev / "sda4").read_bytes() == files["sdb4.ntfs-ptcl-img.zst"]
    resumed = [r for r in server.requests if r[0] == "sdb3.ntfs-ptcl-img.zst.aa"]
    assert len(resumed) == 2 and resumed[1][1] == "bytes=120000-"              # reprise avec Range, pas de téléchargement refait

    log = calls(lab)
    order = [l.split()[0] + " " + (l.split()[1] if l.startswith("sgdisk") else "") for l in log if l.startswith(("wipefs", "sgdisk", "partprobe"))]
    assert order[0].startswith("wipefs") and "--zap-all" in log[1] and any("--load-backup=" in l for l in log) \
        and any(l.startswith("sgdisk -e") for l in log) and any(l.startswith("partprobe") for l in log)
    assert f"partclone.vfat -r -s - -o {dev}/sda1" in log and f"partclone.ntfs -r -s - -o {dev}/sda3" in log
    assert any(l.startswith("dd of=") and l.endswith("conv=fsync") for l in log)    # sans en-tête partclone -> dd brut
    assert not any(l.startswith("partclone.dd") for l in log)

    # unattend déposé dans la partition Windows (la plus grosse), pas dans la récupération
    win = next(p for p in lab["snap"].iterdir() if (p / "windows").exists())
    assert (win / "windows" / "Panther" / "Unattend" / "Unattend.xml").read_bytes() == UNATTEND.encode("utf-8")
    assert (win / "windows" / "Setup" / "Scripts" / "SetupComplete.cmd").read_bytes() == b"@echo off\r\ndel unattend.xml\r\n"
    # démarrage : chemin de secours EFI copié, entrée UEFI créée si le système est en UEFI
    esp = next(p for p in lab["snap"].iterdir() if (p / "EFI").exists())
    assert (esp / "EFI" / "BOOT" / "BOOTX64.EFI").read_bytes() == b"MZ"
    assert [s[0] for s in ui.steps] == [1, 2, 3, 4, 5] and ui.progressed
    assert all("MOT-DE-PASSE" not in line for line in ui.lines)               # jamais affiché


def test_dd_partition_with_a_partclone_header_uses_partclone_dd(lab):
    files = image_files()
    files["sdb2.dd-ptcl-img.gz"] = gzip.compress(b"partclone-image\x00" + bytes(1000))
    server = FileServer(files)
    run_restore(lab, start_for(server, files))
    assert any(l.startswith("partclone.dd -r -s - -o") for l in calls(lab))


def test_hibernated_windows_partition_is_retried_with_remove_hiberfile(lab, monkeypatch):
    monkeypatch.setenv("FAKE_MOUNT_FAIL_RW", "1")
    files = image_files()
    run_restore(lab, start_for(FileServer(files), files))
    ntfs_mounts = [l for l in calls(lab) if "ntfs-3g" in l and l.startswith("mount")]
    assert any("-o rw," in l and "remove_hiberfile" in l for l in ntfs_mounts)
    assert any(p for p in lab["snap"].iterdir() if (p / "windows" / "Panther").exists())


def test_partclone_failure_is_reported_with_the_log(lab, monkeypatch):
    monkeypatch.setenv("FAKE_SINK_FAIL", "ntfs")
    files = image_files()
    with pytest.raises(InstallError) as exc:
        run_restore(lab, start_for(FileServer(files), files))
    assert exc.value.step == "restore" and "sdb3" in exc.value.message and "Erreur partclone simulee" in exc.value.message


def test_bad_token_stops_before_writing_anything_to_the_partition(lab):
    files = image_files()
    server = FileServer(files)
    ui = DummyUI()
    with pytest.raises(Exception) as exc:
        lr.restore(ui, start_for(server, files), {"name": "sda"}, "uefi", "mauvais-jeton", dev_root=lab["dev"], check_device=False)
    assert "401" in str(getattr(exc.value, "status", "")) or "ApiError" in type(exc.value).__name__


# --------------------------------------------------------------------------- garde-fous : rien n'est touché

def refuses(lab, start, match, disk=None, firmware="uefi", **kw):
    before = calls(lab)
    with pytest.raises(InstallError, match=match):
        run_restore(lab, start, disk, firmware, **kw)
    assert calls(lab) == before and not any(Path(lab["dev"]).iterdir())            # aucune commande, aucun octet écrit


def test_bios_pc_is_refused(lab):
    files = image_files()
    refuses(lab, start_for(FileServer(files), files), "BIOS classique", firmware="bios")


def test_mbr_image_is_refused(lab):
    files = image_files()
    del files["sdb-gpt.gdisk"]
    refuses(lab, start_for(FileServer(files), files), "GPT/UEFI")


def test_disk_smaller_than_the_original_is_refused(lab):
    files = image_files()
    refuses(lab, start_for(FileServer(files), files), "plus petit", disk={"name": "sda", "size_bytes": 128 * 10**9})


def test_missing_tools_are_listed_before_touching_the_disk(lab, monkeypatch):
    files = image_files()
    empty = lab["tmp"] / "vide"; empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(InstallError, match="Outils absents.*partclone.ntfs"):
        lr.restore(DummyUI(), start_for(FileServer(files), files), {"name": "sda"}, "uefi", TOKEN,
                   dev_root=lab["dev"], check_device=False)


def test_target_must_be_a_real_block_device(lab):
    files = image_files()
    (Path(lab["dev"]) / "sda").write_text("fichier ordinaire")
    with pytest.raises(InstallError, match="n'est pas un disque"):
        lr.restore(DummyUI(), start_for(FileServer(files), files), {"name": "sda"}, "uefi", TOKEN, dev_root=lab["dev"])


@pytest.mark.parametrize("name", ["../etc", "sda; rm", "SDA", "", "sda/1"])
def test_disk_name_is_validated(lab, name):
    files = image_files()
    with pytest.raises(InstallError, match="Nom de disque invalide"):
        run_restore(lab, start_for(FileServer(files), files), disk={"name": name})


def test_dry_run_runs_nothing_and_hides_secrets(lab, monkeypatch):
    files = image_files()
    server = FileServer(files)
    monkeypatch.setenv("PATH", str(lab["tmp"]))                                   # aucun outil : rien ne doit être lancé
    ui = DummyUI()
    lr.restore(ui, start_for(server, files), {"name": "sda", "size_bytes": 256060514304}, "uefi", TOKEN,
               dry_run=True, dev_root=lab["dev"])
    text = "\n".join(ui.lines)
    assert server.requests == [] and calls(lab) == [] and not any(Path(lab["dev"]).iterdir())
    assert "sgdisk --load-backup=" in text and "partclone.ntfs -r -s - -o" in text and "Unattend.xml" in text
    assert "MOT-DE-PASSE" not in text and TOKEN not in text


# --------------------------------------------------------------------------- de VRAIS fichiers d'image (facultatif)

REAL = os.environ.get("MDTPROX_REAL_IMAGE")  # un dossier d'image Clonezilla réelle (lecture seule)


def _reference(path):
    """Le flux décompressé par l'outil de référence, sans passer par notre code."""
    tool = {"zst": ["zstd", "-dc"], "gz": ["gzip", "-dc"]}[lr.parse_image_file(path.name)["ext"]]
    return subprocess.run(tool + [str(path)], capture_output=True, check=True).stdout


@pytest.mark.skipif(not REAL or not Path(REAL).is_dir(), reason="définir MDTPROX_REAL_IMAGE=/dossier/d/une/image pour ce test")
def test_real_image_files_stream_through_the_real_decompressor(lab):
    folder = Path(REAL)
    chosen = {}
    for f in sorted(folder.iterdir()):
        info = lr.parse_image_file(f.name)
        if info and info["ext"] in ("zst", "gz") and not info["split"] and info["fs"] in ("vfat", "dd") and f.stat().st_size < 60_000_000:
            chosen.setdefault(info["fs"], f)
    if "vfat" not in chosen or not all(shutil.which(t) for t in ("zstd", "gzip")):
        pytest.skip("pas de petite partition vfat compressée dans ce dossier, ou zstd/gzip absent")
    for tool in ("zstd", "gzip"):
        if (Path(lab["bin"]) / tool).exists():
            (Path(lab["bin"]) / tool).unlink()                                              # on utilise les VRAIS outils
    files = {f.name: f.read_bytes() for f in chosen.values()}
    server = FileServer(files)
    plan = lr.plan_partitions([{"name": n, "size": len(b)} for n, b in files.items()], [lr.parse_image_file(n)["part"] for n in files])
    log = str(lab["tmp"] / "restore.log")
    for n, item in enumerate(plan, start=1):
        target = Path(lab["dev"]) / ("part%d" % n)
        lr.restore_partition(item, str(target), server.url, TOKEN, log)
        assert target.read_bytes() == _reference(chosen[item["fs"]]), item["part"]          # octet pour octet
    efi = (Path(lab["dev"]) / "part1").read_bytes() if plan[0]["fs"] == "vfat" else b""
    assert not efi or efi.startswith(lr.PARTCLONE_MAGIC)                                    # un vrai flux partclone
