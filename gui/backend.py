"""GUI bridge onto the unmodified chaoxing project (main.py + api/).

Everything the CLI does is reused as-is; the GUI only hooks in from outside:
  * config lives in the same config.ini the CLI reads (edited line-by-line so
    the template's comments survive), next to the exe
  * video progress: api.base's tqdm is swapped for a class that reports to the UI
  * chapter results / graceful stop: main.process_chapter is wrapped
  * status text: an extra loguru sink forwards INFO+ lines to the UI
"""
import os
import sys


def _project_root():
    # Frozen (PyInstaller): data files (cookies.txt, config.ini, chaoxing.log,
    # cache.json) belong next to the exe. From source: the project root, one
    # level above gui/.
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = _project_root()
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# api/ resolves cookies.txt, chaoxing.log, cache.json and config.ini relative
# to the working directory - pin it so a shortcut's "Start in" can't scatter them.
os.chdir(ROOT)


class _SafeStream:
    """Replaces sys.stdout/stderr for the whole process.

    A --windowed build has no console (sys.stdout/stderr are None) and
    api/logger.py grabs sys.stderr at import time for its tqdm sink, so this
    must be installed before the first `import api...`. Writes are swallowed
    (or teed to a real console when one exists).
    """

    def __init__(self, real):
        self._real = real

    def write(self, s):
        if self._real is not None and s:
            try:
                self._real.write(s)
            except Exception:
                pass
        return len(s or "")

    def flush(self):
        if self._real is not None:
            try:
                self._real.flush()
            except Exception:
                pass

    def isatty(self):
        return False


sys.stdout = _SafeStream(sys.__stdout__)
sys.stderr = _SafeStream(sys.__stderr__)

import builtins
import configparser
import io
import re
import threading
import time

import resources


def _import_font_module():
    """api.cxsecret_font reads resource/font_map_table.json from disk while it
    is being imported. The table travels inside the app (see resources.py), so
    for the length of that one import `open` answers for that file from there."""
    table = resources.read_text("resource/font_map_table.json")
    real_open = builtins.open

    def open_font_table(path, *args, **kwargs):
        if os.path.basename(str(path)) == "font_map_table.json":
            return io.StringIO(table)
        return real_open(path, *args, **kwargs)

    builtins.open = open_font_table
    try:
        import api.cxsecret_font  # noqa: F401
    finally:
        builtins.open = real_open


_import_font_module()

# ---- the real project, imported unmodified ----
import main as cx
import api.base as cx_base
from api.answer import Tiku
from api.base import Account, Chaoxing, SessionManager
from api.config import GlobalConst
from api.logger import logger
from api.notification import Notification
from api.process import increase_learning_count_for_course

CONFIG_PATH = os.path.join(ROOT, "config.ini")
COOKIES_PATH = os.path.join(ROOT, GlobalConst.COOKIES_PATH)

APP_VERSION = "3.1.3"

# ---------------------------------------------------------------------------
# UI push channel (set by app.py) - every push is a fire-and-forget event
# ---------------------------------------------------------------------------

_push = lambda *a, **k: None  # noqa: E731


def set_push(fn):
    global _push
    _push = fn


# ---------------------------------------------------------------------------
# config.ini - the CLI's own file, edited in place so its comments survive
# ---------------------------------------------------------------------------

_config_lock = threading.RLock()


def _ensure_config():
    """First run: seed config.ini from the template, with the placeholder
    account and the default question bank / notification provider cleared
    (an empty provider means "don't use" to the project itself)."""
    if os.path.isfile(CONFIG_PATH):
        return
    with open(CONFIG_PATH, "wb") as f:
        f.write(resources.read("config_template.ini"))
    _write_ini({
        ("common", "username"): "",
        ("common", "password"): "",
        ("common", "course_list"): "",
        ("common", "use_cookies"): "false",
        ("tiku", "provider"): "",
        ("notification", "provider"): "",
        ("notification", "tg_chat_id"): "",
    })


def _read_ini():
    _ensure_config()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(CONFIG_PATH, encoding="utf-8")
    return parser


