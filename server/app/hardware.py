"""Jugement du matériel (règles A.7 du CDC).

assess(hw, os_family) est une fonction pure : elle ne plante jamais sur une valeur manquante ou
bizarre, et renvoie la structure `assessment` de la réponse /api/v1/session.
Les seuils sont dans assess() : c'est ici qu'il faut les modifier.
"""
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import clonezilla

OK, WARN, DISCOURAGED = "ok", "warn", "discouraged"
_RANK = {OK: 0, WARN: 1, DISCOURAGED: 2}
OS_NAMES = {"windows10": "Windows 10", "windows11": "Windows 11"}

# Les valeurs lues sont un peu inférieures aux valeurs commerciales (« 4 Go » annoncés = 3,5 Go lus,
# « 64 Go » = 59 Go lus) : on tolère 15 % avant de déclarer qu'un minimum n'est pas atteint.
TOLERANCE = 0.85


# --------------------------------------------------------------------------
# Lecture prudente des valeurs envoyées par le client
# --------------------------------------------------------------------------

def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _choice(value: Any, allowed: Tuple[str, ...]) -> Optional[str]:
    """La valeur (en minuscules) si elle fait partie des valeurs permises, sinon None (= inconnu)."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().lower()
    return text if text in allowed else None


def _text(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _year(value: Any) -> Optional[int]:
    """Année d'une date de BIOS : « 2017-05-01 », « 20170501 », « 05/01/2017 »…"""
    text = _text(value)
    if not text:
        return None
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text) or re.match(r"((?:19|20)\d{2})\d{4}$", text)
    return int(match.group(1)) if match else None


def _ordinal(n: int) -> str:
    return "1ʳᵉ" if n == 1 else "%dᵉ" % n


def _gb(megabytes: float) -> str:
    gb = megabytes / 1024
    return "%d Go" % round(gb) if gb >= 1.5 else "%d Mo" % round(megabytes)


def _below(value: float, minimum: float) -> bool:
    return value < minimum * TOLERANCE


# --------------------------------------------------------------------------
# Processeur
# --------------------------------------------------------------------------

@dataclass
class Cpu:
    label: str = ""                # nom nettoyé, pour l'affichage
    known: bool = False            # False = nom absent ou non reconnu
    gen: Optional[int] = None      # génération Intel (Core 2 = 0 ; équivalent pour Celeron/Pentium/Atom)
    zen: Optional[int] = None      # Ryzen : 1 pour la série 1000, 2 pour la 2000, etc.
    amd_old: bool = False          # AMD d'avant Ryzen (A10, Athlon II, FX…)
    i3: bool = False
    entry_level: bool = False      # Celeron / Pentium / Atom
    show_gen: bool = False         # afficher « (8ᵉ génération) » à côté du nom

    @property
    def text(self) -> str:
        """Pour la liste des caractéristiques : « Intel Core i3-8100 (8ᵉ génération) »."""
        return "%s (%s génération)" % (self.label, _ordinal(self.gen)) if self.show_gen and self.gen else self.label

    @property
    def short(self) -> str:
        """Pour l'intérieur d'une phrase entre parenthèses : « Intel Core i3-8100, 8ᵉ génération »."""
        return "%s, %s génération" % (self.label, _ordinal(self.gen)) if self.show_gen and self.gen else self.label


def _clean_cpu_name(raw: str) -> str:
    name = re.sub(r"\((?:R|TM|r|tm)\)", "", raw)
    name = re.sub(r"^\s*\d+(?:st|nd|rd|th) Gen\s+", "", name, flags=re.I)
    name = re.sub(r"@.*$|\bw/.*$|,.*$", "", name)
    name = re.sub(r"\b\d+-Core\b|\bProcessor\b|\bCPU\b", "", name, flags=re.I)
    name = re.sub(r"\bCore2\b", "Core 2", name)
    return re.sub(r"\s+", " ", name).strip()


