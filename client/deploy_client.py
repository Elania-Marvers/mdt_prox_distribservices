"""Client d'installation (mini-système Linux ou WinPE, annexe A.8 du CDC).

Conception et développement : Baptiste Renneson-Boutard (Elania-Marvers), concepteur principal.
Projet libre (licence MIT) : reprise et adaptation bienvenues, en conservant cette mention et la licence.


Déroulé : réseau prêt -> matériel -> code de salle -> verdict -> nom du PC -> confirmation OUI
-> deploy/start -> partitionnement -> image -> démarrage -> fichiers -> rapport -> redémarrage.

Codes de sortie : 0 = installé (redémarrage lancé) ou pénalité (extinction) ; 1 = échec.
"""
from __future__ import annotations

import os
import sys

# Le Python « embeddable » (fichier ._pth) n'ajoute pas le dossier du script au chemin d'import.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse  # noqa: E402
import json  # noqa: E402
import traceback  # noqa: E402

import api  # noqa: E402
import linux_restore  # noqa: E402
import probe  # noqa: E402
import windows_install  # noqa: E402
from ui import UI, YELLOW  # noqa: E402

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client.json")
DEFAULT_CONFIG = {"server_url": "", "penalty_seconds": 90, "ascii_only": False}
# Limite connue : 3 fixe côté client (le serveur a son propre MAX_BAD_CODES) ; à lire dans la réponse du serveur si besoin
MAX_REFUSALS = 3  # codes faux avant pénalité


class Stop(Exception):
    """Arrête le programme proprement avec ce code de sortie."""

    def __init__(self, code: int):
        self.code = code


def load_config(path: str = CONFIG_PATH) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:  # utf-8-sig : tolère le BOM du Bloc-notes
            cfg.update(json.load(f))
    return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Client d'installation des postes.")
    p.add_argument("--server", help="adresse de l'API, ex. http://10.0.0.3:8080 (prioritaire sur client.json)")
    p.add_argument("--simulate", choices=sorted(probe.SIMULATED), help="matériel factice (tests)")
    p.add_argument("--hardware-json", help="fichier JSON de matériel factice (tests)")
    p.add_argument("--dry-run", action="store_true", help="n'exécute aucune commande destructrice : affiche")
    return p.parse_args(argv)


def fail(ui: UI, base: str, token: str | None, step: str, message: str, dry_run: bool) -> int:
    """Affiche l'erreur, prévient le serveur (au mieux) et renvoie le code de sortie 1."""
    ui.error(message)
    if dry_run:
        ui.say("[SIMULATION] rapport d'échec non envoyé au serveur.", YELLOW)
    elif token:
        api.report(base, token, "failed", step, message)
    return 1


def explain(exc: Exception) -> tuple[str, str]:
    """(étape, message en français) pour n'importe quelle erreur d'installation."""
    if isinstance(exc, windows_install.InstallError):
        return exc.step, exc.message
    if isinstance(exc, api.ApiError):
        return "api", exc.user_message()
    if isinstance(exc, api.DownloadError):
        return "download", str(exc)
    return "unexpected", f"Erreur inattendue ({type(exc).__name__}) : {exc}"


def login(ui: UI, base: str, hw: dict) -> dict:
    """Demande le code jusqu'à obtenir une session.
    Pénalité (puis Stop(0)) si : code vide, réponse penalty:true / 429, ou MAX_REFUSALS refus."""
    refusals = 0
    while True:
        code = ui.ask_code()
        if not code.strip():
            ui.penalty()
            raise Stop(0)
        try:
            return api.session(base, code, hw)
        except api.ApiError as e:
            if e.penalty:
                ui.penalty()
                raise Stop(0)
            if e.status != 401:
                ui.error(e.user_message())
                raise Stop(1)
            refusals += 1
            if refusals >= MAX_REFUSALS:
                ui.penalty()
                raise Stop(0)
            left = e.attempts_left if isinstance(e.attempts_left, int) else MAX_REFUSALS - refusals
            ui.error(f"Code incorrect. Il vous reste {left} essai(s).")


