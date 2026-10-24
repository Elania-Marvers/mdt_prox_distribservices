import importlib.util
import os
from pathlib import Path

import pytest

from app import images
from app.config import ConfigError, load_profiles, load_settings
from app.security import make_hash, verify_code

SERVER = Path(__file__).resolve().parent.parent


def test_example_profiles_are_valid_and_their_documented_codes_work():
    profiles = load_profiles(SERVER / "profiles.example.yaml")
    assert [r.id for r in profiles.rooms] == ["ROOMA", "ROOMB", "ROOMC", "ROOMAC"]
    assert verify_code("exemple-rooma", profiles.rooms[0].code_hash)
    assert not verify_code("exemple-roomb", profiles.rooms[0].code_hash)
    assert profiles.rooms[2].ou is None and profiles.images["win10-polyvalent"].os_family == "windows10"
    assert verify_code("exemple-roomac", profiles.rooms[3].code_hash)
    cz = profiles.images[profiles.rooms[3].image]
    assert (cz.kind, cz.dir) == ("clonezilla", "W10-22H2-ROOMA")


def test_env_example_has_every_variable_of_the_contract():
    lines = (SERVER / ".env.example").read_text(encoding="utf-8").splitlines()
    env = dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))
    env = {k: v.strip('"') for k, v in env.items()}
    assert set(env) == {"PUBLIC_BASE_URL", "BIND_HOST", "BIND_PORT", "DATA_DIR", "DOMAIN_FQDN", "DOMAIN_JOIN_USER",
                        "DOMAIN_JOIN_PASSWORD", "LOCAL_ADMIN_USER", "LOCAL_ADMIN_PASSWORD", "WIN_TIMEZONE",
                        "WIN_LOCALE", "MAX_BAD_CODES", "LOCKOUT_SECONDS", "ADMIN_USER", "ADMIN_PASSWORD", "LAB_MODE"}
    assert load_settings(env).missing == []


