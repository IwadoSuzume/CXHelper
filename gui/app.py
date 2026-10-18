import json
import os
import queue
import sys
import threading
import time
import webbrowser

import backend  # sets up paths/cwd/stdout before anything from the project loads
import resources
import webview


class Api:
    def __init__(self):
        self._window = None
        # evaluate_js blocks until the UI thread has run the script. Pushes come
        # from the run's worker threads (every log line, every progress tick),
        # so they go through a queue drained by one dispatcher thread: a busy or
        # stalled UI can never hold up the workers - or a stop.
        self._outbox = queue.Queue()
        threading.Thread(target=self._dispatch, daemon=True, name="ui-push").start()

    def set_window(self, window):
        self._window = window

    def _push(self, event, *args):
        # args are JSON-encoded so arbitrary course/video names can't break the
        # JS; each call is wrapped so one failing handler can't drop the rest
        # of a batch
        self._outbox.put("try{window.__bridge && window.__bridge(%s, %s)}catch(e){}" % (
            json.dumps(event), json.dumps(list(args), ensure_ascii=False)))

    def _dispatch(self):
        while True:
            batch = [self._outbox.get()]
            time.sleep(0.1)  # let a burst (log lines + progress ticks) pile up
            try:
                while len(batch) < 300:
                    batch.append(self._outbox.get_nowait())
            except queue.Empty:
                pass
            window = self._window
            if window is None:
                continue
            # pywebview's evaluate_js registers a callback slot per call and
            # never frees it when no callback is given - over a many-hour run
            # that's a steady leak, so drop the slots this call added.
            callbacks = getattr(window, "_callbacks", None)
            before = set(callbacks) if isinstance(callbacks, dict) else None
            # Never use evaluate_js's callback= form here - on the WinForms
            # backend it throws off the main thread (pywebview#906).
            try:
                window.evaluate_js(";".join(batch))
            except Exception:
                pass
            if before is not None:
                for key in [k for k in list(callbacks) if k not in before and callbacks.get(k) is None]:
                    callbacks.pop(key, None)

    def get_bootstrap(self):
        return backend.get_bootstrap()

    def get_courses(self):
        return backend.get_courses()

    def login(self, username, password, remember=False):
        return backend.start_login(username, password, bool(remember))

    def logout(self):
        return backend.logout()

    def start_run(self, course_keys):
        return backend.start_run(course_keys or [])

    def stop_run(self, force=False):
        return backend.request_stop(force=bool(force))

    def get_settings(self):
        return backend.get_settings()

    def save_settings(self, patch=None):
        return backend.save_settings(patch or {})

    def open_folder(self):
        os.startfile(backend.ROOT)

    def minimize_window(self):
        if self._window:
            self._window.minimize()

    def close_window(self):
        if self._window:
            self._window.destroy()

    def open_url(self, url):
        webbrowser.open(url)


