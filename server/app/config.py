"""Configuration : variables d'environnement (A.3) et fichier profiles.yaml (A.4).

Les messages d'erreur sont en français et destinés à l'administrateur (jamais de valeur secrète).
BIND_HOST / BIND_PORT ne sont pas lus ici : ce sont les options --host / --port d'uvicorn.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import yaml

from .security import make_hash, parse_hash, verify_code


class ConfigError(Exception):
    """Configuration invalide. str(exc) est un message clair en français."""


# --------------------------------------------------------------------------
# Variables d'environnement
# --------------------------------------------------------------------------

@dataclass
class Settings:
    public_base_url: str
    data_dir: Path
    domain_fqdn: str
    domain_join_user: str
    domain_join_password: str
    local_admin_user: str
    local_admin_password: str
    win_timezone: str
    win_locale: str
    max_bad_codes: int
    lockout_seconds: int
    admin_user: str = ""        # pages /admin : vide = désactivées
    admin_password: str = ""
    lab_mode: bool = False      # banc d'essai isolé : autorise les identifiants de démonstration

    @property
    def missing(self) -> List[str]:
        """Noms des variables obligatoires non renseignées (jamais leurs valeurs)."""
        required = [
            ("PUBLIC_BASE_URL", self.public_base_url),
            ("DOMAIN_FQDN", self.domain_fqdn),
            ("DOMAIN_JOIN_USER", self.domain_join_user),
            ("DOMAIN_JOIN_PASSWORD", self.domain_join_password),
            ("LOCAL_ADMIN_USER", self.local_admin_user),
            ("LOCAL_ADMIN_PASSWORD", self.local_admin_password),
        ]
        return [name for name, value in required if not value]


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise ConfigError("%s doit être un nombre entier supérieur à 0 (valeur lue : « %s »)." % (name, raw))
    return int(raw)


def read_env_file(path: str) -> Dict[str, str]:
    """Lit un fichier KEY="valeur" (le même format que le EnvironmentFile de systemd) : lignes # ignorées,
    \" et \\ permis dans les valeurs entre guillemets. Sert surtout sous Windows, où il n'y a pas de systemd."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        raise ConfigError("Fichier d'environnement %s introuvable ou illisible (variable MDTPROX_ENV_FILE)." % path) from None
    values = {}  # type: Dict[str, str]
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = re.sub(r'\\(["\\])', r"\1", value[1:-1])
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Lit les variables d'environnement. Une variable obligatoire absente n'empêche pas le démarrage
    (voir Settings.missing) : l'API répond alors 503 sur /session et /deploy/start.

    Si MDTPROX_ENV_FILE désigne un fichier, ses valeurs complètent l'environnement réel (qui l'emporte)."""
    env = dict(os.environ if env is None else env)
    if env.get("MDTPROX_ENV_FILE"):
        env = {**read_env_file(env["MDTPROX_ENV_FILE"]), **{k: v for k, v in env.items() if v != ""}}
    for name in ("DOMAIN_JOIN_USER", "DOMAIN_JOIN_PASSWORD", "LOCAL_ADMIN_USER", "LOCAL_ADMIN_PASSWORD"):
        if re.search(r"[\x00-\x1f]", env.get(name, "")):  # rendrait unattend.xml invalide, en silence
            raise ConfigError("%s contient un caractère de contrôle (retour à la ligne, tabulation…) : "
                              "vérifiez /etc/mdtprox.env." % name)

    def get(name: str, default: str = "") -> str:
        value = env.get(name, "")
        return value if value.strip() else default  # un mot de passe n'est jamais « nettoyé »

    return Settings(
        public_base_url=get("PUBLIC_BASE_URL").strip().rstrip("/"),
        data_dir=Path(get("DATA_DIR", "/srv/deploy")),
        domain_fqdn=get("DOMAIN_FQDN").strip(),
        domain_join_user=get("DOMAIN_JOIN_USER").strip(),
        domain_join_password=get("DOMAIN_JOIN_PASSWORD"),
        local_admin_user=get("LOCAL_ADMIN_USER").strip(),
        local_admin_password=get("LOCAL_ADMIN_PASSWORD"),
        win_timezone=get("WIN_TIMEZONE", "Romance Standard Time").strip(),
        win_locale=get("WIN_LOCALE", "fr-FR").strip(),
        max_bad_codes=_positive_int(env, "MAX_BAD_CODES", 3),
        lockout_seconds=_positive_int(env, "LOCKOUT_SECONDS", 300),
        admin_user=get("ADMIN_USER").strip(),
        admin_password=get("ADMIN_PASSWORD"),
        lab_mode=get("LAB_MODE").strip().lower() in ("1", "true", "oui", "yes"),
    )


# --------------------------------------------------------------------------
# profiles.yaml
# --------------------------------------------------------------------------

@dataclass
class Image:
    id: str
    label: str
    os_family: str                       # windows10 | windows11
    kind: str = "wim"                    # wim | clonezilla
    file: Optional[str] = None           # wim : nom du fichier dans DATA_DIR/images/
    wim_index: int = 1                   # wim : numéro de l'image dans le fichier
    dir: Optional[str] = None            # clonezilla : nom du dossier d'image dans DATA_DIR/images/
    source_model: Optional[str] = None   # clonezilla : remplace le modèle lu dans Info-dmi.txt


@dataclass
class Room:
    id: str
    label: str
    code_hash: str     # empreinte scrypt : seule chose utilisée pour VÉRIFIER un code
    image: str         # identifiant de l'image (clé de Profiles.images)
    name_prefix: str
    ou: Optional[str]
    code: Optional[str] = None  # le code en clair, facultatif : seulement pour l'AFFICHER aux administrateurs


@dataclass
class Profiles:
    images: Dict[str, Image]
    rooms: List[Room]


def _entry(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError("%s : une série de lignes « clé: valeur » est attendue." % where)
    return value


def _required(entry: dict, key: str, where: str) -> str:
    value = entry.get(key)
    if value is None or not str(value).strip():
        raise ConfigError("%s : la clé « %s » est obligatoire." % (where, key))
    return str(value).strip()


def _optional(entry: dict, key: str) -> Optional[str]:
    value = entry.get(key)
    return str(value).strip() if value is not None and str(value).strip() else None


def load_profiles(path: Path) -> Profiles:
    """Lit et vérifie profiles.yaml. Lève ConfigError (message en français) au moindre problème."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise ConfigError("Fichier %s introuvable ou illisible." % path) from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = " (ligne %d)" % (mark.line + 1) if mark else ""
        # On n'affiche que le « problème » de PyYAML, pas l'extrait de fichier.
        raise ConfigError("profiles.yaml : syntaxe YAML invalide%s : %s." % (line, getattr(exc, "problem", "erreur"))) from None
    if not isinstance(data, dict) or not isinstance(data.get("images"), dict) or not isinstance(data.get("rooms"), list):
        raise ConfigError("profiles.yaml doit contenir une section « images » (liste nommée) et une section « rooms » (liste).")

    images = {}  # type: Dict[str, Image]
    for image_id, raw in data["images"].items():
        image_id = str(image_id)
        where = "profiles.yaml, image « %s »" % image_id
        if not re.fullmatch(r"[A-Za-z0-9._-]+", image_id):
            raise ConfigError("%s : l'identifiant ne peut contenir que lettres, chiffres, point, tiret et underscore." % where)
        raw = _entry(raw, where)
        kind = _optional(raw, "kind") or "wim"
        if kind not in ("wim", "clonezilla"):
            raise ConfigError("%s : kind doit valoir wim ou clonezilla (lu : « %s »)." % (where, kind))
        os_family = _required(raw, "os_family", where)
        if os_family not in ("windows10", "windows11"):
            raise ConfigError("%s : os_family doit valoir windows10 ou windows11 (lu : « %s »)." % (where, os_family))
        label = _optional(raw, "label") or image_id
        if kind == "clonezilla":
            folder = _required(raw, "dir", where)
            if folder in (".", "..") or "/" in folder or "\\" in folder or folder.startswith("."):
                raise ConfigError("%s : « dir » doit être un simple nom de dossier de images/ (pas de chemin)." % where)
            images[image_id] = Image(image_id, label, os_family, kind="clonezilla", dir=folder,
                                     source_model=_optional(raw, "source_model"))
            continue
        file = _required(raw, "file", where)
        if file in (".", "..") or "/" in file or "\\" in file:
            raise ConfigError("%s : « file » doit être un simple nom de fichier du dossier images/ (pas de chemin)." % where)
        wim_index = raw.get("wim_index", 1)
        if isinstance(wim_index, bool) or not isinstance(wim_index, int) or wim_index < 1:
            raise ConfigError("%s : wim_index doit être un nombre entier supérieur ou égal à 1." % where)
        images[image_id] = Image(image_id, label, os_family, file=file, wim_index=wim_index)
    if not images:
        raise ConfigError("profiles.yaml : la section « images » est vide.")

    rooms = []  # type: List[Room]
    for number, raw in enumerate(data["rooms"], start=1):
        raw = _entry(raw, "profiles.yaml, salle n°%d" % number)
        room_id = _required(raw, "id", "profiles.yaml, salle n°%d" % number)
        where = "profiles.yaml, salle « %s »" % room_id
        if any(r.id == room_id for r in rooms):
            raise ConfigError("%s : cet identifiant est utilisé deux fois." % where)
        code, code_hash = _optional(raw, "code"), _optional(raw, "code_hash")
        if not code and not code_hash:
            raise ConfigError("%s : renseignez « code_hash » (recommandé : empreinte seule) ou « code » "
                              "(affichable dans /admin/images)." % where)
        if code_hash:
            try:
                parse_hash(code_hash)
            except ValueError:
                raise ConfigError("%s : code_hash invalide (format attendu : scrypt$N$r$p$sel$empreinte, "
                                  "à produire avec server/tools/hash_code.py)." % where) from None
            if code and not verify_code(code, code_hash):
                raise ConfigError("%s : « code » et « code_hash » ne correspondent pas." % where)
        else:
            code_hash = make_hash(code)  # l'empreinte est calculée au chargement ; elle seule sert à vérifier
        image_id = _required(raw, "image", where)
        if image_id not in images:
            raise ConfigError("%s : l'image « %s » n'existe pas dans la section « images »." % (where, image_id))
        rooms.append(Room(room_id, _optional(raw, "label") or room_id, code_hash, image_id,
                          _optional(raw, "name_prefix") or "", _optional(raw, "ou"), code))
    if not rooms:
        raise ConfigError("profiles.yaml : la section « rooms » est vide.")
    return Profiles(images, rooms)
