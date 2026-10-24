"""Restauration d'une image Clonezilla depuis le mini-système Linux (CDC, annexe B).

Une image Clonezilla (`ocs-sr savedisk`) est un dossier : une sauvegarde de la table de partitions GPT
(`<disque>-gpt.gdisk`, produite par `sgdisk -b`) et un fichier compressé par partition
(`sdb3.ntfs-ptcl-img.zst`). On refait la même chose que `ocs-sr restoredisk`, mais SANS stocker l'image :
chaque fichier arrive par HTTP (avec le jeton, reprise après coupure) et traverse
    décompresseur (zstd, gzip…)  ->  partclone.<système de fichiers>  ->  partition
Déroulé : table de partitions -> partitions -> démarrage UEFI -> unattend.xml déposé via ntfs-3g.

Tout ce qui n'a pas pu être vérifié sans un vrai PC et un vrai mini-système est marqué À VÉRIFIER SUR BANC.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse

import api
from windows_install import InstallError

DECOMPRESSORS = {"zst": ["zstd", "-dc"], "gz": ["gzip", "-dc"], "bz2": ["bzip2", "-dc"],
                 "xz": ["xz", "-dc"], "lz4": ["lz4", "-dc"], "": None}  # "" = non compressé
PARTCLONE_MAGIC = b"partclone-image\x00"
_IMAGE_FILE = re.compile(r"^(?P<part>[a-z0-9]+)\.(?P<fs>[a-z0-9]+)-ptcl-img(?:\.(?P<rest>.+))?$")
_PEEK = 1024 * 1024
MOUNT_ROOT = None  # Limite connue : dossier de montage créé à la demande (tempfile) ; fixé ici seulement par les tests


# --------------------------------------------------------------------------- lecture de la liste de fichiers

def parse_image_file(name: str) -> dict | None:
    """'sdb3.ntfs-ptcl-img.zst' -> {part, fs, ext, split} ; None si ce n'est pas un fichier de partition."""
    m = _IMAGE_FILE.match(name)
    if not m:
        return None
    tokens = m["rest"].split(".") if m["rest"] else []
    ext = tokens[0] if tokens and tokens[0] in DECOMPRESSORS and tokens[0] else ""
    tokens = tokens[1:] if ext else tokens
    if len(tokens) > 1 or (tokens and not re.fullmatch(r"[a-z]{2}", tokens[0])):
        return None  # suffixe inconnu : on préfère refuser que deviner
    return {"part": m["part"], "fs": m["fs"], "ext": ext, "split": tokens[0] if tokens else ""}


def plan_partitions(files: list, parts: list) -> list:
    """[{part, num, fs, ext, pieces: [{name, size}], total}] dans l'ordre de `parts`. Lève InstallError."""
    found = {}
    for f in files:
        info = parse_image_file(f["name"])
        if info:
            found.setdefault(info["part"], []).append((info, f))
    plan = []
    for part in parts:
        pieces = sorted(found.get(part, []), key=lambda p: p[0]["split"])
        num = re.search(r"(\d+)$", part)
        if not pieces or not num:
            raise InstallError("image", f"L'image est incomplète : aucun fichier pour la partition « {part} ».")
        kinds = {(i["fs"], i["ext"]) for i, _ in pieces}
        if len(kinds) != 1:
            raise InstallError("image", f"L'image est incohérente : la partition « {part} » a plusieurs formats.")
        fs, ext = next(iter(kinds))
        if fs != "dd" and not re.fullmatch(r"[a-z0-9]+", fs):
            raise InstallError("image", f"Système de fichiers non pris en charge pour « {part} » : {fs}.")
        plan.append({"part": part, "num": int(num.group(1)), "fs": fs, "ext": ext,
                     "pieces": [{"name": f["name"], "size": f["size"]} for _, f in pieces],
                     "total": sum(f["size"] for _, f in pieces)})
    return plan


def part_device(dev_root: str, disk: str, num: int) -> str:
    """/dev/sda + 3 -> /dev/sda3 ; /dev/nvme0n1 + 3 -> /dev/nvme0n1p3."""
    return os.path.join(dev_root, f"{disk}p{num}" if disk[-1].isdigit() else f"{disk}{num}")


def sink_command(fs: str, device: str) -> list:
    return ["partclone." + fs, "-r", "-s", "-", "-o", device]  # À VÉRIFIER SUR BANC : options de la version de partclone installée


