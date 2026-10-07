"""Offline tests for the installer's logic. Run: python -m unittest discover -s tests -v"""
import builtins
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("lai", os.path.join(ROOT, "local_ai_installer.py"))
lai = importlib.util.module_from_spec(_spec)
sys.modules["lai"] = lai
_spec.loader.exec_module(lai)


def siblings(*pairs):
    return [{"rfilename": f"x-{q}.gguf", "size": int(g * 1024**3)} for q, g in pairs]


MODELS = {
    "a/Qwen3-14B-abliterated-GGUF": siblings(("Q4_K_M", 9), ("Q8_0", 15.7), ("Q6_K", 12.1)),
    "b/Old-Dolphin-70b-uncensored-GGUF": siblings(("Q4_K_M", 40)),
    "c/Qwen3-32B-abliterated-GGUF": siblings(("Q4_K_M", 19.8), ("Q3_K_M", 15.9)),
    "d/Qwen2.5-Coder-14B-abliterated-GGUF": siblings(("Q4_K_M", 9), ("Q6_K", 12.1)),
}


def fake_detail(url):
    rid = url.split("/api/models/")[1].split("?")[0]
    return {"siblings": MODELS[rid]}


def listing(created="2026-08-01T00:00:00Z"):
    return [{"id": k, "downloads": 50000, "likes": 100, "createdAt": created} for k in MODELS]


class Research(unittest.TestCase):
    def test_picks_biggest_model_that_fits(self):
        best = lai.rank_candidates(listing(), 13.0, "chat", detail=fake_detail)[0]
        self.assertEqual(best["tag"], "hf.co/a/Qwen3-14B-abliterated-GGUF:Q6_K")
        best = lai.rank_candidates(listing(), 20.0, "chat", detail=fake_detail)[0]
        self.assertEqual(best["tag"], "hf.co/c/Qwen3-32B-abliterated-GGUF:Q4_K_M")

    def test_coder_and_chat_are_separate(self):
        coder = lai.rank_candidates(listing(), 13.0, "coder", detail=fake_detail)
        self.assertEqual([c["repo"] for c in coder], ["d/Qwen2.5-Coder-14B-abliterated-GGUF"])

    def test_old_models_dropped_unknown_age_kept(self):
        self.assertEqual(lai.rank_candidates(listing("2020-01-01T00:00:00Z"), 13.0, "chat", detail=fake_detail), [])
        no_age = [{"id": k, "downloads": 10, "likes": 1} for k in MODELS]
        self.assertTrue(lai.rank_candidates(no_age, 13.0, "chat", detail=fake_detail))

    def test_nothing_fits(self):
        self.assertEqual(lai.rank_candidates(listing(), 1.0, "chat", detail=fake_detail), [])

    def test_best_quant_handles_underscore_names(self):
        files = [("Model_Q4_K_M.gguf", 4.0), ("Model_Q6_K.gguf", 6.0), ("Model-Q8_0-00001-of-00002.gguf", 7.0),
                 ("mmproj-F16.gguf", 1.0)]
        self.assertEqual(lai.best_quant(files, 5.0), ("Q4_K_M", 4.0))
        self.assertEqual(lai.best_quant(files, 6.5), ("Q6_K", 6.0))
        self.assertIsNone(lai.best_quant(files, 2.0))

    def test_param_b(self):
        self.assertEqual(lai.param_b("x/Qwen3-14B-abliterated"), 14)
        self.assertEqual(lai.param_b("x/Qwen3-30B-A3B-abliterated"), 30)
        self.assertEqual(lai.param_b("x/Llama-3.2-1B-uncensored"), 1)
        self.assertEqual(lai.param_b("x/Mixtral-8x7B-dolphin"), 56)
        self.assertEqual(lai.param_b("x/gemma-3-27b-it-abliterated"), 27)
        self.assertIsNone(lai.param_b("x/Phi-4-mini-abliterated"))

    def test_bad_names_filtered(self):
        for bad in ("a/foo-abliterated-lora", "a/foo-abliterated-embedding", "a/foo-abliterated-base",
                    "pottokao/Qwen-Image-2.1-Text-Encoder-Heretic-GGUF", "ponpoke/flux2-klein-9b-uncensored-text-encoder",
                    "a/foo-uncensored-text-encoders", "a/sdxl-uncensored-vae"):
            self.assertTrue(lai.BAD_NAME.search(bad.lower()), bad)
        self.assertFalse(lai.BAD_NAME.search("a/Colorado-abliterated-14B"))

    def test_coder_detection_ignores_encoder_and_decoder(self):
        for yes in ("x/gemma-4-12b-coder-heretic-gguf", "x/qwen2.5-coder-14b-abliterated", "x/starcoder2-15b-uncensored",
                    "x/devstral-small-abliterated", "x/lfm2.5-2.6b-coding-agent-heretic", "x/qwen3-coder-30b-a3b"):
            self.assertTrue(lai.CODER_RE.search(yes), yes)
        for no in ("x/qwen-image-text-encoder-heretic", "x/foo-decoder-uncensored", "x/unicode-uncensored",
                   "x/ornith-1.5-9b-uncensored", "x/dolphin3.0-llama3.1-8b"):
            self.assertFalse(lai.CODER_RE.search(no), no)

    def test_image_text_encoders_never_ranked_as_coders(self):
        junk = "pottokao/Qwen-Image-2.1-Text-Encoder-Heretic-GGUF"
        MODELS[junk] = siblings(("Q8_0", 8.1))
        try:
            models = listing() + [{"id": junk, "downloads": 900000, "likes": 500, "createdAt": "2026-09-30T00:00:00Z"}]
            for want in ("coder", "chat"):
                repos = [c["repo"] for c in lai.rank_candidates(models, 13.0, want, detail=fake_detail)]
                self.assertNotIn(junk, repos)
        finally:
            del MODELS[junk]

    def test_months_old(self):
        self.assertIsNone(lai.months_old(None))
        self.assertIsNone(lai.months_old("garbage"))
        self.assertGreater(lai.months_old("2020-01-01T00:00:00Z"), 12)

    def test_recommend_tiers(self):
        self.assertTrue(lai.recommend({"usable_gb": 2})["tier"].startswith("tiny"))
        self.assertTrue(lai.recommend({"usable_gb": 8})["tier"].startswith("medium"))
        self.assertTrue(lai.recommend({"usable_gb": 24})["tier"].startswith("xlarge"))
        self.assertTrue(lai.recommend({"usable_gb": 64})["tier"].startswith("workstation"))

    def test_curated_lists_end_with_a_standard_fallback(self):
        for _, label, coder, chat in lai.TIERS:
            self.assertTrue(coder and chat, label)

    def test_est_size(self):
        self.assertEqual(lai.est_size_gb("qwen2.5-coder:7b", []), 4.2)
        cands = [{"tag": "hf.co/x/y:Q4_K_M", "size_gb": 9.1}]
        self.assertEqual(lai.est_size_gb("hf.co/x/y:Q4_K_M", cands), 9.1)


