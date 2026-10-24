"""Affichage et saisie en mode texte (console WinPE). Aucune dépendance.

Tout ce que voit le technicien passe par la classe UI. Messages en français, courts.
"""
from __future__ import annotations

import ctypes
import getpass
import os
import re
import subprocess
import sys
import time
import unicodedata

EGG_MESSAGE = "Allez vous faire cuire un œuf"
EGG_ART = r"""
        .-~~~~~-.
      .'   .-.   '.
     /    (   )    \
     \     '-'     /
      '.         .'
        '-.___.-'
"""
RED, GREEN, YELLOW, CYAN = "91", "92", "93", "96"
LEVEL_COLOR = {"ok": GREEN, "warn": YELLOW, "discouraged": RED}

_FOLD = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "’": "'", "‘": "'", "«": '"',
                       "»": '"', "–": "-", "—": "-", "…": "...", " ": " ", " ": " "})


def ascii_fold(text: str) -> str:
    """œ -> oe, é -> e, etc. (pour les consoles dont la police n'a pas les accents)."""
    return unicodedata.normalize("NFKD", text.translate(_FOLD)).encode("ascii", "ignore").decode("ascii")


def hostname_error(name: str) -> str | None:
    """Règle A.6 : 1 à 15 caractères, lettres/chiffres/tiret, pas de tiret en tête, pas que des chiffres.
    Renvoie un message en français, ou None si le nom est valide (le serveur reste l'autorité)."""
    if not name:
        return "Le nom ne peut pas être vide."
    if len(name) > 15:
        return "Le nom est trop long : 15 caractères au maximum."
    if not re.fullmatch(r"[A-Za-z0-9-]+", name):
        return "Le nom ne peut contenir que des lettres (sans accent), des chiffres et des tirets."
    if name[0] == "-" or name[-1] == "-":
        return "Le nom ne peut pas commencer ni finir par un tiret."
    if name.isdigit():
        return "Le nom ne peut pas être composé uniquement de chiffres."
    return None


def disk_label(d: dict) -> str:
    size = f"{d['size_gb']} Go" if d.get("size_gb") else "taille inconnue"
    kind = {"ssd": ", SSD", "hdd": ", disque dur"}.get(d.get("kind"), "")
    return f"disque {d['name']} ({size}{kind})"


