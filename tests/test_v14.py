"""Offline tests for v1.4: unlocked-only models, helper models, Ollama local-only, Open WebUI login + model list,
Continue config ownership, and cleanup. Run: python -m unittest discover -s tests"""
import contextlib
import http.server
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("lai_v14", os.path.join(ROOT, "local_ai_installer.py"))
lai = importlib.util.module_from_spec(_spec)
sys.modules["lai_v14"] = lai
_spec.loader.exec_module(lai)


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def put(path, text, mode="w"):
    with open(path, mode) as f:
        f.write(text)


class Sandbox(unittest.TestCase):
    """A throw-away HOME / settings folder so nothing touches the real ones."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = {k: getattr(lai, k) for k in ("HOME", "STATE_DIR", "STATE_FILE", "LOG_FILE", "WEBUI_DATA", "WEBUI_LOG",
                                                  "WEBUI_LOGIN", "UV_CACHE", "ODYSSEUS_DIR", "ODYSSEUS_VENV",
                                                  "ODYSSEUS_DATA")}
        home = self.tmp.name
        lai.HOME = home
        lai.STATE_DIR = os.path.join(home, ".local_ai_installer")
        os.makedirs(lai.STATE_DIR)
        lai.STATE_FILE = os.path.join(lai.STATE_DIR, "state.json")
        lai.LOG_FILE = os.path.join(lai.STATE_DIR, "install.log")
        lai.WEBUI_DATA = os.path.join(lai.STATE_DIR, "webui-data")
        lai.WEBUI_LOG = os.path.join(lai.STATE_DIR, "webui.log")
        lai.WEBUI_LOGIN = os.path.join(lai.STATE_DIR, "webui-login.txt")
        lai.UV_CACHE = os.path.join(lai.STATE_DIR, "uv-cache")
        lai.ODYSSEUS_DIR = os.path.join(lai.STATE_DIR, "odysseus")
        lai.ODYSSEUS_VENV = os.path.join(lai.STATE_DIR, "odysseus-venv")
        lai.ODYSSEUS_DATA = os.path.join(lai.STATE_DIR, "odysseus-data")

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(lai, k, v)
        self.tmp.cleanup()


class World:
    """A fake Ollama: the models on disk, and what `ollama pull / rm / cp / create` do to them."""

    def __init__(self, present=(), pull_fail=(), run_fail=(), refuse=(), leak=(), fixable=()):
        self.present = list(present)
        self.pull_fail, self.run_fail, self.refuse, self.leak, self.fixable = (set(pull_fail), set(run_fail), set(refuse),
                                                                               set(leak), set(fixable))
        self.calls = []
        self.saved = {}

    def install(self):
        for name in ("subprocess.call", "installed_models", "verify", "thinking_leak", "refusal_check", "fix_gemma4",
                     "ollama_bin"):
            obj, attr = (lai.subprocess, "call") if name.startswith("subprocess") else (lai, name)
            self.saved[name] = getattr(obj, attr)
        lai.subprocess.call = self.call
        lai.installed_models = lambda: list(self.present)
        lai.verify = lambda model, prompt, label: model not in self.run_fail
        lai.thinking_leak = lambda model: model in self.leak
        lai.refusal_check = lambda model: (3, 3) if model in self.refuse else (0, 3)
        lai.fix_gemma4 = self.fix
        lai.ollama_bin = lambda: "ollama"
        return self

    def uninstall(self):
        for name, fn in self.saved.items():
            obj, attr = (lai.subprocess, "call") if name.startswith("subprocess") else (lai, name)
            setattr(obj, attr, fn)

    def fix(self, tag):
        if tag in self.fixable:
            name = "local-ai/" + tag.replace(":", "-")
            self.present.append(name)
            return name
        return None

    def call(self, cmd, *a, **k):
        self.calls.append(list(cmd[1:]))
        verb, args = cmd[1], cmd[2:]
        if verb == "pull":
            if args[0] in self.pull_fail:
                return 1
            if args[0] not in self.present:
                self.present.append(args[0])
        elif verb == "rm":
            self.present = [m for m in self.present if m != args[0]]
        elif verb == "cp":
            self.present.append(args[1])
        return 0

    def removed(self):
        return [c[1] for c in self.calls if c[0] == "rm"]


@contextlib.contextmanager
def world(**kw):
    w = World(**kw).install()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield w
    finally:
        w.uninstall()


class Unlocked(Sandbox):
    def test_standard_models_are_not_offered_unless_asked(self):
        for _, label, coder, chat in lai.TIERS:
            spec = {"usable_gb": lai.TIERS[[t[1] for t in lai.TIERS].index(label)][0]}
            rec = lai.recommend(spec)
            self.assertTrue(rec["coder"] and rec["chat"], label)
            self.assertTrue(all(lai.is_unlocked(t) for t in rec["coder"] + rec["chat"]), (label, rec))
            allowed = lai.recommend(spec, allow_standard=True)
            self.assertEqual((allowed["coder"], allowed["chat"]), (coder, chat))

    def test_refusals_are_recognised(self):
        for text in ("I'm sorry, but I cannot create or assist with the creation of bots.",
                     "I can't help with that request.", "Sorry, I won't write that.",
                     "As an AI language model, I cannot provide that.", "I must decline.",
                     "<think>hmm</think>I cannot assist with picking locks."):
            self.assertTrue(lai.is_refusal(text), text)
        for text in ("Sure! Here's how a pin tumbler lock works: ...", "Hello there!",
                     "Ha! Mondays are a crime against humanity, a #@!% disaster.", "",
                     "The villain laughs: 'I cannot be stopped, I will take this city!'"):
            self.assertFalse(lai.is_refusal(text), text)

    def test_strip_think(self):
        self.assertEqual(lai.strip_think("<think>a\nb</think>Hi"), "Hi")
        self.assertEqual(lai.strip_think("<think>unfinished thought"), "")
        self.assertEqual(lai.strip_think("plain"), "plain")

    def test_refusing_models_are_skipped_and_cleaned_up(self):
        with world(refuse={"a"}) as w:
            state = {}
            got = lai.pull_working(["a", "b"], "p", "chat model", state, "chat", probe=True)
            self.assertEqual(got, "b")
            self.assertEqual(w.present, ["b"])           # the refuser was deleted (we downloaded it just to test it)
            self.assertIn("b", state["pulled"])
            self.assertNotIn("a", state["pulled"])

    def test_if_all_refuse_the_best_one_is_kept_with_a_warning(self):
        out = io.StringIO()
        w = World(refuse={"a", "b"}).install()
        try:
            with contextlib.redirect_stdout(out):
                got = lai.pull_working(["a", "b"], "p", "chat model", {}, "chat", probe=True)
        finally:
            w.uninstall()
        self.assertEqual(got, "a")
        self.assertEqual(w.present, ["a"])
        self.assertIn("WARNING", out.getvalue())

    def test_models_you_already_had_are_never_deleted(self):
        with world(present=["mine"], run_fail={"mine"}, refuse={"x"}) as w:
            self.assertEqual(lai.pull_working(["mine", "good"], "p", "m", {}, "chat"), "good")
            self.assertIn("mine", w.present)
            self.assertNotIn("mine", w.removed())
            self.assertNotIn(["pull", "mine"], w.calls)   # already there: not downloaded again
        with world(present=["mine"], refuse={"mine"}) as w:
            lai.pull_working(["mine", "other"], "p", "m", {}, "chat", probe=True)
            self.assertNotIn("mine", w.removed())

    def test_raw_gemma_thinking_text_gets_fixed_or_the_model_is_dropped(self):
        with world(leak={"g"}, fixable={"g"}) as w:
            state = {}
            got = lai.pull_working(["g", "z"], "p", "chat model", state, "chat")
            self.assertEqual(got, "local-ai/g")
            self.assertEqual(w.present, ["local-ai/g"])  # the unfixed original is gone, the copy shares its files
        with world(leak={"g"}) as w:
            self.assertEqual(lai.pull_working(["g", "z"], "p", "chat model", {}, "chat"), "z")
            self.assertEqual(w.present, ["z"])

    def test_nothing_works(self):
        with world(pull_fail={"a"}, run_fail={"b"}) as w:
            self.assertIsNone(lai.pull_working(["a", "b"], "p", "m", {}, "chat", probe=True))
            self.assertEqual(w.present, [])

    def test_research_failure_keeps_a_working_model(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(lai.decide("chat", ["curated"], [], {"chat": {"tag": "hf.co/x/y:Q4"}}),
                             (["hf.co/x/y:Q4"], False))


class HelperModels(Sandbox):
    def test_helpers_get_clear_names_and_share_the_download(self):
        with world() as w:
            state = {}
            self.assertEqual(lai.helper_model(state, "autocomplete", lai.AUTOCOMPLETE_TAGS, lai.AUTOCOMPLETE_ALIAS),
                             lai.AUTOCOMPLETE_ALIAS)
            self.assertEqual(lai.helper_model(state, "embed", [lai.EMBED_MODEL], lai.EMBED_ALIAS), lai.EMBED_ALIAS)
            self.assertEqual(sorted(w.present), sorted([lai.AUTOCOMPLETE_ALIAS, lai.EMBED_ALIAS]))
            n = len(w.calls)
            lai.helper_model(state, "embed", [lai.EMBED_MODEL], lai.EMBED_ALIAS)   # again: nothing to do
            self.assertEqual(len(w.calls), n)

    def test_old_installs_are_converted_and_your_own_copy_is_kept(self):
        with world(present=["qwen2.5-coder:1.5b-base", "nomic-embed-text:latest"]) as w:
            state = {"autocomplete": "qwen2.5-coder:1.5b-base", "embed": "nomic-embed-text"}
            lai.migrate_ledger(state)
            lai.helper_model(state, "autocomplete", lai.AUTOCOMPLETE_TAGS, lai.AUTOCOMPLETE_ALIAS)
            self.assertNotIn("qwen2.5-coder:1.5b-base", w.present)       # ours: replaced by the alias
            self.assertIn(lai.AUTOCOMPLETE_ALIAS, w.present)
        with world(present=["qwen2.5-coder:1.5b-base"]) as w:           # you installed this one yourself
            lai.helper_model({}, "autocomplete", lai.AUTOCOMPLETE_TAGS, lai.AUTOCOMPLETE_ALIAS)
            self.assertIn("qwen2.5-coder:1.5b-base", w.present)
            self.assertIn(lai.AUTOCOMPLETE_ALIAS, w.present)

    def test_prune_only_removes_what_we_downloaded_and_nothing_in_use(self):
        with world(present=["old-chat", "new-chat", "yours", "helper"]) as w:
            state = {"pulled": {"old-chat": {}, "new-chat": {}, "helper": {}, "gone": {}}}
            removed = lai.prune_old_models(state, ["new-chat", "helper:latest", None])
            self.assertEqual(removed, ["old-chat"])
            self.assertEqual(sorted(w.present), ["helper", "new-chat", "yours"])
            self.assertNotIn("gone", state["pulled"])                      # already deleted elsewhere: forgotten
        with world(present=["m"]) as w:
            self.assertEqual(lai.prune_old_models({"pulled": {"m": {}}}, ["m"]), [])
            self.assertEqual(w.present, ["m"])


class OllamaLocalOnly(Sandbox):
    def test_cloud_switched_off_and_other_settings_kept(self):
        path = os.path.join(self.tmp.name, ".ollama", "server.json")
        self.assertTrue(lai.ollama_local_only())
        self.assertEqual(jload(path), {"disable_ollama_cloud": True})
        self.assertFalse(lai.ollama_local_only())                          # nothing to do the second time
        jdump({"other": 1}, path)
        self.assertTrue(lai.ollama_local_only())
        self.assertEqual(jload(path), {"other": 1, "disable_ollama_cloud": True})

    def test_a_file_we_cannot_read_is_left_alone(self):
        os.makedirs(os.path.join(self.tmp.name, ".ollama"))
        path = os.path.join(self.tmp.name, ".ollama", "server.json")
        put(path, "{not json")
        self.assertFalse(lai.ollama_local_only())
        with open(path) as f:
            self.assertEqual(f.read(), "{not json")

    def test_app_model_list_only_shows_yours(self):
        self.assertTrue(lai.write_model_recommendations([("chat:1", "Chat"), ("code:1", "Code")]))
        path = os.path.join(self.tmp.name, ".ollama", "cache", "model-recommendations.json")
        got = jload(path)["recommendations"]
        self.assertEqual([r["model"] for r in got], ["chat:1", "code:1"])
        self.assertFalse(lai.write_model_recommendations([("chat:1", "Chat"), ("code:1", "Code")]))
        self.assertFalse(lai.write_model_recommendations([]))

    def test_cloud_entries_are_removed(self):
        rows = {"models": [{"name": "glm-5.3:cloud", "remote_host": "https://ollama.com:443"}, {"name": "mine:1"},
                           {"name": "mine:cloud"}]}
        calls = []
        saved = lai.http_json, lai.subprocess.call
        lai.http_json = lambda url, timeout=15: rows
        lai.subprocess.call = lambda cmd, **k: calls.append(cmd[-1]) or 0
        try:
            self.assertEqual(lai.remove_cloud_stubs(), ["glm-5.3:cloud"])   # a model of yours that merely ends in "cloud" stays
        finally:
            lai.http_json, lai.subprocess.call = saved
        self.assertEqual(calls, ["glm-5.3:cloud"])

    def test_our_own_ollama_server_has_cloud_off(self):
        self.assertEqual(lai.ollama_env()["OLLAMA_NO_CLOUD"], "1")


class FakeWebUI(http.server.BaseHTTPRequestHandler):
    """Just enough of Open WebUI's API: sign-in, model list, connection settings."""
    password = "pw"
    models = ["chat:1", "coder:1", "helper/x:1", "qwen2.5-coder:1.5b-base"]
    allow = []
    default = None

    def log_message(self, *a):
        pass

    def reply(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/health":
            return self.reply({"status": True})
        if self.headers.get("Authorization") != "Bearer tok":
            return self.reply({"detail": "no"}, 401)
        cls = type(self)
        if self.path == "/api/models":
            ids = [m for m in cls.models if not cls.allow or m in cls.allow]
            return self.reply({"data": [{"id": m} for m in ids]})
        if self.path == "/ollama/config":
            return self.reply({"ENABLE_OLLAMA_API": True, "OLLAMA_BASE_URLS": ["http://127.0.0.1:11434"],
                               "OLLAMA_API_CONFIGS": {}})
        if self.path == "/api/v1/configs/models":
            return self.reply({"DEFAULT_MODELS": cls.default, "DEFAULT_MODEL_METADATA": {"x": 1}})
        self.reply({}, 404)

    def do_POST(self):
        data = self.body()
        cls = type(self)
        if self.path == "/api/v1/auths/signin":
            if data.get("password") == cls.password:
                return self.reply({"token": "tok"})
            return self.reply({"detail": "bad"}, 400)
        if self.headers.get("Authorization") != "Bearer tok":
            return self.reply({"detail": "no"}, 401)
        if self.path == "/ollama/config/update":
            cls.allow = data["OLLAMA_API_CONFIGS"]["0"]["model_ids"]
            return self.reply(data)
        if self.path == "/api/v1/configs/models":
            cls.default = data["DEFAULT_MODELS"]
            return self.reply(data)
        self.reply({}, 404)


class ChatLogin(Sandbox):
    def start_fake(self):
        FakeWebUI.allow, FakeWebUI.default = [], None
        srv = http.server.HTTPServer(("127.0.0.1", 0), FakeWebUI)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_first_login_is_created_once_and_kept_private(self):
        email, pw = lai.webui_first_login()
        self.assertEqual(email, lai.WEBUI_EMAIL)
        self.assertLessEqual(len(pw.encode()), 72)
        self.assertEqual(lai.webui_login(), (email, pw))
        self.assertEqual(lai.webui_first_login(), (email, pw))             # re-runs never change the password
        if os.name == "posix":
            self.assertEqual(os.stat(lai.WEBUI_LOGIN).st_mode & 0o777, 0o600)

    def test_env_locks_the_chat_down_and_only_the_first_start_gets_an_admin(self):
        env = lai.webui_env("chat:1", 3210, ["chat:1", "coder:1"], ("owner@localai.local", "secret"))
        self.assertEqual(env["CORS_ALLOW_ORIGIN"], "http://127.0.0.1:3210;http://localhost:3210")
        self.assertEqual(env["ENABLE_SIGNUP"], "false")
        self.assertEqual(env["ENABLE_OPENAI_API"], "false")
        self.assertEqual(env["WEBUI_ADMIN_EMAIL"], "owner@localai.local")
        self.assertEqual(env["DEFAULT_MODELS"], "chat:1")
        cfg = json.loads(env["OLLAMA_API_CONFIGS"])["0"]
        self.assertEqual(cfg["model_ids"], ["chat:1", "coder:1"])
        self.assertIn("127.0.0.1", env["NO_PROXY"])
        self.assertNotIn("WEBUI_AUTH", env)                                 # login stays on
        later = lai.webui_env("chat:1", 3210)
        self.assertNotIn("WEBUI_ADMIN_PASSWORD", later)
        self.assertNotIn("OLLAMA_API_CONFIGS", later)

    def test_no_proxy_is_merged_not_replaced(self):
        self.assertEqual(lai.merge_no_proxy("corp.example"), "corp.example,127.0.0.1,localhost")
        self.assertEqual(lai.merge_no_proxy("localhost,127.0.0.1"), "localhost,127.0.0.1")

    def test_has_users(self):
        os.makedirs(lai.WEBUI_DATA)
        self.assertFalse(lai.webui_has_users())                              # no database yet
        con = sqlite3.connect(os.path.join(lai.WEBUI_DATA, "webui.db"))
        con.execute("CREATE TABLE user (id TEXT)")
        con.commit()
        self.assertFalse(lai.webui_has_users())
        con.execute("INSERT INTO user VALUES ('1')")
        con.commit()
        con.close()
        self.assertTrue(lai.webui_has_users())

    def test_saved_settings_are_tidied_while_the_chat_is_stopped(self):
        os.makedirs(lai.WEBUI_DATA)
        db = os.path.join(lai.WEBUI_DATA, "webui.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, value JSON, updated_at INTEGER)")
        rows = {"ollama.api_configs": {"0": {"enable": True, "tags": ["t"]}}, "ui.default_models": "old",
                "evaluation.arena.enable": True, "openai.enable": True, "unrelated": {"keep": 1}}
        for k, v in rows.items():
            con.execute("INSERT INTO config VALUES (?, ?, 0)", (k, json.dumps(v)))
        con.commit()
        con.close()
        self.assertTrue(lai.patch_webui_config(["chat:1", "coder:1"], "chat:1"))
        con = sqlite3.connect(db)
        got = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM config")}
        con.close()
        self.assertEqual(got["ollama.api_configs"]["0"]["model_ids"], ["chat:1", "coder:1"])
        self.assertEqual(got["ollama.api_configs"]["0"]["tags"], ["t"])      # other settings in the entry survive
        self.assertEqual(got["ui.default_models"], "chat:1")
        self.assertFalse(got["evaluation.arena.enable"])
        self.assertFalse(got["openai.enable"])
        self.assertEqual(got["unrelated"], {"keep": 1})

    def test_settings_patch_never_breaks_a_different_database_layout(self):
        os.makedirs(lai.WEBUI_DATA)
        con = sqlite3.connect(os.path.join(lai.WEBUI_DATA, "webui.db"))
        con.execute("CREATE TABLE config (id INTEGER, data JSON)")
        con.commit()
        con.close()
        self.assertFalse(lai.patch_webui_config(["a"], "a"))
        self.assertFalse(lai.patch_webui_config([], "a"))

    def test_sign_in_and_model_list_repair(self):
        port = self.start_fake()
        with open(lai.WEBUI_LOGIN, "w") as f:
            f.write("Email:    owner@localai.local\nPassword: pw\n")
        self.assertEqual(lai.webui_signin(port), "tok")
        with open(lai.WEBUI_LOGIN, "w") as f:
            f.write("Email:    owner@localai.local\nPassword: changed-by-user\n")
        self.assertIsNone(lai.webui_signin(port))
        with open(lai.WEBUI_LOGIN, "w") as f:
            f.write("Email:    owner@localai.local\nPassword: pw\n")
        self.assertEqual(len(lai.webui_visible_models(port, "tok")), 4)
        self.assertTrue(lai.webui_apply_models(port, "tok", ["chat:1", "coder:1"], "chat:1"))
        self.assertEqual(lai.webui_visible_models(port, "tok"), ["chat:1", "coder:1"])
        self.assertEqual(FakeWebUI.default, "chat:1")

    def test_health_check_repairs_an_untidy_model_list(self):
        port = self.start_fake()
        with open(lai.WEBUI_LOGIN, "w") as f:
            f.write("Email:    owner@localai.local\nPassword: pw\n")
        state = {"chat": {"tag": "chat:1"}, "coder": {"tag": "coder:1"}}
        results = []
        saved = lai.installed_models
        lai.installed_models = lambda: ["chat:1", "coder:1", "helper/x:1", "qwen2.5-coder:1.5b-base"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                lai.webui_checks(state, port, "chat:1", lambda *a, **k: results.append(a), repair=True)
        finally:
            lai.installed_models = saved
        self.assertEqual([r[1] for r in results], [True, True])
        self.assertEqual(lai.webui_visible_models(port, "tok"), ["chat:1", "coder:1"])

    def test_port_goes_back_to_the_usual_one(self):
        saved = lai.webui_health, lai.port_free
        lai.webui_health = lambda p: False
        lai.port_free = lambda p: True
        try:
            self.assertEqual(lai.choose_port({"webui_port": 3215}), lai.DEFAULT_PORT)
            lai.port_free = lambda p: p != lai.DEFAULT_PORT
            self.assertEqual(lai.choose_port({"webui_port": 3215}), lai.DEFAULT_PORT + 1)
            lai.webui_health = lambda p: p == 3215
            self.assertEqual(lai.choose_port({"webui_port": 3215}), 3215)  # already running there: stay
        finally:
            lai.webui_health, lai.port_free = saved


class ContinueConfig(Sandbox):
    OURS = ["coder:1", "chat:1", "auto:1"]

    def test_continue_rewriting_our_file_does_not_make_it_yours(self):
        # what Continue leaves after its "Use local models" button: no comment, its own models first
        rewritten = ("name: Local AI\nversion: 1.0.1\nschema: v1\nmodels:\n"
                     "  - name: Llama 3.1 8B\n    provider: ollama\n    model: llama3.1:8b\n    roles: [chat]\n"
                     "  - name: Qwen\n    provider: ollama\n    model: qwen2.5-coder:1.5b-base\n"
                     "  - name: Local Coder\n    provider: ollama\n    model: coder:1\n")
        self.assertEqual(lai.classify_continue_config(rewritten, self.OURS), "managed")
        self.assertEqual(lai.classify_continue_config("name: Main Config\nversion: 1.0.0\nschema: v1\nmodels: []\n"), "managed")
        self.assertEqual(lai.classify_continue_config(""), "empty")
        self.assertEqual(lai.classify_continue_config("# managed by Local AI Installer\nname: x\n"), "managed")
        self.assertEqual(lai.classify_continue_config("name: " + lai.CONTINUE_NAME + "\nmodels: []\n"), "managed")

    def test_hand_made_configs_are_yours(self):
        mine = ("name: My setup\nmodels:\n  - name: gpt\n    provider: openai\n    model: gpt-4o\n    apiKey: sk-x\n")
        self.assertEqual(lai.classify_continue_config(mine, self.OURS), "user")
        self.assertEqual(lai.classify_continue_config("name: mine\n"), "user")
        other = "name: Local AI\nmodels:\n  - name: m\n    provider: ollama\n    model: some-model-i-added:7b\n"
        self.assertEqual(lai.classify_continue_config(other, self.OURS), "user")
        cloud = "name: Local AI\nmodels:\n  - model: coder:1\n    apiBase: https://api.example.com/v1\n"
        self.assertEqual(lai.classify_continue_config(cloud, self.OURS), "user")
        foreign_mcp = "name: Local AI\nmodels: []\nmcpServers:\n  - name: x\n    command: /usr/bin/something\n"
        self.assertEqual(lai.classify_continue_config(foreign_mcp, self.OURS), "user")

    def test_replaced_files_are_backed_up_and_only_three_are_kept(self):
        path = lai.continue_config_path()
        os.makedirs(os.path.dirname(path))
        with contextlib.redirect_stdout(io.StringIO()):
            for i in range(5):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"name: Main Config\nversion: 1.0.0\nschema: v1\nmodels: []\n# {i}\n")
                self.assertTrue(lai.write_continue_config("coder:1", "chat:1", None))
                time.sleep(1.1)  # the backups are named by the second
        backups = sorted(os.listdir(os.path.dirname(path)))
        self.assertEqual(len([b for b in backups if ".lai-2" in b and b.endswith(".bak")]), 3)
        self.assertTrue(any(b.endswith("lai-original.bak") for b in backups))        # the first file we replaced is kept for good
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(lai.CONTINUE_NAME, text)
        self.assertIn("Embeddings", text)

    def test_helper_names_end_up_in_the_config(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(lai.write_continue_config("coder:1", "chat:1", lai.AUTOCOMPLETE_ALIAS, None, None,
                                                      lai.EMBED_ALIAS))
        with open(lai.continue_config_path(), encoding="utf-8") as f:
            text = f.read()
        self.assertIn(lai.AUTOCOMPLETE_ALIAS, text)
        self.assertIn(lai.EMBED_ALIAS, text)
        self.assertNotIn('model: "nomic-embed-text"', text)
        self.assertTrue(lai.continue_config_ok("coder:1", "chat:1"))


class Safety(Sandbox):
    def test_the_coding_model_is_never_removed_as_a_helper(self):
        with world(present=["qwen2.5-coder:1.5b"]) as w:
            state = {"pulled": {"qwen2.5-coder:1.5b": {}}}
            lai.helper_model(state, "autocomplete", lai.AUTOCOMPLETE_TAGS, lai.AUTOCOMPLETE_ALIAS, ["qwen2.5-coder:1.5b"])
            self.assertIn("qwen2.5-coder:1.5b", w.present)
            self.assertNotIn("qwen2.5-coder:1.5b", w.removed())

    def test_models_that_may_have_been_yours_are_not_claimed_when_upgrading_from_an_old_install(self):
        state = {"coder": {"tag": "c"}, "chat": {"tag": "h"}, "embed": "nomic-embed-text", "autocomplete": "a"}
        lai.migrate_ledger(state)
        self.assertEqual(sorted(state["pulled"]), ["a", "c", "h"])
        with world(present=["nomic-embed-text:latest"]) as w:
            lai.helper_model(state, "embed", [lai.EMBED_MODEL], lai.EMBED_ALIAS)
            self.assertIn("nomic-embed-text:latest", w.present)         # yours: copied, never removed

    def test_continue_configs_with_anything_extra_are_yours(self):
        ours = ["coder:1"]
        for text in ("name: Local AI\nmodels:\n  - model: llama3.1:8b\nrules:\n  - be terse\n",
                     "name: Local Assistant\nmodels:\n  - {name: x, provider: ollama, model: my-private-finetune}\n",
                     "name: Local Config\nmodels:\n  - model: coder:1\n    apiBase: http://gpubox.lan:11434\n",
                     "name: Local AI\nmodels:\n  - uses: ollama/llama3\n",
                     "name: Local AI\nmodels:\n  - model: coder:1\n    systemMessage: be rude\n",
                     "# Local AI (managed by Local AI Installer) is a nice tool\nname: mine\n"):
            self.assertEqual(lai.classify_continue_config(text, ours), "user", text)
        ok = "name: Local AI\nversion: 1.0.1\nschema: v1\nmodels:\n  - model: coder:1\n    apiBase: http://localhost:11434\n"
        self.assertEqual(lai.classify_continue_config(ok, ours), "managed")

    def test_the_original_hand_made_config_survives_many_runs(self):
        path = lai.continue_config_path()
        os.makedirs(os.path.dirname(path))
        put(path, "name: Main Config\nversion: 1.0.0\nschema: v1\nmodels: []\n")   # Continue's own default
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(5):
                lai.write_continue_config("coder:1", "chat:1", None)
                time.sleep(1.1)
        with open(path + ".lai-original.bak", encoding="utf-8") as f:
            self.assertIn("Main Config", f.read())

    def test_only_our_own_temp_folders_are_cleaned(self):
        tmp = tempfile.mkdtemp()
        saved = tempfile.gettempdir, lai.clean_legacy_uv_cache
        tempfile.gettempdir = lambda: tmp
        lai.clean_legacy_uv_cache = lambda st: 0
        try:
            mine, yours = os.path.join(tmp, "lai-uv-abc"), os.path.join(tmp, "lai-project")
            for d in (mine, yours):
                os.makedirs(d)
                put(os.path.join(d, "f.txt"), "x")
                past = time.time() - 5 * 86400
                os.utime(d, (past, past))
            with world():
                lai.cleanup({}, [])
            self.assertFalse(os.path.exists(mine))
            self.assertTrue(os.path.exists(yours))
        finally:
            tempfile.gettempdir, lai.clean_legacy_uv_cache = saved
            lai.shutil.rmtree(tmp, ignore_errors=True)


class Tidying(Sandbox):
    def test_log_rotation(self):
        log = os.path.join(self.tmp.name, "x.log")
        put(log, "a" * 100)
        lai.rotate_log(log, limit=1000)
        self.assertTrue(os.path.exists(log))
        put(log, "a" * 2000)
        lai.rotate_log(log, limit=1000)
        self.assertFalse(os.path.exists(log))
        self.assertTrue(os.path.exists(log + ".old"))
        lai.rotate_log(os.path.join(self.tmp.name, "missing.log"))        # never raises

    def test_cleanup_empties_the_download_cache_and_prunes_old_models(self):
        os.makedirs(lai.UV_CACHE)
        put(os.path.join(lai.UV_CACHE, "wheel"), b"x" * 100, "wb")
        with world(present=["old", "new", "yours"]) as w:
            state = {"pulled": {"old": {}, "new": {}}}
            saved = lai.clean_legacy_uv_cache
            lai.clean_legacy_uv_cache = lambda st: 0
            try:
                lai.cleanup(state, ["new"])
                self.assertFalse(os.path.exists(lai.UV_CACHE))
                self.assertEqual(sorted(w.present), ["new", "yours"])
                w.present.append("old")
                state["pulled"]["old"] = {}
                lai.cleanup(state, ["new"], keep_old=True)                 # --keep-old: nothing deleted
                self.assertIn("old", w.present)
            finally:
                lai.clean_legacy_uv_cache = saved

    def test_stale_leftovers_are_removed_but_fresh_ones_are_not(self):
        old = os.path.join(lai.STATE_DIR, "ody-old")
        new = os.path.join(lai.STATE_DIR, "ody-new")
        for d in (old, new):
            os.makedirs(d)
        past = time.time() - 3 * 86400
        os.utime(old, (past, past))
        saved = lai.clean_legacy_uv_cache
        lai.clean_legacy_uv_cache = lambda st: 0
        try:
            with world():
                lai.cleanup({}, [])
        finally:
            lai.clean_legacy_uv_cache = saved
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_your_own_uv_cache_is_never_emptied(self):
        os.makedirs(os.path.join(lai.STATE_DIR, "uv"))
        exe = os.path.join(lai.STATE_DIR, "uv", "uv.exe" if lai.IS_WIN else "uv")
        put(exe, "")
        state = {}
        saved = lai.shutil.which
        lai.shutil.which = lambda name: "/usr/bin/uv"            # you have your own uv
        try:
            self.assertEqual(lai.clean_legacy_uv_cache(state), 0)
        finally:
            lai.shutil.which = saved
        self.assertNotIn("uv_cache_cleared", state)

    def test_windows_marker_keeps_ollamas_window_from_opening(self):
        saved = lai.IS_WIN
        lai.IS_WIN = True
        old_env = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = os.path.join(self.tmp.name, "Local")
        try:
            lai.ollama_start_hidden()
            self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "Local", "Ollama", "upgraded")))
        finally:
            lai.IS_WIN = saved
            if old_env is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = old_env


