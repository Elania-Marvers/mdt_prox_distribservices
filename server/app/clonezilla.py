"""Images Clonezilla : lecture d'un dossier d'image (`ocs-sr savedisk`) et comparaison PC / image.

Un dossier d'image contient : `disk` (ex. « sdb »), `parts`, `Info-dmi.txt` (matériel d'ORIGINE),
`<disque>-gpt.sgdisk` (taille du disque d'origine) et un fichier `<partition>.<fs>-ptcl-img.*` par
partition. On ne lit que les petits fichiers d'information : les gros fichiers ne sont jamais ouverts.

Ce module ne dépend ni de FastAPI ni de hardware.py (hardware.assess l'appelle).
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PTCL = re.compile(r"-ptcl-img(?:\.|$)")          # sdb3.ntfs-ptcl-img.zst, nvme0n1p2.dd-ptcl-img.gz…
_MAX_INFO = 1024 * 1024                            # on ne lit jamais plus de 1 Mo d'un fichier d'information
# Une restauration Clonezilla échoue dès que le disque cible est plus petit que l'original, même de quelques
# Mo (la partition de récupération est à la fin du disque). Quand le client donne la taille EXACTE
# (disks[].size_bytes), la comparaison est donc stricte ; quand il ne donne que des Go arrondis (size_gb),
# on ne refuse que si même en arrondissant vers le haut (+0,5 Go) le disque serait trop petit.
GB_ROUNDING = 500_000_000


def _read(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            return f.read(_MAX_INFO).decode("utf-8", errors="replace")
    except OSError:
        return ""


def read_image_dir(path: Path) -> Optional[dict]:
    """Description du dossier d'image, ou None s'il n'existe pas ou n'a pas l'air d'une image Clonezilla.

    {"name", "files": [{"name", "size"}], "size_bytes", "disk", "parts",
     "source_manufacturer", "source_model", "source_disk_bytes"}   (None quand l'information manque)
    """
    try:
        entries = sorted(p for p in Path(path).iterdir() if p.is_file() and not p.name.startswith("."))
    except OSError:
        return None
    names = {p.name for p in entries}
    disk = _read(Path(path) / "disk").strip() if "disk" in names else ""
    if not entries or "parts" not in names or not re.fullmatch(r"[a-z0-9]+", disk) \
            or not any(_PTCL.search(n) for n in names):
        return None
    sizes = [(p.name, p.stat().st_size) for p in entries]
    dmi = _read(Path(path) / "Info-dmi.txt")
    return {
        "name": Path(path).name,
        "files": [{"name": n, "size": s} for n, s in sizes],
        "size_bytes": sum(s for _, s in sizes),
        "disk": disk,
        "parts": _read(Path(path) / "parts").split(),
        "source_manufacturer": _dmi_field(dmi, "Manufacturer"),
        "source_model": _dmi_field(dmi, "Product Name"),
        "source_disk_bytes": _source_disk_bytes(Path(path), disk),
    }


def _dmi_field(dmi: str, field: str) -> Optional[str]:
    """Champ du bloc « System Information » de dmidecode (le bloc « Base Board » a aussi un Product Name)."""
    block = re.search(r"^System Information[^\n]*\n((?:[ \t].*\n?)+)", dmi, re.M)
    if not block:
        return None
    m = re.search(r"^[ \t]*%s:[ \t]*(.+?)[ \t]*$" % re.escape(field), block.group(1), re.M)
    return m.group(1) if m and m.group(1).strip() else None


def _source_disk_bytes(path: Path, disk: str) -> Optional[int]:
    sg = _read(path / (disk + "-gpt.sgdisk"))
    sectors = re.search(r"^Disk /dev/\S+: (\d+) sectors", sg, re.M)
    sector = re.search(r"Sector size \(logical/physical\): (\d+)/", sg)
    if sectors:
        return int(sectors.group(1)) * (int(sector.group(1)) if sector else 512)
    pt = re.search(r"^Disk /dev/\S+: (\d+)s\b", _read(path / (disk + "-pt.parted")), re.M)
    return int(pt.group(1)) * 512 if pt else None  # Limite connue : disques MBR (sfdisk) non lus ; toutes les images actuelles sont GPT


# --------------------------------------------------------------------------
# Comparaison du modèle du PC avec celui de l'image
# --------------------------------------------------------------------------

_NOISE = {"hp", "hewlett", "packard", "inc", "corporation", "corp", "pc", "dell", "lenovo", "the"}
_FORM_FACTORS = (  # (nom, motif) ; « mt » avant « twr » pour que « microtower » soit un MT
    ("sff", r"\bsff\b|small form factor"),
    ("mt", r"\bmt\b|micro ?tower"),
    ("twr", r"\btwr\b|\btower\b"),
    ("dm", r"\bdm\b|desktop mini"),
    ("aio", r"\baio\b|all in one"),
)


def parse_model(text: Any) -> Dict[str, Any]:
    """Découpe « HP Exemple 600 G3 MT » en famille / numéro / génération / format."""
    low = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()
    words = [w for w in low.split() if w not in _NOISE]
    num = re.search(r"\b(\d{3,4}) g(\d{1,2})\b", low)
    family = next((w for w in words if w.isalpha() and len(w) > 3
                   and w not in ("small", "form", "factor", "micro", "tower", "desktop", "mini")), None)
    return {
        "norm": " ".join(words),
        "family": family,
        "number": num.group(1) if num else None,
        "gen": num.group(2) if num else None,
        "ff": next((name for name, rx in _FORM_FACTORS if re.search(rx, low)), None),
    }


def _maker(text: Any) -> Optional[str]:
    """Premier mot du constructeur, « Hewlett-Packard » et « HP Inc. » comptant pour « hp »."""
    words = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split()
    return ("hp" if words[0] == "hewlett" else words[0]) if words else None


def compare_models(source_model: Any, source_maker: Any, pc_model: Any, pc_maker: Any) -> str:
    """« same » | « variant » (même modèle, autre format : SFF / MT) | « different » | « unknown »."""
    a, b = parse_model(source_model), parse_model(pc_model)
    if not a["norm"] or not b["norm"]:
        return "unknown"
    maker_a, maker_b = _maker(source_maker), _maker(pc_maker)
    if maker_a and maker_b and maker_a != maker_b:
        return "different"
    if a["norm"] == b["norm"]:
        return "same"
    key_a, key_b = (a["family"], a["number"], a["gen"]), (b["family"], b["number"], b["gen"])
    if all(key_a) and all(key_b):
        if key_a != key_b:
            return "different"
        return "variant" if a["ff"] and b["ff"] and a["ff"] != b["ff"] else "same"
    return "unknown"  # noms trop différents pour être comparés : on ne condamne pas sur une incertitude


# --------------------------------------------------------------------------
# Verdict sur un PC pour une image (appelé par hardware.assess)
# --------------------------------------------------------------------------

def _disk_bytes(disk: dict, generous: bool = False) -> Optional[int]:
    """Taille du disque en octets ; `generous` : lecture la plus favorable quand seul size_gb est connu."""
    exact = disk.get("size_bytes")
    if isinstance(exact, int) and not isinstance(exact, bool) and exact > 0:
        return exact
    gb = disk.get("size_gb")
    if isinstance(gb, (int, float)) and not isinstance(gb, bool) and gb > 0:
        return int(gb * 1e9) + (GB_ROUNDING if generous else 0)  # size_gb est en Go « commerciaux » (240 Go = 240e9 octets)
    return None


def pick_disk(hw: Dict[str, Any], name: Any = None) -> Optional[dict]:
    """Le disque nommé `name` s'il existe, sinon le plus grand disque du PC."""
    disks = [d for d in (hw.get("disks") if isinstance(hw.get("disks"), list) else []) if isinstance(d, dict)]
    chosen = [d for d in disks if name is not None and str(d.get("name")) == str(name)]
    sized = [d for d in (chosen or disks) if _disk_bytes(d)]
    return max(sized, key=_disk_bytes) if sized else None


def disk_problem(hw: Dict[str, Any], image: dict, disk_name: Any = None) -> Optional[str]:
    """Message (français) si le disque du PC est trop petit pour l'image, sinon None."""
    source = image.get("source_disk_bytes")
    disk = pick_disk(hw, disk_name)
    pc = _disk_bytes(disk, generous=True) if disk else None
    if not source or not pc or pc >= source:
        return None
    return ("Le disque du poste (%d Go) est plus petit que le disque d'origine de l'image (%d Go) : "
            "la restauration ne pourra pas tenir dessus." % (round(_disk_bytes(disk) / 1e9), round(source / 1e9)))


def check_pc(hw: Dict[str, Any], image: dict) -> Tuple[List[Tuple[str, str, bool]], bool]:
    """([(niveau, texte, simple incertitude ?)], bloquant ?) pour ce PC et cette image."""
    findings = []  # type: List[Tuple[str, str, bool]]
    source = image.get("source_model")
    if source:
        pc = hw.get("model") if isinstance(hw.get("model"), str) and hw["model"].strip() else None
        verdict = compare_models(source, image.get("source_manufacturer"), pc, hw.get("manufacturer")) if pc else "unknown"
        if verdict == "different":
            findings.append(("discouraged", "Cette image a été préparée pour un %s, et ce poste est un %s. "
                             "Les pilotes et le disque ne correspondent probablement pas : le poste risque de ne pas "
                             "démarrer ou de mal fonctionner." % (source, pc), False))
        elif verdict == "variant":
            findings.append(("warn", "Cette image a été préparée pour un %s ; ce poste (%s) est du même modèle "
                             "mais d'un autre format. Cela devrait fonctionner : vérifiez après l'installation."
                             % (source, pc), False))
        elif verdict == "unknown":
            findings.append(("warn", "Le modèle de ce poste (%s) n'a pas pu être comparé à celui pour lequel l'image "
                             "a été préparée (%s)." % (pc or "non lu", source), True))
    problem = disk_problem(hw, image)
    if problem:
        findings.append(("discouraged", problem, False))
    return findings, problem is not None