def _entry_level_gen(label: str) -> Optional[int]:
    """Génération équivalente d'un Celeron / Pentium / Atom, d'après son numéro. None si on ne sait pas."""
    low = label.lower()
    if re.search(r"pentium (?:4|d|iii|ii|m)\b|dual-core", low):
        return 0
    if "atom" in low:
        return 11 if re.search(r"\bx6\d{3}", low) else 0  # seuls les Atom x6000 sont récents
    m = re.search(r"\bG(\d{3,4})\b", label)  # Pentium/Celeron « G » : G530, G1610, G4560, G5400…
    if m:
        digits = m.group(1)
        if len(digits) == 3:
            return 2
        number = int(digits)
        for limit, gen in ((3000, 3), (3900, 4), (4540, 6), (4900, 7), (5900, 8), (6900, 10)):
            if number < limit:
                return gen
        return 12
    m = re.search(r"\b[NJ](\d{3,4})\b", label)  # Celeron/Pentium Silver « N » et « J » : N4020, J4105…
    if m:
        digits = m.group(1)
        return 0 if len(digits) == 3 else (5 if digits[0] in "123" else 8)
    if re.search(r"\b[EBTP]\d{3,4}\b", label):  # E5300, B960, T4500… : Core 2 / 2ᵉ génération
        return 1
    return None


def parse_cpu(name: Any) -> Cpu:
    """Reconnaît un nom de processeur Intel ou AMD. Ne lève jamais d'exception."""
    raw = _text(name)
    if not raw:
        return Cpu()
    label = _clean_cpu_name(raw)
    low = label.lower()
    cpu = Cpu(label=label)

    # --- AMD
    if "ryzen" in low:
        m = re.search(r"ryzen\b.*?\b(\d{4})(?=\D|$)", low)
        cpu.zen = int(m.group(1)) // 1000 if m else 8  # sans numéro à 4 chiffres : nommage récent (Ryzen AI…)
        cpu.known = True
        return cpu
    if re.search(r"athlon\W+(?:(?:silver|gold|pro)\W+)?3\d{3}[a-z]", low):  # Athlon 3000G, 3150U… (Zen)
        cpu.zen, cpu.known = 3, True
        return cpu
    if re.search(r"athlon|sempron|phenom|turion|\bfx-|\ba(?:4|6|8|10|12)-\d|\be[12]?-\d{3}", low):
        cpu.amd_old = cpu.known = True
        return cpu
    if re.search(r"\bamd\b", low):  # autre AMD (EPYC…) : on ne sait pas juger
        return cpu

    # --- Intel
    if re.search(r"core ultra", low):
        cpu.gen, cpu.known = 14, True
        return cpu
    if re.search(r"\bcore 2\b", low):
        cpu.gen, cpu.known = 0, True
        return cpu
    m = re.search(r"\bi([3579])(?:-|\s+)(\d{3,5})", low)
    if m:
        digits = m.group(2)
        prefix = re.match(r"\s*(\d+)(?:st|nd|rd|th) gen", raw, re.I)  # « 12th Gen Intel… » : génération écrite
        if prefix:
            cpu.gen = int(prefix.group(1))
        elif len(digits) == 3:
            cpu.gen = 1
        else:
            cpu.gen = int(digits[:2]) if len(digits) == 5 or digits[0] == "1" else int(digits[0])
        cpu.known, cpu.show_gen, cpu.i3 = True, True, m.group(1) == "3"
        return cpu
    if re.search(r"celeron|pentium|atom", low):
        cpu.entry_level, cpu.known = True, True
        cpu.gen = _entry_level_gen(label)
    return cpu


# --------------------------------------------------------------------------
# Le jugement
# --------------------------------------------------------------------------