class OdysseusRemoval(Sandbox):
    def test_program_goes_but_chats_and_login_stay(self):
        saved = {k: getattr(lai, k) for k in ("odysseus_health",)}
        lai.odysseus_health = lambda port: False
        try:
            for d in (lai.ODYSSEUS_DIR, lai.ODYSSEUS_VENV, lai.ODYSSEUS_DATA):
                os.makedirs(d)
            desk = os.path.join(self.tmp.name, "Desktop")
            os.makedirs(desk)
            keep, gone = os.path.join(desk, "Local AI Chat.lnk"), os.path.join(desk, "Odysseus AI.lnk")
            put(keep, "")
            put(gone, "")
            state = {"odysseus": {"port": 7000}, "shortcuts": [keep, gone]}
            lai.remove_odysseus(state)
        finally:
            for k, v in saved.items():
                setattr(lai, k, v)
        self.assertFalse(os.path.exists(lai.ODYSSEUS_DIR))
        self.assertTrue(os.path.exists(os.path.join(lai.STATE_DIR, "odysseus-data")))
        self.assertTrue(os.path.exists(keep))
        self.assertFalse(os.path.exists(gone))
        self.assertNotIn("odysseus", state)
        self.assertEqual(state["shortcuts"], [keep])


class WholeRun(Sandbox):
    """lai.main() with the outside world faked: a fresh install, then an update that swaps a model."""

    SPEC = {"os": "Windows 11", "arch": "AMD64", "cpu": "cpu", "cores": 8, "ram_gb": 32.0,
            "gpus": [{"name": "RTX", "vram_gb": 12.0, "vendor": "nvidia"}], "usable_gb": 12.0,
            "mode": "dedicated GPU VRAM", "disk_free_gb": 500.0}

    def cand(self, repo, score):
        return {"repo": repo, "quant": "Q4_K_M", "size_gb": 5.0, "age_months": 1.0, "downloads": 10, "likes": 1,
                "score": score, "tag": f"hf.co/{repo}:Q4_K_M"}

    def run_main(self, w, live, argv=("--yes", "--no-open", "--no-pewdiepie", "--no-vscode")):
        names = ("detect", "research", "find_ollama", "start_ollama", "stop_ollama", "upgrade_ollama", "install_ollama",
                 "ollama_up", "remove_cloud_stubs", "find_code", "setup_webui", "webui_health", "install_self_copy",
                 "free_gb", "open_url", "make_shortcuts")
        saved = {n: getattr(lai, n) for n in names}
        lai.detect = lambda max_gb=None: dict(self.SPEC)
        lai.research = lambda spec, stats=None: live
        lai.find_ollama = lambda: "/fake/ollama"
        lai.start_ollama = lambda wait=90: True
        lai.stop_ollama = lambda: True
        lai.upgrade_ollama = lambda: None
        lai.install_ollama = lambda: True
        lai.ollama_up = lambda: True
        lai.remove_cloud_stubs = lambda: []
        lai.find_code = lambda: None
        lai.setup_webui = lambda state, chat: "http://localhost:3210"
        lai.webui_health = lambda port: True
        lai.install_self_copy = lambda: None
        lai.free_gb = lambda p: 500.0
        lai.open_url = lambda url: None
        lai.make_shortcuts = lambda *a, **k: []
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                rc = lai.main(list(argv))
        finally:
            for n, fn in saved.items():
                setattr(lai, n, fn)
        return rc, out.getvalue(), (jload(lai.STATE_FILE) if os.path.exists(lai.STATE_FILE) else None)

    def test_fresh_install_then_upgrade_leaves_one_tidy_set(self):
        live = {"coder": [self.cand("a/Coder-heretic-GGUF", 10)], "chat": [self.cand("a/Chat-heretic-GGUF", 10)]}
        w = World().install()
        try:
            rc, out, state = self.run_main(w, live)
            self.assertEqual(rc, 0, out)
            self.assertEqual(sorted(w.present), ["hf.co/a/Chat-heretic-GGUF:Q4_K_M", "hf.co/a/Coder-heretic-GGUF:Q4_K_M"])
            self.assertIn("Open your AI", out)
            self.assertIn("Which AI to pick", out)
            self.assertTrue(os.path.exists(os.path.join(self.tmp.name, ".ollama", "server.json")))
            self.assertEqual(state["chat"]["tag"], "hf.co/a/Chat-heretic-GGUF:Q4_K_M")
            w.present.append("my-own-model:7b")                          # something you installed yourself
            live = {"coder": [self.cand("b/Coder2-heretic-GGUF", 20), self.cand("a/Coder-heretic-GGUF", 10)],
                    "chat": [self.cand("b/Chat2-heretic-GGUF", 20), self.cand("a/Chat-heretic-GGUF", 10)]}
            rc, out, state = self.run_main(w, live)
            self.assertEqual(rc, 0, out)
            self.assertEqual(sorted(w.present), ["hf.co/b/Chat2-heretic-GGUF:Q4_K_M", "hf.co/b/Coder2-heretic-GGUF:Q4_K_M",
                                                 "my-own-model:7b"])
            self.assertIn("Cleaned up: old models", out)
        finally:
            w.uninstall()

    def test_update_without_vscode_keeps_the_helper_models_continue_still_uses(self):
        chat, coder = "hf.co/a/Chat-heretic-GGUF:Q4_K_M", "hf.co/a/Coder-heretic-GGUF:Q4_K_M"
        auto, emb = lai.AUTOCOMPLETE_ALIAS, lai.EMBED_ALIAS
        os.makedirs(lai.STATE_DIR, exist_ok=True)
        jdump({"coder": {"tag": coder, "score": 5}, "chat": {"tag": chat, "score": 5}, "autocomplete": auto, "embed": emb,
               "vscode": True, "continue_configured": True,
               "pulled": {coder: {}, chat: {}, auto: {}, emb: {}}}, lai.STATE_FILE)
        live = {"coder": [self.cand("a/Coder-heretic-GGUF", 10)], "chat": [self.cand("a/Chat-heretic-GGUF", 10)]}
        w = World(present=[coder, chat, auto, emb]).install()
        try:
            rc, out, state = self.run_main(w, live)
            self.assertEqual(rc, 0, out)
            self.assertEqual(sorted(w.present), sorted([coder, chat, auto, emb]))
            self.assertEqual((state["autocomplete"], state["embed"], state["vscode"]), (auto, emb, True))
        finally:
            w.uninstall()

    def test_failed_upgrade_keeps_the_model_that_works_and_deletes_nothing_in_use(self):
        old_chat, old_coder = "hf.co/a/Chat-heretic-GGUF:Q4_K_M", "hf.co/a/Coder-heretic-GGUF:Q4_K_M"
        os.makedirs(lai.STATE_DIR, exist_ok=True)
        jdump({"coder": {"tag": old_coder, "score": 5}, "chat": {"tag": old_chat, "score": 5}}, lai.STATE_FILE)
        live = {"coder": [self.cand("b/Coder2-heretic-GGUF", 20)], "chat": [self.cand("b/Chat2-heretic-GGUF", 20)]}
        w = World(present=[old_coder, old_chat], pull_fail={"hf.co/b/Coder2-heretic-GGUF:Q4_K_M",
                                                            "hf.co/b/Chat2-heretic-GGUF:Q4_K_M"}).install()
        try:
            rc, out, state = self.run_main(w, live)
            self.assertEqual(rc, 0, out)
            self.assertEqual(state["chat"]["tag"], old_chat)
            self.assertEqual(sorted(w.present), sorted([old_coder, old_chat]))
            self.assertEqual(w.removed(), [])
        finally:
            w.uninstall()

    def test_no_unlocked_model_means_a_clear_failure_not_a_silent_standard_one(self):
        w = World(pull_fail={"hf.co/a/Chat-heretic-GGUF:Q4_K_M"}).install()
        try:
            live = {"coder": [self.cand("a/Coder-heretic-GGUF", 10)], "chat": [self.cand("a/Chat-heretic-GGUF", 10)]}
            fail = {t for _, _, c, h in lai.TIERS for t in h if lai.is_unlocked(t)}
            w.pull_fail |= fail
            rc, out, _ = self.run_main(w, live)
            self.assertEqual(rc, 1)
            self.assertIn("--allow-standard", out)
            self.assertFalse([m for m in w.present if not lai.is_unlocked(m) and "heretic" not in m.lower()])
        finally:
            w.uninstall()


if __name__ == "__main__":
    unittest.main()
