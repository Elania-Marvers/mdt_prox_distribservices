import ui as uimod
from ui import EGG_MESSAGE, UI, ascii_fold, hostname_error

DISKS = [{"name": "0", "size_gb": 256, "kind": "ssd"}, {"name": "1", "size_gb": 500, "kind": "hdd"}]


def test_nom_de_pc_valide_et_invalide():
    for good in ("ROOMA-01", "a", "PC-1", "A" * 15, "12ab"):
        assert hostname_error(good) is None, good
    for bad in ("", "A" * 16, "-PC", "12345", "PC 01", "PC_01", "PÇ-01", "PC.01"):
        assert hostname_error(bad), bad


def test_ascii_fold():
    assert ascii_fold("Allez vous faire cuire un œuf") == "Allez vous faire cuire un oeuf"
    assert ascii_fold("Êtes-vous sûr(e) ? «Élève» – ça") == 'Etes-vous sur(e) ? "Eleve" - ca'


def test_penalite_compte_a_rebours_et_extinction(capsys, monkeypatch):
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    UI(penalty_seconds=90, dry_run=True).penalty()
    out = capsys.readouterr().out
    assert sleeps == [1] * 90                       # une seconde par tour, jamais d'attente réelle ici
    assert "forcer le passage" in out and "ce poste ne sera pas installé" in out
    assert "01:30" in out and "00:01" in out and "00:00" in out
    assert EGG_MESSAGE == "Allez vous faire cuire un œuf" and EGG_MESSAGE in out
    assert "[SIMULATION] Extinction" in out


def test_penalite_en_ascii(capsys):
    UI(ascii_only=True, penalty_seconds=2, dry_run=True).penalty()
    out = capsys.readouterr().out
    assert "Allez vous faire cuire un oeuf" in out and "œ" not in out


def test_saisie_du_code_via_input(answers):
    answers(["MonCode"])
    assert UI().ask_code() == "MonCode"


def test_verdict_ok_ne_demande_rien(capsys, answers):
    answers([])  # toute question lèverait EOFError
    assert UI().show_assessment({"level": "ok", "title": "Parfait", "reasons": [], "specs": ["Mémoire : 16 Go"]})
    assert "Parfait" in capsys.readouterr().out


def test_verdict_warn_defaut_non(answers):
    a = {"level": "warn", "title": "Juste", "reasons": ["Core i3"], "specs": [], "question": "Sûr(e) ?"}
    for reply, expected in (("", False), ("non", False), ("peut-être", False), ("oui", True), ("OUI", True)):
        answers([reply])
        assert UI().show_assessment(a) is expected, reply


def test_confirmation_finale_exige_OUI_en_majuscules(answers, capsys):
    room, image = {"id": "ROOMA", "label": "Salle ROOMA"}, {"label": "Windows 11"}
    for reply, expected in (("OUI", True), ("oui", False), ("", False), ("OUI merci", False)):
        answers([reply])
        assert UI().final_confirm(room, image, "ROOMA-01", DISKS[0]) is expected, reply
    out = capsys.readouterr().out
    assert "ENTIÈREMENT EFFACÉ" in out and "ROOMA-01" in out


def test_nom_redemande_jusqua_valide(answers, capsys):
    answers(["12345", "-pc", "ROOMA-01"])
    assert UI().ask_hostname("ROOMA-", "ROOMA-01") == "ROOMA-01"
    assert capsys.readouterr().out.count("Le nom ne peut pas") == 2


def test_choix_du_disque(answers):
    assert UI().choose_disk([DISKS[0]]) == DISKS[0]            # un seul disque : proposé sans question
    answers(["7", "disque", "1"])
    assert UI().choose_disk(DISKS) == DISKS[1]
    assert UI().choose_disk([]) is None


def test_barre_de_progression(capsys):
    u = UI()
    u.progress(0, 2_000_000)
    u.progress(2_000_000, 2_000_000)
    out = capsys.readouterr().out
    assert "100 %" in out and "Mo/s" in out


def test_power_ne_touche_pas_au_mac(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(uimod.subprocess, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(uimod.sys, "platform", "darwin")
    UI().power("shutdown")
    assert not called and "non pris en charge" in capsys.readouterr().out
