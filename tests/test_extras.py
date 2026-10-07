"""Offline tests for PewDiePie's AI search, the GitHub connector and Odysseus. Run: python -m unittest discover -s tests"""
import http.server
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("lai_extras", os.path.join(ROOT, "local_ai_installer.py"))
lai = importlib.util.module_from_spec(_spec)
sys.modules["lai_extras"] = lai
_spec.loader.exec_module(lai)

GB = 1024**3
PAGE = """
<a href="https://huggingface.co/pewdiepie/Ajax-9B">Download Ajax</a>
Weights: see huggingface.co/pewdiepie/Ajax-9B. Base model: https://huggingface.co/Qwen/Qwen3.5-9B
Training data: https://huggingface.co/datasets/pewdiepie/ajax-training  Demo: https://huggingface.co/spaces/x/ajax-demo
Or run it: ollama run pewdiepie/ajax:9b   (get Ollama at https://ollama.com/download)
Quantized: hf.co/pewdiepie/Ajax-9B-GGUF:Q4_K_M
"""


def fetcher(pages=None, featured=None):
    """Fake fetch_text: official pages -> text, featured.json -> JSON; anything else (or None) fails."""
    def fetch(url, timeout=15):
        if url == lai.FEATURED_URL:
            if featured is None:
                raise OSError("offline")
            return json.dumps({"pewdiepie": featured})
        text = (pages or {}).get(url)
        if text is None:
            raise OSError("offline")
        return text
    return fetch


def hf(models):
    """Fake Hugging Face API. models: {repo: {"files": [(name, gb)], "created": iso, "quantized": [ids]}}."""
    calls = []

    def detail(url):
        calls.append(url)
        if "?filter=" in url:
            base = lai.urllib.parse.unquote(url.split("?filter=")[1].split("&")[0]).split("base_model:quantized:")[1]
            return [{"id": rid} for rid in models.get(base, {}).get("quantized", [])]
        rid = url.split("/api/models/")[1].split("?")[0]
        if rid not in models:
            raise OSError("404")
        m = models[rid]
        return {"createdAt": m.get("created", "2026-10-03T00:00:00Z"),
                "siblings": [{"rfilename": n, "size": int(g * GB)} for n, g in m.get("files", [])]}
    detail.calls = calls
    return detail


class OfficialSources(unittest.TestCase):
    def test_only_ajax_model_links_from_official_pages(self):
        refs = lai.official_refs(fetcher({lai.PEWDIEPIE_PAGES[0][1]: PAGE}))
        self.assertEqual(refs["hf"], ["pewdiepie/Ajax-9B", "pewdiepie/Ajax-9B-GGUF"])  # no datasets/spaces/base
        self.assertEqual(refs["ollama"], ["pewdiepie/ajax:9b"])
        self.assertEqual(refs["reached"], ["PewDiePie's Ajax page"])

    def test_featured_list_is_used_as_is(self):
        refs = lai.official_refs(fetcher({}, {"huggingface": ["odysseus-dev/Odin-12B"], "ollama": ["pewdiepie/odin"]}))
        self.assertEqual((refs["hf"], refs["ollama"], refs["reached"]),
                         (["odysseus-dev/Odin-12B"], ["pewdiepie/odin"], ["this installer's list"]))

    def test_shipped_featured_file_is_valid(self):
        with open(os.path.join(ROOT, "featured.json"), encoding="utf-8") as f:
            data = json.load(f)["pewdiepie"]
        self.assertIsInstance(data["huggingface"], list)
        self.assertIsInstance(data["ollama"], list)