def _ansi_supported() -> bool:
    """Couleurs seulement si la console les accepte (sinon texte brut)."""
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        try:  # À VÉRIFIER SUR BANC : console WinPE et séquences ANSI
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = k32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            return bool(k32.GetConsoleMode(handle, ctypes.byref(mode))
                        and k32.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:
            return False
    return os.environ.get("TERM", "") not in ("", "dumb")


def _read_masked(prompt: str) -> str:
    """Saisie Windows : chaque caractère tapé est remplacé par « * »."""
    import msvcrt  # À VÉRIFIER SUR BANC : console WinPE
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars: list[str] = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            break
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x00", "\xe0"):  # touche spéciale (flèches…) : 2 caractères à ignorer
            msvcrt.getwch()
        elif ch == "\b":
            if chars:
                chars.pop()
                sys.stdout.write("\b \b")
        else:
            chars.append(ch)
            sys.stdout.write("*")
        sys.stdout.flush()
    sys.stdout.write("\n")
    return "".join(chars)


class UI:
    def __init__(self, ascii_only: bool = False, penalty_seconds: int = 90, dry_run: bool = False):
        self.ascii_only = ascii_only
        self.penalty_seconds = penalty_seconds
        self.dry_run = dry_run
        self.color = _ansi_supported()
        self._progress = None  # (instant de départ, octets de départ, dernier affichage)

    # ---- affichage ------------------------------------------------------

    def _t(self, text: str) -> str:
        return ascii_fold(text) if self.ascii_only else text

    def say(self, text: str = "", color: str | None = None) -> None:
        text = self._t(text)
        if color and self.color:
            text = f"\033[{color}m{text}\033[0m"
        try:
            print(text)
        except UnicodeEncodeError:  # console sans les accents : on se rabat sur l'ASCII
            print(ascii_fold(text))

    def error(self, text: str) -> None:
        self.say(text, RED)

    def clear(self) -> None:
        if os.name == "nt":
            os.system("cls")
        elif self.color:
            print("\033[2J\033[H", end="")

    def title(self, text: str) -> None:
        self.say("=" * 64, CYAN)
        self.say("  " + text, CYAN)
        self.say("=" * 64, CYAN)

    def step(self, n: int, total: int, text: str) -> None:
        self.say()
        self.say(f"[{n}/{total}] {text}", CYAN)

    def _overwrite(self, text: str) -> None:
        sys.stdout.write("\r" + self._t(text).ljust(72))
        sys.stdout.flush()

    # ---- saisie ---------------------------------------------------------

    def ask(self, prompt: str) -> str:
        return input(self._t(prompt))

    def ask_secret(self, prompt: str) -> str:
        """Saisie masquée. Sans vrai clavier (tests, entrée redirigée) : simple input()."""
        prompt = self._t(prompt)
        if sys.stdin.isatty():
            return _read_masked(prompt) if os.name == "nt" else getpass.getpass(prompt)
        return input(prompt)

    def yes_no(self, question: str) -> bool:
        """Question oui/NON : la réponse par défaut (Entrée) est NON."""
        return self.ask(f"{question} (oui/NON) : ").strip().lower() in ("oui", "o")

    def ask_code(self) -> str:
        self.say()
        return self.ask_secret("Code de la salle : ")

    # ---- pénalité -------------------------------------------------------

    def penalty(self) -> None:
        """Message ferme, compte à rebours avec l'œuf, puis extinction du poste."""
        self.say()
        self.say("Il semblerait que vous tentiez de forcer le passage.", RED)
        self.say("Merci de renoncer : ce poste ne sera pas installé.", RED)
        self.say(EGG_ART)
        for left in range(self.penalty_seconds, 0, -1):
            self._overwrite(f"   {left // 60:02d}:{left % 60:02d}   {EGG_MESSAGE}")
            time.sleep(1)
        self._overwrite(f"   00:00   {EGG_MESSAGE}")
        print()
        self.power("shutdown")

    def power(self, action: str) -> None:
        """Éteint ou redémarre le poste (action = "shutdown" ou "reboot")."""
        word = "Extinction" if action == "shutdown" else "Redémarrage"
        if self.dry_run:
            self.say(f"[SIMULATION] {word} du poste : non exécuté.", YELLOW)
            return
        if sys.platform == "win32":  # wpeutil n'existe que dans WinPE ; repli sur shutdown
            flag = "/s" if action == "shutdown" else "/r"
            commands = [["wpeutil", action], ["shutdown", flag, "/t", "0"]]
        elif sys.platform.startswith("linux"):
            commands = [["poweroff" if action == "shutdown" else "reboot"]]
        else:  # on ne touche jamais au Mac du développeur
            self.say(f"({word} non pris en charge sur ce système.)")
            return
        for cmd in commands:
            try:
                if subprocess.run(cmd).returncode == 0:
                    return
            except OSError:
                continue

    # ---- déroulé --------------------------------------------------------

    def show_assessment(self, a: dict) -> bool:
        """Affiche le verdict du serveur. True = on continue ; False = la personne renonce."""
        level = a.get("level", "ok")
        color = LEVEL_COLOR.get(level)
        self.say()
        self.say("-" * 64, color)
        self.say("  " + (a.get("title") or "Verdict sur le matériel"), color)
        self.say("-" * 64, color)
        for reason in a.get("reasons") or []:
            self.say("  - " + reason)
        if a.get("specs"):
            self.say()
            self.say("  Caractéristiques du poste :")
            for spec in a["specs"]:
                self.say("    * " + spec)
        if level == "ok":
            return True
        self.say()
        if a.get("blocked"):  # ex. disque trop petit pour l'image : inutile de proposer de continuer
            self.error("Cette installation est impossible sur ce poste (voir ci-dessus). Rien n'a été modifié.")
            return False
        return self.yes_no(a.get("question") or "Êtes-vous sûr(e) de votre choix ?")

    def ask_hostname(self, prefix: str, example: str) -> str:
        self.say()
        self.say(f"Nom du PC : doit commencer par {prefix}  (exemple : {example})")
        self.say("1 à 15 caractères : lettres, chiffres et tirets, pas uniquement des chiffres.")
        while True:
            name = self.ask("Nom du PC : ").strip()
            problem = hostname_error(name)
            if problem is None:
                return name
            self.error(problem)

    def choose_disk(self, disks: list) -> dict | None:
        if not disks:
            return None
        if len(disks) == 1:
            self.say()
            self.say(f"Disque cible : {disk_label(disks[0])}")
            return disks[0]
        self.say()
        self.say("Plusieurs disques ont été détectés :")
        for d in disks:
            self.say("  - " + disk_label(d))
        names = [d["name"] for d in disks]
        while True:
            answer = self.ask("Numéro du disque sur lequel installer : ").strip()
            if answer in names:
                return disks[names.index(answer)]
            self.error(f"Choisissez un numéro parmi : {', '.join(names)}.")

    def final_confirm(self, room: dict, image: dict, hostname: str, disk: dict) -> bool:
        """Dernière chance : seul « OUI » en majuscules lance l'installation."""
        self.say()
        self.title("Récapitulatif")
        self.say(f"  Salle   : {room.get('id', '?')} - {room.get('label', '')}")
        self.say(f"  Image   : {image.get('label', image.get('id', '?'))}")
        self.say(f"  Nom     : {hostname}")
        self.say(f"  Disque  : {disk_label(disk)}")
        self.say()
        self.say(f"Le disque {disk['name']} sera ENTIÈREMENT EFFACÉ.", RED)
        if self.ask("Tapez OUI (en majuscules) pour continuer : ").strip() == "OUI":
            return True
        self.say("Annulé : rien n'a été modifié sur le PC.")
        return False

    def progress(self, done: int, total: int) -> None:
        """Barre de progression du téléchargement : pourcentage et débit en Mo/s."""
        now = time.monotonic()
        if self._progress is None or done < self._progress[1]:  # début, ou reprise depuis zéro
            self._progress = (now, done, now - 1)
        start, start_done, last = self._progress
        if now - last < 0.5 and done < total:
            return
        self._progress = (start, start_done, now)
        speed = (done - start_done) / max(now - start, 0.001) / 1e6
        pct = f"{done * 100 // total:3d} %" if total else "  ? %"
        bar = "#" * (done * 30 // total) if total else ""
        mb = f"{done / 1e6:,.0f}".replace(",", " ")
        line = f"  [{bar:<30}] {pct}  {mb} Mo  {speed:.1f} Mo/s".replace(".", ",")
        self._overwrite(line)
        if total and done >= total:
            print()
            self._progress = None