def _write_ini(values):
    """values: {(section, key): str}. Rewrites only the `key = value` lines it
    touches (uncommented ones inside the right section); keys that don't exist
    yet are appended to the end of their section."""
    with _config_lock:
        _ensure_config()  # no-op once the file exists (it calls back here only right after creating it)
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")
        pending = dict(values)
        section = None
        section_end = {}
        for i, line in enumerate(lines):
            m = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
            if m:
                section = m.group(1).strip()
                section_end[section] = i + 1
                continue
            if section is None:
                continue
            if line.strip() and not line.lstrip().startswith((";", "#")):
                section_end[section] = i + 1
            km = re.match(r"^(\s*)([A-Za-z0-9_]+)(\s*)=", line)
            if km and (section, km.group(2)) in pending:
                key = km.group(2)
                lines[i] = "%s = %s" % (key, pending.pop((section, key)))
        # append whatever wasn't there yet, bottom-up so indices stay valid
        extra = {}
        for (sec, key), value in pending.items():
            extra.setdefault(sec, []).append("%s = %s" % (key, value))
        for sec in sorted(extra, key=lambda s: section_end.get(s, len(lines)), reverse=True):
            if sec in section_end:
                at = section_end[sec]
                lines[at:at] = extra[sec]
            else:
                lines += ["", "[%s]" % sec] + extra[sec]
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


def _get(parser, section, key, default=""):
    try:
        return parser.get(section, key).strip()
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default