class FindPewDiePieAI(unittest.TestCase):
    def find(self, models, page="", featured=None, budget=8.0, ollama_size=None):
        pages = {lai.PEWDIEPIE_PAGES[1][1]: page}
        detail = hf(models)
        res = lai.pewdiepie_ai(budget, fetch=fetcher(pages, featured), detail=detail,
                               ollama_size=ollama_size or (lambda tag: None))
        return res, detail.calls

    def test_unreachable_and_not_released(self):
        self.assertEqual(lai.pewdiepie_ai(8, fetch=fetcher(), detail=hf({}))["status"], "unreachable")
        self.assertEqual(self.find({}, page="Ajax is coming soon")[0]["status"], "not_released")

    def test_lookalikes_are_never_used(self):
        fake = {"Ajax-PewDiePie/PewDiePie-Ajax-Uncensored-AI": {"files": [("ajax-Q4_K_M.gguf", 5)]}}
        res, calls = self.find(fake, page="no official link yet")
        self.assertEqual(res["status"], "not_released")
        self.assertEqual(calls, [])

    def test_official_gguf_picks_best_quant_that_fits(self):
        models = {"pewdiepie/Ajax-9B": {"files": [("Ajax-9B-Q4_K_M.gguf", 5.6), ("Ajax-9B-Q8_0.gguf", 9.5),
                                                  ("Ajax-9B-Q6_K.gguf", 7.4)]}}
        res, _ = self.find(models, featured={"huggingface": ["pewdiepie/Ajax-9B"]})
        self.assertEqual(res["status"], "found")
        self.assertEqual(res["tag"], "hf.co/pewdiepie/Ajax-9B:Q6_K")
        self.assertEqual(res["size_gb"], 7.4)

    def test_too_big(self):
        models = {"pewdiepie/Ajax-9B": {"files": [("Ajax-9B-Q4_K_M.gguf", 5.6)]}}
        res, _ = self.find(models, featured={"huggingface": ["pewdiepie/Ajax-9B"]}, budget=3.0)
        self.assertEqual((res["status"], res["need_gb"]), ("too_big", 5.6))

    def test_weights_only_uses_trusted_repackager_only(self):
        models = {
            "pewdiepie/Ajax-9B": {"files": [], "quantized": ["evil/Ajax-9B-GGUF", "bartowski/pewdiepie_Ajax-9B-GGUF"]},
            "evil/Ajax-9B-GGUF": {"files": [("x-Q8_0.gguf", 7.0)]},
            "bartowski/pewdiepie_Ajax-9B-GGUF": {"files": [("x-Q4_K_M.gguf", 5.6), ("x-Q5_K_M.gguf", 6.5)]},
        }
        res, calls = self.find(models, page=PAGE)
        self.assertEqual(res["tag"], "hf.co/bartowski/pewdiepie_Ajax-9B-GGUF:Q5_K_M")
        self.assertEqual(res["official"], "pewdiepie/Ajax-9B")
        self.assertFalse(any("evil/" in c for c in calls), calls)

    def test_weights_only_without_trusted_build(self):
        models = {"pewdiepie/Ajax-9B": {"files": [], "quantized": ["evil/Ajax-9B-GGUF"]},
                  "evil/Ajax-9B-GGUF": {"files": [("x-Q4_K_M.gguf", 5.0)]}}
        res, _ = self.find(models, featured={"huggingface": ["pewdiepie/Ajax-9B"]})
        self.assertEqual((res["status"], res["official"]), ("no_gguf", "pewdiepie/Ajax-9B"))

    def test_newest_official_release_wins(self):
        models = {"pewdiepie/Ajax-9B": {"files": [("a-Q4_K_M.gguf", 5)], "created": "2026-10-03T00:00:00Z"},
                  "pewdiepie/Ajax-2-9B": {"files": [("b-Q4_K_M.gguf", 5)], "created": "2027-03-01T00:00:00Z"}}
        res, _ = self.find(models, featured={"huggingface": ["pewdiepie/Ajax-9B", "pewdiepie/Ajax-2-9B"]})
        self.assertEqual(res["official"], "pewdiepie/Ajax-2-9B")

    def test_ollama_tag_from_official_page(self):
        res, _ = self.find({}, page="ollama pull pewdiepie/ajax:9b", ollama_size=lambda tag: 5.5)
        self.assertEqual((res["status"], res["tag"]), ("found", "pewdiepie/ajax:9b"))
        res, _ = self.find({}, page="ollama pull pewdiepie/ajax:9b", ollama_size=lambda tag: 30.0)
        self.assertEqual(res["status"], "too_big")

    def test_messages_never_crash(self):
        for res in ({"status": "found", "official": "a/b", "repo": "c/d", "tag": "hf.co/c/d:Q4_K_M", "size_gb": 5},
                    {"status": "too_big", "official": "a/b", "need_gb": 9}, {"status": "no_gguf", "official": "a/b"},
                    {"status": "not_released"}, {"status": "unreachable"}):
            lai.show_pewdiepie(res, 8)