def test_hash_tool_prints_a_hash_the_server_accepts(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("hash_code", SERVER / "tools" / "hash_code.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    answers = iter(["mon-code", "mon-code"])
    monkeypatch.setattr(tool.getpass, "getpass", lambda prompt: next(answers))
    assert tool.main() == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("scrypt$16384$8$1$") and "mon-code" not in line
    assert verify_code("mon-code", line) and not verify_code("autre", line)
    answers = iter(["un", "deux"])  # deux saisies différentes : refus
    assert tool.main() == 1


def test_same_code_gives_different_hashes():
    assert make_hash("x", n=1024) != make_hash("x", n=1024)  # le sel change à chaque fois


BAD_PROFILES = [
    ("images: {}\nrooms: []", "vide"),
    ("rooms: []", "section « images »"),
    ("images: {a: {file: a.wim, os_family: windows7}}\nrooms: []", "os_family"),
    ("images: {a: {file: ../a.wim, os_family: windows10}}\nrooms: []", "simple nom de fichier"),
    ("images: {'a b': {file: a.wim, os_family: windows10}}\nrooms: []", "identifiant"),
    ("images: {a: {file: a.wim, os_family: windows10}}\nrooms: [{id: X, code_hash: pasunhash, image: a}]", "code_hash invalide"),
    ("images: {a: {file: a.wim, os_family: windows10}}\nrooms: [{id: X, code_hash: HASH, image: b}]", "l'image « b » n'existe pas"),
    ("images: {a: {file: a.wim, os_family: windows10}}\nrooms: [{id: X, code_hash: HASH, image: a}, {id: X, code_hash: HASH, image: a}]", "deux fois"),
    ("images: [oups", "syntaxe YAML"),
]


@pytest.mark.parametrize("text,message", BAD_PROFILES)
def test_bad_profiles_give_a_clear_french_error(tmp_path, text, message):
    path = tmp_path / "profiles.yaml"
    path.write_text(text.replace("HASH", make_hash("x", n=1024)))
    with pytest.raises(ConfigError, match=message):
        load_profiles(path)
    with pytest.raises(ConfigError, match="introuvable"):
        load_profiles(tmp_path / "absent.yaml")


def test_settings_defaults_and_errors():
    s = load_settings({"PUBLIC_BASE_URL": "http://x:8080/"})
    assert s.public_base_url == "http://x:8080" and s.win_timezone == "Romance Standard Time"
    assert (s.win_locale, s.max_bad_codes, s.lockout_seconds, str(s.data_dir)) == ("fr-FR", 3, 300, "/srv/deploy")
    assert s.missing == ["DOMAIN_FQDN", "DOMAIN_JOIN_USER", "DOMAIN_JOIN_PASSWORD", "LOCAL_ADMIN_USER", "LOCAL_ADMIN_PASSWORD"]
    with pytest.raises(ConfigError, match="MAX_BAD_CODES"):
        load_settings({"MAX_BAD_CODES": "beaucoup"})
    with pytest.raises(ConfigError, match="LOCAL_ADMIN_PASSWORD"):
        load_settings({"LOCAL_ADMIN_PASSWORD": "abc\ndef"})


def test_image_sha256_sidecar(tmp_path):
    wim = tmp_path / "a.wim"
    wim.write_bytes(b"contenu" * 1000)
    info = images.image_info(wim)
    assert info["size_bytes"] == 7000 and len(info["sha256"]) == 64
    sidecar = tmp_path / "a.wim.sha256"
    assert sidecar.read_text().split()[0] == info["sha256"]  # écrit à côté de l'image

    sidecar.write_text("b" * 64 + "  a.wim\n")  # un sidecar plus récent que l'image est cru sur parole
    images._cache.clear()
    assert images.image_info(wim)["sha256"] == "b" * 64

    os.utime(wim, (sidecar.stat().st_atime + 10, sidecar.stat().st_mtime + 10))  # l'image devient plus récente
    images._cache.clear()
    assert images.image_info(wim)["sha256"] == info["sha256"]  # sidecar périmé : recalculé

    assert images.image_info(tmp_path / "absente.wim") is None


# --------------------------------------------------------------------------- fichier d'environnement (Windows, sans systemd)

def test_env_file_fills_in_but_never_overrides_the_real_environment(tmp_path):
    f = tmp_path / "mdtprox.env"
    f.write_text('# commentaire\nPUBLIC_BASE_URL="http://srv:8080"\nDOMAIN_FQDN=example.local\n'
                 'DOMAIN_JOIN_USER=\'svc\'\nDOMAIN_JOIN_PASSWORD="a\\"b\\\\c"\nLOCAL_ADMIN_USER=adm\nLOCAL_ADMIN_PASSWORD=pw\n'
                 'ADMIN_USER=chef\nLAB_MODE=1\n', encoding="utf-8-sig")
    s = load_settings({"MDTPROX_ENV_FILE": str(f), "DOMAIN_FQDN": "reel.local", "DATA_DIR": str(tmp_path)})
    assert s.public_base_url == "http://srv:8080" and s.domain_join_user == "svc"
    assert s.domain_join_password == 'a"b\\c'                     # \" et \\ décodés comme systemd
    assert s.domain_fqdn == "reel.local"                           # la vraie variable l'emporte sur le fichier
    assert s.admin_user == "chef" and s.lab_mode is True and s.missing == []


def test_missing_env_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="MDTPROX_ENV_FILE"):
        load_settings({"MDTPROX_ENV_FILE": str(tmp_path / "absent.env")})


# --------------------------------------------------------------------------- code en clair ou empreinte

def _room_profile(tmp_path, room_line):
    p = tmp_path / "profiles.yaml"
    p.write_text("images:\n  i: {file: a.wim, os_family: windows10}\nrooms:\n  - {id: A, image: i, %s}\n" % room_line)
    return p


def test_room_with_a_plain_code_gets_its_hash_and_can_show_the_code(tmp_path):
    room = load_profiles(_room_profile(tmp_path, "code: code-de-test")).rooms[0]
    assert room.code == "code-de-test" and verify_code("code-de-test", room.code_hash) and not verify_code("autre", room.code_hash)


def test_room_with_only_a_hash_cannot_show_its_code(tmp_path):
    room = load_profiles(_room_profile(tmp_path, 'code_hash: "%s"' % make_hash("secret", n=1024))).rooms[0]
    assert room.code is None and verify_code("secret", room.code_hash)


def test_code_and_hash_must_agree_and_one_is_required(tmp_path):
    with pytest.raises(ConfigError, match="ne correspondent pas"):
        load_profiles(_room_profile(tmp_path, 'code: un-code, code_hash: "%s"' % make_hash("autre-code", n=1024)))
    with pytest.raises(ConfigError, match="renseignez"):
        load_profiles(_room_profile(tmp_path, "label: sans-code"))