class Updates(unittest.TestCase):
    def test_vtuple(self):
        self.assertGreater(lai.vtuple("v1.10.0"), lai.vtuple("1.9.9"))
        self.assertEqual(lai.vtuple("1.2"), (1, 2, 0))

    def test_decide(self):
        live = [{"tag": "new", "score": 10}, {"tag": "old", "score": 9.5}]
        state = {"coder": {"tag": "old"}}
        self.assertEqual(lai.decide("coder", ["new", "x"], live, state), (["old"], False))   # marginal gain
        live[1]["score"] = 7
        self.assertEqual(lai.decide("coder", ["new", "x"], live, state), (["new", "x"], True))  # clear gain
        self.assertEqual(lai.decide("coder", ["old", "x"], live, state), (["old"], False))   # same
        self.assertEqual(lai.decide("chat", ["a"], live, state), (["a"], False))             # nothing installed

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            old = lai.STATE_DIR, lai.STATE_FILE
            lai.STATE_DIR, lai.STATE_FILE = d, os.path.join(d, "state.json")
            try:
                self.assertEqual(lai.load_state(), {})
                lai.save_state({"coder": {"tag": "t"}})
                self.assertEqual(lai.load_state()["coder"]["tag"], "t")
                self.assertEqual(lai.load_state()["installer_version"], lai.VERSION)
            finally:
                lai.STATE_DIR, lai.STATE_FILE = old

    def test_has_model(self):
        names = ["nomic-embed-text:latest", "HF.co/User/Repo:Q4_K_M", "qwen3:8b"]
        self.assertTrue(lai.has_model("nomic-embed-text", names))
        self.assertTrue(lai.has_model("hf.co/user/repo:Q4_K_M", names))
        self.assertTrue(lai.has_model("qwen3:8b", names))
        self.assertFalse(lai.has_model("qwen3:14b", names))


