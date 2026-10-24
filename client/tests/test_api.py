import os

import pytest

import api

HW = {"model": "Test"}


def test_session_ok(fake_server):
    s = api.session(fake_server.url, "good-ok", HW)
    assert s["token"] == "TOKEN" and s["assessment"]["level"] == "ok"
    assert fake_server.calls[-1][2] == {"code": "good-ok", "hardware": HW}


def test_session_401_code_faux_sans_penalite(fake_server):
    with pytest.raises(api.ApiError) as e:
        api.session(fake_server.url, "bad", HW)
    assert e.value.status == 401 and e.value.attempts_left == 2 and not e.value.penalty
    assert e.value.user_message() == "Code incorrect."


def test_session_429_bloque_donc_penalite(fake_server):
    with pytest.raises(api.ApiError) as e:
        api.session(fake_server.url, "lock", HW)
    assert e.value.status == 429 and e.value.penalty and e.value.retry_after == 287
    assert "287" in e.value.user_message()


def test_session_400_code_vide_penalite(fake_server):
    with pytest.raises(api.ApiError) as e:
        api.session(fake_server.url, "", HW)
    assert e.value.status == 400 and e.value.penalty


def test_serveur_injoignable():
    with pytest.raises(api.ApiError) as e:
        api.session("http://127.0.0.1:1", "x", HW)
    assert e.value.status == 0 and "serveur" in e.value.user_message()
    assert api.wait_ready("http://127.0.0.1:1", attempts=2) is False


def test_messages_503_409_et_wait_ready(fake_server):
    assert "indisponible" in api.ApiError(503).user_message()
    assert "confirmer" in api.ApiError(409, {"detail": "warning_not_confirmed"}).user_message()
    assert api.wait_ready(fake_server.url) is True


def test_report_ne_leve_jamais(fake_server):
    assert api.report(fake_server.url, "TOKEN", "failed", "apply-image", "x" * 900) is True
    assert len(fake_server.calls[-1][2]["message"]) == 500
    assert api.report("http://127.0.0.1:1", "TOKEN", "success") is False


def test_telechargement_et_sha256(fake_server, tmp_path):
    dest = str(tmp_path / "w" / "install.wim")
    seen = []
    api.download(fake_server.url + "/api/v1/images/x/download", "TOKEN", dest, fake_server.sha,
                 len(fake_server.blob), progress=lambda d, t: seen.append((d, t)))
    assert open(dest, "rb").read() == fake_server.blob
    assert seen[-1] == (len(fake_server.blob), len(fake_server.blob))


def test_telechargement_reprend_avec_range_apres_coupure(fake_server, tmp_path):
    fake_server.cut_at = 70_000
    dest = str(tmp_path / "install.wim")
    api.download(fake_server.url + "/api/v1/images/x/download", "TOKEN", dest, fake_server.sha, len(fake_server.blob))
    assert open(dest, "rb").read() == fake_server.blob  # le SHA-256 a aussi été vérifié par download()
    ranges = [c[3].get("Range") for c in fake_server.calls if c[1].endswith("/download")]
    assert ranges == [None, "bytes=70000-"]


def test_sha256_different_supprime_le_fichier(fake_server, tmp_path):
    dest = str(tmp_path / "install.wim")
    with pytest.raises(api.DownloadError, match="corrompue"):
        api.download(fake_server.url + "/api/v1/images/x/download", "TOKEN", dest, "0" * 64, len(fake_server.blob))
    assert not os.path.exists(dest)


def test_telechargement_401_sans_reessayer(fake_server, tmp_path):
    with pytest.raises(api.ApiError) as e:
        api.download(fake_server.url + "/api/v1/images/x/download", "MAUVAIS", str(tmp_path / "a.wim"), "0" * 64)
    assert e.value.status == 401
    assert len([c for c in fake_server.calls if c[1].endswith("/download")]) == 1


def test_telechargement_abandonne_apres_3_essais(tmp_path):
    with pytest.raises(api.DownloadError, match="3 essais"):
        api.download("http://127.0.0.1:1/x", "T", str(tmp_path / "a.wim"), "0" * 64, 10)
