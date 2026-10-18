"""Release build: dist\\CXHelper\\, the setup exe and the release folder.

    venv\\Scripts\\python gui\\build_release.py [--installer] [--test-installer]
                                              [--skip-tests] [--skip-selftest]

Steps
  1. reference - release_probe.probe() is run against the ORIGINAL sources (in
                 a scratch copy of the project) and its output kept; the
                 packaged build must reproduce it exactly.
  2. bundle    - web UI (JS/CSS minified), config_template.ini, the font
                 table, the reference values and build info are zipped into a
                 generated module, gui\\build\\stage\\gui\\_bundle.py, so they
                 travel inside the exe (see resources.py).
  3. stage     - every project .py the GUI imports (gui\\*.py, main.py, api\\)
                 is run through python-minifier (docstrings/comments dropped,
                 local names shortened, module-level names kept because
                 backend.py patches upstream modules by name) into
                 gui\\build\\stage\\. Only these copies are packaged; the real
                 sources are never touched.
  4. tests     - the upstream unit tests, gui\\tests_force_stop.py and the probe
                 run against the staged (minified) code in a scratch folder.
  5. freeze    - PyInstaller one-folder build of the staged copies; the
                 modules end up as bytecode inside the exe's archive. Unused
                 OCR models / OpenCV video DLL are trimmed afterwards.
  6. notices   - LICENSE.txt (chaoxing, GPL-3.0) and THIRD-PARTY-NOTICES.txt
                 (the licence texts of Python and every bundled package).
  7. scan      - no personal files, no loose project sources in the build.
  8. selftest  - a copy of the build runs `CXHelper.exe --selftest` in a
                 scratch folder (never in dist, so no config/logs land there).
  9. installer - ISCC compiles installer\\CXHelper.iss.
 10. release   - setup exe, licence files, checksums, build info and a copy of
                 the build scripts are gathered in release\\CXHelper-<version>\\.

Not packaged: README/Dockerfile/pyproject, tests\\, .github\\, config.ini,
cookies.txt, cache.json, chaoxing.log, venv\\, site\\ (the website page) and
anything else the GUI doesn't import.
"""
import argparse
import ast
import datetime
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build")
STAGE = os.path.join(BUILD, "stage")
DIST = os.path.join(ROOT, "dist")
NAME = "CXHelper"
APP_DIR = os.path.join(DIST, NAME)
RELEASE = os.path.join(ROOT, "release")

sys.path.insert(0, HERE)
import build_bundle  # noqa: E402
import resources  # noqa: E402

# project modules the packaged GUI imports, relative to the project root
SOURCES = (
    ["gui/app.py", "gui/backend.py", "gui/resources.py", "gui/release_probe.py", "main.py"]
    + sorted("api/" + os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "api", "*.py")))
)

# data files a scratch copy of the project needs to run from source
DATA_FILES = list(resources.FILES)

# written next to the exe at run time - must never be in a build
USER_FILES = ("config.ini", "cookies.txt", "cache.json", "chaoxing.log", "selftest.json")

# files ddddocr / OpenCV ship that this project never loads (~105 MB):
# api/captcha.py uses DdddOcr(show_ad=False) = common_old.onnx only, and
# captcha OCR never touches OpenCV's video IO. The selftest's captcha_ocr
# check covers this.
TRIM = ("ddddocr/common.onnx", "ddddocr/common_det.onnx", "cv2/opencv_videoio_ffmpeg*.dll")

# project source text that must only exist as bytecode inside the exe
NEEDLES = (b"class GuiBar", b"def _gui_process_chapter", b"class JobProcessor", b"def process_chapter",
           b"def decode_course_card", b"window.__bridge = ", b"d_yHJ!$pdA~5", b"def probe(")

EXTRA_DISTRIBUTIONS = ("pyinstaller",)


def step(title):
    print("\n== %s" % title, flush=True)


def app_version():
    with open(os.path.join(HERE, "backend.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "APP_VERSION" for t in node.targets):
            return node.value.value
    raise SystemExit("APP_VERSION not found in gui/backend.py")


# ------------------------------------------------------------------- sandbox

def make_sandbox(code_root, prefix):
    """A throwaway project tree: code from `code_root` (original or staged),
    data files from the real project. Runs never write into either."""
    box = tempfile.mkdtemp(prefix=prefix)
    for rel in SOURCES:
        dest = os.path.join(box, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(os.path.join(code_root, rel), dest)
    for rel in DATA_FILES:
        dest = os.path.join(box, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, rel), dest)
    return box


def run_in(box, args, timeout=600):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable] + args, cwd=box, env=env, timeout=timeout)


