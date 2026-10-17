"""Mises à jour : description en ligne, téléchargement vérifié, remplacement de
l'exe, vérifications en arrière-plan (serveur HTTP local)."""

import hashlib
import http.server
import json
import threading

import pytest

from conftest import pump


@pytest.fixture
def server(monkeypatch):
    """Petit serveur HTTP local : files[chemin] = contenu (bytes)."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    files: dict[str, bytes] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (API http.server)
            body = files.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, files
    httpd.shutdown()
    httpd.server_close()


def publish(base, files, build=7, exe=b"MZ nouvel exe REKA" * 100, sha=None):
    files["/REKA.exe"] = exe
    files["/version.json"] = json.dumps({
        "version": "0.6.0", "build": build, "commit": "abc", "url": f"{base}/REKA.exe",
        "sha256": sha or hashlib.sha256(exe).hexdigest(), "size": len(exe),
        "notes": "Nouveautés", "published": "2026-10-17T00:00:00+00:00"}).encode()
    return exe


def build_info(build=5):
    from core.updater import BuildInfo

    return BuildInfo("0.5.0", build, "def", "regret-py/rekav2", "build-main")


def test_build_info_and_urls(tmp_path):
    from core.updater import BuildInfo, load_build_info, version_url

    assert load_build_info(tmp_path / "absent.json") is None        # développement
    path = tmp_path / "build_info.json"
    path.write_text(json.dumps({"version": "0.5.0", "build": 12, "commit": "c",
                                "repo": "o/r", "tag": "build-x"}))
    info = load_build_info(path)
    assert info == BuildInfo("0.5.0", 12, "c", "o/r", "build-x")
    assert info.describe() == "0.5.0 (build 12)"
    assert version_url({}, info) == "https://github.com/o/r/releases/download/build-x/version.json"
    assert version_url({"update": {"url": "http://x/v.json"}}, info) == "http://x/v.json"
    assert version_url({}, None) is None
    path.write_text("{pas du json")
    assert load_build_info(path) is None


def test_parse_and_compare():
    from core.updater import UpdateError, is_newer, parse_update

    good = {"version": "0.6.0", "build": 9, "url": "https://h/REKA.exe", "sha256": "a" * 64,
            "size": 10}
    update = parse_update(good)
    assert update.build == 9 and update.describe() == "0.6.0 (build 9)"
    assert is_newer(update, build_info(5)) and not is_newer(update, build_info(9))
    assert not is_newer(update, build_info(5), skipped=9)              # version ignorée
    assert not is_newer(update, None)
    for bad in ({**good, "build": "x"}, {**good, "sha256": "court"}, {**good, "url": "file:///c"},
                {k: v for k, v in good.items() if k != "url"}):
        with pytest.raises(UpdateError):
            parse_update(bad)


def test_fetch_and_download(server, appdata, tmp_path):
    from core.updater import UpdateError, download, fetch_update

    base, files = server
    exe = publish(base, files)
    update = fetch_update(f"{base}/version.json")
    assert update.build == 7 and update.size == len(exe)
    seen = []
    path = download(update, tmp_path / "REKA-7.exe", lambda got, total: seen.append((got, total)))
    assert path.read_bytes() == exe and seen[-1] == (len(exe), len(exe))
    assert not list(tmp_path.glob("*.part"))

    with pytest.raises(UpdateError):
        fetch_update(f"{base}/absent.json")
    files["/version.json"] = b"[1, 2]"
    with pytest.raises(UpdateError):
        fetch_update(f"{base}/version.json")

    publish(base, files, sha="0" * 64)                                   # fichier abîmé
    with pytest.raises(UpdateError, match="abîmé"):
        download(fetch_update(f"{base}/version.json"), tmp_path / "REKA-8.exe")
    assert not (tmp_path / "REKA-8.exe").exists() and not list(tmp_path.glob("*.part"))
    with pytest.raises(UpdateError, match="annulé"):
        download(update, tmp_path / "REKA-9.exe", cancelled=lambda: True)


def test_install_and_cleanup(tmp_path, appdata, monkeypatch):
    from core import updater
    from core.updater import UpdateError, cleanup_previous, install, update_dir

    current, new = tmp_path / "REKA.exe", tmp_path / "dl" / "REKA-7.exe"
    new.parent.mkdir()
    current.write_bytes(b"ancien")
    new.write_bytes(b"nouveau")
    install(new, current)
    assert current.read_bytes() == b"nouveau"
    assert (tmp_path / "REKA.old.exe").read_bytes() == b"ancien" and not new.exists()
    update_dir().mkdir(parents=True)
    (update_dir() / "REKA-6.exe").write_bytes(b"x")
    assert cleanup_previous(current)
    assert not (tmp_path / "REKA.old.exe").exists() and not update_dir().exists()

    # Échec pendant l'installation : l'exe d'origine est remis en place
    new.write_bytes(b"encore")

    def broken_move(*_args):
        raise OSError("disque plein")

    monkeypatch.setattr(updater.shutil, "move", broken_move)
    with pytest.raises(UpdateError, match="disque plein"):
        install(new, current)
    assert current.read_bytes() == b"nouveau" and not (tmp_path / "REKA.old.exe").exists()


def test_update_checker(qapp, server, cfg):
    from core.updater import UpdateChecker

    base, files = server
    publish(base, files, build=7)
    cfg["update"]["url"] = f"{base}/version.json"
    checker = UpdateChecker(cfg, build_info(5))
    events = []
    checker.available.connect(lambda u: events.append(("available", u.build)))
    checker.up_to_date.connect(lambda: events.append(("up_to_date",)))
    checker.failed.connect(lambda message: events.append(("failed",)))

    def run(manual=False):
        events.clear()
        assert checker.check(manual)
        for _ in range(100):
            pump(qapp, 0.05)
            if events:
                return events[0]
        return None

    assert checker.enabled
    assert run() == ("available", 7) and checker.latest.build == 7
    cfg["update"]["skipped_build"] = 7
    assert run() == ("up_to_date",)                      # version ignorée…
    assert run(manual=True) == ("available", 7)          # … sauf recherche manuelle
    files.pop("/version.json")
    assert run() == ("failed",)
    assert not UpdateChecker(cfg, None).check()          # développement : rien


def test_update_downloader(qapp, server, appdata):
    from core.updater import UpdateDownloader, fetch_update

    base, files = server
    exe = publish(base, files)
    downloader = UpdateDownloader(fetch_update(f"{base}/version.json"))
    done, progress = [], []
    downloader.finished.connect(done.append)
    downloader.progress.connect(lambda got, total: progress.append(got))
    downloader.start()
    for _ in range(100):
        pump(qapp, 0.05)
        if done:
            break
    assert done and done[0].read_bytes() == exe and progress[-1] == len(exe)
