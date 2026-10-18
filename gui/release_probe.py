"""Deterministic outputs of the upstream logic CXHelper relies on.

build_release.py runs probe() against the ORIGINAL sources and stores the
result in the bundle; `CXHelper.exe --selftest` runs it again inside the
packaged (minified) build and compares. Any difference means minification
changed what the code does, and the build fails.

Only pure functions are used: no network, no account, no files outside a
temporary folder.
"""
import hashlib
import json
import os
import tempfile

COURSE_LIST_HTML = """
<div class="course" id="c1" info="i1" roleid="3">
  <input class="clazzId" value="9001"><input class="courseId" value="2001">
  <a href="https://mooc1.chaoxing.com/visit?courseid=2001&clazzid=9001&cpi=55&ut=s">x</a>
  <span class="course-name" title="高等数学"></span>
  <p class="margint10" title="第一学期"></p><p class="color3" title="张老师"></p>
</div>
<div class="course" id="c2" info="i2" roleid="3"><a class="not-open-tip">未开放</a></div>
<div class="course" id="c3" info="i3" roleid="3">
  <input class="clazzId" value="9002"><input class="courseId" value="2002">
  <a href="https://mooc1.chaoxing.com/visit?courseid=2002&clazzid=9002&cpi=56&ut=s">y</a>
  <span class="course-name" title="大学英语"></span><p class="color3" title="李老师"></p>
</div>
"""

COURSE_CARD_HTML = (
    'var x = 1; mArg = {"defaults": {"ktoken": "kt", "mtEnc": "me", "reportTimeInterval": 60,'
    ' "defenc": "de", "cardid": 7, "cpi": 55, "qnenc": "qn", "knowledgeid": 123},'
    ' "attachments": ['
    '{"job": true, "type": "video", "jobid": "j1", "mid": "m1", "objectId": "o1", "aid": 1,'
    ' "otherInfo": "nodeId_1-cpi_55&courseId=2001", "playTime": 10, "attDuration": "600",'
    ' "property": {"name": "第1讲.mp4", "rt": "0.9"}},'
    '{"job": true, "type": "video", "jobid": "j2", "property": {"name": "转码失败.mp4"}},'
    '{"job": true, "type": "document", "jobid": "j3", "jtoken": "jt", "mid": "m3",'
    ' "property": {"objectid": "o3"}},'
    '{"job": true, "type": "workid", "jobid": "work-9", "enc": "e9", "mid": "m9"},'
    '{"job": null, "type": "read", "jobid": "r1", "property": {"title": "阅读", "id": "rid"}},'
    '{"job": true, "type": "live", "jobid": "l1", "property": {"title": "直播课", "liveId": 77}},'
    '{"job": true, "type": "video", "jobid": "p1", "isPassed": true, "mid": "mp"}'
    ']};'
)

RECORD_LIST_HTML = (
    '<span class="viewNum">第1次</span><span class="viewScore">80.5分</span>'
    '<span class="viewNum">第2次</span><span class="viewScore">100分</span>'
)


def probe():
    import main as cli
    from api import answer_check
    from api.base import (Chaoxing, _parse_work_record_list, best_option_by_similarity, clean_res,
                          normalize_answer_text, normalize_text)
    from api.cipher import AESCipher
    from api.cxsecret_font import fonthash_dao
    from api.decode import decode_course_card, decode_course_list

    import resources

    out = {}
    aes = AESCipher()
    out["aes"] = [aes.encrypt(s) for s in ("13800138000", "pa$$w0rd-中文-%20")]
    out["enc"] = Chaoxing.get_enc(None, "9001", "j1", "o1", 30, 600, "u42")
    out["normalize_answer"] = [normalize_answer_text(s) for s in
                               ("true", "错误", "T", "0", "A", " B ", "对", "ABD", "A、北京")]
    out["normalize_text"] = normalize_text("A. 北⻛（东）-门, 车!")
    out["clean_res"] = clean_res(["A. 北京", "B、上海", "C", "西安。"])
    out["best_option"] = [best_option_by_similarity("北京市", ["A. 上海", "B. 北京市", "C. 广州"]),
                          best_option_by_similarity("完全无关", ["A. 上海", "B. 北京"])]
    out["cut"] = [answer_check.cut(s) for s in ("A#B#C", "北京|上海", "单个答案", "A\nB,C")]
    out["record_list"] = _parse_work_record_list(RECORD_LIST_HTML)
    out["course_list"] = decode_course_list(COURSE_LIST_HTML)
    out["course_card"] = decode_course_card(COURSE_CARD_HTML)
    out["course_card_closed"] = decode_course_card("<div>章节未开放</div>")
    out["str_to_bool"] = [cli.str_to_bool(v) for v in ("true", "False", "1", "0", "yes", True)]
    out["format_time"] = [cli.format_time(n) for n in (5, 125.4, 3725)]
    courses = out["course_list"] + [{"courseId": "2001", "clazzId": "9003", "title": "高数(2)"}]
    out["filter_courses"] = [c["clazzId"] for c in cli.filter_courses(courses, ["2001"])]
    out["chapter_order"] = sorted(
        [cli.ChapterTask(index=i, point={"id": i}, course={}).index for i in (3, 1, 2)])

    # the CLI's own config parser, fed the shipped template
    with tempfile.TemporaryDirectory(prefix="cx-probe-") as tmp:
        path = os.path.join(tmp, "config.ini")
        with open(path, "wb") as f:
            f.write(resources.read("config_template.ini"))
        common, tiku, notify = cli.load_config_from_file(path)
    out["config"] = {"common": common, "tiku": tiku, "notification": notify}

    table = json.dumps(sorted(fonthash_dao.char_map.items()), ensure_ascii=False)
    out["font_table"] = [len(fonthash_dao.char_map), hashlib.sha256(table.encode("utf-8")).hexdigest()]
    # through JSON so tuples and lists compare the same on both sides
    return json.loads(json.dumps(out, ensure_ascii=False, default=repr))
