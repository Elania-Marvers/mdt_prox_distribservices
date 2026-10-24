"""Mini-système Linux (live-build) : vérifications de COHÉRENCE faisables sans construire l'image.

Ce que ça prouve : tout ce que le client exige est dans la liste de paquets, le lanceur passe bien
l'adresse du serveur au client, le script iPXE est complet, les scripts passent shellcheck.
Ce que ça ne prouve PAS : que l'image se construit et démarre (il faut une VM Debian : voir le CDC, section « Fabriquer le mini-système Linux »).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "live"
START = LIVE / "config" / "includes.chroot" / "opt" / "mdtprox" / "start.sh"
sys.path.insert(0, str(ROOT / "client"))

import linux_restore  # noqa: E402


def packages():
    lines = (LIVE / "config" / "package-lists" / "mdtprox.list.chroot").read_text(encoding="utf-8").splitlines()
    return {l.strip() for l in lines if l.strip() and not l.lstrip().startswith("#")}


# Paquet Debian de chaque programme exigé par le client. « base » = déjà dans tout Debian minimal.
TOOL_PACKAGE = {
    "sgdisk": "gdisk", "partprobe": "parted", "mount.ntfs-3g": "ntfs-3g", "zstd": "zstd", "bzip2": "bzip2",
    "xz": "xz-utils", "lz4": "lz4", "wipefs": "base", "blockdev": "base", "mount": "base", "umount": "base",
    "sync": "base", "dd": "base", "gzip": "base", "cat": "base",
}


def test_every_tool_the_client_requires_is_installed_in_the_image():
    plan = [{"fs": fs, "ext": ext} for fs in ("ntfs", "vfat", "dd") for ext in linux_restore.DECOMPRESSORS]
    wanted = linux_restore.required_tools(plan)
    installed = packages()
    for tool in wanted:
        package = "partclone" if tool.startswith("partclone.") else TOOL_PACKAGE.get(tool)
        assert package, f"« {tool} » est exigé par le client mais absent de TOOL_PACKAGE (ajoutez son paquet Debian)"
        assert package == "base" or package in installed, f"« {tool} » exige le paquet {package}, absent de mdtprox.list.chroot"


def test_the_image_has_what_it_needs_to_boot_and_run():
    installed = packages()
    for needed in ("live-boot", "live-config", "systemd-sysv", "curl",   # curl : fetch=http:// dans l'initrd
                   "python3", "efibootmgr", "console-setup", "keyboard-configuration"):
        assert needed in installed, needed
    assert not installed & {"xserver-xorg", "network-manager", "openssh-server", "sudo"}   # rien d'inutile ni d'ouvert


def test_service_hook_and_launcher_agree():
    service = (LIVE / "config" / "includes.chroot" / "etc" / "systemd" / "system" / "mdtprox-client.service").read_text()
    hook = (LIVE / "config" / "hooks" / "normal" / "9000-mdtprox.hook.chroot").read_text()
    assert "ExecStart=/opt/mdtprox/start.sh" in service and "TTYPath=/dev/tty1" in service
    assert "Conflicts=getty@tty1.service" in service
    assert "systemctl enable mdtprox-client.service" in hook and "systemctl enable systemd-networkd.service" in hook
    assert 'systemctl mask "getty@tty$n.service"' in hook                      # aucune invite de connexion
    assert "DHCP=ipv4" in (LIVE / "config" / "includes.chroot" / "etc" / "systemd" / "network" / "10-mdtprox-dhcp.network").read_text()
    assert 'XKBLAYOUT="fr"' in (LIVE / "config" / "includes.chroot" / "etc" / "default" / "keyboard").read_text()
    assert 'CODESET="Lat15"' in (LIVE / "config" / "includes.chroot" / "etc" / "default" / "console-setup").read_text()


def test_the_client_the_image_ships_has_the_files_the_launcher_calls():
    assert (ROOT / "client" / "deploy_client.py").is_file() and (ROOT / "client" / "linux_restore.py").is_file()
    assert os.access(START, os.X_OK) and os.access(LIVE / "auto" / "config", os.X_OK)


# --------------------------------------------------------------------------- le lanceur start.sh

@pytest.fixture
def launcher(tmp_path):
    log = tmp_path / "log"
    fake_python = tmp_path / "python3"
    fake_python.write_text('#!/bin/sh\necho "python $*" >> "%s"\nexit "${FAKE_PYTHON_EXIT:-0}"\n' % log)
    fake_shell = tmp_path / "shell"
    fake_shell.write_text('#!/bin/sh\necho "shell $*" >> "%s"\n' % log)
    for f in (fake_python, fake_shell):
        f.chmod(0o755)
    (tmp_path / "client").mkdir()

    def run(cmdline, python_exit=0):
        (tmp_path / "cmdline").write_text(cmdline + "\n")
        log.write_text("")
        env = dict(os.environ, MDTPROX_CMDLINE=str(tmp_path / "cmdline"), MDTPROX_PYTHON=str(fake_python),
                   MDTPROX_SHELL=str(fake_shell), MDTPROX_CLIENT_DIR=str(tmp_path / "client"), FAKE_PYTHON_EXIT=str(python_exit))
        r = subprocess.run(["sh", str(START)], env=env, capture_output=True, text=True, timeout=30)
        return r.returncode, log.read_text().splitlines(), r.stdout
    return run


def test_launcher_passes_the_server_given_by_the_kernel(launcher):
    code, calls, _ = launcher("BOOT_IMAGE=vmlinuz boot=live quiet mdtprox_server=http://10.0.0.3:8080 ip=dhcp")
    assert code == 0 and calls == ["python deploy_client.py --server http://10.0.0.3:8080"]


def test_launcher_without_server_lets_the_client_use_its_config(launcher):
    code, calls, _ = launcher("boot=live quiet mdtprox_server_autre=x")          # un paramètre voisin ne doit pas être pris
    assert code == 0 and calls == ["python deploy_client.py"]


@pytest.mark.parametrize("flag", ["mdtprox_debug", "mdtprox_debug=1"])
def test_launcher_debug_mode_opens_a_shell_instead_of_the_client(launcher, flag):
    code, calls, out = launcher("boot=live mdtprox_server=http://x:8080 " + flag)
    assert calls == ["shell -l"] and "Mode dépannage" in out


def test_launcher_failure_leaves_a_shell_without_rebooting(launcher):
    code, calls, out = launcher("boot=live mdtprox_server=http://x:8080", python_exit=3)
    assert calls == ["python deploy_client.py --server http://x:8080", "shell -l"] and "code 3" in out


# --------------------------------------------------------------------------- boot-linux.ipxe

def rendered(url="http://10.0.0.3:8080"):
    """Le script tel que l'API le sert, puis tel qu'iPXE le lit (la variable ${base} développée)."""
    text = (ROOT / "boot" / "boot-linux.ipxe").read_text(encoding="utf-8").replace("@@BASE_URL@@", url)
    base = re.search(r"^set base (\S+)$", text, re.M).group(1)
    return text.replace("${base}", base)


def test_boot_linux_ipxe_is_complete():
    text = rendered()
    assert text.startswith("#!ipxe") and "@@" not in text
    base = "http://10.0.0.3:8080"
    kernel = next(l for l in text.splitlines() if l.startswith("kernel "))
    for part in (f"kernel {base}/live/vmlinuz", "initrd=initrd.img", "boot=live", f"fetch={base}/live/filesystem.squashfs",
                 "ip=dhcp", "noautologin", "keyboard-layouts=fr", f"mdtprox_server={base}"):
        assert part in kernel, part
    assert f"initrd --name initrd.img {base}/live/initrd.img" in text           # le nom doit être celui de « initrd= »
    for command in ("kernel ", "initrd ", "boot "):
        line = next(l for l in text.splitlines() if l.startswith(command))
        assert line.rstrip().endswith("|| goto echec"), line
    assert ":echec" in text and "\nshell" in text


def test_boot_linux_ipxe_screen_messages_are_ascii():
    for line in rendered().splitlines():
        if not line.lstrip().startswith("#"):
            assert line.isascii(), line                                         # la console d'iPXE n'affiche pas l'UTF-8


# --------------------------------------------------------------------------- shellcheck

@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck absent")
@pytest.mark.parametrize("path", ["scripts/setup-vm.sh", "scripts/build-live.sh", "live/auto/config",
                                  "live/config/includes.chroot/opt/mdtprox/start.sh",
                                  "live/config/hooks/normal/9000-mdtprox.hook.chroot"])
def test_shellcheck(path):
    r = subprocess.run(["shellcheck", "-x", str(ROOT / path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("path", ["scripts/setup-vm.sh", "scripts/build-live.sh"])
def test_scripts_refuse_to_run_without_root_and_check_their_inputs(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "set -euo pipefail" in text and "$EUID" in text


def test_build_live_never_deletes_a_work_dir_that_still_has_mounts():
    text = (ROOT / "scripts" / "build-live.sh").read_text(encoding="utf-8")
    cleanup = text[text.index("cleanup() {"):text.index("trap cleanup EXIT")]
    assert "/proc/mounts" in cleanup and cleanup.index("/proc/mounts") < cleanup.index("rm -rf")
    assert '"$WORK_PARENT/$WORK_PREFIX".*' in cleanup                             # et seulement un dossier créé par le script


def test_the_lab_dhcp_config_is_pinned_to_one_network_card():
    """Un DHCP d'essai qui écouterait partout répondrait aux postes de la production."""
    text = (ROOT / "boot" / "dnsmasq-lab.conf").read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert any(l.startswith("interface=") for l in lines) and "bind-interfaces" in lines
    assert "port=0" in lines and any(l.startswith("dhcp-range=") for l in lines)
    assert "dhcp-option=option:router" in lines                                # aucune passerelle annoncée : réseau fermé
    assert any("tag:ipxe,http://" in l and l.endswith("/boot.ipxe") for l in lines)
    assert "A_RELEVER" in text                                                 # le fichier doit être complété avant usage


def test_the_production_tftp_config_has_no_dhcp():
    text = (ROOT / "boot" / "dnsmasq-tftp.conf").read_text(encoding="utf-8")
    assert not any(l.strip().startswith("dhcp-range") for l in text.splitlines())


def test_the_api_service_refuses_to_start_with_placeholder_secrets():
    text = (ROOT / "deploy" / "mdtprox-api.service").read_text(encoding="utf-8")
    assert "ExecStartPre=+/bin/sh -c '! grep -q CHANGEZ-MOI /etc/mdtprox.env'" in text
