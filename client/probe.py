"""Détection du matériel : produit l'objet « matériel » de l'annexe A.5 du CDC.

Règle d'or : aucune sonde ne doit planter. Valeur inconnue = None ou "unknown"
(le serveur sait les gérer). Les petites fonctions parse_* prennent du TEXTE :
elles se testent avec des chaînes d'exemple, sans machine réelle.
"""
from __future__ import annotations

import copy
import ctypes
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile

# --------------------------------------------------------------------------
# Matériel factice (--simulate) et valeurs par défaut
# --------------------------------------------------------------------------

EMPTY = {
    "environment": "linux", "manufacturer": None, "model": None, "serial": None,
    "bios_date": None, "firmware": "unknown", "cpu_name": None, "cpu_cores": None,
    "ram_mb": None, "disks": [], "tpm": "unknown", "secure_boot": None, "mac": None,
}

SIMULATED = {
    # PC récent : le verdict doit être « ok »
    "modern": {
        "environment": "winpe", "manufacturer": "Dell Inc.", "model": "OptiPlex 7090",
        "serial": "7K2M9Z3", "bios_date": "2022-03-14", "firmware": "uefi",
        "cpu_name": "11th Gen Intel(R) Core(TM) i7-11700 @ 2.50GHz", "cpu_cores": 16,
        "ram_mb": 16384, "disks": [{"name": "0", "size_gb": 512, "kind": "ssd"}],
        "tpm": "2.0", "secure_boot": True, "mac": "3C:EC:EF:12:34:56",
    },
    # Core i3 récent : le verdict doit être « warn »
    "hp-i3": {
        "environment": "winpe", "manufacturer": "HP", "model": "HP Exemple 800 G4 SFF",
        "serial": "CZC9123XYZ", "bios_date": "2019-02-12", "firmware": "uefi",
        "cpu_name": "Intel(R) Core(TM) i3-8100 CPU @ 3.60GHz", "cpu_cores": 4,
        "ram_mb": 8192, "disks": [{"name": "0", "size_gb": 256, "kind": "ssd"}],
        "tpm": "2.0", "secure_boot": True, "mac": "9C:B6:54:AA:BB:CC",
    },
    # Vieux PC en BIOS classique, sans TPM, disque dur : « discouraged »
    # Le même PC vu depuis le mini-système Linux : disque « sda », taille exacte (pour l'image Clonezilla de la ROOMA)
    "exemple-600-g3-linux": {
        "environment": "linux", "manufacturer": "HP", "model": "HP Exemple 600 G3 MT",
        "serial": "CZC1234567", "bios_date": "2017-05-01", "firmware": "uefi",
        "cpu_name": "Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz", "cpu_cores": 4,
        "ram_mb": 8192, "disks": [{"name": "sda", "size_gb": 256, "size_bytes": 256060514304, "kind": "ssd"}],
        "tpm": "2.0", "secure_boot": True, "mac": "AA:BB:CC:DD:EE:FF",
    },
    "old-lenovo": {
        "environment": "winpe", "manufacturer": "LENOVO", "model": "ThinkCentre M82",
        "serial": "MJ01ABCD", "bios_date": "2012-08-10", "firmware": "bios",
        "cpu_name": "Intel(R) Core(TM) i5-3470 CPU @ 3.20GHz", "cpu_cores": 4,
        "ram_mb": 4096, "disks": [{"name": "0", "size_gb": 500, "kind": "hdd"}],
        "tpm": "none", "secure_boot": None, "mac": "00:21:CC:AA:BB:01",
    },
    # Exemple de l'annexe A.5 : processeur 6e génération, donc déconseillé pour Windows 11
    "exemple-600-g3": {
        "environment": "winpe", "manufacturer": "HP", "model": "HP Exemple 600 G3 MT",
        "serial": "CZC1234567", "bios_date": "2017-05-01", "firmware": "uefi",
        "cpu_name": "Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz", "cpu_cores": 4,
        "ram_mb": 8192, "disks": [{"name": "0", "size_gb": 256, "kind": "ssd"}],
        "tpm": "2.0", "secure_boot": True, "mac": "AA:BB:CC:DD:EE:FF",
    },
}

