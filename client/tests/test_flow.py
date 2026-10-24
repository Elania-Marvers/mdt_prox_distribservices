"""Déroulé complet de deploy_client contre un faux serveur (dry-run, matériel simulé)."""
import json

import pytest

import deploy_client


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    cfg = tmp_path / "client.json"
    cfg.write_text(json.dumps({"penalty_seconds": 3}), encoding="utf-8")
    monkeypatch.setattr(deploy_client, "CONFIG_PATH", str(cfg))


def go(srv, hw="modern"):
    return deploy_client.main(["--server", srv.url, "--simulate", hw, "--dry-run"])


def posts(srv, path):
    return [c for c in srv.calls if c[0] == "POST" and c[1] == path]


def test_code_vide_penalite_sans_appel_api(fake_server, answers, capsys):
    answers([""])
    assert go(fake_server) == 0
    out = capsys.readouterr().out
    assert "forcer le passage" in out and "Allez vous faire cuire un œuf" in out and "[SIMULATION] Extinction" in out
    assert not posts(fake_server, "/api/v1/session")


def test_trois_codes_faux_penalite(fake_server, answers, capsys):
    answers(["bad", "bad", "bad"])
    assert go(fake_server) == 0
    out = capsys.readouterr().out
    assert out.count("Code incorrect.") == 2 and "Il vous reste 2 essai(s)" in out
    assert len(posts(fake_server, "/api/v1/session")) == 3
    assert "Allez vous faire cuire un œuf" in out


def test_ip_bloquee_429_penalite_immediate(fake_server, answers, capsys):
    answers(["lock"])
    assert go(fake_server) == 0
    assert "Allez vous faire cuire un œuf" in capsys.readouterr().out


def test_installation_nominale_en_dry_run(fake_server, answers, capsys):
    answers(["good-ok", "ROOMA-01", "OUI"])
    assert go(fake_server) == 0
    out = capsys.readouterr().out
    assert "Ce PC convient parfaitement" in out and "[SIMULATION] dism /Apply-Image" in out
    start = posts(fake_server, "/api/v1/deploy/start")[0]
    assert start[2] == {"hostname": "ROOMA-01", "disk": "0", "confirmed_warning": False}
    assert start[3]["Authorization"] == "Bearer TOKEN"
    assert "MOT-DE-PASSE-SECRET" not in out and "TOKEN" not in out
    assert not posts(fake_server, "/api/v1/deploy/report")        # le dry-run ne prétend rien au serveur
    assert not [c for c in fake_server.calls if c[1].endswith("/download")]


def test_avertissement_confirme_est_transmis(fake_server, answers):
    answers(["good-warn", "oui", "ROOMA-01", "OUI"])
    assert go(fake_server, "hp-i3") == 0
    assert posts(fake_server, "/api/v1/deploy/start")[0][2]["confirmed_warning"] is True


def test_avertissement_refuse_retour_au_debut_sans_penalite(fake_server, answers, capsys):
    answers(["good-warn", "", "good-ok", "ROOMA-01", "OUI"])
    assert go(fake_server) == 0
    out = capsys.readouterr().out
    assert "on recommence depuis le début" in out and "Allez vous faire cuire" not in out
    assert len(posts(fake_server, "/api/v1/session")) == 2


def test_confirmation_finale_autre_que_OUI_annule(fake_server, answers):
    answers(["good-ok", "ROOMA-01", "oui"])               # "oui" minuscule : annulé, puis plus d'entrée -> arrêt
    assert go(fake_server) == 1
    assert not posts(fake_server, "/api/v1/deploy/start")


def test_nom_refuse_par_le_serveur_est_redemande(fake_server, answers, capsys):
    answers(["good-ok", "RESERVE-1", "OUI", "ROOMA-02", "OUI"])
    assert go(fake_server) == 0
    assert "Ce nom est réservé par le serveur." in capsys.readouterr().out
    assert [c[2]["hostname"] for c in posts(fake_server, "/api/v1/deploy/start")] == ["RESERVE-1", "ROOMA-02"]


def test_famille_de_systeme_non_prise_en_charge(fake_server, answers, capsys):
    answers(["good-linux"])
    assert go(fake_server) == 1
    assert "pas encore prise en charge" in capsys.readouterr().out
    assert not posts(fake_server, "/api/v1/deploy/start")


def test_serveur_injoignable_sort_en_erreur(answers, capsys, monkeypatch):
    monkeypatch.setattr(deploy_client.api, "wait_ready", lambda *a, **k: False)
    assert deploy_client.main(["--server", "http://127.0.0.1:1", "--simulate", "modern", "--dry-run"]) == 1
    assert "ne répond pas" in capsys.readouterr().out


def test_materiel_factice_sans_dry_run_refuse(capsys):
    assert deploy_client.main(["--server", "http://x", "--simulate", "modern"]) == 1


def test_ctrl_c_sort_proprement(fake_server, monkeypatch):
    def interrupt(prompt=""):
        raise KeyboardInterrupt
    monkeypatch.setattr("builtins.input", interrupt)
    assert go(fake_server) == 1