PROBE_CMD = ("import sys, json; sys.path.insert(0, 'gui'); import backend, release_probe; "
             "json.dump(release_probe.probe(), open(sys.argv[1], 'w', encoding='utf-8'), ensure_ascii=False)")


def run_probe(code_root, label):
    box = make_sandbox(code_root, "cx-probe-")
    try:
        out = os.path.join(box, "probe.json")
        if run_in(box, ["-c", PROBE_CMD, out], timeout=180).returncode != 0:
            raise SystemExit("probe failed on the %s sources" % label)
        with open(out, encoding="utf-8") as f:
            return json.load(f)
    finally:
        shutil.rmtree(box, ignore_errors=True)


# --------------------------------------------------------------------- stage

def stage_sources():
    step("minify project sources into gui\\build\\stage")
    import python_minifier

    total_in = total_out = 0
    for rel in SOURCES:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            source = f.read()
        out = python_minifier.minify(
            source,
            filename=rel,
            remove_literal_statements=True,  # docstrings - nothing reads __doc__
            rename_locals=True,
            rename_globals=False,  # backend.py patches upstream modules by name
            remove_asserts=False,
            remove_debug=False,
        )
        before, after = _top_level_names(source), _top_level_names(out)
        if before - after:
            raise SystemExit("minifier dropped top-level names from %s: %s" % (rel, sorted(before - after)))
        compile(out, rel, "exec")
        dest = os.path.join(STAGE, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(out)
        n_in, n_out = len(source.encode("utf-8")), len(out.encode("utf-8"))
        total_in += n_in
        total_out += n_out
        print("  %-24s %7d -> %7d bytes" % (rel, n_in, n_out))
    print("  %d files, %d -> %d bytes" % (len(SOURCES), total_in, total_out))


def _top_level_names(source):
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def test_stage(reference):
    step("tests against the minified sources (scratch copy)")
    box = make_sandbox(STAGE, "cx-stagetest-")
    try:
        shutil.copytree(os.path.join(ROOT, "tests"), os.path.join(box, "tests"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copyfile(os.path.join(HERE, "tests_force_stop.py"), os.path.join(box, "gui", "tests_force_stop.py"))
        if run_in(box, ["-m", "unittest", "discover", "-s", "tests", "-t", "."]).returncode != 0:
            raise SystemExit("upstream unit tests failed on the minified sources")
        if run_in(box, [os.path.join("gui", "tests_force_stop.py")], timeout=900).returncode != 0:
            raise SystemExit("gui/tests_force_stop.py failed on the minified sources")
    finally:
        shutil.rmtree(box, ignore_errors=True)
    got = run_probe(STAGE, "minified")
    diff = sorted(k for k in set(reference) | set(got) if reference.get(k) != got.get(k))
    if diff:
        raise SystemExit("minified sources behave differently: %s" % diff)
    print("  probe: %d results identical to the original sources" % len(reference))


# -------------------------------------------------------------------- freeze

def write_version_file(version):
    parts = tuple(([int(p) for p in version.split(".")] + [0, 0, 0, 0])[:4])
    text = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=%(t)r, prodvers=%(t)r, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('080404b0', [
      StringStruct('CompanyName', 'Suzume'),
      StringStruct('FileDescription', 'CXHelper'),
      StringStruct('FileVersion', %(v)r),
      StringStruct('InternalName', 'CXHelper'),
      StringStruct('LegalCopyright', 'chaoxing (c) Samueli924, GPL-3.0; GUI by Suzume'),
      StringStruct('OriginalFilename', 'CXHelper.exe'),
      StringStruct('ProductName', 'CXHelper'),
      StringStruct('ProductVersion', %(v)r)])]),
    VarFileInfo([VarStruct('Translation', [0x0804, 1200])])
  ]
)
""" % {"t": parts, "v": version}
    path = os.path.join(BUILD, "version_info.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def save_user_files():
    """PyInstaller wipes dist\\CXHelper\\; keep any user data someone left in it."""
    found = [f for f in USER_FILES if os.path.isfile(os.path.join(APP_DIR, f))]
    if not found:
        return
    keep = os.path.join(BUILD, "dist-userdata-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(keep)
    for f in found:
        shutil.move(os.path.join(APP_DIR, f), os.path.join(keep, f))
    print("  user files found in dist moved to %s: %s" % (keep, ", ".join(found)))


def freeze(version_file):
    step("PyInstaller one-folder build")
    save_user_files()
    # One folder rather than --onefile: ddddocr/onnxruntime/OpenCV make the app
    # large, and a onefile exe would unpack all of it to %TEMP% on every launch.
    # ddddocr/onnxruntime: their modules go into the archive, only data files
    # and DLLs are copied (--collect-all would drop their .py files as well).
    cmd = [
        sys.executable, "-m", "PyInstaller", os.path.join(STAGE, "gui", "app.py"),
        "--name", NAME, "--onedir", "--windowed", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyi"), "--specpath", BUILD,
        "--paths", STAGE, "--paths", os.path.join(STAGE, "gui"),
        "--icon", os.path.join(HERE, "assets", "icon.ico"),
        "--version-file", version_file,
        "--hidden-import", "main", "--hidden-import", "_bundle", "--hidden-import", "release_probe",
        "--collect-submodules", "api",
        "--collect-data", "ddddocr", "--collect-submodules", "ddddocr",
        "--collect-binaries", "onnxruntime", "--collect-submodules", "onnxruntime",
        "--exclude-module", "celery", "--exclude-module", "flask", "--exclude-module", "tkinter",
        "--exclude-module", "fastapi", "--exclude-module", "uvicorn",
        # build tools that live in the same venv
        "--exclude-module", "PyInstaller", "--exclude-module", "python_minifier",
        "--exclude-module", "rjsmin", "--exclude-module", "rcssmin",
    ]
    subprocess.run(cmd, check=True, cwd=BUILD)
    internal = os.path.join(APP_DIR, "_internal")
    for pattern in TRIM:
        for p in glob.glob(os.path.join(internal, pattern)):
            os.remove(p)
            print("  trimmed %s" % os.path.relpath(p, APP_DIR))


# ------------------------------------------------------------------- notices

def bundled_distributions():
    from importlib import metadata

    top = set()
    with open(os.path.join(BUILD, "pyi", NAME, "PYZ-00.toc"), encoding="utf-8") as f:
        pyz = ast.literal_eval(f.read())
    for entry in pyz[-1]:
        top.add(entry[0].split(".")[0])
    for name in os.listdir(os.path.join(APP_DIR, "_internal")):
        top.add(name.split(".")[0])
    mapping = metadata.packages_distributions()
    dists = {d for t in top for d in mapping.get(t, [])}
    dists.update(EXTRA_DISTRIBUTIONS)
    return sorted(dists, key=str.lower)


def _is_license_file(path):
    base = os.path.basename(path).upper()
    in_licenses_dir = "/licenses/" in path.replace("\\", "/").lower()
    return ".DIST-INFO" in path.upper() and (
        in_licenses_dir or base.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "THIRDPARTY")))


def _project_url(dist):
    for entry in dist.metadata.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository", "source code"):
            return url.strip()
    return ""


def write_notices():
    step("licence files")
    from importlib import metadata

    shutil.copyfile(os.path.join(ROOT, "LICENSE"), os.path.join(APP_DIR, "LICENSE.txt"))
    out = [
        "CXHelper - licence and third-party notices",
        "=" * 72,
        "",
        "CXHelper is a graphical interface for chaoxing by Samueli924",
        "(https://github.com/Samueli924/chaoxing), which is licensed under the",
        "GNU General Public License v3.0 - full text in LICENSE.txt. CXHelper",
        "includes that project's code and is distributed under the same licence.",
        "The source code of chaoxing is available at the address above; the",
        "source code of the CXHelper interface can be requested from the",
        "publisher (https://suzume.cloud).",
        "",
        "This build also contains the components listed below. Each is",
        "redistributed under its own licence, reproduced in full.",
        "",
    ]
    out += ["-" * 72, "Python %d.%d.%d - Python Software Foundation License" % sys.version_info[:3], "-" * 72, ""]
    with open(os.path.join(sys.base_prefix, "LICENSE.txt"), encoding="utf-8", errors="replace") as f:
        out += [f.read().strip(), ""]

    names = []
    for dist_name in bundled_distributions():
        try:
            dist = metadata.distribution(dist_name)
        except metadata.PackageNotFoundError:
            continue
        lic = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        if len(lic) > 80 or "\n" in lic:
            lic = ""
        if not lic:
            lic = ", ".join(c.split("::")[-1].strip() for c in dist.metadata.get_all("Classifier") or []
                            if c.startswith("License ::"))
        files = [p for p in (dist.files or []) if _is_license_file(str(p))]
        out += ["-" * 72, "%s %s - %s" % (dist.metadata["Name"], dist.version, lic or "see below"),
                dist.metadata.get("Home-page") or _project_url(dist) or "", "-" * 72, ""]
        texts = [(p.name, p.locate().read_text(encoding="utf-8", errors="replace")) for p in files]
        texts = [(n, t) for n, t in texts if t and t.strip()]
        if texts:
            for n, t in texts:
                out += ["[%s]" % n, t.strip(), ""]
        else:
            out += ["(no licence file shipped with the package; licence: %s)" % (lic or "unknown"), ""]
        names.append(dist.metadata["Name"])

    out += ["-" * 72, "Microsoft Edge WebView2 SDK (Microsoft.Web.WebView2.Core.dll,",
            "Microsoft.Web.WebView2.WinForms.dll, WebView2Loader.dll; shipped with pywebview)", "-" * 72, "",
            "Copyright (C) Microsoft Corporation. Redistributed under the Microsoft",
            "WebView2 SDK licence: https://www.nuget.org/packages/Microsoft.Web.WebView2/",
            "The WebView2 Runtime itself is not included; it is part of Windows 10/11",
            "or available from https://developer.microsoft.com/microsoft-edge/webview2/", "",
            "-" * 72, "Microsoft Visual C++ runtime (vcruntime140*.dll, msvcp140*.dll)", "-" * 72, "",
            "Redistributable files of Microsoft Visual Studio, shipped as permitted",
            "by its licence terms for redistributable code.", ""]
    with open(os.path.join(APP_DIR, "THIRD-PARTY-NOTICES.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(out))
    print("  %d packages: %s" % (len(names), ", ".join(names)))


# ---------------------------------------------------------------------- scan

def scan_build():
    step("scan build for stray files")
    bad = []
    loose_py = []
    for dirpath, _, filenames in os.walk(APP_DIR):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, APP_DIR).replace("\\", "/")
            low = fn.lower()
            if low in USER_FILES or low.endswith(".log"):
                bad.append(rel)
            if low.endswith(".py"):
                # OpenCV's loader reads its config*.py files from disk; nothing else may be source
                (loose_py if rel.startswith("_internal/cv2/") else bad).append(rel)
            if os.path.getsize(p) > 256 * 1024 * 1024:
                continue
            with open(p, "rb") as f:
                data = f.read()
            for n in NEEDLES:
                if n in data:
                    bad.append("%s contains %r" % (rel, n))
    if bad:
        raise SystemExit("build contains files that must not ship:\n  " + "\n  ".join(bad))
    count = sum(len(f) for _, _, f in os.walk(APP_DIR))
    size = sum(os.path.getsize(os.path.join(d, x)) for d, _, f in os.walk(APP_DIR) for x in f)
    print("  clean: %d files, %.1f MB; only loose .py: %d OpenCV loader configs" % (count, size / 1048576, len(loose_py)))
    return {"files": count, "bytes": size}


# ------------------------------------------------------------------ selftest

def run_selftest():
    step("selftest (copy of the build, scratch folder)")
    scratch = tempfile.mkdtemp(prefix="cx-selftest-")
    try:
        target = os.path.join(scratch, NAME)
        shutil.copytree(APP_DIR, target)
        proc = subprocess.run([os.path.join(target, NAME + ".exe"), "--selftest"], timeout=300)
        with open(os.path.join(target, "selftest.json"), encoding="utf-8") as f:
            result = json.load(f)
        for name, r in result.items():
            if not name.startswith("_"):
                print("  %-18s %s  %s" % (name, "ok  " if r["ok"] else "FAIL", str(r["detail"])[:90]))
        if proc.returncode != 0 or not result["_summary"]["ok"]:
            raise SystemExit("selftest failed (exit %s)" % proc.returncode)
        return {k: v["ok"] for k, v in result.items() if not k.startswith("_")}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ----------------------------------------------------------------- installer

def find_iscc():
    for p in (os.environ.get("ISCC", ""),
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
              r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
              r"C:\Program Files\Inno Setup 6\ISCC.exe"):
        if p and os.path.isfile(p):
            return p
    raise SystemExit("ISCC.exe (Inno Setup 6) not found - set ISCC=<path>")


def build_installer(version, test_build=False):
    step("Inno Setup installer%s" % (" (TestBuild)" if test_build else ""))
    cmd = [find_iscc(), "/Q", "/DAppVersion=" + version, "/DSrcDir=" + ROOT]
    if test_build:
        cmd.append("/DTestBuild")
    cmd.append(os.path.join(ROOT, "installer", NAME + ".iss"))
    subprocess.run(cmd, check=True)
    return os.path.join(ROOT, "installer", "output", ("CXHelperTest" if test_build else NAME) + "-Setup.exe")


# ------------------------------------------------------------------- release

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gather_release(version, setup, info):
    step("release folder")
    out = os.path.join(RELEASE, "%s-%s" % (NAME, version))
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "build-scripts"))
    shutil.copy2(setup, out)
    for fn in ("LICENSE.txt", "THIRD-PARTY-NOTICES.txt"):
        shutil.copy2(os.path.join(APP_DIR, fn), out)
    shutil.copy2(os.path.join(ROOT, "BUILD-RELEASE.md"), out)
    for rel in ("gui/build_exe.bat", "gui/build_release.py", "gui/build_bundle.py", "gui/release_probe.py",
                "installer/CXHelper.iss", "installer/ChineseSimplified.isl", "requirements-build.txt"):
        shutil.copy2(os.path.join(ROOT, rel), os.path.join(out, "build-scripts"))
    digest = sha256(setup)
    with open(os.path.join(out, "SHA256SUMS.txt"), "w", encoding="ascii", newline="\r\n") as f:
        f.write("%s *%s\n" % (digest, os.path.basename(setup)))
    info = dict(info, installer={"file": os.path.basename(setup), "bytes": os.path.getsize(setup), "sha256": digest})
    with open(os.path.join(out, "build-info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    print("  %s" % out)
    for fn in sorted(os.listdir(out)):
        p = os.path.join(out, fn)
        if os.path.isfile(p):
            print("    %-28s %12d bytes" % (fn, os.path.getsize(p)))
    print("  sha256 %s" % digest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--installer", action="store_true", help="compile the setup exe and fill release\\")
    ap.add_argument("--test-installer", action="store_true", help="also compile the throwaway CXHelperTest setup")
    ap.add_argument("--skip-tests", action="store_true", help="skip the tests on the minified sources")
    ap.add_argument("--skip-selftest", action="store_true")
    args = ap.parse_args()

    version = app_version()
    print("%s %s" % (NAME, version))
    shutil.rmtree(STAGE, ignore_errors=True)
    shutil.rmtree(os.path.join(BUILD, "pyi"), ignore_errors=True)
    os.makedirs(os.path.join(STAGE, "gui"))

    step("reference values from the original sources")
    reference = run_probe(ROOT, "original")
    print("  %d probes" % len(reference))

    import PyInstaller
    import python_minifier
    info = {
        "name": NAME, "version": version,
        "built": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": "%d.%d.%d" % sys.version_info[:3], "pyinstaller": PyInstaller.__version__,
        "python_minifier": getattr(python_minifier, "__version__", "") or _dist_version("python-minifier"),
    }
    step("bundle web UI + data files")
    build_bundle.build(os.path.join(STAGE, "gui", "_bundle.py"), extra={
        "build/reference.json": json.dumps(reference, ensure_ascii=False).encode("utf-8"),
        "build/info.json": json.dumps({"version": version, "built": info["built"]}).encode("utf-8"),
    })
    stage_sources()
    if not args.skip_tests:
        test_stage(reference)
    freeze(write_version_file(version))
    write_notices()
    info["app"] = scan_build()
    if not args.skip_selftest:
        info["selftest"] = run_selftest()
    if args.test_installer:
        build_installer(version, test_build=True)
    if args.installer:
        gather_release(version, build_installer(version), info)
    print("\nDone: %s" % APP_DIR)


def _dist_version(name):
    from importlib import metadata
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


if __name__ == "__main__":
    main()