def _bool(v):
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _num(v, default, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


# Settings the UI edits, as (section, key, ui-name, kind)
_SETTING_FIELDS = [
    ("common", "speed", "speed", "float"),
    ("common", "jobs", "jobs", "int"),
    ("common", "notopen_action", "notopenAction", "str"),
    ("common", "retry_interval", "retryInterval", "float"),
    ("common", "add_learning_count", "addLearningCount", "bool"),
    ("common", "target_count", "targetCount", "int"),
    ("tiku", "provider", "tikuProvider", "str"),
    ("tiku", "tokens", "tikuTokens", "str"),
    ("tiku", "url", "tikuUrl", "str"),
    ("tiku", "go_authorization", "tikuGoAuth", "str"),
    ("tiku", "endpoint", "aiEndpoint", "str"),
    ("tiku", "key", "aiKey", "str"),
    ("tiku", "model", "aiModel", "str"),
    ("tiku", "siliconflow_key", "sfKey", "str"),
    ("tiku", "siliconflow_model", "sfModel", "str"),
    ("tiku", "submit", "tikuSubmit", "bool"),
    ("tiku", "cover_rate", "tikuCoverRate", "float"),
    ("notification", "provider", "notifyProvider", "str"),
    ("notification", "url", "notifyUrl", "str"),
    ("notification", "tg_chat_id", "notifyTgChatId", "str"),
]

_DEFAULTS = {
    "speed": 1.0, "jobs": 4, "notopenAction": "retry", "retryInterval": 1.0,
    "addLearningCount": False, "targetCount": 100, "tikuSubmit": False, "tikuCoverRate": 0.9,
}


def get_settings():
    p = _read_ini()
    out = {}
    for section, key, name, kind in _SETTING_FIELDS:
        raw = _get(p, section, key, None)
        if kind == "bool":
            out[name] = _bool(raw) if raw is not None else _DEFAULTS.get(name, False)
        elif kind == "float":
            out[name] = _num(raw, _DEFAULTS.get(name, 0.0), float)
        elif kind == "int":
            out[name] = _num(raw, _DEFAULTS.get(name, 0), int)
        else:
            out[name] = raw if raw is not None else _DEFAULTS.get(name, "")
    # the GUI can't answer console prompts: 'ask' behaves like 'retry' here
    if out["notopenAction"] not in ("retry", "continue"):
        out["notopenAction"] = "retry"
    if out["tikuProvider"] == "TikuManual":
        out["tikuProvider"] = ""
    return out


def save_settings(patch):
    values = {}
    for section, key, name, kind in _SETTING_FIELDS:
        if name not in (patch or {}):
            continue
        v = patch[name]
        if kind == "bool":
            v = "true" if v else "false"
        elif kind == "float":
            v = str(_num(v, _DEFAULTS.get(name, 0.0), float))
        elif kind == "int":
            v = str(_num(v, _DEFAULTS.get(name, 0), int))
        else:
            v = str(v if v is not None else "").strip()
        values[(section, key)] = v
    if values:
        _write_ini(values)
    return {"ok": True}


# ---------------------------------------------------------------------------
# worker thread + per-run stop context
# ---------------------------------------------------------------------------
#
# Stopping has to reach whatever a run is doing: a chapter can hold dozens of
# task points that each "complete instantly" (no progress bar is ever drawn),
# quizzes spend their time inside question-bank / AI requests, captcha
# handling and rate limiters sleep, live sessions loop for the whole lecture,
# and upstream wraps most of that in `except Exception`. So a stop is checked
# at every place a run can spend time - task point boundaries, every HTTP
# send (and urllib3's internal retries), every sleep inside api.base /
# api.live_process - and the exception used for it is a BaseException so
# upstream's `except Exception` blocks can't swallow it.

_worker = {"thread": None, "kind": None, "ctx": None}
_worker_lock = threading.Lock()
_tls = threading.local()

# after a force stop, give threads stuck inside one in-flight request this
# long before the run stops waiting for them (they stay stopped - see RunCtx)
FORCE_GRACE_S = 15.0


class StopRequested(BaseException):
    """Raised inside a run's threads to abort it.

    BaseException (like KeyboardInterrupt), not Exception: upstream code is
    full of `except Exception` (captcha, quiz answering, tiku providers, live
    sessions) that would otherwise catch it, log it and carry on."""


class RunCtx:
    """State shared by one run's threads, bound to each thread the run owns
    (GUI run worker, JobProcessor workers, live threads) through _tls.

    Hooks consult the calling thread's ctx, never a global flag, so a
    course-list refresh on a UI thread is never affected by a stop, and a
    straggler from a force-stopped run stays stopped instead of being released
    when the next run starts."""

    def __init__(self):
        self.stop = threading.Event()   # graceful: finish the current task point
        self.force = threading.Event()  # force: abort right now
        self.force_at = 0.0
        self.phase = "run"              # "count" while adding study counts
        self.notopen = "retry"
        self.settled = set()


def _ctx():
    return getattr(_tls, "ctx", None)


def _abort_if_forced():
    ctx = _ctx()
    if ctx is not None and ctx.force.is_set():
        raise StopRequested("用户强制停止")


def _busy():
    t = _worker["thread"]
    return t is not None and t.is_alive()


def _begin(kind, target, ctx=None):
    with _worker_lock:
        if _busy():
            return False
        t = threading.Thread(target=target, daemon=True, name="gui-" + kind)
        _worker.update(thread=t, kind=kind, ctx=ctx)
        t.start()
        return True


def request_stop(force=False):
    with _worker_lock:
        ctx = _worker["ctx"]
        if not _busy() or _worker["kind"] != "run" or ctx is None:
            return {"ok": False, "error": "not_running"}
        ctx.stop.set()
        if force and not ctx.force.is_set():
            ctx.force_at = time.time()
            ctx.force.set()
    return {"ok": True}


# ---------------------------------------------------------------------------
# hooks into the project
# ---------------------------------------------------------------------------

class GuiBar:
    """Drop-in for the tqdm bar api.base.study_video draws per video: same
    constructor/`n`/refresh()/close() surface, but reports to the UI."""

    _ids = 0
    _lock = threading.Lock()

    def __init__(self, total=None, initial=0, desc="", **_kw):
        with GuiBar._lock:
            GuiBar._ids += 1
            self.id = GuiBar._ids
        self.total = int(total or 0)
        self.n = int(initial or 0)
        self.desc = desc or ""
        self._closed = False
        self._last = 0.0
        self.refresh(force=True)

    def refresh(self, force=False):
        _abort_if_forced()
        now = time.time()
        if force or now - self._last >= 0.9:
            self._last = now
            _push("onVideo", {"id": self.id, "name": self.desc, "n": int(self.n), "total": self.total})

    def update(self, k=1):
        self.n += k
        self.refresh()

    def close(self):
        if not self._closed:
            self._closed = True
            _push("onVideoEnd", self.id)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


cx_base.tqdm = GuiBar


class _InterruptibleTime:
    """Stands in for the `time` module inside api.base and api.live_process
    (never globally, never main.time): sleep() returns at once on a force stop
    - and, while adding study counts, on a graceful stop too; everything else
    is the real time module. Covers the rate limiters, the video tick, 403 /
    captcha back-offs, quiz redo waits, the 30 s study-count wait and the
    59 s live-session wait."""

    def __getattr__(self, name):
        return getattr(time, name)

    def sleep(self, seconds):
        ctx = _ctx()
        if ctx is None:
            return time.sleep(seconds)
        end = time.monotonic() + max(0.0, float(seconds))
        while True:
            if ctx.force.is_set() or (ctx.phase == "count" and ctx.stop.is_set()):
                raise StopRequested("用户强制停止")
            left = end - time.monotonic()
            if left <= 0:
                return
            ctx.force.wait(min(left, 0.25))


import api.answer as cx_answer  # noqa: E402  (question-bank delays / provider back-offs)
import api.live as cx_live  # noqa: E402
import api.live_process as cx_live_process  # noqa: E402  (59 s live-session waits)
import api.process as cx_process  # noqa: E402  (study-count loop)

for _mod in (cx_base, cx_answer, cx_live, cx_live_process, cx_process):
    _mod.time = _InterruptibleTime()

# third-party waits a run can sit in: urllib3's Retry-After / backoff sleeps
# (SessionManager retries up to 10x), tenacity's retry waits (study_work) and
# the openai SDK's retry backoff (AI / SiliconFlow question banks)
import tenacity.nap  # noqa: E402
import urllib3.util.retry  # noqa: E402

for _mod in (urllib3.util.retry, tenacity.nap):
    _mod.time = _InterruptibleTime()
try:
    import openai._base_client as _openai_base  # noqa: E402

    _openai_base.time = _InterruptibleTime()
except Exception:
    pass

# study_work's @retry(retry=retry_if_exception(...)) is built at call time from
# api.base's globals; never let it retry a stop
_orig_retry_if_exception = cx_base.retry_if_exception
cx_base.retry_if_exception = lambda predicate: _orig_retry_if_exception(
    lambda e: not isinstance(e, StopRequested) and predicate(e))

_DEFAULT_TIMEOUT = (10, 30)  # for bare requests.* calls upstream makes with no timeout


def _hook_http():
    import requests
    from urllib3.util.retry import Retry

    orig_send = requests.Session.send

    def send(self, request, **kwargs):
        _abort_if_forced()
        # Session.request always passes timeout= (None when the caller gave none)
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = _DEFAULT_TIMEOUT
        return orig_send(self, request, **kwargs)

    requests.Session.send = send

    # SessionManager mounts HTTPAdapter(max_retries=10): one send can retry
    # internally ~10 times without coming back through Session.send
    orig_increment = Retry.increment

    def increment(self, *a, **k):
        _abort_if_forced()
        return orig_increment(self, *a, **k)

    Retry.increment = increment

    try:  # the openai SDK (AI / SiliconFlow question banks) talks through httpx
        import httpx
    except ImportError:
        return
    orig_sync = httpx.Client.send
    orig_async = httpx.AsyncClient.send

    def ssend(self, request, *a, **k):
        _abort_if_forced()
        return orig_sync(self, request, *a, **k)

    async def asend(self, request, *a, **k):
        _abort_if_forced()
        return await orig_async(self, request, *a, **k)

    httpx.Client.send = ssend
    httpx.AsyncClient.send = asend

    # openai >= 3 sends through the separate httpx2 package, not httpx
    try:
        import httpx2
    except ImportError:
        return
    orig_sync2 = httpx2.Client.send
    orig_async2 = httpx2.AsyncClient.send

    def ssend2(self, request, *a, **k):
        _abort_if_forced()
        return orig_sync2(self, request, *a, **k)

    async def asend2(self, request, *a, **k):
        _abort_if_forced()
        return await orig_async2(self, request, *a, **k)

    httpx2.Client.send = ssend2
    httpx2.AsyncClient.send = asend2


_hook_http()

# live sessions run in a thread main.process_job starts itself; hand it the
# ctx of the worker that started it (keyed by the job dict, which becomes
# live.attachment)
_live_ctx = {}
_orig_run_live = cx.LiveProcessor.run_live


class _GuiLiveProcessor(cx.LiveProcessor):
    @staticmethod
    def run_live(live, speed=1.0):
        _tls.ctx = _live_ctx.get(id(getattr(live, "attachment", None)))
        try:
            return _orig_run_live(live, speed)
        except StopRequested:
            return False


cx.LiveProcessor = _GuiLiveProcessor

_orig_process_job = cx.process_job


def _gui_process_job(chaoxing, course, job, job_info, speed):
    ctx = _ctx()
    if ctx is not None and ctx.stop.is_set():
        # both kinds of stop take effect between task points - a graceful stop
        # finishes the current video, not a whole chapter of them
        raise StopRequested("停止")
    if ctx is not None:
        _live_ctx[id(job)] = ctx
    try:
        result = _orig_process_job(chaoxing, course, job, job_info, speed)
    finally:
        _live_ctx.pop(id(job), None)
    _abort_if_forced()  # a job that swallowed the stop must not report a result
    return result


cx.process_job = _gui_process_job

_orig_process_chapter = cx.process_chapter


def _point_id(point):
    return str(point.get("id") or point.get("title", ""))


def _chapter_stopped(info):
    _push("onChapter", dict(info, result="STOPPED", settled=False))
    return cx.ChapterResult.SUCCESS  # lets JobProcessor count it off


def _gui_process_chapter(chaoxing, course, point, speed):
    ctx = _ctx()
    info = {"course": _course_key(course), "id": _point_id(point), "title": point.get("title", "")}
    if ctx is not None and ctx.stop.is_set():
        return cx.ChapterResult.SUCCESS  # not started yet: drain straight through
    try:
        result = _orig_process_chapter(chaoxing, course, point, speed)
    except StopRequested:
        return _chapter_stopped(info)
    except Exception as e:
        # Upstream lets anything else (JSON decode errors, exhausted retries,
        # KeyError from a changed page...) escape the worker thread, which then
        # dies without task_done and leaves JobProcessor.run() waiting forever.
        # Report it as a failed attempt instead; upstream retries it.
        if ctx is not None and ctx.stop.is_set():
            return _chapter_stopped(info)
        logger.opt(exception=e).error("章节「{}」处理出错: {}", info["title"], e)
        _push("onChapter", dict(info, result="ERROR", settled=False))
        return cx.ChapterResult.ERROR
    if ctx is not None and (ctx.force.is_set() or (ctx.stop.is_set() and result != cx.ChapterResult.SUCCESS)):
        return _chapter_stopped(info)
    settled = result == cx.ChapterResult.SUCCESS or (
        result == cx.ChapterResult.NOT_OPEN and ctx is not None and ctx.notopen == "continue")
    if settled and ctx is not None:
        ctx.settled.add((info["course"], info["id"]))
    _push("onChapter", dict(info, result=result.name, settled=settled))
    return result


cx.process_chapter = _gui_process_chapter


class _GuiJobProcessor(cx.JobProcessor):
    """Upstream's JobProcessor, with its threads bound to the run's ctx, a
    retry loop that drains at once on a stop, and a join() that can't hang."""

    def __init__(self, chaoxing, tasks, config, ctx):
        super().__init__(chaoxing, tasks, config)
        self.ctx = ctx

    def worker_thread(self):
        _tls.ctx = self.ctx
        return cx.JobProcessor.worker_thread(self)

    def retry_thread(self):
        _tls.ctx = self.ctx
        while True:
            try:
                task = self.retry_queue.get()
                self.task_queue.put(task)
            except cx.ShutDown:
                return
            # upstream's order: put first, then task_done (the reverse could let
            # unfinished_tasks touch 0 while a task is still pending)
            self.task_queue.task_done()
            if not self.ctx.stop.is_set():
                self.ctx.stop.wait(self.retry_interval)

    def run(self):
        for task in self.tasks:
            self.task_queue.put(task)
        for _ in range(self.worker_num):
            t = threading.Thread(target=self.worker_thread, daemon=True, name="cx-worker")
            self.threads.append(t)
            t.start()
        threading.Thread(target=self.retry_thread, daemon=True, name="cx-retry").start()

        q = self.task_queue
        while True:
            with q.all_tasks_done:
                if not q.unfinished_tasks:
                    break
                q.all_tasks_done.wait(0.5)
            if not any(t.is_alive() for t in self.threads):
                logger.error("任务线程已全部退出，结束本次运行")
                break
            if self.ctx.force.is_set() and time.time() - self.ctx.force_at > FORCE_GRACE_S:
                logger.warning("仍有任务卡在网络请求里，不再等待")
                break
        time.sleep(0.3)
        for queue_ in (self.task_queue, self.retry_queue):
            try:
                queue_.shutdown(immediate=True)
            except Exception:
                pass


def _log_sink(message):
    rec = message.record
    level = rec["level"].name
    if level in ("TRACE", "DEBUG"):
        return
    _push("onLog", {"level": level, "text": str(rec["message"])})


logger.add(_log_sink, level="INFO", enqueue=False)


# ---------------------------------------------------------------------------
# account
# ---------------------------------------------------------------------------

_session_info = {"loggedIn": False, "name": "", "username": ""}
# The password typed at login is kept in memory for the life of the process
# (never written unless 记住密码 is ticked), so a run can re-authenticate by
# itself when the cookie session expires mid-run - the CLI always can.
_creds = {"username": "", "password": ""}


def _course_key(course):
    return "%s_%s" % (course.get("courseId"), course.get("clazzId"))


def _account():
    p = _read_ini()
    username = _get(p, "common", "username")
    password = _get(p, "common", "password")
    if _creds["password"] and _creds["username"] == username:
        password = _creds["password"]
    return Account(username, password)


def _mask(username):
    return username[:3] + "****" + username[-4:] if len(username) >= 11 else username


def _try_cookie_login():
    if not os.path.isfile(COOKIES_PATH) or os.path.getsize(COOKIES_PATH) == 0:
        return False
    try:
        acc = _account()
        state = Chaoxing(account=acc).login(login_with_cookies=True)
    except Exception as e:
        logger.debug("cookie login failed: {}", e)
        return False
    if state.get("status"):
        _session_info.update(loggedIn=True, name=Chaoxing.get_name() or "", username=acc.username)
        return True
    return False


def _saved_course_ids(p):
    # course_list holds plain courseIds (the CLI's format); older GUI builds
    # wrote courseId_clazzId - accept both
    ids = []
    for k in _get(p, "common", "course_list").split(","):
        cid = k.strip().split("_")[0]
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def get_bootstrap():
    logged_in = _session_info["loggedIn"] or _try_cookie_login()
    p = _read_ini()
    remembered = bool(_get(p, "common", "password"))
    return {
        "version": APP_VERSION,
        "loggedIn": logged_in,
        "name": _session_info["name"],
        "username": _get(p, "common", "username"),
        "maskedUsername": _mask(_session_info["username"] or _get(p, "common", "username")),
        "rememberPassword": remembered,
        "settings": get_settings(),
        "lastCourseIds": _saved_course_ids(p),
        "busy": _busy(),
    }


def start_login(username, password, remember):
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return {"ok": False, "error": "请填写手机号和密码"}

    def worker():
        try:
            _push("onLoginStatus", "pending")
            state = Chaoxing(account=Account(username, password)).login(login_with_cookies=False)
            if not state.get("status"):
                _push("onLoginStatus", "failed:" + (state.get("msg") or "未知错误"))
                return
            _write_ini({
                ("common", "username"): username,
                ("common", "password"): password if remember else "",
            })
            _creds.update(username=username, password=password)
            _session_info.update(loggedIn=True, name=Chaoxing.get_name() or "", username=username)
            _push("onLoginStatus", "success")
        except Exception as e:
            logger.exception(e)
            _push("onLoginStatus", "failed:%s" % e)
        finally:
            _push("onLoginFinished", {"name": _session_info["name"], "maskedUsername": _mask(_session_info["username"])})

    return {"ok": True} if _begin("login", worker) else {"ok": False, "error": "busy"}


def logout():
    if _busy():
        return {"ok": False, "error": "busy"}
    try:
        if os.path.isfile(COOKIES_PATH):
            os.remove(COOKIES_PATH)
    except OSError:
        pass
    try:
        SessionManager.get_instance()._session.cookies.clear()
    except Exception:
        pass
    _write_ini({("common", "password"): ""})
    _creds.update(username="", password="")
    _session_info.update(loggedIn=False, name="", username="")
    _courses.clear()
    return {"ok": True}


# ---------------------------------------------------------------------------
# courses
# ---------------------------------------------------------------------------

_courses = {}


def get_courses():
    if not _session_info["loggedIn"]:
        return {"ok": False, "error": "not_logged_in", "courses": []}
    try:
        courses = Chaoxing(account=_account()).get_course_list()
    except Exception as e:
        logger.exception(e)
        return {"ok": False, "error": str(e), "courses": []}
    _courses.clear()
    out = []
    for c in courses:
        key = _course_key(c)
        if key in _courses:
            continue
        _courses[key] = c
        out.append({"key": key, "courseId": str(c.get("courseId", "")), "title": c.get("title", ""),
                    "teacher": c.get("teacher", "")})
    return {"ok": True, "courses": out}


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

class _RawConfigparser:
    """Stands in for the configparser module inside main.py only:
    load_config_from_file's ConfigParser, minus '%' interpolation. The GUI
    stores raw values (passwords, tokens, URLs) where a '%' is legitimate and
    would otherwise make every run fail with InterpolationSyntaxError."""

    def __getattr__(self, name):
        return getattr(configparser, name)

    @staticmethod
    def ConfigParser(*a, **k):
        k["interpolation"] = None
        return configparser.ConfigParser(*a, **k)


cx.configparser = _RawConfigparser()


def _notify(notification, message):
    if notification is None:
        return
    try:
        notification.send(message)
    except Exception:
        pass


def start_run(course_keys):
    keys = [k for k in (course_keys or []) if k in _courses]
    if not keys:
        return {"ok": False, "error": "no_courses"}
    course_ids = []
    for k in keys:
        cid = str(_courses[k].get("courseId", ""))
        if cid and cid not in course_ids:
            course_ids.append(cid)
    _write_ini({("common", "course_list"): ",".join(course_ids)})  # the CLI's own format
    ctx = RunCtx()
    summary = {"ok": 0, "failed": 0, "total": 0, "stopped": False, "error": ""}

    def body():
        _tls.ctx = ctx
        tasks = []
        notification = None
        try:
            common, tiku_conf, notify_conf = cx.load_config_from_file(CONFIG_PATH)
            common["speed"] = min(2.0, max(1.0, float(common.get("speed", 1.0))))
            common["jobs"] = max(1, int(common.get("jobs", 4) or 4))
            if common.get("notopen_action") not in ("retry", "continue"):
                common["notopen_action"] = "retry"  # 'ask' needs a console
            ctx.notopen = common["notopen_action"]

            # TikuManual answers through console input(): unusable here, and the
            # settings page already shows it as "not used"
            providers = [s.strip() for s in str(tiku_conf.get("provider", "")).split(",")
                         if s.strip() and s.strip() != "TikuManual"]
            if tiku_conf:
                tiku_conf["provider"] = ",".join(providers)
            tiku = Tiku.get_tiku_from_config(tiku_conf, config_path=CONFIG_PATH)
            tiku.init_tiku()
            if any(p in ("AI", "SiliconFlow") for p in providers) and \
                    str(tiku_conf.get("check_llm_connection", "true")).lower() == "true":
                logger.info("正在验证大模型配置...")
                if not tiku.check_llm_connection():
                    logger.warning("大模型连接检查失败，章节检测可能无法准确作答，继续运行")

            chaoxing = Chaoxing(
                account=_account(),
                tiku=tiku,
                query_delay=tiku_conf.get("delay", 0),
                work_max_retries=common.get("work_max_retries", 3),
                work_redo_enabled=common.get("work_redo_enabled", False),
            )
            notification = Notification()
            notification.config_set(notify_conf)
            notification = notification.get_notification_from_config()
            notification.init_notification()

            # like main.py: (re)check the login at the start of every run -
            # cookies are validated and, if they expired, the password is used
            state = chaoxing.login(login_with_cookies=True)
            if not state.get("status"):
                raise RuntimeError("登录已失效，请退出登录后重新登录（%s）" % state.get("msg", ""))

            courses = [_courses[k] for k in keys]
            plan = []
            for course in courses:
                if ctx.stop.is_set():
                    break
                logger.info("正在读取课程章节: {}", course["title"])
                points = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
                plan.append({"course": _course_key(course), "total": len(points)})
                for i, point in enumerate(points):
                    tasks.append(cx.ChapterTask(point=point, index=i, course=course))
            _push("onPlan", plan)

            if tasks and not ctx.stop.is_set():
                processor = _GuiJobProcessor(chaoxing, tasks, common, ctx)
                processor.run()
                summary["failed"] = len(processor.failed_tasks)

            if not ctx.stop.is_set():
                logger.info("所有课程学习任务已完成")
                _notify(notification, "chaoxing : 所有课程学习任务已完成")

            if not ctx.stop.is_set() and cx.str_to_bool(common.get("add_learning_count", False)):
                ctx.phase = "count"  # a graceful stop now interrupts the 30 s waits too
                logger.info("刷课完成，开始增加章节学习次数...")
                for course in courses:
                    if ctx.stop.is_set():
                        break
                    increase_learning_count_for_course(chaoxing, course, common)
                if not ctx.stop.is_set():
                    logger.info("所有课程章节学习次数增加完成")
                    _notify(notification, "chaoxing : 所有课程章节学习次数增加完成")
        except StopRequested:
            pass
        except Exception as e:
            logger.exception(e)
            summary["error"] = "%s: %s" % (type(e).__name__, e) if not isinstance(e, RuntimeError) else str(e)
            _notify(notification, "chaoxing : 出现错误 %s: %s" % (type(e).__name__, e))
        finally:
            summary["ok"] = len(ctx.settled)
            summary["total"] = len(tasks)
            if not ctx.stop.is_set() and not summary["error"]:
                # chapters upstream gave up on silently (e.g. still not open
                # after all retries) count as not done too
                summary["failed"] = max(summary["failed"], len(tasks) - summary["ok"])

    def worker():
        # The run's own network work (login check, reading chapters, the
        # study-count loop) happens on a child thread so a force stop can stop
        # waiting for it, the same way _GuiJobProcessor.run does.
        _tls.ctx = ctx
        t = threading.Thread(target=body, daemon=True, name="cx-run")
        t.start()
        while t.is_alive():
            t.join(0.5)
            if ctx.force.is_set() and time.time() - ctx.force_at > FORCE_GRACE_S:
                logger.warning("仍有任务卡在网络请求里，不再等待")
                break
        summary["stopped"] = ctx.stop.is_set()
        _push("onRunFinished", dict(summary))

    return {"ok": True} if _begin("run", worker, ctx) else {"ok": False, "error": "busy"}
