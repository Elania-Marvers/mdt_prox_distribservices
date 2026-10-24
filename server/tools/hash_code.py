#!/usr/bin/env python3
"""Fabrique l'empreinte (hash) du code d'une salle, à coller dans profiles.yaml (champ code_hash).

Usage :  python3 server/tools/hash_code.py
Le code est demandé deux fois, sans rien afficher à l'écran. Seule l'empreinte est imprimée :
le code lui-même n'est écrit nulle part.
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # pour trouver le paquet « app »
from app.security import make_hash  # noqa: E402  (bibliothèque standard uniquement : pas besoin de FastAPI)


def main() -> int:
    code = getpass.getpass("Code de la salle (rien ne s'affiche) : ").strip()
    if not code:
        print("Le code ne peut pas être vide.", file=sys.stderr)
        return 1
    if getpass.getpass("Saisissez-le une deuxième fois : ").strip() != code:
        print("Les deux saisies sont différentes : recommencez.", file=sys.stderr)
        return 1
    print("Collez cette ligne dans profiles.yaml, comme valeur de code_hash :", file=sys.stderr)
    print(make_hash(code))
    return 0


if __name__ == "__main__":
    sys.exit(main())
