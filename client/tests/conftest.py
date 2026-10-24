"""Outils communs : faux serveur d'API (http.server dans un thread) et entrées clavier simulées."""
import hashlib
import http.server
import io
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ASSESS_OK = {"level": "ok", "title": "Ce PC convient parfaitement", "reasons": [],
             "specs": ["Mémoire : 16 Go"], "question": None}
ASSESS_WARN = {"level": "warn", "title": "Ce PC est un peu juste", "reasons": ["Processeur Core i3."],
               "specs": ["Mémoire : 8 Go"], "question": "Êtes-vous sûr(e) de votre choix ?"}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        srv = self.server
        srv.calls.append(("GET", self.path, None, dict(self.headers)))
        if self.path == "/healthz":
            return self._send(200, {"status": "ok"})
        if self.path.endswith("/download"):
            if self.headers.get("Authorization") != "Bearer TOKEN":
                return self._send(401, {"detail": "bad_token"})
            blob, start = srv.blob, 0
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(blob) - start))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{len(blob) - 1}/{len(blob)}")
            self.end_headers()
            if srv.cut_at is not None and not srv.cut_done and start == 0:
                srv.cut_done = True  # première réponse coupée net : le client doit reprendre avec Range
                self.wfile.write(blob[:srv.cut_at])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(blob[start:])
            return
        self._send(404, {"detail": "not_found"})

    def do_POST(self):
        srv = self.server
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.loads(raw or b"{}")
        srv.calls.append(("POST", self.path, body, dict(self.headers)))
        if self.path == "/api/v1/session":
            return self._session(body.get("code", ""))
        if self.headers.get("Authorization") != "Bearer TOKEN":
            return self._send(401, {"detail": "bad_token"})
        if self.path == "/api/v1/deploy/start":
            if body["hostname"].startswith("RES"):
                return self._send(400, {"detail": "bad_hostname", "message": "Ce nom est réservé par le serveur."})
            return self._send(200, {
                "image_url": srv.url + "/api/v1/images/win11/download", "sha256": srv.sha, "size_bytes": len(srv.blob),
                "wim_index": 1, "unattend_xml": '<?xml version="1.0"?><p>MOT-DE-PASSE-SECRET</p>',
                "setup_complete_cmd": "@echo off\r\ndel unattend.xml\r\n"})
        if self.path == "/api/v1/deploy/report":
            return self._send(204)
        self._send(404, {"detail": "not_found"})

    def _session(self, code):
        ok = {"token": "TOKEN", "expires_in": 3600,
              "room": {"id": "ROOMA", "label": "Salle ROOMA", "name_prefix": "ROOMA-", "name_example": "ROOMA-01"},
              "image": {"id": "win11", "label": "Windows 11 - physique", "os_family": "windows11",
                        "size_bytes": 1000, "sha256": "x", "wim_index": 1},
              "assessment": ASSESS_OK}
        if code == "good-ok":
            return self._send(200, ok)
        if code == "good-warn":
            return self._send(200, {**ok, "assessment": ASSESS_WARN})
        if code == "good-linux":
            return self._send(200, {**ok, "image": {**ok["image"], "os_family": "linux"}})
        if code == "":
            return self._send(400, {"detail": "empty_code", "penalty": True})
        if code == "lock":
            return self._send(429, {"detail": "locked", "retry_after": 287, "penalty": True})
        self._send(401, {"detail": "bad_code", "attempts_left": 2, "penalty": False})


@pytest.fixture
def fake_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    srv.calls = []
    srv.blob = bytes(range(256)) * 800  # ~200 Ko
    srv.sha = hashlib.sha256(srv.blob).hexdigest()
    srv.cut_at, srv.cut_done = None, False
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(autouse=True)
def no_real_keyboard(monkeypatch):
    """Jamais de vrai clavier ni de vraie attente pendant les tests."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    monkeypatch.setattr("time.sleep", lambda s: None)


@pytest.fixture
def answers(monkeypatch):
    """answers(["a", "b"]) : les prochains input() renverront a puis b, puis EOFError."""
    def feed(values):
        it = iter(values)

        def fake_input(prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError from None
        monkeypatch.setattr("builtins.input", fake_input)
    return feed