def required_tools(plan: list) -> list:
    tools = ["sgdisk", "wipefs", "blockdev", "partprobe", "mount", "umount", "mount.ntfs-3g", "sync"]
    for item in plan:
        tools.append("dd" if item["fs"] == "dd" else "partclone." + item["fs"])
        if DECOMPRESSORS[item["ext"]]:
            tools.append(DECOMPRESSORS[item["ext"]][0])
    return sorted(set(tools))


# --------------------------------------------------------------------------- noms de fichiers insensibles à la casse

def ci_find(root: str, *parts: str) -> str | None:
    """Le chemin root/parts… s'il existe, en ignorant la casse (NTFS ne la distingue pas), sinon None."""
    path = root
    for part in parts:
        try:
            names = os.listdir(path)
        except OSError:
            return None
        match = next((n for n in names if n.lower() == part.lower()), None)
        if match is None:
            return None
        path = os.path.join(path, match)
    return path


def ci_makedirs(root: str, *parts: str) -> str:
    path = root
    for part in parts:
        found = ci_find(path, part)
        path = found or os.path.join(path, part)
        if not found:
            os.mkdir(path)
    return path


def write_target_files(win_root: str, unattend_xml: str, setup_complete_cmd: str) -> list:
    """Dépose unattend.xml et SetupComplete.cmd dans la partition Windows montée. Renvoie les chemins écrits."""
    win = ci_find(win_root, "Windows")
    if win is None:
        raise InstallError("unattend", "Dossier Windows introuvable dans la partition restaurée.")
    written = []
    for folder, name, data in (
            (("Panther", "Unattend"), "Unattend.xml", unattend_xml.encode("utf-8")),
            (("Setup", "Scripts"), "SetupComplete.cmd", setup_complete_cmd.encode("cp1252", errors="replace"))):
        directory = ci_makedirs(win, *folder)
        path = ci_find(directory, name) or os.path.join(directory, name)  # écrase un fichier du même nom, quelle que soit la casse
        with open(path, "wb") as f:
            f.write(data)
        written.append(path)
    return written


def ensure_efi_fallback(esp_root: str) -> bool:
    """Copie bootmgfw.efi vers \\EFI\\BOOT\\BOOTX64.EFI (chemin que tout firmware UEFI sait lancer) s'il manque."""
    boot = ci_find(esp_root, "EFI", "Microsoft", "Boot", "bootmgfw.efi")
    if boot is None:
        return False
    directory = ci_makedirs(esp_root, "EFI", "BOOT")
    if ci_find(directory, "BOOTX64.EFI") is None:
        shutil.copyfile(boot, os.path.join(directory, "BOOTX64.EFI"))
    return True


# --------------------------------------------------------------------------- exécution de commandes

def _run(cmd: list, step: str, what: str) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        raise InstallError(step, f"Impossible de lancer {cmd[0]} ({e}).") from None
    if r.returncode != 0:
        tail = "\n".join((r.stderr or r.stdout or "").strip().splitlines()[-5:])
        raise InstallError(step, f"{what} a échoué (code {r.returncode}). {tail}".strip())
    return r.stdout


