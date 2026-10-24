"""Installation de Windows sur le disque : partitionnement, image, démarrage, fichiers de configuration.

Les fonctions qui FABRIQUENT des textes/commandes (diskpart_script, dism_command, bcdboot_command…)
sont séparées de celles qui EXÉCUTENT (install). En --dry-run, rien n'est exécuté : on affiche.
Les mots de passe contenus dans l'unattend ne sont jamais affichés ni écrits ailleurs que dans
le fichier Unattend.xml du PC installé.
"""
from __future__ import annotations

import ntpath
import os
import re
import shutil
import subprocess

import api
import probe

SUPPORTED_FAMILIES = ("windows10", "windows11")

SYSTEM_DRIVE = "S:"                     # petite partition de démarrage
WINDOWS_ROOT = "W:\\"                   # partition Windows
WORK_DIR = "W:\\_deploy"                # dossier temporaire (supprimé à la fin)
WIM_PATH = WORK_DIR + "\\install.wim"
SPACE_FACTOR = 2.5                      # place nécessaire = 2,5 x la taille du .wim (le .wim + l'image appliquée)


class InstallError(Exception):
    """Échec d'une étape. `step` est un mot-clé court envoyé au serveur ; `message` est en français."""

    def __init__(self, step: str, message: str):
        self.step = step
        self.message = message
        super().__init__(message)


def supported(os_family: str | None) -> bool:
    return os_family in SUPPORTED_FAMILIES


def boot_mode(firmware: str) -> str:
    """Valeur de l'option /f de bcdboot, selon le mode de démarrage détecté."""
    if firmware == "uefi":
        return "UEFI"
    if firmware == "bios":
        return "BIOS"
    raise InstallError("firmware", "Impossible de savoir si ce PC démarre en UEFI ou en BIOS : "
                                   "l'installation est annulée (rien n'a été modifié).")


def diskpart_script(disk: str, firmware: str) -> str:
    """Texte du script diskpart : UEFI -> GPT, BIOS -> MBR.
    Limite connue : pas de partition de récupération (WinRE) dans ce POC ; à ajouter si les PC en ont besoin."""
    if not str(disk).isdigit():
        raise ValueError(f"numéro de disque invalide : {disk!r}")
    if firmware == "uefi":
        lines = ["select disk " + str(disk), "clean", "convert gpt",
                 "create partition efi size=260", 'format quick fs=fat32 label="Systeme"', "assign letter=S",
                 "create partition msr size=16",
                 "create partition primary", 'format quick fs=ntfs label="Windows"', "assign letter=W", "exit"]
    elif firmware == "bios":
        lines = ["select disk " + str(disk), "clean", "convert mbr",
                 "create partition primary size=500", 'format quick fs=ntfs label="Systeme"', "active",
                 "assign letter=S",
                 "create partition primary", 'format quick fs=ntfs label="Windows"', "assign letter=W", "exit"]
    else:
        boot_mode(firmware)  # lève l'erreur « firmware inconnu »
    return "\r\n".join(lines) + "\r\n"


def dism_command(wim_index: int) -> list:
    return ["dism", "/Apply-Image", f"/ImageFile:{WIM_PATH}", f"/Index:{int(wim_index)}",
            "/ApplyDir:" + WINDOWS_ROOT, "/Verify"]


def bcdboot_command(firmware: str) -> list:
    return ["bcdboot", WINDOWS_ROOT + "Windows", "/s", SYSTEM_DRIVE, "/f", boot_mode(firmware)]


def check_space(free_bytes: float, size_bytes: int) -> None:
    needed = size_bytes * SPACE_FACTOR
    if free_bytes < needed:
        raise InstallError("space", f"Espace disque insuffisant : {free_bytes / 1e9:.0f} Go disponibles, "
                                    f"{needed / 1e9:.0f} Go nécessaires pour cette image.")


def target_paths(root: str = WINDOWS_ROOT, join=os.path.join) -> tuple[str, str]:
    return (join(root, "Windows", "Panther", "Unattend", "Unattend.xml"),
            join(root, "Windows", "Setup", "Scripts", "SetupComplete.cmd"))