class GitHubConnector(unittest.TestCase):
    def test_asset_names(self):
        saved = lai.OS, lai.IS_WIN, platform.machine
        try:
            for os_name, machine, want in (("Windows", "AMD64", "github-mcp-server_Windows_x86_64.zip"),
                                           ("Windows", "ARM64", "github-mcp-server_Windows_arm64.zip"),
                                           ("Darwin", "arm64", "github-mcp-server_Darwin_arm64.tar.gz"),
                                           ("Linux", "x86_64", "github-mcp-server_Linux_x86_64.tar.gz"),
                                           ("Linux", "aarch64", "github-mcp-server_Linux_arm64.tar.gz"),
                                           ("Linux", "riscv64", None)):
                lai.OS, lai.IS_WIN = os_name, os_name == "Windows"
                platform.machine = lambda m=machine: m
                self.assertEqual(lai.gh_mcp_asset(), want, (os_name, machine))
        finally:
            lai.OS, lai.IS_WIN, platform.machine = saved

    def test_read_only_and_lockdown_by_default(self):
        cmd = lai.github_cmd()
        self.assertEqual(cmd[1:], ["stdio", "--lockdown-mode", "--read-only"])
        self.assertNotIn("--read-only", lai.github_cmd(write=True))
        self.assertIn("--lockdown-mode", lai.github_cmd(write=True))

    def run_install(self, good_checksum=True, digest_in_api=True):
        with tempfile.TemporaryDirectory() as d:
            asset = lai.gh_mcp_asset()
            name = "github-mcp-server" + (".exe" if lai.IS_WIN else "")
            arc = os.path.join(d, "src-" + asset)
            if asset.endswith(".zip"):
                with zipfile.ZipFile(arc, "w") as z:
                    z.writestr(name, b"fake binary")
            else:
                with tarfile.open(arc, "w:gz") as t:
                    info = tarfile.TarInfo(name)
                    info.size = 11
                    t.addfile(info, io.BytesIO(b"fake binary"))
            digest = lai.sha256_file(arc) if good_checksum else "0" * 64
            release = {"tag_name": "v2.0.1", "assets": [
                {"name": asset, "browser_download_url": "https://example/" + asset,
                 **({"digest": "sha256:" + digest} if digest_in_api else {})},
                {"name": "github-mcp-server_2.0.1_checksums.txt", "browser_download_url": "https://example/sums"}]}
            saved = lai.BIN_DIR, lai.http_json, lai.download, lai.fetch_text, lai.run
            lai.BIN_DIR = os.path.join(d, "bin")
            lai.http_json = lambda url, timeout=15: release
            lai.download = lambda url, dest, label=None: shutil.copy(arc, dest)
            lai.fetch_text = lambda url, timeout=15: f"{digest}  {asset}\n"
            lai.run = lambda cmd, timeout=60, env=None: "GitHub MCP Server\nVersion: 2.0.1"
            try:
                return lai.install_github_mcp({}), os.path.isfile(lai.gh_mcp_path())
            finally:
                lai.BIN_DIR, lai.http_json, lai.download, lai.fetch_text, lai.run = saved

    @unittest.skipIf(lai.gh_mcp_asset() is None, "no GitHub connector build for this machine")
    def test_install_checks_the_published_checksum(self):
        self.assertEqual(self.run_install(), ("v2.0.1", True))
        self.assertEqual(self.run_install(digest_in_api=False), ("v2.0.1", True))  # from the checksums file
        self.assertEqual(self.run_install(good_checksum=False), (None, False))

    def test_continue_config_gets_github_and_pewdiepie(self):
        with tempfile.TemporaryDirectory() as d:
            old_home = lai.HOME
            lai.HOME = d
            try:
                cmd = [r"C:\Users\O'Brien\.local_ai_installer\bin\github-mcp-server.exe", "stdio", "--read-only"]
                self.assertTrue(lai.write_continue_config("coder:1", "chat:1", None, "hf.co/p/Ajax:Q4_K_M", cmd))
                with open(lai.continue_config_path(), encoding="utf-8") as f:
                    text = f.read()
            finally:
                lai.HOME = old_home
        self.assertIn("mcpServers:\n  - name: GitHub\n", text)
        self.assertIn(r"command: 'C:\Users\O''Brien\.local_ai_installer\bin\github-mcp-server.exe'", text)
        self.assertIn("args: ['stdio', '--read-only']", text)
        self.assertIn('model: "hf.co/p/Ajax:Q4_K_M"', text)


