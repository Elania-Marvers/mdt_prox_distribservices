import probe

CPUINFO = """processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz

processor\t: 1
model name\t: Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz
"""

DISKPART_EN = """
  Disk ###  Status         Size     Free     Dyn  Gpt
  --------  -------------  -------  -------  ---  ---
  Disk 0    Online          238 GB      0 B        *
  Disk 1    No Media           0 B      0 B
  Disk 2    Online           29 GB      0 B
"""

DISKPART_FR = """
  N° disque  Statut          Taille   Libre    Dyn  GPT
  ---------  --------------  -------  -------  ---  ---
  Disque 0   En ligne         465 Go      0 o        *
"""


def test_cpuinfo():
    assert probe.parse_cpuinfo(CPUINFO) == ("Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz", 2)
    assert probe.parse_cpuinfo("") == (None, None)


def test_meminfo_et_arrondi_au_go_superieur():
    mb = probe.parse_meminfo_mb("MemTotal:        7864320 kB\nMemFree:  1 kB\n")
    assert mb == 7680
    assert probe.round_ram_mb(mb) == 8192
    assert probe.round_ram_mb(8192) == 8192
    assert probe.parse_meminfo_mb("rien") is None and probe.round_ram_mb(None) is None


def test_bios_date_plusieurs_formats():
    assert probe.parse_bios_date("05/01/2017") == "2017-05-01"    # mm/jj/aaaa (SMBIOS)
    assert probe.parse_bios_date("2017-05-01") == "2017-05-01"
    assert probe.parse_bios_date("20170501") == "2017-05-01"
    assert probe.parse_bios_date("13/02/2018") == "2018-02-13"    # jj/mm/aaaa détecté
    assert probe.parse_bios_date("03/04/12") == "2012-03-04"
    assert probe.parse_bios_date("n'importe quoi") is None and probe.parse_bios_date(None) is None


def test_disque_linux():
    assert probe.parse_block_device("sda", "500118192", "1") == {
        "name": "sda", "size_gb": 256, "size_bytes": 500118192 * 512, "kind": "hdd"}
    assert probe.parse_block_device("nvme0n1", "1000215216", "0")["kind"] == "ssd"
    assert probe.parse_block_device("sdb", "1000", None)["kind"] == "unknown"
    assert probe.parse_block_device("sdc", "0", "1") is None and probe.parse_block_device("sdd", "x", "1") is None


def test_tpm_et_secure_boot():
    assert probe.parse_tpm_version("2\n") == "2.0"
    assert probe.parse_tpm_version("1") == "1.2"
    assert probe.parse_tpm_version(None) == "unknown"
    assert probe.parse_secure_boot_var(b"\x06\x00\x00\x00\x01") is True
    assert probe.parse_secure_boot_var(b"\x06\x00\x00\x00\x00") is False
    assert probe.parse_secure_boot_var(b"") is None and probe.parse_secure_boot_var(None) is None


def test_mac_et_numero_de_serie():
    assert probe.parse_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert probe.parse_mac("00:00:00:00:00:00") is None
    getmac = '"00-11-22-33-44-55","Media disconnected"\r\n"AA-BB-CC-DD-EE-FF","\\Device\\Tcpip_{1234}"\r\n'
    assert probe.parse_getmac(getmac) == "AA:BB:CC:DD:EE:FF"
    assert probe.parse_wmic_value("SerialNumber  \r\nCZC1234567  \r\n\r\n") == "CZC1234567"
    assert probe.parse_wmic_value("SerialNumber\r\nTo Be Filled By O.E.M.\r\n") is None
    assert probe.parse_wmic_value("") is None


def test_diskpart_list_anglais_et_francais():
    # diskpart compte en Gio : 238 GB (Gio) = disque « 256 Go » de l'étiquette ; 0 B (lecteur vide) est ignoré
    assert probe.parse_diskpart_list(DISKPART_EN) == [
        {"name": "0", "size_gb": 256, "kind": "unknown"}, {"name": "2", "size_gb": 31, "kind": "unknown"}]
    assert probe.parse_diskpart_list(DISKPART_FR) == [{"name": "0", "size_gb": 499, "kind": "unknown"}]
    assert probe.parse_diskpart_list("") == []


def test_diskpart_detail_ssd_seulement_si_prouve():
    assert probe.parse_diskpart_detail("Samsung SSD 860 EVO\nType   : SATA") == "ssd"
    assert probe.parse_diskpart_detail("WDC WD5000\nType   : NVMe") == "ssd"
    assert probe.parse_diskpart_detail("WDC WD5000AZLX\nType   : SATA") == "unknown"


def test_materiel_simule_respecte_A5():
    for name in probe.SIMULATED:
        hw = probe.detect(simulate=name)
        assert set(hw) == set(probe.EMPTY)
        assert hw["environment"] in ("winpe", "linux") and hw["firmware"] in probe.FIRMWARES
        assert hw["tpm"] in probe.TPMS and all(d["kind"] in probe.KINDS for d in hw["disks"])


def test_normalize_remplace_les_valeurs_invalides():
    hw = probe.normalize({"firmware": "efi", "tpm": 2, "secure_boot": "oui", "model": "X",
                          "disks": [{"name": 0, "size_gb": "gros", "kind": "nvme"}, {"size_gb": 5}, "?"]})
    assert hw["firmware"] == "unknown" and hw["tpm"] == "unknown" and hw["secure_boot"] is None
    assert hw["model"] == "X" and hw["serial"] is None
    assert hw["disks"] == [{"name": "0", "size_gb": None, "kind": "unknown"}]


def test_hardware_json(tmp_path):
    f = tmp_path / "hw.json"
    f.write_text('{"model": "Test", "ram_mb": 4096, "firmware": "bios"}', encoding="utf-8")
    hw = probe.detect(hardware_json=str(f))
    assert hw["model"] == "Test" and hw["firmware"] == "bios" and hw["disks"] == []


def test_detection_reelle_ne_plante_pas():
    hw = probe.detect()
    assert set(hw) == set(probe.EMPTY)