def selftest():
    """Hidden `CXHelper.exe --selftest [--online]`: exercises the frozen
    build's internals and writes selftest.json next to the exe - used to
    verify every packaged build. Exit code 0 = all checks passed.
    --online also opens Chaoxing's login page (read-only, no account)."""
    import traceback
    result = {}

    def check(name, fn):
        try:
            result[name] = {"ok": True, "detail": fn()}
        except Exception as e:
            result[name] = {"ok": False, "detail": "%s: %s" % (type(e).__name__, e), "trace": traceback.format_exc()[-800:]}

    def web_ui():
        html = resources.ui_html()
        for needle in ("window.__bridge", "<style>", "data:image/png;base64,"):
            if needle not in html:
                raise RuntimeError("UI document is missing %r" % needle)
        return len(html)

    check("web_ui", web_ui)
    check("settings", lambda: backend.get_settings()["speed"])
    check("hooks", lambda: [backend.cx.process_chapter.__name__, backend.cx_base.tqdm.__name__])
    def font_map():
        size = len(__import__("api.cxsecret_font", fromlist=["x"]).fonthash_dao.char_map)
        if not size:
            raise RuntimeError("font table is empty")
        return size

    check("font_map", font_map)

    def no_loose_sources():
        # in a packaged build nothing of the project may sit next to the exe as a file
        if not getattr(sys, "frozen", False):
            return "not frozen"
        base = sys._MEIPASS
        found = [p for p in ("web", "resource", os.path.join("..", "config_template.ini"))
                 if os.path.exists(os.path.join(base, p))]
        if found:
            raise RuntimeError("found on disk: %s" % found)
        return "clean"

    check("no_loose_sources", no_loose_sources)

    def ocr():
        import io
        from PIL import Image, ImageDraw
        from api import captcha
        engine = captcha.ocr_init()
        if engine is None:
            raise RuntimeError("ocr_init returned None")
        im = Image.new("RGB", (120, 40), "white")
        ImageDraw.Draw(im).text((12, 12), "a8K3", fill="black")
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return engine.classification(buf.getvalue())

    check("captcha_ocr", ocr)

    def upstream_logic():
        # the same probe build_release.py ran against the ORIGINAL sources:
        # any difference means minifying the code changed what it does
        import release_probe
        got = release_probe.probe()
        if not getattr(sys, "frozen", False):
            return "not frozen: %d probes ran" % len(got)
        expected = json.loads(resources.read_text("build/reference.json"))
        diff = sorted(k for k in set(expected) | set(got) if expected.get(k) != got.get(k))
        if diff:
            raise RuntimeError("differs from the original sources: %s" % diff)
        return "%d probes match the original sources" % len(expected)

    check("upstream_logic", upstream_logic)

    def version():
        if not getattr(sys, "frozen", False):
            return backend.APP_VERSION
        built = json.loads(resources.read_text("build/info.json"))
        if built["version"] != backend.APP_VERSION:
            raise RuntimeError("bundle built for %s, app says %s" % (built["version"], backend.APP_VERSION))
        return built

    check("version", version)

    if "--online" in sys.argv:
        def online():
            import requests
            r = requests.get("https://passport2.chaoxing.com/login", timeout=20)
            r.raise_for_status()
            if "chaoxing" not in r.text.lower():
                raise RuntimeError("unexpected login page (%d bytes)" % len(r.text))
            return "HTTP %d, %d bytes" % (r.status_code, len(r.content))

        check("online_login_page", online)

    def ui_window():
        # Opens the real window (hidden) and waits for the version label, which
        # only appears once the page has loaded, its script has run and a call
        # into Python has come back.
        api = Api()
        backend.set_push(api._push)
        window = webview.create_window("CXHelper", html=resources.ui_html(), js_api=api, hidden=True, width=1100, height=665)
        api.set_window(window)
        seen = {}

        def probe():
            try:
                deadline = time.time() + 30
                while time.time() < deadline and not (seen.get("state") or {}).get("version"):
                    time.sleep(0.5)
                    try:
                        seen["state"] = window.evaluate_js(
                            "({version: (document.getElementById('version-text') || {}).textContent || '',"
                            " logo: (document.querySelector('.brand-badge img') || {}).naturalWidth || 0,"
                            " cssRules: document.styleSheets.length ? document.styleSheets[0].cssRules.length : 0,"
                            " tabs: document.querySelectorAll('.tab').length})")
                    except Exception as e:
                        seen["error"] = "%s: %s" % (type(e).__name__, e)
            finally:
                window.destroy()

        webview.start(probe)
        state = seen.get("state") or {}
        if not (state.get("version") and state.get("logo") and state.get("cssRules") and state.get("tabs")):
            raise RuntimeError("UI did not come up: %s" % seen)
        return state

    check("ui_window", ui_window)
    ok = all(r["ok"] for r in result.values())
    result["_summary"] = {"ok": ok, "version": backend.APP_VERSION, "frozen": bool(getattr(sys, "frozen", False))}
    with open(os.path.join(backend.ROOT, "selftest.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return ok


def main():
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    api = Api()
    backend.set_push(api._push)
    # the UI is handed over as one in-memory document - there are no web files on disk
    window = webview.create_window(
        "CXHelper",
        html=resources.ui_html(),
        js_api=api,
        frameless=True,
        easy_drag=False,
        width=1100,
        height=665,
        min_size=(640, 500),
        background_color="#141317",
    )
    api.set_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