class FakeOdysseus(http.server.BaseHTTPRequestHandler):
    """Just enough of Odysseus's API: login (cookie), list / add / delete MCP servers."""
    servers = []

    def log_message(self, *a):
        pass

    def reply(self, obj, status=200, cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def authed(self):
        return "session=ok" in (self.headers.get("Cookie") or "")

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/api/auth/login":
            ok = json.loads(data) == {"username": "admin", "password": "pw-123456789"}
            return self.reply({"ok": ok}, 200 if ok else 401, "session=ok; HttpOnly; Path=/" if ok else None)
        if self.path == "/api/mcp/servers" and self.authed():
            boundary = self.headers["Content-Type"].split("boundary=")[1]
            fields = {}
            for part in data.decode().split("--" + boundary)[1:-1]:
                head, _, value = part.partition("\r\n\r\n")
                fields[head.split('name="')[1].split('"')[0]] = value[:-2]
            srv = {"id": "s%d" % len(FakeOdysseus.servers), "name": fields["name"], "command": fields["command"],
                   "args": json.loads(fields["args"]), "transport": fields["transport"]}
            FakeOdysseus.servers.append(srv)
            return self.reply({"id": srv["id"], "connected": True})
        self.reply({"detail": "nope"}, 401)

    def do_GET(self):
        if self.path == "/api/mcp/servers" and self.authed():
            return self.reply(FakeOdysseus.servers)
        self.reply({"detail": "nope"}, 401)

    def do_DELETE(self):
        if self.path.startswith("/api/mcp/servers/") and self.authed():
            FakeOdysseus.servers[:] = [s for s in FakeOdysseus.servers if s["id"] != self.path.rsplit("/", 1)[1]]
            return self.reply({"ok": True})
        self.reply({"detail": "nope"}, 401)


class Odysseus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (lai.STATE_DIR, lai.ODYSSEUS_DIR, lai.ODYSSEUS_DATA, lai.ODYSSEUS_LOGIN, lai.download)
        lai.STATE_DIR = self.tmp
        lai.ODYSSEUS_DIR = os.path.join(self.tmp, "odysseus")
        lai.ODYSSEUS_DATA = os.path.join(self.tmp, "odysseus-data")
        lai.ODYSSEUS_LOGIN = os.path.join(self.tmp, "odysseus-login.txt")

    def tearDown(self):
        lai.STATE_DIR, lai.ODYSSEUS_DIR, lai.ODYSSEUS_DATA, lai.ODYSSEUS_LOGIN, lai.download = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_keeps_data_outside_the_program_folder(self):
        env = lai.odysseus_env()
        self.assertEqual(env["ODYSSEUS_DATA_DIR"], lai.ODYSSEUS_DATA)
        self.assertTrue(env["DATABASE_URL"].startswith("sqlite:///"))
        self.assertNotIn("\\", env["DATABASE_URL"])
        self.assertTrue(env["DATABASE_URL"].endswith("odysseus-data/app.db"))
        self.assertEqual((env["AUTH_ENABLED"], env["LOCALHOST_BYPASS"]), ("true", "false"))

    def zip_with(self, files):
        path = os.path.join(self.tmp, "src.zip")
        with zipfile.ZipFile(path, "w") as z:
            for name, data in files.items():
                z.writestr(name, data)
        return path

    def test_update_swaps_program_and_keeps_old_copy_on_bad_download(self):
        good = self.zip_with({"odysseus-abc/app.py": "v1", "odysseus-abc/requirements.txt": "fastapi"})
        lai.download = lambda url, dest, label=None: shutil.copy(good, dest)
        self.assertTrue(lai.fetch_odysseus("a" * 40))
        with open(os.path.join(lai.ODYSSEUS_DIR, "app.py")) as f:
            self.assertEqual(f.read(), "v1")
        bad = self.zip_with({"something-else/readme.md": "?"})
        lai.download = lambda url, dest, label=None: shutil.copy(bad, dest)
        self.assertFalse(lai.fetch_odysseus("b" * 40))
        with open(os.path.join(lai.ODYSSEUS_DIR, "app.py")) as f:
            self.assertEqual(f.read(), "v1")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["odysseus", "src.zip"])  # no leftovers

    def test_login_file(self):
        lai.save_odysseus_login("pw-123456789")
        self.assertEqual(lai.odysseus_password(), "pw-123456789")
        if not lai.IS_WIN:
            self.assertEqual(os.stat(lai.ODYSSEUS_LOGIN).st_mode & 0o777, 0o600)

    def test_registers_github_once_and_replaces_outdated(self):
        lai.save_odysseus_login("pw-123456789")
        FakeOdysseus.servers = []
        server = http.server.HTTPServer(("127.0.0.1", 0), FakeOdysseus)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        try:
            ro, rw = ["/x/github-mcp-server", "stdio", "--read-only"], ["/x/github-mcp-server", "stdio"]
            self.assertEqual(lai.odysseus_add_github(port, ro), "added")
            self.assertEqual(lai.odysseus_add_github(port, ro), "present")  # already there: nothing added
            self.assertEqual([(s["name"], s["command"], s["args"]) for s in FakeOdysseus.servers],
                             [("GitHub", ro[0], ro[1:])])
            self.assertEqual(lai.odysseus_add_github(port, rw), "added")  # settings changed: replaced
            self.assertEqual([s["args"] for s in FakeOdysseus.servers], [rw[1:]])
            lai.save_odysseus_login("wrong-password")
            self.assertIsNone(lai.odysseus_add_github(port, ro))
        finally:
            server.shutdown()
            server.server_close()