class Helpers(unittest.TestCase):
    def test_confirm(self):
        self.assertTrue(lai.confirm("q", True))
        real = builtins.input
        try:
            def eof(_):
                raise EOFError
            builtins.input = eof
            self.assertTrue(lai.confirm("q", False))
            self.assertFalse(lai.confirm("q", False, default=False))
            builtins.input = lambda _: "n"
            self.assertFalse(lai.confirm("q", False))
            builtins.input = lambda _: ""
            self.assertTrue(lai.confirm("q", False))
        finally:
            builtins.input = real

    def test_parse_nvidia(self):
        gpus = lai.parse_nvidia("NVIDIA GeForce RTX 4070, 12282\nNVIDIA GeForce RTX 3060, 12288\nbad line")
        self.assertEqual([g["name"] for g in gpus], ["NVIDIA GeForce RTX 4070", "NVIDIA GeForce RTX 3060"])
        self.assertAlmostEqual(gpus[0]["vram_gb"], 12282 / 1024)

    def test_gpu_entry_vendor(self):
        self.assertEqual(lai.gpu_entry("AMD Radeon RX 7900 XTX", 24)["vendor"], "amd")
        self.assertEqual(lai.gpu_entry("NVIDIA GeForce RTX 4090", 24)["vendor"], "nvidia")
        self.assertEqual(lai.gpu_entry("Intel(R) Arc(TM) A770", 16)["vendor"], "intel")

    def test_models_dir_and_cloud_detection(self):
        self.assertTrue(os.path.isabs(lai.models_dir("somewhere")))

    def test_unlocked_label(self):
        self.assertTrue(lai.is_unlocked("huihui_ai/qwen3-abliterated:8b"))
        self.assertTrue(lai.is_unlocked("dolphin3:8b"))
        self.assertFalse(lai.is_unlocked("qwen2.5-coder:7b"))

    def test_continue_config_written_and_user_edits_respected(self):
        with tempfile.TemporaryDirectory() as d:
            old_home = lai.HOME
            lai.HOME = d
            try:
                self.assertTrue(lai.write_continue_config("coder:1", "chat:1", "auto:1"))
                self.assertTrue(lai.continue_config_ok("coder:1", "chat:1"))
                with open(lai.continue_config_path(), encoding="utf-8") as f:
                    self.assertIn('model: "auto:1"', f.read())
                self.assertTrue(lai.write_continue_config("coder:2", "chat:2", None))   # managed file: updated
                self.assertTrue(lai.continue_config_ok("coder:2", "chat:2"))
                with open(lai.continue_config_path(), "w", encoding="utf-8") as f:
                    f.write("name: mine\n")
                self.assertFalse(lai.write_continue_config("coder:3", "chat:3", None))  # user file: untouched
                with open(lai.continue_config_path(), encoding="utf-8") as f:
                    self.assertEqual(f.read(), "name: mine\n")
            finally:
                lai.HOME = old_home


class Pulling(unittest.TestCase):
    def run_pull(self, tags, ok_models):
        calls = []
        real_call, real_verify = lai.subprocess.call, lai.verify

        def fake_call(cmd, *a, **k):
            calls.append(list(cmd[1:]))
            return 1 if cmd[1] == "pull" and cmd[2] == "gone" else 0

        lai.subprocess.call = fake_call
        lai.verify = lambda tag, prompt, label: tag in ok_models
        try:
            return lai.pull_working(tags, "p", "model"), calls
        finally:
            lai.subprocess.call, lai.verify = real_call, real_verify

    def test_skips_models_that_cannot_be_downloaded_or_do_not_run(self):
        got, calls = self.run_pull(["gone", "broken", "good", "never"], {"good", "never"})
        self.assertEqual(got, "good")
        self.assertIn(["rm", "broken"], calls)           # downloaded but does not run -> removed
        self.assertNotIn(["rm", "gone"], calls)          # never downloaded -> nothing to remove
        self.assertNotIn(["pull", "never"], calls)       # stops at the first working model

    def test_returns_none_when_nothing_works(self):
        got, _ = self.run_pull(["gone", "broken"], set())
        self.assertIsNone(got)


