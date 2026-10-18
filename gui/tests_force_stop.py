"""Regression tests for CXHelper's stop / force stop (no network, no real account).

    venv\\Scripts\\python.exe gui\\tests_force_stop.py

A fake Chaoxing client stands in for api.base.Chaoxing. Everything else is
real: upstream main.JobProcessor / process_chapter / process_job (with the
GUI's hooks), real `requests` sessions against a local HTTP server, the real
live-session loop (api.live_process) and api.base's sleeps.
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backend  # noqa: E402  (sets up paths / hooks)
import requests  # noqa: E402
import api.base as cx_base  # noqa: E402
import api.live as cx_live  # noqa: E402
from api.base import StudyResult  # noqa: E402


class _Slow(BaseHTTPRequestHandler):
    def do_POST(self):  # a minimal OpenAI-compatible chat completion
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        time.sleep(0.3)
        body = (b'{"id":"x","object":"chat.completion","created":0,"model":"m","choices":[{"index":0,'
                b'"message":{"role":"assistant","content":"A"},"finish_reason":"stop"}]}')
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/429"):
            self.send_response(429)
            self.send_header("Retry-After", "6")
            self.end_headers()
            return
        time.sleep(float(self.path.split("=")[-1]) if "=" in self.path else 0.1)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


_srv = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % _srv.server_address[1]
URL = BASE + "/?t="
_session = requests.Session()
_job_log = []

# the real live loop, with the two network calls pointed at the local server
cx_live.Live.get_status = lambda self: {"temp": {"data": {"duration": 3600}}}
cx_live.Live.do_finish = lambda self: _session.get(URL + "0.05", timeout=5).ok


def _job(kind, i):
    return {"type": "live" if kind == "live" else "video", "jobid": "%s%d" % (kind, i),
            "name": "%s-%d" % (kind, i), "kind": kind, "property": {"title": "live-%d" % i}}


class FakeCX:
    """Scenario is chosen per run through FakeCX.SCENARIO:
       instant  - course A: one chapter of 12 instant videos (the real-log case),
                  course B: chapters with one long, progress-bar video
       swallow  - a quiz-like job that loops on requests inside `except Exception`
       sleep    - a job sitting in a long api.base rate-limiter-style sleep
       live     - a real live-session loop (59 s waits between reports)
       error    - one chapter raises an unexpected exception
       stuck    - a job stuck in an uninterruptible call
    """
    SCENARIO = "instant"

    LOGINS = []
    TIKU_SEEN = []

    def __init__(self, *a, **k):
        self.tiku = k.get("tiku")
        self.rate_limiter = type("R", (), {"limit_rate": lambda *a, **k: None})()

    def login(self, login_with_cookies=False):
        FakeCX.LOGINS.append(login_with_cookies)
        return {"status": True, "msg": "ok"}

    @staticmethod
    def get_name():
        return "测试"

    def get_uid(self):
        return "1"

    def get_course_list(self):
        return [{"courseId": "A", "clazzId": "1", "cpi": "1", "title": "课程A", "teacher": ""},
                {"courseId": "B", "clazzId": "2", "cpi": "2", "title": "课程B", "teacher": ""}]

    def get_course_point(self, cid, clz, cpi):
        n = 1 if cid == "A" else 3
        return {"points": [{"id": "%s-%d" % (cid, i), "title": "%s章%d" % (cid, i), "has_finished": False}
                           for i in range(n)]}

    def get_job_list(self, course, point):
        s = FakeCX.SCENARIO
        if s == "instant" and course["courseId"] == "A":
            return [_job("instant", i) for i in range(12)], {"knowledgeid": "k"}
        if s in ("swallow", "sleep", "live", "stuck", "openai", "tenacity", "retryafter") and course["courseId"] == "A":
            return [_job(s, 0)], {"knowledgeid": "k"}
        if s == "error" and point["id"] == "B-1":
            return [_job("boom", 0)], {}
        return [_job("long", 0)], {"knowledgeid": "k"}

    def study_video(self, course, job, job_info, _speed=1.0, _type="Video"):
        _job_log.append((time.time(), job["name"]))
        FakeCX.TIKU_SEEN.append(type(self.tiku).__name__)
        kind = job["kind"]
        if kind == "openai":  # AI question bank: the openai SDK (httpx2 underneath)
            from openai import OpenAI
            client = OpenAI(api_key="k", base_url=BASE + "/v1")
            for _ in range(200):
                client.chat.completions.create(model="m", messages=[{"role": "user", "content": "q"}])
            return StudyResult.SUCCESS
        if kind == "tenacity":  # same decorator shape as api.base.study_work
            @cx_base.retry(stop=cx_base.stop_after_attempt(3), wait=cx_base.wait_fixed(1),
                           retry=cx_base.retry_if_exception(lambda e: True), reraise=True)
            def fetch():
                return _session.get(URL + "0.05", timeout=10)
            for _ in range(200):
                fetch()
            return StudyResult.SUCCESS
        if kind == "retryafter":  # urllib3 honouring Retry-After inside one send
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            sess = requests.Session()
            sess.mount("http://", HTTPAdapter(max_retries=Retry(total=5, status_forcelist=[429])))
            sess.get(BASE + "/429", timeout=10)
            return StudyResult.SUCCESS
        if kind == "instant":
            _session.get(URL + "0.4", timeout=10)
            return StudyResult.SUCCESS
        if kind == "swallow":  # like study_work / tiku providers: every error swallowed, keep going
            for _ in range(200):
                try:
                    _session.get(URL + "0.05", timeout=10)
                except Exception:
                    pass
                cx_base.time.sleep(0.05)
            return StudyResult.SUCCESS
        if kind == "sleep":
            cx_base.time.sleep(30)  # api.base's time: RateLimiter / 403 back-off style
            return StudyResult.SUCCESS
        if kind == "stuck":
            time.sleep(30)  # genuinely uninterruptible
            return StudyResult.SUCCESS
        if kind == "boom":
            raise ValueError("unexpected page layout")
        bar = cx_base.tqdm(total=40, initial=0, desc=job["name"])
        try:
            for i in range(40):
                _session.get(URL + "0.02", timeout=10)
                cx_base.time.sleep(0.08)
                bar.n = i + 1
                bar.refresh()
        finally:
            bar.close()
        return StudyResult.SUCCESS


backend.Chaoxing = FakeCX
backend.cx.Chaoxing = FakeCX
backend._session_info.update(loggedIn=True, name="测试", username="13800000000")

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def run(label, scenario, graceful_at=None, force_at=None, budget=60.0, probe=False):
    FakeCX.SCENARIO = scenario
    events, done = [], threading.Event()

    def push(ev, *args):
        events.append((time.time(), ev, args))
        if ev == "onRunFinished":
            done.set()

    backend.set_push(push)
    backend.get_courses()
    _job_log.clear()
    t0 = time.time()
    assert backend.start_run(["A_1", "B_2"])["ok"]
    t_grace = t_force = None
    probe_ok = None
    if graceful_at is not None:
        time.sleep(graceful_at)
        t_grace = time.time()
        backend.request_stop(False)
    if force_at is not None:
        time.sleep(force_at)
        t_force = time.time()
        backend.request_stop(True)
        if probe:  # a UI-thread request while the run is being force-stopped
            try:
                requests.Session().get(URL + "0", timeout=5)
                probe_ok = backend.get_courses()["ok"]
            except BaseException as e:
                probe_ok = repr(e)
    finished = done.wait(budget)
    t_end = time.time()
    t_mark = t_force or t_grace
    after = [n for t, n in _job_log if t_mark and t > t_mark + 0.05]
    summary = next((a[0] for _, ev, a in events if ev == "onRunFinished"), None)
    chapters = [a[0] for _, ev, a in events if ev == "onChapter"]
    latency = (t_end - t_mark) if (finished and t_mark) else None
    print("%-34s finished=%-5s total=%5.1fs stop_latency=%-6s jobs_after_stop=%-2d summary=%s" % (
        label, finished, t_end - t0, "%.2fs" % latency if latency is not None else "-", len(after), summary))
    return {"finished": finished, "latency": latency, "after": after, "summary": summary,
            "chapters": chapters, "probe": probe_ok}


def main():
    backend.save_settings({"jobs": 2, "retryInterval": 0.2})

    r = run("1 full run", "instant")
    check(r["finished"] and not r["summary"]["stopped"] and r["summary"]["ok"] == 4, "1: full run did not complete: %s" % r["summary"])

    r = run("2 force stop, instant-video chapter", "instant", force_at=1.5, probe=True)
    check(r["finished"] and r["latency"] < 1.5, "2: force stop too slow (%s)" % r["latency"])
    check(len(r["after"]) == 0, "2: %d jobs started after force stop" % len(r["after"]))
    check(r["probe"] is True, "2: UI-thread requests broken during force stop: %r" % r["probe"])

    r = run("3 graceful stop", "instant", graceful_at=1.5)
    check(r["finished"] and r["latency"] < 5, "3: graceful stop took %s" % r["latency"])
    check(len(r["after"]) <= 1, "3: graceful stop let %d more jobs start" % len(r["after"]))

    r = run("4 force stop, error-swallowing loop", "swallow", force_at=1.0)
    check(r["finished"] and r["latency"] < 1.5, "4: swallowing loop not stopped (%s)" % r["latency"])

    r = run("5 force stop, long api sleep", "sleep", force_at=1.0)
    check(r["finished"] and r["latency"] < 1.5, "5: sleep not interrupted (%s)" % r["latency"])

    r = run("6 force stop, live session", "live", force_at=1.5)
    check(r["finished"] and r["latency"] < 1.5, "6: live session not stopped (%s)" % r["latency"])

    r = run("7 unexpected error in a chapter", "error", budget=30)
    check(r["finished"], "7: run hung after an exception in a chapter")
    check(r["summary"] and r["summary"]["failed"] == 1 and r["summary"]["ok"] == 3,
          "7: expected 1 failed + 3 ok, got %s" % r["summary"])

    backend.FORCE_GRACE_S = 2.0
    r = run("8 force stop, stuck uninterruptible call", "stuck", force_at=1.0, budget=20)
    check(r["finished"] and r["latency"] < 4, "8: run never gave up on a stuck thread (%s)" % r["latency"])
    backend.FORCE_GRACE_S = 15.0

    r = run("9 force stop, openai SDK (AI bank)", "openai", force_at=1.5)
    check(r["finished"] and r["latency"] < 1.5, "9: openai/httpx2 requests not stopped (%s)" % r["latency"])

    r = run("10 force stop, tenacity retry", "tenacity", force_at=1.0)
    check(r["finished"] and r["latency"] < 1.0, "10: tenacity retried the stop (%s)" % r["latency"])

    r = run("11 force stop, urllib3 Retry-After", "retryafter", force_at=1.0)
    check(r["finished"] and r["latency"] < 1.5, "11: Retry-After sleep not interrupted (%s)" % r["latency"])

    # '%' in a remembered password / token, TikuManual left in config.ini from the CLI
    backend._write_ini({("common", "password"): "Ab%12", ("tiku", "provider"): "TikuManual",
                        ("tiku", "tokens"): "t%x"})
    FakeCX.TIKU_SEEN.clear()
    FakeCX.LOGINS.clear()
    r = run("12 '%' in config + TikuManual", "instant")
    check(r["finished"] and not r["summary"]["error"], "12: run failed: %s" % r["summary"])
    check("TikuManual" not in FakeCX.TIKU_SEEN and FakeCX.TIKU_SEEN, "12: TikuManual used: %s" % set(FakeCX.TIKU_SEEN))
    check(FakeCX.LOGINS[:1] == [True], "12: run did not re-check the login first: %s" % FakeCX.LOGINS[:3])
    backend._write_ini({("common", "password"): "", ("tiku", "provider"): "", ("tiku", "tokens"): ""})

    # course_list must stay in the CLI's format
    import main as cli
    c, _, _ = cli.load_config_from_file(backend.CONFIG_PATH)
    picked = cli.filter_courses(FakeCX().get_course_list(), c.get("course_list"))
    check(c.get("course_list") == ["A", "B"] and len(picked) == 2,
          "13: course_list %r not usable by the CLI" % c.get("course_list"))

    r = run("14 full run after all of the above", "instant")
    check(r["finished"] and not r["summary"]["stopped"] and r["summary"]["ok"] == 4,
          "14: run after stops is not normal: %s" % r["summary"])

    backend.save_settings({"jobs": 4, "retryInterval": 1.0})
    print("\nRESULT:", "PASS" if not failures else "FAIL")
    for f in failures:
        print("  -", f)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
