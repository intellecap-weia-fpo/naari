"""Explicit "poor performance" definitions for survey-6 choice questions.

Rationale
---------
The default heuristic in `insights_metrics` treats the first N options of a
choice question as "poor". That works for Likert scales where options are
ordered bottom→top ("Not at all", "Small extent", ...) but fails for
categorical questions like *"Who usually decides …"* where `Self` (the
empowered answer) sits at ord=0.

Each rule here is keyed by the **full set of option texts** (normalized,
case-insensitive). A rule applies to every question that exposes that exact
set of options — so one entry covers many structurally-identical questions
without repeating ourselves per question id.

Rule shape
----------
    {"kind": "empowered", "values": [...]}   # poor = selected none of the listed options
    {"kind": "poor",      "values": [...]}   # poor = selected any of the listed options
    {"kind": "skip"}                          # don't score this question (card will not be generated)

Consumers
---------
`insights_metrics.classify_question()` returns the matching rule (or None)
and `_compute_choice_poor_rate()` uses it to count "poor" answers.
"""

from __future__ import annotations


def _n(s: str) -> str:
    return (s or "").strip().lower()


def _fs(*texts: str) -> frozenset:
    return frozenset(_n(t) for t in texts)


# Option-set signature -> rule
RULES: dict[frozenset, dict] = {
    # ------------------------------------------------------------
    # "Who usually decides / Who made the decision …" radio.
    # Empowered answer = Self. Everything else is poor.
    # Covers 21 financial + mobility autonomy questions.
    # ------------------------------------------------------------
    _fs(
        "Self",
        "Spouse (or Partner) In HH",
        "Spouse (or Partner) Outside HH",
        "Other HH Member",
        "Non-HH Member, Relative",
        "Non-HH Member, No Relation",
        "Government or Other Institution",
        "Clan (Group of Relatives)",
    ): {"kind": "empowered", "values": [_n("Self")]},

    # ------------------------------------------------------------
    # "How much input did you have …" Likert (3-point).
    # Poor = "Little To No Input In Decisions".
    # Covers 20 livelihood / control-over-income questions.
    # ------------------------------------------------------------
    _fs(
        "Little To No Input In Decisions",
        "Input Into Some Decisions",
        "Input Into Most or All Decisions",
    ): {"kind": "poor", "values": [_n("Little To No Input In Decisions")]},

    # ------------------------------------------------------------
    # "To what extent …" 4-point Likert.
    # Poor = bottom half ("Not At All", "Small Extent").
    # Covers 16 participation-confidence questions.
    # ------------------------------------------------------------
    _fs(
        "Not At All",
        "Small Extent",
        "Medium Extent",
        "High Extent",
    ): {"kind": "poor", "values": [_n("Not At All"), _n("Small Extent")]},

    # ------------------------------------------------------------
    # Self-efficacy 5-point agreement Likert.
    # Poor = "Strongly Disagree", "Disagree".
    # Covers 9 self-efficacy items.
    # ------------------------------------------------------------
    _fs(
        "Strongly Disagree",
        "Disagree",
        "Neither Agree or Disagree",
        "Agree",
        "Strongly Agree",
    ): {"kind": "poor", "values": [_n("Strongly Disagree"), _n("Disagree")]},

    # ------------------------------------------------------------
    # "How often do you go …" frequency Likert.
    # Poor = "Sometimes" (the least-frequent option).
    # Covers 5 visiting-frequency questions.
    # ------------------------------------------------------------
    _fs(
        "Everyday",
        "Every Week At Least Once",
        "Every 2 Weeks At Least Once",
        "Every Month At Least Once",
        "Sometimes",
    ): {"kind": "poor", "values": [_n("Sometimes")]},

    # ------------------------------------------------------------
    # FPO meeting attendance frequency (Q1316).
    # Poor = "Never", "Rarely".
    # ------------------------------------------------------------
    _fs(
        "Never",
        "Rarely",
        "Sometimes",
        "Often",
        "Always",
    ): {"kind": "poor", "values": [_n("Never"), _n("Rarely")]},

    # ------------------------------------------------------------
    # FPO involvement 5-point Likert (Q1318).
    # Poor = "Not at all involved", "Slightly involved".
    # ------------------------------------------------------------
    _fs(
        "Not at all involved",
        "Slightly involved",
        "Moderately involved",
        "Highly involved",
        "Extremely involved",
    ): {"kind": "poor", "values": [_n("Not at all involved"), _n("Slightly involved")]},

    # ------------------------------------------------------------
    # Asset ownership type (Q960).
    # Poor = "None".
    # ------------------------------------------------------------
    _fs(
        "Solely",
        "Jointly",
        "Solely And Jointly",
        "None",
    ): {"kind": "poor", "values": [_n("None")]},
}


# Per-question overrides for cases where option-set matching isn't enough
# or where the signature is ambiguous. Keyed by question_id.
# Use `{"kind": "skip"}` to suppress a card entirely.
PER_QUESTION: dict[int, dict] = {
    # Participation checkbox — "poor" needs the indicator-level logic
    # (at least 2 agri + 1 other). A single-option heuristic would be noise,
    # so we skip and let the indicator-level story cover it.
    909: {"kind": "skip"},

    # Asset ownership checkbox — same reason: "at least 3 asset types" is
    # counted at the indicator level, not per option.
    959: {"kind": "skip"},

    # Yes/No vignette / probe questions (the "Anita" prompts and childcare
    # probes) — their "poor" direction varies by item and isn't uniform.
    1039: {"kind": "skip"},
    1040: {"kind": "skip"},
    1165: {"kind": "skip"},
    1249: {"kind": "skip"},
    1253: {"kind": "skip"},
    1256: {"kind": "skip"},
    1259: {"kind": "skip"},
    1262: {"kind": "skip"},
    1265: {"kind": "skip"},
    1270: {"kind": "skip"},
    1272: {"kind": "skip"},
    1275: {"kind": "skip"},
    1278: {"kind": "skip"},
    1281: {"kind": "skip"},
    1284: {"kind": "skip"},
    1287: {"kind": "skip"},
    1290: {"kind": "skip"},

    # Loan source checkbox (Q984) — "None" here could mean the respondent
    # took no loan at all, which the indicator treats as a prerequisite
    # (no loan = not scored). Skip the per-question card.
    984: {"kind": "skip"},

    # "Do you go to following places?" checkbox — long option text varies
    # across deployments; keyed by id to avoid whitespace-sensitive matching.
    # Poor = only "None" selected (no places visited).
    1126: {"kind": "poor", "values": ["none"]},
}


def classify_question(question) -> dict | None:
    """Return the rule applicable to a given Question, or None if unknown.

    Per-question overrides take precedence over option-set matches.
    """
    rule = PER_QUESTION.get(question.id)
    if rule is not None:
        return rule
    opts = list(question.options.all().order_by("order", "id").values_list("text", flat=True))
    if not opts:
        return None
    sig = frozenset(_n(o) for o in opts)
    return RULES.get(sig)


def poor_option_ids(question, rule: dict) -> set[int]:
    """Given a resolved rule, return the set of option IDs that count as 'poor'."""
    if rule.get("kind") == "skip":
        return set()
    opts = list(question.options.all().order_by("order", "id"))
    lookup = {_n(o.text): o.id for o in opts}
    values = {_n(v) for v in rule.get("values", [])}
    if rule["kind"] == "empowered":
        return {oid for txt, oid in lookup.items() if txt not in values}
    if rule["kind"] == "poor":
        return {oid for txt, oid in lookup.items() if txt in values}
    return set()