def _log_tail(path: str, lines: int = 8) -> str:
    """Les dernières lignes du journal de partclone (qui écrit sa progression avec des retours chariot)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join([l for l in text.replace("\r", "\n").splitlines() if l.strip()][-lines:])


def _mounted_from(device: str) -> bool:
    try:
        with open("/proc/mounts") as f:
            return any(line.split()[0].startswith(device) for line in f if line.strip())
    except OSError:
        return False


# --------------------------------------------------------------------------- une partition : HTTP -> décompresseur -> partclone

CHUNK_PUMP = 4 * 1024 * 1024


def _pump_raw(src, device: str, log, errors: list, kill) -> None:
    """Partition « dd » : le flux décompressé est soit une image partclone, soit des données brutes."""
    try:
        first = src.read(_PEEK)
        cmd = sink_command("dd", device) if first.startswith(PARTCLONE_MAGIC) \
            else ["dd", "of=" + device, "bs=4M", "conv=fsync"]
        sink = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=log)
        try:
            data = first
            while data:
                sink.stdin.write(data)
                data = src.read(CHUNK_PUMP)
            sink.stdin.close()
        except BrokenPipeError:  # dd / partclone s'est arrêté : on débloque l'envoi en tuant le décompresseur
            kill()
        rc = sink.wait()
        if rc != 0:
            errors.append(f"{cmd[0]} (code {rc})")
    except Exception as e:  # le fil d'exécution ne doit jamais mourir en silence
        errors.append(str(e))
        kill()


def restore_partition(item: dict, device: str, files_url: str, token: str, log_path: str, progress=None) -> None:
    """Envoie les fichiers de la partition `item` dans le décompresseur puis dans partclone."""
    log = open(log_path, "ab")
    procs, thread, errors, codes = [], None, [], []
    try:
        dec_cmd = DECOMPRESSORS[item["ext"]]
        if item["fs"] == "dd" and not dec_cmd:
            dec_cmd = ["cat"]
        if item["fs"] == "dd":
            dec = subprocess.Popen(dec_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
            procs.append(dec)
            thread = threading.Thread(target=_pump_raw, args=(dec.stdout, device, log, errors, dec.kill), daemon=True)
            thread.start()
            feed = dec.stdin
        elif dec_cmd:
            dec = subprocess.Popen(dec_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
            sink = subprocess.Popen(sink_command(item["fs"], device), stdin=dec.stdout, stdout=log, stderr=log)
            dec.stdout.close()  # le décompresseur reçoit un SIGPIPE si partclone s'arrête
            procs += [dec, sink]
            feed = dec.stdin
        else:
            sink = subprocess.Popen(sink_command(item["fs"], device), stdin=subprocess.PIPE, stdout=log, stderr=log)
            procs.append(sink)
            feed = sink.stdin

        base = 0
        for piece in item["pieces"]:
            url = files_url.rstrip("/") + "/" + urllib.parse.quote(piece["name"])
            offset = base
            api.stream_to(url, token, feed.write, piece["size"],
                          (lambda d, t, o=offset: progress(o + d, item["total"])) if progress else None)
            base += piece["size"]
        feed.close()
    except api.SinkClosed:
        pass  # le vrai motif est dans le journal de partclone : lu plus bas
    except BaseException:
        for p in procs:
            p.kill()
        raise
    finally:
        try:  # le fichier journal reste ouvert tant que le fil et les processus s'en servent
            if thread:
                thread.join()
            codes = [p.wait() for p in procs]
        finally:
            log.close()
    if any(codes) or errors:
        raise InstallError("restore", f"La restauration de la partition {item['part']} ({item['fs']}) a échoué "
                                      f"(codes {codes}{' ; ' + '; '.join(errors) if errors else ''}). Fin du journal :\n"
                           + _log_tail(log_path))


# --------------------------------------------------------------------------- déroulé complet

def _download_small(url: str, token: str, dest: str) -> None:
    with open(dest, "wb") as f:
        api.stream_to(url, token, f.write)


def _mount(cmd: list, mnt: str, what: str) -> None:
    r = subprocess.run(cmd + [mnt], capture_output=True, text=True)
    if r.returncode != 0:
        raise InstallError("mount", f"{what} a échoué : {(r.stderr or r.stdout).strip()[-300:]}")


def restore(ui, start: dict, disk: dict, firmware: str, token: str, dry_run: bool = False,
            dev_root: str = "/dev", check_device: bool = True) -> None:
    """Déroulé complet. `start` = réponse de deploy/start (image Clonezilla). Lève InstallError/ApiError/DownloadError.

    `dev_root` et `check_device` n'existent que pour les tests (ils remplacent /dev par un dossier)."""
    sim = "[SIMULATION] " if dry_run else ""
    name = str(disk["name"])
    if not re.fullmatch(r"[a-z0-9]+", name):
        raise InstallError("disk", f"Nom de disque invalide : « {name} ».")
    target = os.path.join(dev_root, name)
    files_url, src = start["files_url"], start["source_disk"]
    names = {f["name"] for f in start["files"]}
    plan = plan_partitions(start["files"], start["parts"])
    backup = f"{src}-gpt.gdisk"

    # --- garde-fous AVANT toute modification
    if backup not in names:
        raise InstallError("image", "Cette image n'est pas un disque GPT/UEFI : elle n'est pas prise en charge.")
    if firmware == "bios":
        raise InstallError("firmware", "Cette image est faite pour un PC en UEFI, et ce PC démarre en BIOS classique : "
                                       "Windows ne démarrerait pas. Rien n'a été modifié.")
    needed = start.get("source_disk_bytes")
    exact = disk.get("size_bytes")
    if needed and exact and exact < needed:
        raise InstallError("disk", f"Le disque ({exact / 1e9:.0f} Go) est plus petit que le disque d'origine de l'image "
                                   f"({needed / 1e9:.0f} Go). Rien n'a été modifié.")
    if not dry_run:
        missing = [t for t in required_tools(plan) if not shutil.which(t)]
        if missing:
            raise InstallError("tools", "Outils absents du mini-système : " + ", ".join(missing)
                               + ". Il faut le refabriquer. Rien n'a été modifié.")
        if check_device:
            if not os.path.exists(target) or not _is_block(target):
                raise InstallError("disk", f"{target} n'est pas un disque. Rien n'a été modifié.")
            if _mounted_from(target):
                raise InstallError("disk", f"{target} est en cours d'utilisation (monté). Rien n'a été modifié.")
            size = int(_run(["blockdev", "--getsize64", target], "disk", "La lecture de la taille du disque").strip())
            if needed and size < needed:
                raise InstallError("disk", f"Le disque ({size / 1e9:.0f} Go) est plus petit que le disque d'origine de "
                                           f"l'image ({needed / 1e9:.0f} Go). Rien n'a été modifié.")

    work = tempfile.mkdtemp(prefix="mdtprox-")
    log_path = os.path.join(work, "restauration.log")
    try:
        ui.step(1, 5, f"Préparation du disque {name} (effacement, table de partitions GPT)")
        gdisk = os.path.join(work, backup)
        cmds = [["wipefs", "-a", target], ["sgdisk", "--zap-all", target],
                ["sgdisk", "--load-backup=" + gdisk, target], ["sgdisk", "-e", target], ["partprobe", target]]
        if dry_run:
            ui.say(f"{sim}téléchargement de {files_url}/{backup}")
            for c in cmds:
                ui.say(f"{sim}{' '.join(c)}")
        else:
            _download_small(files_url.rstrip("/") + "/" + urllib.parse.quote(backup), token, gdisk)
            for c in cmds:  # À VÉRIFIER SUR BANC : sgdisk --load-backup puis -e sur un disque plus grand que l'original
                _run(c, "partition", f"La commande « {' '.join(c[:2])} »")
            if shutil.which("udevadm"):
                subprocess.run(["udevadm", "settle"], capture_output=True)
            _wait_for_partitions(plan, dev_root, name, check_device)

        total = len(plan)
        ui.step(2, 5, f"Restauration de {total} partitions ({sum(i['total'] for i in plan) / 1e9:.1f} Go compressés)".replace(".", ","))
        for n, item in enumerate(plan, start=1):
            device = part_device(dev_root, name, item["num"])
            what = "données brutes" if item["fs"] == "dd" else item["fs"]
            ui.say(f"  Partition {n}/{total} : {item['part']} ({what}, {item['total'] / 1e6:.0f} Mo) -> {device}")
            if dry_run:
                for piece in item["pieces"]:
                    ui.say(f"{sim}  {files_url}/{piece['name']}  |  "
                           f"{' '.join(DECOMPRESSORS[item['ext']] or ['(sans compression)'])}  |  "
                           f"{' '.join(sink_command(item['fs'], device)) if item['fs'] != 'dd' else 'dd of=' + device}")
            else:
                restore_partition(item, device, files_url, token, log_path, ui.progress)

        ui.step(3, 5, "Installation du démarrage UEFI")
        esp = next((i for i in plan if i["fs"] == "vfat"), None)
        if dry_run:
            ui.say(f"{sim}montage de la partition EFI, copie de bootmgfw.efi vers \\EFI\\BOOT\\BOOTX64.EFI si absent")
            ui.say(f"{sim}efibootmgr -c -d {target} -p {esp['num'] if esp else '?'} -L \"Windows Boot Manager\" "
                   "-l \\EFI\\Microsoft\\Boot\\bootmgfw.efi")
        elif esp:
            _boot_entries(ui, part_device(dev_root, name, esp["num"]), target, esp["num"])
        else:
            ui.say("  Aucune partition EFI (vfat) dans l'image : démarrage non configuré.")

        ui.step(4, 5, "Dépôt des fichiers de configuration (nom du PC, domaine, administrateur)")
        ntfs = sorted((i for i in plan if i["fs"] == "ntfs"), key=lambda i: -i["total"])
        if dry_run:  # le contenu n'est JAMAIS affiché (mots de passe) : seulement les tailles
            ui.say(f"{sim}montage (ntfs-3g) de la partition Windows parmi : {', '.join(i['part'] for i in ntfs) or 'aucune'}")
            ui.say(f"{sim}écriture de Windows\\Panther\\Unattend\\Unattend.xml ({len(start['unattend_xml'])} caractères)")
            ui.say(f"{sim}écriture de Windows\\Setup\\Scripts\\SetupComplete.cmd ({len(start['setup_complete_cmd'])} caractères)")
        else:
            _deposit(ui, [part_device(dev_root, name, i["num"]) for i in ntfs], start)

        ui.step(5, 5, "Nettoyage")
        if dry_run:
            ui.say(f"{sim}sync ; suppression des fichiers temporaires")
        else:
            subprocess.run(["sync"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _is_block(path: str) -> bool:
    import stat
    return stat.S_ISBLK(os.stat(path).st_mode)


def _wait_for_partitions(plan: list, dev_root: str, disk: str, check: bool, seconds: float = 15) -> None:
    if not check:
        return
    deadline = time.monotonic() + seconds
    missing = [part_device(dev_root, disk, i["num"]) for i in plan]
    while missing and time.monotonic() < deadline:
        missing = [d for d in missing if not os.path.exists(d)]
        if missing:
            time.sleep(0.5)
    if missing:
        raise InstallError("partition", "Les partitions n'apparaissent pas après la création de la table : " + ", ".join(missing))


def _boot_entries(ui, esp_device: str, disk_device: str, esp_num: int) -> None:
    mnt = tempfile.mkdtemp(prefix="mdtprox-esp-", dir=MOUNT_ROOT)
    try:
        _mount(["mount", "-t", "vfat", esp_device], mnt, "Le montage de la partition EFI")
        try:
            if not ensure_efi_fallback(mnt):
                ui.say("  bootmgfw.efi est introuvable dans la partition EFI : l'image n'a peut-être pas de Windows UEFI.")
        finally:
            subprocess.run(["umount", mnt], capture_output=True)
    finally:
        shutil.rmtree(mnt, ignore_errors=True)
    if os.path.isdir("/sys/firmware/efi") and shutil.which("efibootmgr"):
        r = subprocess.run(["efibootmgr", "-c", "-d", disk_device, "-p", str(esp_num), "-L", "Windows Boot Manager",
                            "-l", "\\EFI\\Microsoft\\Boot\\bootmgfw.efi"], capture_output=True, text=True)
        if r.returncode != 0:  # le chemin de secours \EFI\BOOT\BOOTX64.EFI suffit en général : on ne bloque pas
            ui.say("  (l'entrée de démarrage UEFI n'a pas pu être créée ; le chemin de secours est en place)")


def _deposit(ui, devices: list, start: dict) -> None:
    """Monte chaque partition NTFS (la plus grosse d'abord) jusqu'à trouver Windows, y dépose les fichiers."""
    for device in devices:
        mnt = tempfile.mkdtemp(prefix="mdtprox-win-", dir=MOUNT_ROOT)
        try:
            mounted = False
            for opts in ("rw", "rw,remove_hiberfile"):  # 2e essai : image prise sur un PC en veille prolongée
                r = subprocess.run(["mount", "-t", "ntfs-3g", "-o", opts, device, mnt], capture_output=True, text=True)
                if r.returncode == 0:
                    mounted = True
                    break
            if not mounted:
                raise InstallError("mount", f"Le montage de {device} (ntfs-3g) a échoué : {(r.stderr or r.stdout).strip()[-300:]}")
            try:
                if ci_find(mnt, "Windows", "System32") is None:
                    continue  # pas la partition Windows (récupération…) : on essaie la suivante
                write_target_files(mnt, start["unattend_xml"], start["setup_complete_cmd"])
                subprocess.run(["sync"])
                return
            finally:
                subprocess.run(["umount", mnt], capture_output=True)
        finally:
            shutil.rmtree(mnt, ignore_errors=True)
    raise InstallError("unattend", "Aucune partition Windows trouvée dans l'image restaurée : le fichier de configuration "
                                   "n'a pas pu être déposé.")
