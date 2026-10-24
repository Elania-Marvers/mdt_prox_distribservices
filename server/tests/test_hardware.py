import pytest

from app.hardware import assess, parse_cpu

# (fichier de matériel, famille d'OS, niveau attendu)
CASES = [
    ("hp_exemple_600_g3", "windows11", "discouraged"),  # i5-6500 : avant la 8e génération
    ("hp_exemple_600_g3", "windows10", "ok"),
    ("lenovo_old", "windows11", "discouraged"),
    ("lenovo_old", "windows10", "discouraged"),         # Core 2 Duo, BIOS 2010
    ("hp_i3_8100", "windows11", "warn"),                # Core i3, 4 Go, disque dur, BIOS 2018
    ("hp_i3_8100", "windows10", "warn"),                # seulement le disque dur
    ("modern_i5_12400", "windows11", "ok"),
    ("modern_i5_12400", "windows10", "ok"),
    ("missing_fields", "windows11", "warn"),            # on ne sait rien : prudence, pas de plantage
    ("missing_fields", "windows10", "warn"),
]


@pytest.mark.parametrize("fixture,family,level", CASES)
def test_level_for_each_fixture(hw, fixture, family, level):
    result = assess(hw(fixture), family)
    assert result["level"] == level
    assert result["title"] and result["specs"]
    assert (result["question"] is None) == (level == "ok")
    assert (result["reasons"] == []) == (level == "ok")


def test_diplomatic_messages_with_real_specs(hw):
    result = assess(hw("hp_i3_8100"), "windows11")
    assert result["title"] == "Ce poste est un peu juste pour Windows 11."
    assert result["question"] == "Êtes-vous sûr(e) de votre choix ?"
    for spec in ("Modèle : HP Exemple 800 G4 SFF", "Processeur : Intel Core i3-8100 (8ᵉ génération)",
                 "Mémoire : 4 Go", "Disque : 500 Go (mécanique)", "Démarrage : UEFI", "TPM : 2.0"):
        assert spec in result["specs"]
    assert any("Core i3" in reason for reason in result["reasons"])
    assert any("disque est mécanique" in reason for reason in result["reasons"])

    old = assess(hw("lenovo_old"), "windows11")
    assert "Disque : 160 Go (mécanique)" in old["specs"] and "TPM : absent" in old["specs"]
    assert "Démarrage : BIOS classique" in old["specs"]
    assert old["reasons"][0].startswith("Le processeur") or "TPM" in old["reasons"][0]  # les motifs graves d'abord
    assert "fortement déconseillée" in old["question"]


def test_missing_information_is_not_presented_as_a_weak_pc(hw):
    result = assess(hw("missing_fields"), "windows11")
    assert result["title"] == "Ce poste n'a pas pu être vérifié entièrement pour Windows 11."
    assert assess(hw("lenovo_old"), "windows10")["title"] != result["title"]
    assert "(Intel Core i3-8100, 8ᵉ génération)" in assess(hw("hp_i3_8100"), "windows11")["reasons"][0]


def test_never_crashes_on_garbage():
    for hardware in (None, {}, [], "texte", {"cpu_name": 123, "ram_mb": "beaucoup", "disks": "x", "tpm": 2.0},
                     {"disks": [None, 3, {"size_gb": "?", "kind": 7}], "bios_date": 2017, "firmware": ["uefi"]}):
        for family in ("windows10", "windows11"):
            assert assess(hardware, family)["level"] in ("ok", "warn", "discouraged")
    with pytest.raises(ValueError):
        assess({}, "linux")


def test_reported_ram_slightly_below_the_nominal_value_is_accepted():
    modern = {"cpu_name": "Intel(R) Core(TM) i5-12400", "ram_mb": 7600, "disks": [{"size_gb": 59, "kind": "ssd"}],
              "tpm": "2.0", "firmware": "uefi", "bios_date": "2022-01-01"}  # « 8 Go » et « 64 Go » commerciaux
    assert assess(modern, "windows11")["level"] == "ok"


CPUS = [
    # nom, génération Intel, série Ryzen, AMD ancien, Core i3, entrée de gamme
    ("Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz", 6, None, False, False, False),
    ("Intel(R) Core(TM) i3-8100 CPU @ 3.60GHz", 8, None, False, True, False),
    ("11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz", 11, None, False, False, False),
    ("Intel(R) Core(TM) i5-1135G7 @ 2.40GHz", 11, None, False, False, False),
    ("Intel(R) Core(TM) i7-10700K CPU @ 3.80GHz", 10, None, False, False, False),
    ("Intel(R) Core(TM) i5-12400", 12, None, False, False, False),
    ("Intel(R) Core(TM) i7-4770 CPU @ 3.40GHz", 4, None, False, False, False),
    ("Intel(R) Core(TM) i5 CPU         750  @ 2.67GHz", 1, None, False, False, False),
    ("Intel(R) Core(TM) Ultra 7 155H", 14, None, False, False, False),
    ("Intel(R) Core(TM)2 Duo CPU     E8400  @ 3.00GHz", 0, None, False, False, False),
    ("Intel(R) Pentium(R) CPU G4560 @ 3.50GHz", 7, None, False, False, True),
    ("Intel(R) Celeron(R) N4020 CPU @ 1.10GHz", 8, None, False, False, True),
    ("Intel(R) Celeron(R) CPU G1610 @ 2.60GHz", 3, None, False, False, True),
    ("Intel(R) Atom(TM) CPU N270 @ 1.60GHz", 0, None, False, False, True),
    ("AMD Ryzen 5 3600 6-Core Processor", None, 3, False, False, False),
    ("AMD Ryzen 5 PRO 2400GE w/ Radeon Vega Graphics", None, 2, False, False, False),
    ("AMD Ryzen 7 1700 Eight-Core Processor", None, 1, False, False, False),
    ("AMD Ryzen 9 7950X3D 16-Core Processor", None, 7, False, False, False),
    ("AMD A10-7800 Radeon R7, 12 Compute Cores 4C+8G", None, None, True, False, False),
    ("AMD Athlon(tm) II X2 250 Processor", None, None, True, False, False),
    ("AMD Athlon(tm) 64 X2 Dual Core Processor 5000+", None, None, True, False, False),
]


@pytest.mark.parametrize("name,gen,zen,amd_old,i3,entry", CPUS)
def test_cpu_names(name, gen, zen, amd_old, i3, entry):
    cpu = parse_cpu(name)
    assert cpu.known
    assert (cpu.gen, cpu.zen, cpu.amd_old, cpu.i3, cpu.entry_level) == (gen, zen, amd_old, i3, entry)


@pytest.mark.parametrize("name", [None, "", "   ", 42, "Processeur mystère", "Intel(R) Xeon(R) CPU E3-1230 v3 @ 3.30GHz"])
def test_unknown_cpu_gives_a_warning_not_an_error(name):
    assert not parse_cpu(name).known
    result = assess({"cpu_name": name, "ram_mb": 16384, "disks": [{"size_gb": 512, "kind": "ssd"}],
                     "tpm": "2.0", "firmware": "uefi", "bios_date": "2023-01-01"}, "windows11")
    assert result["level"] == "warn" and "processeur n'a pas été reconnu" in result["reasons"][0]


def test_cpu_display_name():
    assert parse_cpu("Intel(R) Core(TM) i3-8100 CPU @ 3.60GHz").text == "Intel Core i3-8100 (8ᵉ génération)"
    assert parse_cpu("Intel(R) Core(TM)2 Duo CPU     E8400  @ 3.00GHz").label == "Intel Core 2 Duo E8400"