class UvInstall(unittest.TestCase):
    def archive(self, folder, kind, name):
        path = os.path.join(folder, name)
        files = {"uv": b"fake uv", "uvx": b"fake uvx", "README.md": b"docs"}
        if kind == "zip":
            with zipfile.ZipFile(path, "w") as z:
                for n, data in files.items():
                    z.writestr(n.replace("uv", "uv.exe") if n.startswith("uv") else n, data)
        else:
            with tarfile.open(path, "w:gz") as t:
                for n, data in files.items():
                    info = tarfile.TarInfo(f"uv-x86_64-unknown-linux-gnu/{n}")
                    info.size = len(data)
                    t.addfile(info, io.BytesIO(data))
        return path

    def run_install(self, os_name, machine, kind, good_checksum=True):
        with tempfile.TemporaryDirectory() as d:
            saved = (lai.OS, lai.IS_WIN, lai.STATE_DIR, lai.HOME, lai.download, lai.fetch_text,
                     lai.shutil.which, platform.machine)
            asset = {"zip": "uv-x86_64-pc-windows-msvc.zip", "tar": "uv-x86_64-unknown-linux-gnu.tar.gz"}[kind]
            arc = self.archive(d, kind, "source-" + asset)
            with open(arc, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest() if good_checksum else "0" * 64
            lai.OS, lai.IS_WIN = os_name, os_name == "Windows"
            lai.STATE_DIR, lai.HOME = os.path.join(d, "state"), os.path.join(d, "home")
            lai.download = lambda url, dest, label=None: shutil.copy(arc, dest)
            lai.fetch_text = lambda url, timeout=15: f"{digest}  {asset}\n"
            lai.shutil.which = lambda *a, **k: None
            platform.machine = lambda: machine
            try:
                ok = lai.install_uv()
                exe = "uv.exe" if lai.IS_WIN else "uv"
                return ok, os.path.exists(os.path.join(lai.STATE_DIR, "uv", exe))
            finally:
                (lai.OS, lai.IS_WIN, lai.STATE_DIR, lai.HOME, lai.download, lai.fetch_text,
                 lai.shutil.which, platform.machine) = saved

    def test_asset_names(self):
        saved = lai.OS, platform.machine
        try:
            for os_name, machine, want in (("Windows", "AMD64", "uv-x86_64-pc-windows-msvc.zip"),
                                           ("Windows", "ARM64", "uv-aarch64-pc-windows-msvc.zip"),
                                           ("Darwin", "arm64", "uv-aarch64-apple-darwin.tar.gz"),
                                           ("Darwin", "x86_64", "uv-x86_64-apple-darwin.tar.gz"),
                                           ("Linux", "x86_64", "uv-x86_64-unknown-linux-gnu.tar.gz"),
                                           ("Linux", "aarch64", "uv-aarch64-unknown-linux-gnu.tar.gz"),
                                           ("Linux", "riscv64", None)):
                lai.OS = os_name
                platform.machine = lambda m=machine: m
                self.assertEqual(lai.uv_asset(), want, (os_name, machine))
        finally:
            lai.OS, platform.machine = saved

    def test_unpacks_zip_and_tarball(self):
        self.assertEqual(self.run_install("Windows", "AMD64", "zip"), (True, True))
        self.assertEqual(self.run_install("Linux", "x86_64", "tar"), (True, True))

    def test_bad_checksum_is_rejected(self):
        self.assertEqual(self.run_install("Linux", "x86_64", "tar", good_checksum=False), (False, False))


class Hardware(unittest.TestCase):
    """Runs on the real CI machines (Windows, macOS, Linux): detection must never crash."""

    def test_detect(self):
        spec = lai.detect()
        self.assertGreater(spec["ram_gb"], 1)
        self.assertTrue(spec["cpu"])
        self.assertGreater(spec["usable_gb"], 0)
        self.assertGreater(spec["disk_free_gb"], 0)
        json.dumps(spec)

    def test_max_gb_caps(self):
        self.assertLessEqual(lai.detect(max_gb=2)["usable_gb"], 2)


if __name__ == "__main__":
    unittest.main()
