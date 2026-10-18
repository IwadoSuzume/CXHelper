(function () {
  "use strict";

  var busy = false;
  var loggedIn = false;
  var lastCourseIds = [];  // courseIds, the CLI's course_list format
  var courseIdByKey = {};

  function $(id) { return document.getElementById(id); }
  function api() { return window.pywebview.api; }

  // ---------------- tabs / title bar ----------------

  function wireTabs() {
    var tabs = document.querySelectorAll(".tab");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        document.querySelectorAll(".tab-panel").forEach(function (p) { p.classList.remove("active"); });
        $("panel-" + tab.dataset.tab).classList.add("active");
      });
    });
  }

  function wireTitlebar() {
    $("btn-min").addEventListener("click", function () { api().minimize_window(); });
    $("btn-close").addEventListener("click", function () { api().close_window(); });
  }

  // ---------------- login ----------------

  function setLoginStatus(text, cls) {
    var el = $("login-status");
    el.textContent = text;
    el.title = text;
    el.className = "status-chip" + (cls ? " " + cls : "");
  }

  function showAccount(name, masked) {
    loggedIn = true;
    $("login-form").hidden = true;
    $("account-info").hidden = false;
    $("btn-logout").hidden = false;
    var display = name || masked || "已登录";
    $("account-avatar").textContent = display.slice(0, 1);
    $("account-name").textContent = display;
    $("account-sub").textContent = name ? masked : "已登录，下次启动自动登录";
    setLoginStatus("已登录", "ok");
  }

  function showLoginForm() {
    loggedIn = false;
    $("login-form").hidden = false;
    $("account-info").hidden = true;
    $("btn-logout").hidden = true;
  }

  function onLoginStatus(status) {
    if (status === "pending") setLoginStatus("正在登录…", "pending");
    else if (status === "success") setLoginStatus("已登录", "ok");
    else if (status && status.indexOf("failed:") === 0) setLoginStatus("登录失败：" + status.slice(7), "bad");
  }

  function onLoginFinished(info) {
    setBusy(false);
    $("btn-login").disabled = false;
    if ($("login-status").classList.contains("ok")) {
      $("in-password").value = "";
      showAccount(info.name, info.maskedUsername);
      loadCourses();
    }
  }

  function wireLogin() {
    $("login-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var u = $("in-username").value.trim();
      var p = $("in-password").value;
      if (!u || !p) {
        setLoginStatus("请填写手机号和密码", "bad");
        (u ? $("in-password") : $("in-username")).focus();
        return;
      }
      setBusy(true);
      $("btn-login").disabled = true;
      setLoginStatus("正在登录…", "pending");
      api().login(u, p, $("in-remember").checked).then(function (r) {
        if (!r.ok) {
          setBusy(false);
          $("btn-login").disabled = false;
          setLoginStatus(r.error === "busy" ? "当前有其他操作在进行" : r.error, "bad");
        }
      });
    });
    $("btn-logout").addEventListener("click", function () {
      api().logout().then(function (r) {
        if (!r.ok) return;
        showLoginForm();
        setLoginStatus("未登录", "");
        renderCourses([]);
        $("courses-hint").textContent = "登录后自动加载课程列表。";
      });
    });
  }

  // ---------------- courses ----------------

  var courseTitle = {};

  function courseRow(c) {
    var row = document.createElement("label");
    row.className = "course-row";
    row.dataset.key = c.key;
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "course-checkbox";
    cb.value = c.key;
    cb.checked = lastCourseIds.indexOf(String(c.courseId)) !== -1;
    var main = document.createElement("span");
    main.className = "course-main";
    var name = document.createElement("span");
    name.className = "course-name";
    name.textContent = c.title;
    name.title = c.title;
    main.appendChild(name);
    if (c.teacher) {
      var t = document.createElement("span");
      t.className = "course-teacher";
      t.textContent = c.teacher;
      main.appendChild(t);
    }
    var prog = document.createElement("span");
    prog.className = "course-progress";
    row.appendChild(cb);
    row.appendChild(main);
    row.appendChild(prog);
    return row;
  }

  function renderCourses(list) {
    var box = $("course-list");
    box.innerHTML = "";
    list.forEach(function (c) {
      courseTitle[c.key] = c.title;
      courseIdByKey[c.key] = String(c.courseId);
      box.appendChild(courseRow(c));
    });
    if (!list.length) box.innerHTML = '<div class="empty-hint">暂无数据</div>';
    updateSelectedStat();
  }

  function loadCourses() {
    $("courses-hint").textContent = "正在加载课程列表…";
    return api().get_courses().then(function (r) {
      if (!r.ok) {
        $("courses-hint").textContent = r.error === "not_logged_in" ? "登录后自动加载课程列表。" : "加载失败，请稍后点「刷新」重试。";
        return;
      }
      $("courses-hint").textContent = "共 " + r.courses.length + " 门课程，勾选后点「开始运行」。";
      renderCourses(r.courses);
    }).catch(function () {
      $("courses-hint").textContent = "加载失败，请稍后点「刷新」重试。";
    });
  }

  function updateSelectedStat() {
    $("stat-selected").textContent = document.querySelectorAll(".course-checkbox:checked").length;
  }

  function selectedCourseKeys() {
    return Array.prototype.map.call(document.querySelectorAll(".course-checkbox:checked"), function (cb) { return cb.value; });
  }

  function wireCourses() {
    $("btn-refresh-courses").addEventListener("click", function () {
      if (!loggedIn) { setLoginStatus("请先登录", "bad"); return; }
      loadCourses();
    });
    $("btn-select-all").addEventListener("click", function () {
      document.querySelectorAll(".course-checkbox").forEach(function (cb) { cb.checked = true; });
      updateSelectedStat();
    });
    $("btn-select-none").addEventListener("click", function () {
      document.querySelectorAll(".course-checkbox").forEach(function (cb) { cb.checked = false; });
      updateSelectedStat();
    });
    $("course-list").addEventListener("change", updateSelectedStat);
  }

  // ---------------- run progress ----------------

  // per-course chapter totals from onPlan, and the set of chapters already
  // settled (finished / skipped) - a chapter can report more than once when
  // the project retries it, so settled ones are de-duplicated by id
  var plan = {};
  var settled = {};
  var totalChapters = 0;
  var settledCount = 0;

  function fmtTime(sec) {
    sec = Math.max(0, Math.round(sec));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    var mm = (m < 10 ? "0" : "") + m, ss = (s < 10 ? "0" : "") + s;
    return h ? h + ":" + mm + ":" + ss : mm + ":" + ss;
  }

  function setOverall() {
    $("stat-chapters").textContent = totalChapters ? settledCount + "/" + totalChapters : "--";
    var pct = totalChapters ? settledCount / totalChapters * 100 : 0;
    $("progress-fill").style.width = pct + "%";
    $("progress-percent").textContent = totalChapters ? pct.toFixed(1) + "%" : "--";
  }

  function setCourseProgress(key) {
    var row = document.querySelector('.course-row[data-key="' + key + '"] .course-progress');
    if (!row || !plan[key]) return;
    var done = Object.keys(settled[key] || {}).length;
    row.textContent = done + "/" + plan[key];
    row.classList.toggle("done", done >= plan[key]);
  }

  function onPlan(list) {
    plan = {}; settled = {}; totalChapters = 0; settledCount = 0;
    list.forEach(function (p) {
      plan[p.course] = p.total;
      settled[p.course] = {};
      totalChapters += p.total;
      setCourseProgress(p.course);
    });
    setOverall();
    $("progress-caption").textContent = "共 " + totalChapters + " 个章节";
  }

  function onChapter(ev) {
    if (!ev.settled || !settled[ev.course]) return;
    if (!settled[ev.course][ev.id]) {
      settled[ev.course][ev.id] = true;
      settledCount++;
    }
    setCourseProgress(ev.course);
    setOverall();
    $("progress-caption").textContent = "已完成 " + settledCount + " / " + totalChapters + " 个章节";
  }

  function onVideo(v) {
    var list = $("active-list");
    var row = list.querySelector('[data-id="' + v.id + '"]');
    if (!row) {
      row = document.createElement("div");
      row.className = "active-row";
      row.dataset.id = v.id;
      row.innerHTML = '<span class="active-name"></span><span class="active-track"><span class="active-fill"></span></span><span class="active-time"></span>';
      row.querySelector(".active-name").textContent = v.name;
      row.querySelector(".active-name").title = v.name;
      list.appendChild(row);
    }
    var pct = v.total ? Math.min(100, v.n / v.total * 100) : 0;
    row.querySelector(".active-fill").style.width = pct + "%";
    row.querySelector(".active-time").textContent = fmtTime(v.n) + " / " + fmtTime(v.total);
  }

  function onVideoEnd(id) {
    var row = $("active-list").querySelector('[data-id="' + id + '"]');
    if (row) row.remove();
  }

  // the project's own log lines, trimmed to one live status line plus a
  // banner for problems
  // routine lines (and the project's ERROR-level "no question bank / no
  // notification configured", which are normal choices here) stay out of the UI
  var QUIET = /^(课程列表读取完毕|课程章节读取成功|章节任务点读取成功|登录成功|Logging in|Queue shut down|未找到题库配置|题库provider配置为空|未找到外部通知配置)/;

  function showAlert(text, gold) {
    var el = $("run-alert");
    el.textContent = text;
    el.title = text;
    el.className = "run-alert" + (gold ? " gold" : "");
    el.hidden = false;
  }

  function onLog(entry) {
    var text = (entry.text || "").split("\n")[0];
    if (!text || QUIET.test(text)) return;
    if (entry.level === "ERROR" || entry.level === "CRITICAL") {
      showAlert(text, false);
    } else if (/验证码/.test(text)) {
      showAlert(text, true);
    } else if (runState !== RUN_IDLE) {
      $("current-status").textContent = text;
    }
  }

  function onRunFinished(summary) {
    clearTimeout(forceHintTimer);
    stopElapsed();
    setBusy(false);
    setRunButton(RUN_IDLE);
    $("live-dot").classList.remove("on");
    $("active-list").innerHTML = "";
    $("current-status").textContent = "";
    if (summary.error) {
      $("progress-caption").textContent = "运行出错，已停止";
      showAlert(summary.error, false);
    } else if (summary.stopped) {
      $("progress-caption").textContent = "已停止（已完成 " + settledCount + " / " + (totalChapters || summary.total || 0) + " 个章节）";
    } else {
      if (totalChapters && !summary.failed) {
        $("progress-fill").style.width = "100%";
        $("progress-percent").textContent = "100%";
      }
      $("progress-caption").textContent = summary.failed
        ? "运行结束，" + summary.failed + " 个章节未完成（失败或未开放），详情见日志"
        : "全部完成";
    }
  }

  // ---------------- elapsed timer ----------------

  var runStartedAt = 0, elapsedTimer = null;

  function tickElapsed() { $("stat-elapsed").textContent = fmtTime((Date.now() - runStartedAt) / 1000); }

  function startElapsed() {
    runStartedAt = Date.now();
    tickElapsed();
    clearInterval(elapsedTimer);
    elapsedTimer = setInterval(tickElapsed, 1000);
  }

  function stopElapsed() {
    if (runStartedAt) tickElapsed();
    clearInterval(elapsedTimer);
    elapsedTimer = null;
  }

  // ---------------- run button ----------------

  // idle -> running (click = stop after the chapters already in progress)
  // -> stopping (click again = abort the running videos right away)
  var RUN_IDLE = "idle", RUN_RUNNING = "running", RUN_STOPPING = "stopping";
  var runState = RUN_IDLE;
  var forceHintTimer = null;

  function setRunButton(state) {
    runState = state;
    var btn = $("btn-run-toggle");
    btn.disabled = false;
    if (state === RUN_RUNNING) {
      btn.textContent = "停止";
      btn.className = "btn btn-run btn-secondary";
    } else if (state === RUN_STOPPING) {
      btn.textContent = "强制停止";
      btn.className = "btn btn-run btn-warn";
    } else {
      btn.textContent = "开始运行";
      btn.className = "btn btn-run btn-primary";
    }
  }

  function resetProgress() {
    plan = {}; settled = {}; totalChapters = 0; settledCount = 0;
    document.querySelectorAll(".course-progress").forEach(function (el) { el.textContent = ""; el.classList.remove("done"); });
    $("progress-fill").style.width = "0%";
    $("progress-percent").textContent = "--";
    $("stat-chapters").textContent = "--";
    $("active-list").innerHTML = "";
    $("current-status").textContent = "";
    $("run-alert").hidden = true;
  }

  function wireRun() {
    $("btn-run-toggle").addEventListener("click", function () {
      if (runState === RUN_IDLE) {
        if (!loggedIn) { setLoginStatus("请先登录", "bad"); $("in-username").focus(); return; }
        var keys = selectedCourseKeys();
        if (!keys.length) { $("progress-caption").textContent = "请先在左侧勾选至少一门课程。"; return; }
        lastCourseIds = keys.map(function (k) { return courseIdByKey[k]; });
        resetProgress();
        startElapsed();
        setBusy(true);
        setRunButton(RUN_RUNNING);
        $("live-dot").classList.add("on");
        $("progress-caption").textContent = "正在读取课程章节…";
        api().start_run(keys).then(function (r) {
          if (!r.ok) {
            stopElapsed();
            setBusy(false);
            setRunButton(RUN_IDLE);
            $("live-dot").classList.remove("on");
            $("progress-caption").textContent = r.error === "busy" ? "当前有其他操作在进行，请稍后再试。" : "请先刷新课程列表后再试。";
          }
        });
      } else if (runState === RUN_RUNNING) {
        $("progress-caption").textContent = "当前任务完成后停止…（再点一次立即停止）";
        api().stop_run(false);
        setRunButton(RUN_STOPPING);
      } else if (runState === RUN_STOPPING) {
        $("progress-caption").textContent = "正在中断…";
        api().stop_run(true);
        $("btn-run-toggle").disabled = true;
        // the backend gives up on requests still in flight after ~15 s; say so
        // rather than leave a dead-looking button if the network is slow
        clearTimeout(forceHintTimer);
        forceHintTimer = setTimeout(function () {
          if (runState === RUN_STOPPING) $("progress-caption").textContent = "正在等待网络请求返回，马上就好…";
        }, 6000);
      }
    });
  }

  // ---------------- settings ----------------

  function setSwitch(id, on) { $(id).setAttribute("aria-checked", on ? "true" : "false"); }
  function getSwitch(id) { return $(id).getAttribute("aria-checked") === "true"; }
  function numOr(id, dflt) { var v = $(id).value; return v === "" ? dflt : Number(v); }

  function syncVisibility() {
    var tiku = $("opt-tiku-provider").value;
    document.querySelectorAll("[data-tiku]").forEach(function (el) {
      el.hidden = el.dataset.tiku.split(" ").indexOf(tiku) === -1;
    });
    document.querySelectorAll("[data-tiku-any]").forEach(function (el) { el.hidden = !tiku; });
    $("opt-tiku-cover").closest(".field-label").hidden = !tiku;
    var notify = $("opt-notify-provider").value;
    document.querySelectorAll("[data-notify]").forEach(function (el) { el.hidden = el.dataset.notify !== notify; });
    document.querySelectorAll("[data-notify-any]").forEach(function (el) { el.hidden = !notify; });
    $("learn-fields").hidden = !getSwitch("opt-learn");
  }

  function fillSettings(s) {
    $("opt-speed").value = s.speed;
    $("opt-jobs").value = s.jobs;
    $("opt-notopen").value = s.notopenAction;
    $("opt-retry-interval").value = s.retryInterval;
    $("opt-tiku-provider").value = s.tikuProvider || "";
    if ($("opt-tiku-provider").value !== (s.tikuProvider || "")) $("opt-tiku-provider").value = "";
    $("opt-tiku-cover").value = s.tikuCoverRate;
    $("opt-tiku-tokens").value = s.tikuTokens || "";
    $("opt-tiku-goauth").value = s.tikuGoAuth || "";
    $("opt-tiku-url").value = s.tikuUrl || "";
    $("opt-ai-endpoint").value = s.aiEndpoint || "";
    $("opt-ai-key").value = s.aiKey || "";
    $("opt-ai-model").value = s.aiModel || "";
    $("opt-sf-key").value = s.sfKey || "";
    $("opt-sf-model").value = s.sfModel || "";
    setSwitch("opt-tiku-submit", s.tikuSubmit);
    setSwitch("opt-learn", s.addLearningCount);
    $("opt-learn-target").value = s.targetCount;
    $("opt-notify-provider").value = s.notifyProvider || "";
    $("opt-notify-url").value = s.notifyUrl || "";
    $("opt-notify-tg").value = s.notifyTgChatId || "";
    syncVisibility();
  }

  function collectSettings() {
    return {
      speed: Math.min(2, Math.max(1, numOr("opt-speed", 1))),
      jobs: Math.min(8, Math.max(1, Math.round(numOr("opt-jobs", 4)))),
      notopenAction: $("opt-notopen").value,
      retryInterval: Math.max(0, numOr("opt-retry-interval", 1)),
      tikuProvider: $("opt-tiku-provider").value,
      tikuCoverRate: Math.min(1, Math.max(0, numOr("opt-tiku-cover", 0.9))),
      tikuTokens: $("opt-tiku-tokens").value,
      tikuGoAuth: $("opt-tiku-goauth").value,
      tikuUrl: $("opt-tiku-url").value,
      aiEndpoint: $("opt-ai-endpoint").value,
      aiKey: $("opt-ai-key").value,
      aiModel: $("opt-ai-model").value,
      sfKey: $("opt-sf-key").value,
      sfModel: $("opt-sf-model").value,
      tikuSubmit: getSwitch("opt-tiku-submit"),
      addLearningCount: getSwitch("opt-learn"),
      targetCount: Math.max(1, Math.round(numOr("opt-learn-target", 100))),
      notifyProvider: $("opt-notify-provider").value,
      notifyUrl: $("opt-notify-url").value,
      notifyTgChatId: $("opt-notify-tg").value
    };
  }

  function wireSettings() {
    document.querySelectorAll(".switch").forEach(function (sw) {
      sw.addEventListener("click", function () { setSwitch(sw.id, !getSwitch(sw.id)); syncVisibility(); });
    });
    $("opt-tiku-provider").addEventListener("change", syncVisibility);
    $("opt-notify-provider").addEventListener("change", syncVisibility);
    $("btn-save-settings").addEventListener("click", function () {
      var s = collectSettings();
      api().save_settings(s).then(function () {
        fillSettings(Object.assign({}, s));
        var hint = $("save-hint");
        hint.textContent = busy ? "已保存，下次运行时生效" : "已保存";
        setTimeout(function () { hint.textContent = ""; }, 2500);
      });
    });
    $("btn-open-folder").addEventListener("click", function () { api().open_folder(); });
    $("btn-visit-site").addEventListener("click", function () { api().open_url("https://suzume.cloud"); });
  }

  // ---------------- busy state ----------------

  function setBusy(v) {
    busy = v;
    $("btn-refresh-courses").disabled = busy;
    $("btn-logout").disabled = busy;
  }

  // ---------------- bridge ----------------

  var HANDLERS = {
    onLoginStatus: onLoginStatus,
    onLoginFinished: onLoginFinished,
    onPlan: onPlan,
    onChapter: onChapter,
    onVideo: onVideo,
    onVideoEnd: onVideoEnd,
    onLog: onLog,
    onRunFinished: onRunFinished
  };

  window.__bridge = function (event, args) {
    var fn = HANDLERS[event];
    if (fn) fn.apply(null, args);
  };

  // ---------------- scaling / splash / init ----------------

  var DESIGN_W = 1080, DESIGN_H = 620;

  function applyScale() {
    var shell = document.querySelector(".app-shell");
    var scale = Math.min(window.innerWidth / DESIGN_W, window.innerHeight / DESIGN_H);
    var offsetX = (window.innerWidth - DESIGN_W * scale) / 2;
    var offsetY = (window.innerHeight - DESIGN_H * scale) / 2;
    shell.style.transform = "translate(" + offsetX + "px, " + offsetY + "px) scale(" + scale + ")";
  }

  function hideSplash() {
    var el = $("splash");
    if (!el) return;
    el.classList.add("hide");
    setTimeout(function () { el.remove(); }, 300);
  }

  function init() {
    applyScale();
    window.addEventListener("resize", applyScale);
    wireTabs();
    wireTitlebar();
    wireLogin();
    wireCourses();
    wireRun();
    wireSettings();

    api().get_bootstrap().then(function (boot) {
      $("version-text").textContent = boot.version ? "v" + boot.version : "";
      fillSettings(boot.settings);
      lastCourseIds = (boot.lastCourseIds || []).map(String);
      $("in-username").value = boot.username || "";
      $("in-remember").checked = !!boot.rememberPassword;
      if (boot.loggedIn) {
        showAccount(boot.name, boot.maskedUsername);
        loadCourses().then(hideSplash);
      } else {
        showLoginForm();
        setTimeout(hideSplash, 1200);
      }
    }).catch(hideSplash);
  }

  window.addEventListener("pywebviewready", init);
})();