class Processes(unittest.TestCase):
    def test_only_stops_processes_that_are_ours(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            alive = lambda: proc.poll() is None  # noqa: E731
            self.assertFalse(lai.stop_process(proc.pid, ("uvicorn", "odysseus"), alive))
            time.sleep(0.5)
            self.assertTrue(alive())
            self.assertTrue(lai.stop_process(proc.pid, ("time.sleep(120)",), alive))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


class CommandLine(unittest.TestCase):
    def test_launch_targets(self):
        p = lai.build_parser()
        self.assertEqual(p.parse_args(["--launch"]).launch, "chat")
        self.assertEqual(p.parse_args(["--launch", "odysseus"]).launch, "odysseus")
        a = p.parse_args(["--launch", "--no-open"])
        self.assertEqual((a.launch, a.no_open), ("chat", True))
        self.assertIsNone(p.parse_args([]).launch)

    def test_named_shortcuts(self):
        with tempfile.TemporaryDirectory() as d:
            saved = lai.HOME, lai.IS_WIN, lai.IS_MAC, lai.have
            lai.HOME, lai.IS_WIN, lai.IS_MAC = d, False, False
            lai.have = lambda cmd: False
            os.makedirs(os.path.join(d, "Desktop"))
            try:
                made = lai.make_shortcuts(["/py", "/copy", "--launch", "odysseus"], "Odysseus AI", "Start Odysseus")
                self.assertEqual(made, [os.path.join(d, "Desktop", "Odysseus AI.desktop")])
                with open(made[0]) as f:
                    text = f.read()
                self.assertIn("Name=Odysseus AI\n", text)
                self.assertIn('Exec="/py" "/copy" "--launch" "odysseus"\n', text)
            finally:
                lai.HOME, lai.IS_WIN, lai.IS_MAC, lai.have = saved


if __name__ == "__main__":
    unittest.main()
