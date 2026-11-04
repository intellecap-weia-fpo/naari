import os
import re
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from unittest.mock import patch

from survey import indicator_sandbox
from survey.indicator_sandbox import SubmissionView, UnsafeScriptError, ViewList, run, validate

BASE_DIR = Path(__file__).resolve().parent.parent


def answer(**options):
    return SimpleNamespace(
        question_id=1,
        text_answer="Yes",
        selected_options=ViewList(SimpleNamespace(text=t) for t in options.get("texts", [])),
    )


class IndicatorSandboxTests(SimpleTestCase):
    def test_read_only_scripts_still_work(self):
        code = (
            "a = answers.get(1)\n"
            "picked = [o.text.strip().lower() for o in a.selected_options.all()]\n"
            "result = {'score': round(math.sqrt(16)) + len(picked), 'yes': a.text_answer.lower() == 'yes'}\n"
        )
        ns = run(code, {"answers": {1: answer(texts=[" Farming ", "Dairy"])}})
        self.assertEqual(ns["result"], {"score": 6, "yes": True})

    def test_escapes_and_writes_are_rejected(self):
        attacks = [
            "import os",
            "from os import system",
            "__import__('os')",
            "result = ().__class__.__bases__[0].__subclasses__()",
            "result = open('/etc/passwd').read()",
            "submission.delete()",
            "submission.survey.save()",
            "result = type(submission).objects.all()",
            "result = '{0.__class__}'.format(1)",
            "result = getattr(submission, 'delete')",
            "result = eval('1')",
            "_x = 1",
            # Escapes found in review on 2026-11-03: modules leading to builtins/sys,
            # generator frames back into the sandbox's globals, ORM writes.
            "b = json.codecs.builtins",
            "result = re.enum.sys.modules",
            "def gen():\n    yield holder[0].gi_frame.f_back.f_back.f_globals\nholder=[None]",
            "result = (lambda: 0).f_globals",
            "class X: pass",
            "survey.save_base()",
            "submission.user.set_password('x')",
            "result = answers[1].selected_options.query",
        ]
        for code in attacks:
            with self.subTest(code=code):
                with self.assertRaises((UnsafeScriptError, NameError, SyntaxError, AttributeError)):
                    run(code, {"submission": SubmissionView.__new__(SubmissionView), "survey": None, "answers": {1: answer()}})

    def test_scripts_shipped_in_the_repo_pass_validation(self):
        scripts = []
        for path in list((BASE_DIR / "dev" / "indicator").rglob("*.py")) + list(
            (BASE_DIR / "survey" / "management" / "commands").glob("*indicator*.py")
        ):
            text = path.read_text()
            for match in re.finditer(r'(?:code|script_code|SCRIPT|script)\s*=\s*[rf]?("""|\'\'\')(.*?)\1', text, re.S):
                try:
                    compile(match.group(2), "<s>", "exec")
                except SyntaxError:
                    continue
                scripts.append((path.name, match.group(2)))
        if not scripts:
            self.skipTest("no sample indicator scripts in this checkout")
        for name, code in scripts:
            with self.subTest(script=name):
                validate(code)

    def test_runaway_loops_are_stopped(self):
        with patch.object(indicator_sandbox, "LINE_BUDGET", 1000):
            with self.assertRaises(UnsafeScriptError):
                run("while True:\n    pass", {})

    def test_helper_modules_are_plain_function_namespaces(self):
        ns = run("result = (json.dumps({'a': 1}), re.findall('[0-9]', 'a1b2'), math.floor(2.5))", {})
        self.assertEqual(ns["result"], ('{"a": 1}', ["1", "2"], 2))

    def test_snapshot_gives_scripts_the_fields_they_use(self):
        option = SimpleNamespace(pk=7, text=" Dairy ", value="dairy")
        orm_answer = SimpleNamespace(
            pk=3, question_id=909, text_answer="5", other_text=None,
            selected_options=SimpleNamespace(all=lambda: [option]),
        )
        orm_submission = SimpleNamespace(
            pk=1, survey_id=2, status="completed", submitted_at=None, language="hi",
            survey=SimpleNamespace(pk=2, title="NAARI"),
            answers=SimpleNamespace(prefetch_related=lambda *a: [orm_answer]),
        )
        code = (
            "answers_map = {}\n"
            "for submission_answer in submission.answers.all():\n"
            "    answers_map[submission_answer.question_id] = submission_answer\n"
            "a = answers_map.get(909)\n"
            "picked = [o.text.strip().lower() for o in a.selected_options.all()] if a.selected_options.exists() else []\n"
            "result = {'picked': picked, 'hours': float(a.text_answer.strip()), 'survey': survey.title}\n"
        )
        ns = run(code, indicator_sandbox.snapshot_submission(orm_submission))
        self.assertEqual(ns["result"], {"picked": ["dairy"], "hours": 5.0, "survey": "NAARI"})

    def test_isolated_run_returns_result_and_output(self):
        result, output = indicator_sandbox.run_isolated("print('hi')\nresult = {'n': len(answers)}", {"answers": {1: 1}})
        self.assertEqual((result, output), ({"n": 1}, "hi\n"))

    def test_isolated_run_stops_scripts_that_swallow_the_budget_or_block_in_c(self):
        swallow = (
            "def burn():\n    while True:\n        pass\n"
            "def go():\n    try:\n        burn()\n    except Exception:\n        return 1\n"
            "go()\nwhile True:\n    pass\n"
        )
        for code in (swallow, "result = sum(range(10**12))", "result = re.match('(a+)+$', 'a' * 40 + 'b')"):
            with self.subTest(code=code[:30]):
                with patch.object(indicator_sandbox, "LINE_BUDGET", 10_000):
                    with self.assertRaises(indicator_sandbox.ScriptFailed):
                        indicator_sandbox.run_isolated(code, {"answers": {}}, timeout=1)

    def test_scripts_cannot_change_shared_helpers(self):
        with self.assertRaises(UnsafeScriptError):
            run("re.search = print", {})
        with self.assertRaises(UnsafeScriptError):
            run("json.loads.box = []", {})
        run("helper = math\nhelper2 = dict(x=1)", {})
        self.assertIs(indicator_sandbox.SAFE_MODULES["math"].sqrt, __import__("math").sqrt)

    def test_isolated_run_reports_unsafe_scripts(self):
        with self.assertRaises(indicator_sandbox.ScriptFailed):
            indicator_sandbox.run_isolated("import os", {})

    def test_child_always_exits_even_if_the_error_message_fails(self):
        parent = os.getpid()
        with self.assertRaises(indicator_sandbox.ScriptFailed):
            indicator_sandbox.run_isolated("raise ValueError(10**5000)", {})
        self.assertEqual(os.getpid(), parent)
