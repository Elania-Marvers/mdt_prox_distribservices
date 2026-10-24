#!/usr/bin/env python3
"""Inventaire des images Clonezilla d'un dossier + extrait de profiles.yaml à copier.

Usage :  python3 server/tools/scan_images.py /srv/deploy/images

Lecture seule : seuls les petits fichiers d'information (Info-dmi.txt, parts, *.sgdisk…) sont lus.
La famille d'OS est DEVINÉE d'après le nom du dossier (W11 -> windows11, W7 -> ignorée, sinon windows10) :
à relire avant de coller dans profiles.yaml.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.clonezilla import read_image_dir  # noqa: E402


def os_family(name: str):
    if re.search(r"(^|[-_])W7([-_]|$)", name, re.I):
        return None  # Windows 7 : hors périmètre du projet
    return "windows11" if re.search(r"W11", name, re.I) else "windows10"


def image_id(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()


def main(argv) -> int:
    if len(argv) != 2 or not Path(argv[1]).is_dir():
        print(__doc__)
        return 2
    rows, yaml = [], ["images:"]
    for d in sorted(p for p in Path(argv[1]).iterdir() if p.is_dir()):
        info = read_image_dir(d)
        if not info:
            continue
        family = os_family(d.name)
        rows.append((d.name, info["source_model"] or "?", info["source_disk_bytes"], info["size_bytes"], family))
        if family is None:
            yaml.append("  # %s : Windows 7, ignorée" % d.name)
            continue
        yaml.append("  %s: {label: %s, kind: clonezilla, dir: %s, os_family: %s}"
                    % (image_id(d.name), json.dumps(d.name, ensure_ascii=False), json.dumps(d.name, ensure_ascii=False), family))
    print("%-60s %-38s %8s %8s  %s" % ("dossier", "PC d'origine", "disque", "image", "OS"))
    for name, model, disk, size, family in rows:
        print("%-60s %-38s %6s Go %6.1f Go  %s" % (name[:60], model[:38], round(disk / 1e9) if disk else "?", size / 1e9, family or "-"))
    print("\n%d images Clonezilla trouvées.\n\n# --- à relire puis coller dans profiles.yaml (les salles et leurs codes restent à écrire) ---" % len(rows))
    print("\n".join(yaml))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