FIRMWARES = ("uefi", "bios", "unknown")
TPMS = ("2.0", "1.2", "none", "unknown")
KINDS = ("ssd", "hdd", "unknown")


def normalize(hw: dict) -> dict:
    """Complète/nettoie un dict pour qu'il respecte A.5 (clés présentes, valeurs permises)."""
    out = copy.deepcopy(EMPTY)
    for key in out:
        if key != "disks" and key in hw:
            out[key] = hw[key]
    if out["environment"] not in ("winpe", "linux"):
        out["environment"] = "winpe" if sys.platform == "win32" else "linux"
    if out["firmware"] not in FIRMWARES:
        out["firmware"] = "unknown"
    if out["tpm"] not in TPMS:
        out["tpm"] = "unknown"
    if not isinstance(out["secure_boot"], bool):
        out["secure_boot"] = None
    disks = []
    for d in hw.get("disks") or []:
        if isinstance(d, dict) and d.get("name") not in (None, ""):
            size = d.get("size_gb")
            size = int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) else None
            kind = d.get("kind") if d.get("kind") in KINDS else "unknown"
            disks.append({"name": str(d["name"]), "size_gb": size, "kind": kind})
    out["disks"] = disks
    return out


def detect(simulate: str | None = None, hardware_json: str | None = None) -> dict:
    """Point d'entrée : matériel factice, fichier JSON, ou détection réelle."""
    if hardware_json:
        with open(hardware_json, encoding="utf-8-sig") as f:
            return normalize(json.load(f))
    if simulate:
        return normalize(SIMULATED[simulate])  # KeyError si nom inconnu : argparse l'évite déjà
    try:
        if sys.platform == "win32":
            hw = _detect_windows()
        elif sys.platform.startswith("linux"):
            hw = _detect_linux()
        else:
            hw = _detect_other()
    except Exception:  # filet de sécurité ultime : mieux vaut un matériel inconnu qu'un plantage
        hw = {}
    return normalize(hw)


# --------------------------------------------------------------------------
# Petites fonctions de lecture de texte (communes à Linux et Windows)
# --------------------------------------------------------------------------