def assess(hw: Dict[str, Any], os_family: str, image: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Juge un PC pour une famille d'OS (« windows10 » ou « windows11 »).

    `image` (facultatif) : la description d'une image Clonezilla (clonezilla.read_image_dir). On juge alors
    aussi le modèle du PC et la taille de son disque par rapport à ceux de l'image."""
    if os_family not in OS_NAMES:
        raise ValueError("os_family inconnue : %r" % (os_family,))
    hw = hw if isinstance(hw, dict) else {}
    os_name = OS_NAMES[os_family]
    win11 = os_family == "windows11"

    cpu = parse_cpu(hw.get("cpu_name"))
    ram_mb = _number(hw.get("ram_mb"))
    tpm = _choice(hw.get("tpm"), ("2.0", "1.2", "none"))
    firmware = _choice(hw.get("firmware"), ("uefi", "bios"))
    bios_year = _year(hw.get("bios_date"))
    # Limite connue : on juge le 1er disque de la liste (le client choisit le disque à effacer plus tard) ;
    # évolution : rejuger avec le disque choisi dans deploy/start.
    disks = [(_number(d.get("size_gb")), _choice(d.get("kind"), ("ssd", "hdd")))
             for d in (hw.get("disks") if isinstance(hw.get("disks"), list) else []) if isinstance(d, dict)]
    disk_gb, disk_kind = disks[0] if disks else (None, None)

    findings = []  # type: List[Tuple[str, str, bool]]  (niveau, texte, simple incertitude ?)
    unknown = []   # type: List[str]

    def add(level: str, text: str, uncertain: bool = False) -> None:
        findings.append((level, text, uncertain))

    # --- Processeur
    if not cpu.known:
        add(WARN, "Le processeur n'a pas été reconnu%s : impossible de dire s'il convient à %s."
            % (" (« %s »)" % cpu.label if cpu.label else "", os_name), uncertain=True)
    elif win11:
        if cpu.gen is not None and cpu.gen < 8:
            add(DISCOURAGED, "Le processeur (%s) est antérieur à la 8ᵉ génération Intel, la plus ancienne "
                "que Windows 11 prenne officiellement en charge." % cpu.short)
        elif cpu.zen is not None and cpu.zen < 2:
            add(DISCOURAGED, "Le processeur (%s) est de la série Ryzen 1000 ; Windows 11 demande au moins "
                "la série 2000." % cpu.short)
        elif cpu.amd_old:
            add(DISCOURAGED, "Le processeur (%s) est plus ancien que la gamme Ryzen, "
                "que Windows 11 exige au minimum." % cpu.short)
        elif cpu.entry_level and cpu.gen is None:
            add(WARN, "Le processeur (%s) est un modèle d'entrée de gamme dont l'âge n'a pas pu être "
                "identifié : Windows 11 risque d'être juste." % cpu.short)
        elif cpu.i3:
            add(WARN, "Le processeur (%s) est un Core i3, de la gamme d'entrée : Windows 11 fonctionnera "
                "mais les performances seront limitées." % cpu.short)
    else:
        if cpu.gen is not None and cpu.gen < 4:
            add(DISCOURAGED, "Le processeur (%s) est antérieur à la 4ᵉ génération Intel : c'est vraiment "
                "ancien, même pour Windows 10." % cpu.short)
        elif cpu.entry_level:
            add(WARN, "Le processeur (%s) est de la gamme d'entrée (Celeron, Pentium ou Atom) : Windows 10 "
                "fonctionnera, mais lentement." % cpu.short)

    # --- Mémoire
    ram_text = _gb(ram_mb) if ram_mb else "inconnue"
    if ram_mb is None:
        unknown.append("mémoire")
    else:
        minimum, comfortable = (4, 8) if win11 else (2, 4)
        if _below(ram_mb, minimum * 1024):
            add(DISCOURAGED, "La mémoire (%s) est inférieure au minimum de %d Go pour %s." % (ram_text, minimum, os_name))
        elif _below(ram_mb, comfortable * 1024):
            add(WARN, "La mémoire (%s) est un peu limitée : %s sera plus à l'aise avec au moins %d Go."
                % (ram_text, os_name, comfortable))

    # --- Disque
    if disk_gb is None:
        unknown.append("disque")
    else:
        minimum = 64 if win11 else 32
        if _below(disk_gb, minimum):
            add(DISCOURAGED, "Le disque (%d Go) est trop petit : %s demande au moins %d Go." % (round(disk_gb), os_name, minimum))
    if disk_kind == "hdd":
        add(WARN, "Le disque est mécanique (HDD) : le démarrage et les mises à jour seront nettement "
            "plus lents qu'avec un SSD.")

    # --- Image Clonezilla : le PC est-il celui pour lequel l'image a été faite ?
    image_texts, blocked = set(), False
    if image:
        image_findings, blocked = clonezilla.check_pc(hw, image)
        for level, text, uncertain in image_findings:
            add(level, text, uncertain)
            image_texts.add(text)

    # --- Démarrage et TPM (exigés par Windows 11 uniquement)
    if win11:
        if firmware is None:
            unknown.append("mode de démarrage")
        elif firmware == "bios":
            add(DISCOURAGED, "Ce poste démarre en BIOS classique (non UEFI), alors que Windows 11 demande l'UEFI.")
        if tpm is None:
            unknown.append("TPM")
        elif tpm != "2.0":
            add(DISCOURAGED, "Ce poste n'a %s, que Windows 11 exige."
                % ("pas de puce TPM" if tpm == "none" else "qu'un TPM 1.2 (il faut la version 2.0)"))

    # --- Âge du BIOS (indice de l'âge de la machine)
    if bios_year is not None:
        if win11 and bios_year <= 2016:
            add(DISCOURAGED, "Le BIOS date de %d : le poste est ancien pour Windows 11." % bios_year)
        elif win11 and bios_year <= 2018:
            add(WARN, "Le BIOS date de %d : le poste commence à dater pour Windows 11." % bios_year)
        elif not win11 and bios_year <= 2012:
            add(DISCOURAGED, "Le BIOS date de %d : le poste est très ancien, même pour Windows 10." % bios_year)

    if unknown:
        add(WARN, "Certaines informations du poste n'ont pas pu être lues (%s) : le verdict est incomplet."
            % ", ".join(unknown), uncertain=True)

    findings.sort(key=lambda f: -_RANK[f[0]])  # les motifs les plus graves d'abord
    level = findings[0][0] if findings else OK
    only_unknown = bool(findings) and all(uncertain for _, _, uncertain in findings)
    titles = {
        OK: "Ce poste convient bien à %s." % os_name,
        WARN: ("Ce poste n'a pas pu être vérifié entièrement pour %s." if only_unknown
               else "Ce poste est un peu juste pour %s.") % os_name,
        DISCOURAGED: "%s n'est pas vraiment adapté à ce poste." % os_name,
    }
    if findings and findings[0][1] in image_texts and not only_unknown:  # le motif principal vient de l'image
        titles[level] = {WARN: "Cette image est prévue pour un format de poste un peu différent.",
                         DISCOURAGED: "Cette image n'a pas été préparée pour ce poste."}[level]
    questions = {
        OK: None,
        WARN: "Êtes-vous sûr(e) de votre choix ?",
        DISCOURAGED: "Cette installation est fortement déconseillée sur ce poste. Êtes-vous vraiment sûr(e) de votre choix ?",
    }
    return {
        "level": level,
        "title": titles[level],
        "reasons": [text for _, text, _ in findings],
        "specs": _specs(hw, cpu, ram_mb, ram_text, disks, firmware, tpm, bios_year),
        "question": questions[level],
        "blocked": blocked,
    }


def _specs(hw, cpu, ram_mb, ram_text, disks, firmware, tpm, bios_year) -> List[str]:
    """Les caractéristiques réelles du PC, en français, pour l'affichage."""
    specs = []
    maker, model = _text(hw.get("manufacturer")), _text(hw.get("model"))
    if model:
        specs.append("Modèle : " + (model if not maker or model.lower().startswith(maker.lower()) else "%s %s" % (maker, model)))
    specs.append("Processeur : " + (cpu.text or "non détecté"))
    specs.append("Mémoire : " + (ram_text if ram_mb else "non détectée"))
    if disks:
        kinds = {"ssd": "SSD", "hdd": "mécanique", None: "type inconnu"}
        for size, kind in disks:
            specs.append("Disque : %s (%s)" % ("%d Go" % round(size) if size else "taille inconnue", kinds[kind]))
    else:
        specs.append("Disque : non détecté")
    specs.append("Démarrage : " + {"uefi": "UEFI", "bios": "BIOS classique"}.get(firmware, "non déterminé"))
    specs.append("TPM : " + {"2.0": "2.0", "1.2": "1.2", "none": "absent"}.get(tpm, "non déterminé"))
    if bios_year:
        specs.append("BIOS : daté de %d" % bios_year)
    return specs
