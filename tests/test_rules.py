"""Rule-level tests: every rule must fire on the real incident and stay quiet otherwise."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import corpus_row, findings_for, make_config, write_draft

from gatecheck.rules import g005_cross_post

GOOD_SOURCE = [
    {"url": "https://example.com/reg", "fetched_at": "2026-10-25T21:38:00+08:00"}
]


class RuleBase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.cfg = make_config(self.root)

    def tearDown(self):
        self._td.cleanup()

    def run_rules(self, name, text, platform="toutiao", meta=None, corpus=None, fenced=False):
        path = write_draft(self.root, name, text, meta)
        return findings_for(path, platform, self.cfg, corpus, fenced)

    def sev(self, name, text, **kwargs):
        return [f.severity for f in self.run_rules(name, text, **kwargs)]


class TestG001Anchor(RuleBase):
    def test_no_meta_no_anchor_warns(self):
        sev = self.sev("a.txt", "一段没有来源的结论，只是泛泛而谈。\n")
        self.assertIn("warn", sev)

    def test_no_meta_but_url_is_info(self):
        found = self.run_rules("b.txt", "数据来自 https://example.com/reg 这份文件。\n")
        g1 = [f for f in found if f.rule == "G001"]
        self.assertEqual(len(g1), 1)
        self.assertEqual(g1[0].severity, "info")

    def test_meta_without_anchor_fails(self):
        sev = self.sev("c.txt", "正文。\n", meta={"title": "标题", "sources": []})
        self.assertIn("fail", sev)

    def test_meta_missing_fetched_at_warns(self):
        sev = self.sev(
            "d.txt", "正文。\n", meta={"title": "标题", "sources": [{"url": "https://e.com"}]}
        )
        self.assertIn("warn", sev)

    def test_valid_meta_is_silent(self):
        found = self.run_rules("e.txt", "正文。\n", meta={"title": "标题", "sources": GOOD_SOURCE})
        self.assertEqual([f for f in found if f.rule == "G001"], [])


class TestG002NumberProvenance(RuleBase):
    def test_collection_count_fails(self):
        sev = self.sev("a.txt", "都收在合集里，合集共九篇。\n", meta={"sources": GOOD_SOURCE})
        self.assertIn("fail", sev)

    def test_ordinal_is_allowed(self):
        sev = self.sev("b.txt", "这是本系列第 9 篇，讲批量转码。\n", meta={"sources": GOOD_SOURCE})
        self.assertNotIn("fail", sev)

    def test_verified_numbers_pass(self):
        sev = self.sev(
            "c.txt",
            "合集共九篇。\n",
            meta={"sources": GOOD_SOURCE, "verified_numbers": {"合计": 9}},
        )
        self.assertNotIn("fail", sev)

    def test_plain_numbers_are_ignored(self):
        sev = self.sev(
            "d.txt", "0.10W 乘 8760 小时等于 0.88 度，按 0.5 元算是 0.44 元。\n",
            meta={"sources": GOOD_SOURCE},
        )
        self.assertNotIn("fail", sev)


class TestG003TitlePayoff(RuleBase):
    def test_question_without_answer_warns(self):
        found = self.run_rules(
            "a.txt", "这件事要看具体情况。\n", meta={"title": "充电器为什么费电", "sources": GOOD_SOURCE}
        )
        self.assertIn("warn", [f.severity for f in found if f.rule == "G003"])

    def test_question_with_criterion_passes(self):
        found = self.run_rules(
            "b.txt",
            "为什么？因为空载功率只有 0.10W，一年 0.88 度。\n",
            meta={"title": "充电器为什么费电", "sources": GOOD_SOURCE},
        )
        self.assertEqual([f for f in found if f.rule == "G003"], [])

    def test_answers_in_150_flag_passes(self):
        found = self.run_rules(
            "c.txt",
            "这件事要看具体情况。\n",
            meta={"title": "充电器为什么费电", "sources": GOOD_SOURCE, "answers_in_150": True},
        )
        self.assertEqual([f for f in found if f.rule == "G003"], [])

    def test_conclusion_with_number_in_lead_passes(self):
        found = self.run_rules(
            "d.txt",
            "先给结论：这块空间是开发工具留下的 8.2GB，跟系统本体没关系。\n",
            meta={"title": "系统数据清不掉？", "sources": GOOD_SOURCE, "answers_in_150": False},
        )
        self.assertEqual([f for f in found if f.rule == "G003"], [])


class TestG004EmptyCta(RuleBase):
    def test_follow_me_fails(self):
        sev = self.sev("a.txt", "写完了。关注我，下次继续。\n", meta={"sources": GOOD_SOURCE})
        self.assertIn("fail", sev)

    def test_request_word_warns(self):
        found = self.run_rules("b.txt", "觉得有用的话，记得点赞。\n", meta={"sources": GOOD_SOURCE})
        self.assertIn("warn", [f.severity for f in found if f.rule == "G004"])

    def test_negated_cta_is_info(self):
        found = self.run_rules("c.txt", "这里不写关注我这种话。\n", meta={"sources": GOOD_SOURCE})
        g4 = [f for f in found if f.rule == "G004"]
        self.assertEqual([f.severity for f in g4], ["info"])

    def test_analytics_word_is_not_cta(self):
        found = self.run_rules("d.txt", "数据看点：3 小时内的收藏与评论。\n", meta={"sources": GOOD_SOURCE})
        self.assertEqual([f for f in found if f.rule == "G004"], [])


class TestG005CrossPost(RuleBase):
    BODY = (
        "把竖屏短片拆成四个环节：写脚本、配音、剪画面、出封面。"
        "每个环节都能在本机跑完，不需要云端模型，也不需要额外账号。"
        "脚本先定三句话，配音只做字幕，剪画面按镜头表拼接，封面最后出两张备选。"
    )

    def test_identical_body_fails(self):
        other = self.root / "other.txt"
        other.write_text(self.BODY, encoding="utf-8")
        mine = write_draft(self.root, "mine.txt", self.BODY, {"sources": GOOD_SOURCE})
        found = findings_for(mine, "csdn", self.cfg, [corpus_row(other, self.BODY)])
        self.assertIn("fail", [f.severity for f in found if f.rule == "G005"])

    def test_near_duplicate_warns(self):
        near = self.BODY + "另外再加一段补充说明，讲清楚字幕的字体与字号。"
        other = self.root / "other.txt"
        other.write_text(near, encoding="utf-8")
        mine = write_draft(self.root, "mine.txt", self.BODY, {"sources": GOOD_SOURCE})
        found = findings_for(mine, "csdn", self.cfg, [corpus_row(other, near)])
        self.assertIn("warn", [f.severity for f in found if f.rule == "G005"])

    def test_embedded_note_is_not_duplicate(self):
        other = self.root / "note.md"
        note = "# 草稿笔记\n\n- 状态：待发\n\n" + self.BODY + "\n\n## 发布注意\n- 勾选声明\n"
        other.write_text(note, encoding="utf-8")
        mine = write_draft(self.root, "mine.txt", self.BODY, {"sources": GOOD_SOURCE})
        found = findings_for(mine, "csdn", self.cfg, [corpus_row(other, note, is_note=True)])
        self.assertEqual([f for f in found if f.rule == "G005"], [])

    def test_unrelated_body_is_silent(self):
        other = self.root / "other.txt"
        other_text = "周末去了趟菜市场，买了三斤橘子，回来路上下了点小雨，很凉快。"
        other.write_text(other_text, encoding="utf-8")
        mine = write_draft(self.root, "mine.txt", self.BODY, {"sources": GOOD_SOURCE})
        found = findings_for(mine, "csdn", self.cfg, [corpus_row(other, other_text)])
        self.assertEqual([f for f in found if f.rule == "G005"], [])


class TestG006TemplateSmell(RuleBase):
    def test_dense_buzzwords_warn(self):
        text = "赋能团队，打造闭环，形成抓手，底层逻辑就是这样，众所周知，干货满满。\n"
        found = self.run_rules("a.txt", text, meta={"sources": GOOD_SOURCE})
        self.assertIn("warn", [f.severity for f in found if f.rule == "G006"])

    def test_single_hit_is_silent(self):
        found = self.run_rules("b.txt", "这一版主打一个打造感。\n", meta={"sources": GOOD_SOURCE})
        self.assertEqual([f for f in found if f.rule == "G006"], [])


class TestG007AiDisclosure(RuleBase):
    def test_always_informational(self):
        for meta in ({"sources": GOOD_SOURCE},
                     {"sources": GOOD_SOURCE, "ai_declaration": True},
                     {"sources": GOOD_SOURCE, "ai_declaration": False}):
            found = self.run_rules("a.txt", "正文。\n", meta=meta)
            g7 = [f for f in found if f.rule == "G007"]
            self.assertEqual(len(g7), 1)
            self.assertIn(g7[0].severity, ("info", "warn"))


class TestG009PlatformWords(RuleBase):
    def test_negated_banned_word_still_fails(self):
        found = self.run_rules(
            "a.txt", "老照片做成短片，不代登录、不代发布，也不用换脸。\n", platform="xianyu"
        )
        fails = [f for f in found if f.rule == "G009" and f.severity == "fail"]
        self.assertGreaterEqual(len(fails), 2)
        self.assertTrue(any("否定句同样命中" in f.message for f in fails))

    def test_negation_marker_limited_to_clause(self):
        found = self.run_rules(
            "b.txt", "不用版权曲库。AI 生成的部分放在元数据里。\n", platform="xhs"
        )
        ai = [f for f in found if f.rule == "G009" and f.data.get("match") == "AI 生成"]
        self.assertTrue(ai)
        self.assertTrue(ai[0].data.get("negated") is False, ai[0].data)

    def test_absolute_words_fail_on_xhs(self):
        sev = self.sev("c.txt", "全网最好的剪辑服务。\n", platform="xhs")
        self.assertIn("fail", sev)

    def test_over_length_fails(self):
        sev = self.sev("d.txt", "长" * 1600, platform="xianyu")
        self.assertIn("fail", sev)

    def test_meta_title_length_warns(self):
        found = self.run_rules(
            "e.txt", "正文首行可以很长，因为它不是标题。\n",
            meta={"title": "这是一个明显超过二十二个字的标题用来测试长度限制是否生效", "sources": GOOD_SOURCE},
        )
        title_warns = [f for f in found if f.rule == "G009" and f.data.get("title_len")]
        self.assertTrue(title_warns)

    def test_long_paragraph_is_not_a_title(self):
        found = self.run_rules("f.txt", "这" * 200 + "\n", meta={"sources": GOOD_SOURCE})
        self.assertEqual([f for f in found if f.rule == "G009"], [])

    def test_xianyu_listing_layout_warns(self):
        found = self.run_rules("g.txt", "老照片做成竖屏短片，含字幕与原创配乐，四十八小时交付，价格 99 元。\n", platform="xianyu")
        self.assertTrue([f for f in found if f.rule == "G009" and f.severity == "warn"])


class TestFencedDrafts(RuleBase):
    def test_fenced_skips_review_notes(self):
        note = (
            "# 草稿\n\n- 备注：上一版含「代登录」被判风险\n\n"
            "## 正文\n\n```\n干净的正文，只写交付与价格。\n```\n"
        )
        sev = self.sev("note.md", note, platform="xhs", fenced=True)
        self.assertNotIn("fail", sev)

    def test_full_file_scan_catches_notes(self):
        note = "# 草稿\n\n- 备注：上一版含「代登录」被判风险\n\n```\n干净的正文。\n```\n"
        sev = self.sev("note.md", note, platform="xhs")
        self.assertIn("fail", sev)


class TestCorpusContainment(unittest.TestCase):
    def test_g005_ignores_substring_docs(self):
        from helpers import T0
        from gatecheck.config import DEFAULTS, Config
        from gatecheck.draft import load_draft
        from gatecheck.rules import RuleContext, run_rules
        import copy

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = "正文段落" * 40
            path = write_draft(root, "a.txt", body, {"sources": GOOD_SOURCE})
            cfg = Config(copy.deepcopy(DEFAULTS), root)
            draft = load_draft(path, "csdn")
            row = {"path": "x", "norm": body + "附加说明" * 20, "digest": "", "mtime": 0}
            found = run_rules(RuleContext(draft, cfg, [row], T0))
            self.assertEqual([f for f in found if f.rule == "G005"], [])


if __name__ == "__main__":
    unittest.main()