def write_target_files(unattend_xml: str, setup_complete_cmd: str, root: str = WINDOWS_ROOT) -> None:
    """Dépose Unattend.xml (UTF-8 sans BOM) et SetupComplete.cmd (ANSI, fins de ligne Windows)."""
    xml_path, cmd_path = target_paths(root)
    cmd_text = re.sub(r"\r?\n", "\r\n", setup_complete_cmd)
    # errors="replace" : un caractère hors ANSI devient « ? » plutôt que de faire échouer l'installation
    for path, data in ((xml_path, unattend_xml.lstrip("\ufeff").encode("utf-8")),
                       (cmd_path, cmd_text.encode("cp1252", errors="replace"))):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)


def _run(cmd: list, step: str, what: str) -> None:
    """Lance une commande en laissant sa sortie à l'écran (dism affiche sa progression)."""
    try:
        code = subprocess.run(cmd).returncode
    except OSError as e:
        raise InstallError(step, f"Impossible de lancer {cmd[0]} ({e}).") from None
    if code != 0:
        raise InstallError(step, f"{what} a échoué (code {code}).")


def install(ui, start: dict, disk: dict, firmware: str, token: str, dry_run: bool = False) -> None:
    """Déroulé complet. `start` = réponse de deploy/start. Lève InstallError/ApiError/DownloadError."""
    mode = boot_mode(firmware)  # avant tout : on refuse sans rien toucher si le mode est inconnu
    size = int(start["size_bytes"])
    script = diskpart_script(disk["name"], firmware)
    sim = "[SIMULATION] " if dry_run else ""

    # Garde-fou AVANT d'effacer : le disque doit pouvoir contenir l'image (1 Go de marge pour S: et MSR)
    if disk.get("size_gb"):
        check_space((disk["size_gb"] - 1) * 1e9, size)

    ui.step(1, 5, f"Partitionnement du disque {disk['name']} ({'UEFI/GPT' if mode == 'UEFI' else 'BIOS/MBR'})")
    if dry_run:
        ui.say(f"{sim}diskpart /s <script> avec :")
        for line in script.splitlines():
            ui.say("      " + line)
    else:
        code, out = probe.run_diskpart(script)  # À VÉRIFIER SUR BANC : lettres S: et W: libres dans WinPE
        if code != 0:
            raise InstallError("partition", "Le partitionnement du disque a échoué (diskpart, code "
                                            f"{code}). Fin de la sortie :\n" + "\n".join(out.strip().splitlines()[-8:]))

    ui.step(2, 5, f"Téléchargement de l'image Windows ({size / 1e9:.1f} Go)".replace(".", ","))
    if dry_run:
        ui.say(f"{sim}téléchargement de {start['image_url']}")
        ui.say(f"      vers {WIM_PATH}, SHA-256 attendu : {start['sha256']}")
    else:
        check_space(shutil.disk_usage(WINDOWS_ROOT).free, size)
        api.download(start["image_url"], token, WIM_PATH, start["sha256"], size, ui.progress)
        ui.say("  Empreinte SHA-256 vérifiée : l'image est intacte.")

    ui.step(3, 5, "Application de l'image (plusieurs minutes, patience)")
    # À VÉRIFIER SUR BANC : dism dans WinPE avec peu de RAM (sinon ajouter /ScratchDir:W:\_deploy)
    dism = dism_command(start["wim_index"])
    if dry_run:
        ui.say(f"{sim}{' '.join(dism)}")
    else:
        _run(dism, "apply-image", "L'application de l'image (dism)")

    ui.step(4, 5, "Installation du démarrage")
    # À VÉRIFIER SUR BANC : bcdboot en UEFI (S: en FAT32) et en BIOS (partition S: active) sur de vrais PC
    bcd = bcdboot_command(firmware)
    if dry_run:
        ui.say(f"{sim}{' '.join(bcd)}")
    else:
        _run(bcd, "bcdboot", "La création du démarrage (bcdboot)")

    ui.step(5, 5, "Dépôt des fichiers de configuration et nettoyage")
    xml_path, cmd_path = target_paths(join=ntpath.join)  # ntpath : affichage correct même hors Windows
    if dry_run:  # le contenu n'est JAMAIS affiché (mots de passe) : seulement les tailles
        ui.say(f"{sim}écriture de {xml_path} ({len(start['unattend_xml'])} caractères)")
        ui.say(f"{sim}écriture de {cmd_path} ({len(start['setup_complete_cmd'])} caractères)")
        ui.say(f"{sim}suppression de {WORK_DIR}")
    else:
        write_target_files(start["unattend_xml"], start["setup_complete_cmd"])  # os.path.join = chemins Windows ici
        shutil.rmtree(WORK_DIR, ignore_errors=True)