def round_ram_mb(mb: int | None) -> int | None:
    """Arrondit au Go supérieur : le système annonce un peu moins que la RAM installée
    (mémoire réservée au processeur graphique, etc.), un PC de 8 Go afficherait 7,6 Go."""
    return None if not mb else -(-mb // 1024) * 1024


def parse_bios_date(text: str | None) -> str | None:
    """'05/01/2017' (mm/jj/aaaa, format SMBIOS), '2017-05-01' ou '20170501' -> '2017-05-01'."""
    text = (text or "").strip()
    m = re.match(r"^(\d{4})[-/.]?(\d{2})[-/.]?(\d{2})", text)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
        if not m:
            return None
        mo, d, y = map(int, m.groups())
        if mo > 12 >= d:  # jj/mm/aaaa
            mo, d = d, mo
        if y < 100:
            y += 2000 if y < 70 else 1900
    try:
        return datetime.date(y, mo, d).isoformat()
    except ValueError:
        return None


_MAC_RE = re.compile(r"\b([0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5})\b")


def parse_mac(text: str | None) -> str | None:
    """Première adresse MAC trouvée dans le texte, au format AA:BB:CC:DD:EE:FF."""
    for m in _MAC_RE.finditer(text or ""):
        mac = m.group(1).replace("-", ":").upper()
        if mac not in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
            return mac
    return None


_BAD_SERIALS = {"", "0", "none", "n/a", "default string", "system serial number",
                "to be filled by o.e.m.", "not specified", "not applicable"}


def clean_serial(text: str | None) -> str | None:
    text = (text or "").strip()
    return None if text.lower() in _BAD_SERIALS else text


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------

def read_text(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def parse_cpuinfo(text: str | None) -> tuple[str | None, int | None]:
    """Contenu de /proc/cpuinfo -> (nom du processeur, nombre de cœurs logiques)."""
    name, cores = None, 0
    for line in (text or "").splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "processor":
            cores += 1
        elif key == "model name" and name is None:
            name = value.strip() or None
    return name, cores or None


def parse_meminfo_mb(text: str | None) -> int | None:
    m = re.search(r"^MemTotal:\s+(\d+)\s*kB", text or "", re.M)
    return int(m.group(1)) // 1024 if m else None


def parse_tpm_version(text: str | None) -> str:
    """/sys/class/tpm/tpm0/tpm_version_major : '2' -> '2.0', '1' -> '1.2'."""
    return {"2": "2.0", "1": "1.2"}.get((text or "").strip(), "unknown")


def parse_secure_boot_var(data: bytes | None) -> bool | None:
    """Variable EFI SecureBoot : 4 octets d'attributs puis 1 octet (1 = activé)."""
    if not data or len(data) != 5:
        return None
    return {1: True, 0: False}.get(data[4])


def parse_block_device(name: str, size_sectors: str | None, rotational: str | None) -> dict | None:
    """/sys/block/<nom>/size (secteurs de 512 octets) et queue/rotational -> disque A.5."""
    try:
        size_bytes = int((size_sectors or "").strip()) * 512
    except ValueError:
        return None
    if size_bytes <= 0:
        return None
    kind = {"0": "ssd", "1": "hdd"}.get((rotational or "").strip(), "unknown")
    return {"name": name, "size_gb": round(size_bytes / 1e9), "size_bytes": size_bytes, "kind": kind}


_VIRTUAL_BLOCK = ("loop", "ram", "sr", "zram", "dm-", "md", "fd", "nbd")


def _linux_disks() -> list:
    disks = []
    try:
        names = sorted(os.listdir("/sys/block"))
    except OSError:
        return disks
    for name in names:
        if name.startswith(_VIRTUAL_BLOCK):
            continue
        base = "/sys/block/" + name
        if (read_text(base + "/removable") or "").strip() == "1":  # clé USB, lecteur de cartes : jamais une cible
            continue  # Limite connue : un disque externe « amovible » ne sera pas proposé ; à revoir si besoin
        d = parse_block_device(name, read_text(base + "/size"), read_text(base + "/queue/rotational"))
        if d:
            disks.append(d)
    return disks


def _linux_mac() -> str | None:
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return None
    for name in names:  # seules les vraies cartes ont un lien « device »
        if os.path.exists(f"/sys/class/net/{name}/device"):
            mac = parse_mac(read_text(f"/sys/class/net/{name}/address"))
            if mac:
                return mac
    return None


def _linux_tpm() -> str:
    if not os.path.isdir("/sys/class/tpm") or not os.listdir("/sys/class/tpm"):
        return "none"
    return parse_tpm_version(read_text("/sys/class/tpm/tpm0/tpm_version_major"))


def _linux_secure_boot() -> bool | None:
    try:
        path = "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
        with open(path, "rb") as f:
            return parse_secure_boot_var(f.read())
    except OSError:
        return None


def _detect_linux() -> dict:
    dmi = lambda n: read_text("/sys/class/dmi/id/" + n)  # noqa: E731
    name, cores = parse_cpuinfo(read_text("/proc/cpuinfo"))
    uefi = os.path.isdir("/sys/firmware/efi")
    return {
        "environment": "linux", "manufacturer": dmi("sys_vendor"), "model": dmi("product_name"),
        "serial": clean_serial(dmi("product_serial")), "bios_date": parse_bios_date(dmi("bios_date")),
        "firmware": "uefi" if uefi else "bios", "cpu_name": name, "cpu_cores": cores,
        "ram_mb": round_ram_mb(parse_meminfo_mb(read_text("/proc/meminfo"))),
        "disks": _linux_disks(), "tpm": _linux_tpm(),
        "secure_boot": _linux_secure_boot() if uefi else None, "mac": _linux_mac(),
    }


def _detect_other() -> dict:
    """macOS & co : matériel « inconnu mais raisonnable », juste de quoi tester le déroulé."""
    hw = {"environment": "linux", "cpu_name": platform.processor() or platform.machine() or None,
          "cpu_cores": os.cpu_count()}
    if sys.platform == "darwin":
        hw["manufacturer"] = "Apple"
    try:
        hw["ram_mb"] = round_ram_mb(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 2**20)
    except (ValueError, OSError, AttributeError):
        pass
    try:
        hw["disks"] = [{"name": "0", "size_gb": round(shutil.disk_usage("/").total / 1e9), "kind": "unknown"}]
    except OSError:
        pass
    return hw


# --------------------------------------------------------------------------
# Windows / WinPE  (sans WMI ni PowerShell, qui peuvent manquer dans WinPE)
# --------------------------------------------------------------------------

_UNITS = {"b": 1, "o": 1, "kb": 1024, "ko": 1024, "mb": 1024**2, "mo": 1024**2,
          "gb": 1024**3, "go": 1024**3, "tb": 1024**4, "to": 1024**4}
_DISK_RE = re.compile(r"^\s*(?:Disk|Disque)\s+(\d+)\s+.*?(\d+)\s*(B|KB|MB|GB|TB|o|Ko|Mo|Go|To)\b",
                      re.I | re.M)


def parse_diskpart_list(text: str | None) -> list:
    """Sortie de `diskpart` > `list disk` (anglais ou français) -> disques A.5.
    diskpart compte en Gio ('238 GB') : on convertit en Go décimaux (256) comme sur l'étiquette."""
    disks = []
    for m in _DISK_RE.finditer(text or ""):
        size_bytes = int(m.group(2)) * _UNITS[m.group(3).lower()]
        if size_bytes > 0:  # écarte les lecteurs de cartes vides (0 B)
            disks.append({"name": m.group(1), "size_gb": round(size_bytes / 1e9), "kind": "unknown"})
    return disks


def parse_diskpart_detail(text: str | None) -> str:
    """`detail disk` : seul un modèle contenant SSD/NVMe prouve un SSD ; sinon on ne devine pas.
    Limite connue : ssd ou unknown, jamais hdd (rien de fiable dans WinPE sans PowerShell), à affiner sur banc."""
    t = (text or "").lower()
    return "ssd" if ("ssd" in t or "nvme" in t) else "unknown"


def parse_wmic_value(text: str | None) -> str | None:
    """Sortie de `wmic bios get serialnumber` : l'en-tête, puis la valeur."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return clean_serial(lines[1]) if len(lines) >= 2 else None


def parse_getmac(text: str | None) -> str | None:
    """`getmac /fo csv /nh` : on préfère la carte réellement connectée (nom d'interface Tcpip_)."""
    lines = (text or "").splitlines()
    return parse_mac("\n".join([l for l in lines if "Tcpip" in l] or lines))


def _reg(path: str, name: str):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            value = winreg.QueryValueEx(key, name)[0]
        return value.strip() if isinstance(value, str) else value
    except Exception:
        return None


def _run_text(cmd: list, timeout: int = 30) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout).stdout


def run_diskpart(script: str, timeout: int | None = None) -> tuple[int, str]:
    """Exécute un script diskpart (`diskpart /s fichier`) -> (code retour, texte affiché)."""
    # À VÉRIFIER SUR BANC : diskpart /s renvoie bien un code non nul en cas d'erreur de script
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="ascii") as f:
        f.write(script)
    try:
        r = subprocess.run(["diskpart", "/s", f.name], capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return r.returncode, r.stdout
    finally:
        os.unlink(f.name)


def _win_firmware() -> str:
    # À VÉRIFIER SUR BANC : PEFirmwareType et le repli GetFirmwareEnvironmentVariableW dans un vrai WinPE
    # PEFirmwareType : 1 = BIOS, 2 = UEFI (présent dans WinPE)
    t = _reg(r"SYSTEM\CurrentControlSet\Control", "PEFirmwareType")
    if t in (1, 2):
        return "bios" if t == 1 else "uefi"
    # Repli : en BIOS classique, lire une variable EFI échoue avec ERROR_INVALID_FUNCTION (1)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    k32.GetFirmwareEnvironmentVariableW("", "{00000000-0000-0000-0000-000000000000}", None, 0)
    err = ctypes.get_last_error()
    return "bios" if err == 1 else "uefi" if err in (203, 998) else "unknown"


def _win_cores() -> int | None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor") as k:
        return winreg.QueryInfoKey(k)[0] or None


def _win_ram_mb() -> int | None:
    # À VÉRIFIER SUR BANC : GlobalMemoryStatusEx dans WinPE (la valeur doit être proche de la RAM installée)
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = MemoryStatus()
    st.dwLength = ctypes.sizeof(st)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
    return int(st.ullTotalPhys // 2**20) or None


def _win_disks() -> list:
    # À VÉRIFIER SUR BANC : texte réel de `list disk` / `detail disk` (anglais ET français) dans WinPE
    disks = parse_diskpart_list(run_diskpart("list disk\n", timeout=120)[1])
    for d in disks:
        try:
            d["kind"] = parse_diskpart_detail(run_diskpart(f"select disk {d['name']}\ndetail disk\n", 120)[1])
        except Exception:
            pass
    return disks


def _win_tpm() -> str:
    """Un TPM 2.0 s'annonce à Windows par l'identifiant ACPI MSFT0101 (identifiant principal ou « compatible »).
    Limite connue : on sait seulement dire « 2.0 » ou « unknown » (jamais « none »), le TPM 1.2 n'est pas reconnu.
    # À VÉRIFIER SUR BANC : le périphérique apparaît-il dans l'énumération PnP de WinPE sans pilote TPM ?"""
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\ACPI") as acpi:
        for i in range(winreg.QueryInfoKey(acpi)[0]):
            dev = winreg.EnumKey(acpi, i)
            if "MSFT0101" in dev.upper():
                return "2.0"
            try:
                with winreg.OpenKey(acpi, dev) as devkey:
                    for j in range(winreg.QueryInfoKey(devkey)[0]):
                        with winreg.OpenKey(devkey, winreg.EnumKey(devkey, j)) as inst:
                            if "MSFT0101" in _ids(inst).upper():
                                return "2.0"
            except OSError:
                continue
    return "unknown"


def _ids(inst_key) -> str:
    import winreg
    out = []
    for name in ("HardwareID", "CompatibleIDs"):
        try:
            out += winreg.QueryValueEx(inst_key, name)[0]
        except OSError:
            pass
    return " ".join(out)


def _win_secure_boot() -> bool | None:
    # À VÉRIFIER SUR BANC : nom exact de la valeur de registre dans WinPE (le CDC cite UEFISECURESTATE)
    path = r"SYSTEM\CurrentControlSet\Control\SecureBoot\State"
    for name in ("UEFISecureBootEnabled", "UEFISECURESTATE"):  # le 2e nom est celui du CDC
        value = _reg(path, name)
        if value in (0, 1):
            return bool(value)
    return None


def _detect_windows() -> dict:
    # À VÉRIFIER SUR BANC : clés de registre HARDWARE\DESCRIPTION, getmac, wmic (peut manquer dans WinPE)
    # Limite connue : série via wmic seulement (le registre BIOS ne la contient pas) ; absent = None, à remplacer par
    # une lecture SMBIOS brute (GetSystemFirmwareTable) si wmic manque dans le WinPE
    bios = r"HARDWARE\DESCRIPTION\System\BIOS"
    cpu = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    mac = parse_getmac(_safe(_run_text, ["getmac", "/fo", "csv", "/nh"], default="")) \
        or parse_mac(_safe(_run_text, ["ipconfig", "/all"], default=""))
    return {
        "environment": "winpe",
        "manufacturer": _reg(bios, "SystemManufacturer"),
        "model": _reg(bios, "SystemProductName"),
        "serial": parse_wmic_value(_safe(_run_text, ["wmic", "bios", "get", "serialnumber"], default="")),
        "bios_date": parse_bios_date(_reg(bios, "BIOSReleaseDate")),
        "firmware": _safe(_win_firmware, default="unknown"),
        "cpu_name": _reg(cpu, "ProcessorNameString"),
        "cpu_cores": _safe(_win_cores),
        "ram_mb": round_ram_mb(_safe(_win_ram_mb)),
        "disks": _safe(_win_disks, default=[]),
        "tpm": _safe(_win_tpm, default="unknown"),
        "secure_boot": _win_secure_boot(),
        "mac": mac,
    }


def _safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception:
        return default