def ask_target(ui: UI, base: str, sess: dict, hw: dict, dry_run: bool):
    """Disque, nom du PC, confirmation OUI, puis deploy/start.
    Renvoie (réponse de deploy/start, disque) ou None si la personne a annulé."""
    token = sess["token"]
    disk = ui.choose_disk(hw["disks"])
    if disk is None:
        raise Stop(fail(ui, base, token, "disk", "Aucun disque détecté sur ce PC : installation impossible.", dry_run))
    room = sess["room"]
    confirmed_warning = sess["assessment"].get("level", "ok") != "ok"  # arrivé ici = la personne a confirmé
    while True:
        hostname = ui.ask_hostname(room.get("name_prefix", ""), room.get("name_example", ""))
        if not ui.final_confirm(room, sess["image"], hostname, disk):
            return None
        try:
            return api.deploy_start(base, token, hostname, disk["name"], confirmed_warning), disk
        except api.ApiError as e:
            if e.detail == "bad_hostname":  # le serveur est l'autorité : on affiche son message
                ui.error(e.message or "Ce nom est refusé par le serveur.")
                continue
            raise Stop(fail(ui, base, token, "deploy-start", e.user_message(), dry_run))


def run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(CONFIG_PATH)
        ui = UI(ascii_only=bool(cfg["ascii_only"]), penalty_seconds=int(cfg["penalty_seconds"]), dry_run=args.dry_run)
    except (OSError, ValueError, TypeError) as e:
        print(f"Le fichier client.json est illisible ({e}). Corrigez-le puis relancez.")
        return 1
    base = (args.server or cfg["server_url"] or "").rstrip("/")
    ui.clear()
    ui.title("Installation automatique des postes")
    if not base:
        ui.error("Adresse du serveur inconnue : renseignez « server_url » dans client.json ou utilisez --server.")
        return 1
    if args.dry_run:
        ui.say("MODE SIMULATION (--dry-run) : aucune commande destructrice ne sera exécutée.", YELLOW)

    ui.say("Attente du réseau et du serveur...")
    if not api.wait_ready(base, on_wait=lambda i: ui.say("  ...le serveur ne répond pas encore, nouvel essai.")):
        ui.error(f"Le serveur {base} ne répond pas. Vérifiez le câble réseau, puis relancez.")
        return 1

    hw = probe.detect(args.simulate, args.hardware_json)
    ui.say(f"Poste détecté : {hw['manufacturer'] or '?'} {hw['model'] or ''} (n° de série {hw['serial'] or 'inconnu'})")

    while True:  # un tour = une tentative complète ; on recommence si la personne renonce
        sess = login(ui, base, hw)
        token, room, image = sess["token"], sess["room"], sess["image"]
        ui.say(f"Salle : {room.get('label', room.get('id', '?'))} - Image : {image.get('label', image.get('id', '?'))}")

        if not windows_install.supported(image.get("os_family")):
            return fail(ui, base, token, "os-family", "Cette famille de système (" + str(image.get("os_family"))
                        + ") n'est pas encore prise en charge par le client. Rien n'a été modifié.", args.dry_run)
        if not ui.show_assessment(sess["assessment"]):
            ui.say("D'accord : on recommence depuis le début.")
            continue
        if image.get("kind") == "clonezilla":  # image Clonezilla : se restaure depuis le mini-système Linux
            if not sys.platform.startswith("linux") and not args.dry_run:
                return fail(ui, base, token, "platform", "Une image Clonezilla se restaure depuis le mini-système "
                            "Linux, pas depuis ce système. Essayez avec --dry-run.", args.dry_run)
        elif sys.platform != "win32" and not args.dry_run:
            return fail(ui, base, token, "platform", "L'installation d'une image Windows (.wim) n'est possible que "
                        "depuis WinPE. Essayez avec --dry-run.", args.dry_run)
        target = ask_target(ui, base, sess, hw, args.dry_run)
        if target is not None:
            break
        ui.say("On recommence depuis le début.")
    start, disk = target

    try:
        if start.get("kind") == "clonezilla":
            linux_restore.restore(ui, start, disk, hw["firmware"], token, args.dry_run)
        else:
            windows_install.install(ui, start, disk, hw["firmware"], token, args.dry_run)
    except Exception as exc:  # toute erreur d'étape : message clair + rapport + code 1
        step, message = explain(exc)
        return fail(ui, base, token, step, message, args.dry_run)

    if not args.dry_run:
        api.report(base, token, "success")
    ui.say()
    ui.say("Installation terminée. Le PC va redémarrer.", "92")
    ui.power("reboot")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if (args.simulate or args.hardware_json) and not args.dry_run:
        # Garde-fou : un matériel factice ne doit jamais servir à effacer un vrai disque.
        print("--simulate et --hardware-json exigent --dry-run.", file=sys.stderr)
        return 1
    try:
        return run(args)
    except Stop as stop:
        return stop.code
    except (KeyboardInterrupt, EOFError):
        print("\nInterrompu.")
        return 1
    except Exception as exc:  # bogue du client : on affiche tout pour le dépannage
        traceback.print_exc()
        print(f"\nErreur inattendue du client ({type(exc).__name__}). Un shell de dépannage va s'ouvrir.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
