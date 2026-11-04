"""Guidance corpus extracted from `NAARI Insights - Revised.docx`.

Keyed by indicator topic (loose match on indicator.name) → list of entries, each
carrying a question phrasing and the associated symptom / cause / action triad
from the source doc. Used by `insights_service.InsightsService` to ground the
LLM so its output stays on-message with the programme team's framing.

Lookup is substring / fuzzy — indicator names and question texts in the DB
may include survey-specific placeholders like [SOURCE] or (Activity).
"""

from __future__ import annotations

GUIDANCE: dict[str, list[dict]] = {
    "financial_decisions": [
        {
            "question_keywords": ["taken any loan", "loan from the following lenders"],
            "symptoms": "Women demonstrate low participation in formal and informal credit systems.",
            "likely_causes": "Barriers in access, awareness, trust, or perceived need for credit.",
            "action": "Expand financial literacy; expand financial access; use SHGs/FPOs as a mode of enhancing credit access.",
        },
        {
            "question_keywords": ["decision to borrow"],
            "symptoms": "Women lack autonomy when it comes to making decisions regarding the need to borrow.",
            "likely_causes": "Limited financial knowledge, cultural norms, risk aversion, lack of documentation.",
            "action": "Promote financial literacy initiatives that enhance women's ability to make informed financial decisions.",
        },
        {
            "question_keywords": ["what to do with the money", "money or item borrowed"],
            "symptoms": "Women lack confidence regarding how to use borrowed funds.",
            "likely_causes": "Complexity of products, no training on returns, fear of wrong spending.",
            "action": "Strengthen women's access to formal financial services, savings mechanisms, credit, and finance deployment. Publicize simplified financial product designs and loan schemes.",
        },
    ],
    "autonomy_in_income": [
        {
            "question_keywords": ["autonomous", "autonomy in income"],
            "symptoms": "Autonomous decision-making is low",
            "likely_causes": "",
            "action": "Implement integrated interventions like leadership training, financial literacy and women empowerment campaigns.",
        },
    ],
    "control_over_income": [
        {
            "question_keywords": ["participate in the following activities", "past 12 months"],
            "symptoms": "Women show limited participation in agricultural and non-agri activities.",
            "likely_causes": "Limited skills or information, time constraints, family commitments.",
            "action": "Disseminate information on women-centric schemes and incentives like Krishi Sakhi programme, Sub-Mission on Agricultural Mechanization and other programmes that promote women participation in agriculture.",
        },
        {
            "question_keywords": ["keep for consumption at home", "rather than selling"],
            "symptoms": "Women have low input in crop/livestock output decisions.",
            "likely_causes": "Economic dependence, limited or no market information, no decision practice.",
            "action": "Enable women's control over crop/livestock output decisions by ensuring they track sales, receive regular market price updates, participate in local price discussions, and build basic negotiation skills through simple, practical trainings.",
        },
        {
            "question_keywords": ["income generated from", "how to use income"],
            "symptoms": "Women lack control over income from farm or livestock work.",
            "likely_causes": "Low bargaining power, no personal accounts, spending veto power, lack of income tracking provisions.",
            "action": "Enable women's control over income by ensuring earnings are deposited into accounts they own or access, while supporting simple income tracking and basic financial awareness.",
        },
        {
            "question_keywords": ["could participate in decisions", "participate in decisions regarding"],
            "symptoms": "Women lack confidence in participating in activity decisions.",
            "likely_causes": "Traditional gender roles, cumulative exclusion, fear of ridicule.",
            "action": "Strengthen women's participation in decision-making by building confidence through simple learning modules, ensuring structured opportunities to speak in local forums, and promoting supportive norms—including engaging male family members.",
        },
    ],
    "group_membership": [
        {
            "question_keywords": ["attend FPO", "group meetings"],
            "symptoms": "Women do not or are unable most (FPO) group meetings",
            "likely_causes": "Logistical constraints limiting mobility, caregiving responsibilities, safety concerns",
            "action": "Improve women's participation in FPOs by addressing mobility, childcare, and scheduling barriers while clearly communicating membership benefits and incentives—allowing you to prioritize the most binding constraint.",
        },
        {
            "question_keywords": ["involved", "FPO group activities"],
            "symptoms": "Women feel minimally involved in FPO activities.",
            "likely_causes": "Lack of role clarity, agenda irrelevance, limited onboarding.",
            "action": "Increase women's involvement by assigning clear roles, improving onboarding, and ensuring meeting agendas reflect women's priorities so participation becomes meaningful rather than passive.",
        },
    ],
    "livelihood_decisions": [
        {
            "question_keywords": ["participate in any of the following activities"],
            "symptoms": "Women exhibit low participation in livelihood activities",
            "likely_causes": "Lack of awareness, lack of familial permissions, time constraints",
            "action": "Boost women's participation and decision-making in livelihoods through community outreach, local support systems, and confidence-building efforts, while addressing household-level barriers—letting you act at either community or family level.",
        },
        {
            "question_keywords": ["input did you have", "decisions about"],
            "symptoms": "Women have limited input in livelihood activity decisions.",
            "likely_causes": "Capacity and skill constraints, family restrictions.",
            "action": "Strengthen women's voice by combining practical skill-building with norm-change efforts that engage families and create an enabling environment for active decision-making.",
        },
        {
            "question_keywords": ["can participate in decisions regarding"],
            "symptoms": "Women lack confidence in livelihood decision participation.",
            "likely_causes": "Educational gaps, normative expectations, cognitive overload from chores.",
            "action": "Launch or encourage mindset-change campaigns targeting families and community leaders and incentivising families with higher women participation.",
        },
    ],
    "ownership_assets": [
        {
            "question_keywords": ["household currently have", "anyone in your household"],
            "symptoms": "Households lack access to essential assets.",
            "likely_causes": "Awareness gaps, lack of financing options, high upfront costs, market distance.",
            "action": "Improve household access to essential assets by enabling affordable financing, promoting awareness of government schemes, and creating shared access models (e.g., rental hubs or mobile services).",
        },
        {
            "question_keywords": ["type of ownership"],
            "symptoms": "Women lack individual or joint asset ownership.",
            "likely_causes": "Cultural norms, legal hurdles, family resistance, lack of proper documents.",
            "action": "Promote women's individual and joint asset ownership by increasing legal awareness, incentivizing joint titles, and engaging communities to shift norms — while leveraging cooperatives and government benefits to improve access.",
        },
    ],
    # NOTE: the NAARI Insights doc intentionally leaves the "Likely causes"
    # column blank for the 9 Self-efficacy Likert items — only Questions /
    # Symptoms / Potential actions are populated. We preserve that fidelity
    # and let the LLM infer causes from the symptom + action pair.
    "self_efficacy": [
        {
            "question_keywords": ["difficult tasks", "certain that I will accomplish"],
            "symptoms": "Half the respondents lack confidence in overcoming challenges, signalling potential barriers to tackling hurdles like market access or crop failures.",
            "likely_causes": "",
            "action": "Targeted training on problem-solving and success storytelling could rebuild this certainty, boosting overall resilience.",
        },
        {
            "question_keywords": ["outcomes that are important to me"],
            "symptoms": "Low scores indicate widespread doubt in achieving personal goals, which may undermine participation in livelihood programs or asset-building initiatives.",
            "likely_causes": "",
            "action": "Introduce goal-setting workshops linked to WEAI+MI metrics to foster belief in attainable outcomes like income diversification.",
        },
        {
            "question_keywords": ["effectively on many different tasks"],
            "symptoms": "With majority feeling ineffective across tasks, versatility in roles (e.g., farming, marketing, finance) within FPO or in daily lives may suffer.",
            "likely_causes": "",
            "action": "Cross-training sessions on multi-skilling could enhance adaptability and build resilience and efficacious attitudes.",
        },
        {
            "question_keywords": ["when things are tough", "perform quite well"],
            "symptoms": "Majority doubt performance under adversity, risking dropout from tough rural programs amid climate, familial or market stresses.",
            "likely_causes": "",
            "action": "Resilience-building via peer mentoring and stress simulation exercises would strengthen endurance in agricultural and livelihood activities.",
        },
        {
            "question_keywords": ["plan tasks carefully"],
            "symptoms": "Low planning confidence among majority suggests disorganized local governance operations, from sowing schedules to credit applications.",
            "likely_causes": "",
            "action": "Capacity-building on simple planning tools designed in simple language can improve execution and policy compliance.",
        },
        {
            "question_keywords": ["look forward to my work"],
            "symptoms": "Lacking enthusiasm points to demotivation in daily rural tasks, potentially stalling women's empowerment efforts.",
            "likely_causes": "",
            "action": "Incorporate motivational incentives like recognition events to reignite passion for women empowerment, financial agency, and overall morale upliftment.",
        },
        {
            "question_keywords": ["work hard even when I face obstacles"],
            "symptoms": "Persistent effort is weak for many, threatening long-term participation in livelihood activities, long-term sustainability amid obstacles like societal restrictions, family constraints.",
            "likely_causes": "",
            "action": "Obstacle-navigation modules with real case studies from NAARI surveys could cultivate perseverance.",
        },
        {
            "question_keywords": ["even if it isn't popular", "as well as I can"],
            "symptoms": "Low commitment to excellence despite opposition may hinder innovative practices like sustainable farming in conservative communities.",
            "likely_causes": "",
            "action": "Advocacy training emphasizing intrinsic value would empower women to prioritize quality over social pressure.",
        },
        {
            "question_keywords": ["motivated to do my work better"],
            "symptoms": "Half lack drive for improvement, limiting progress in skill-upgrading for rural finance or policy engagement.",
            "likely_causes": "",
            "action": "Personalized feedback loops tied to performance metrics can spark continuous self-betterment in empowerment programs.",
        },
    ],
    "visiting_locations": [
        {
            "question_keywords": ["go to following places"],
            "symptoms": "Women exhibit highly restricted mobility.",
            "likely_causes": "Safety concerns, lack of safe transportation options, time mismatches, high travel costs.",
            "action": "Improve women's mobility by ensuring safe, affordable, and accessible transport options while addressing social norms.",
        },
        {
            "question_keywords": ["how often do you go"],
            "symptoms": "Women visit essential locations infrequently.",
            "likely_causes": "Information delays, transportation gaps, workload peaks.",
            "action": "Strengthen local infrastructure and services that improve women's mobility and safety. Promote community awareness initiatives that encourage women's participation in public and economic spaces.",
        },
        {
            "question_keywords": ["decides whether you can go"],
            "symptoms": "Women lack autonomy in deciding mobility.",
            "likely_causes": "Lack of precedence, group dependence, family restrictions.",
            "action": "Strengthen women's autonomy in mobility decisions by building confidence, reducing financial and access barriers, and promoting supportive household norms.",
        },
    ],
    "work_balance": [
        {
            "question_keywords": ["work balance", "time poverty", "total time"],
            "symptoms": "Half of women face severe time poverty.",
            "likely_causes": "Unpaid domestic demands, caregiving responsibilities, lack of labor-saving tools, lack of burden sharing.",
            "action": "Reduce women's time poverty by introducing labour-saving tools, shared childcare solutions, and efficiency-enhancing services.",
        },
    ],
}


_INDICATOR_ALIAS = [
    ("financial_decisions", ["financial decision", "access to and decisions on financial", "credit", "loan"]),
    ("autonomy_in_income", ["autonomy in income"]),
    ("control_over_income", ["control over use of income", "control over income"]),
    ("group_membership", ["group membership"]),
    ("livelihood_decisions", ["input into livelihood", "livelihood decisions"]),
    ("ownership_assets", ["ownership of land", "ownership of assets", "asset"]),
    ("self_efficacy", ["self-efficacy", "self efficacy"]),
    ("visiting_locations", ["visiting important", "physical mobility", "mobility"]),
    ("work_balance", ["work balance", "time allocation", "time poverty"]),
]


def resolve_indicator_key(indicator_name: str) -> str | None:
    """Map a free-form indicator name to a key in GUIDANCE. Returns None if no match."""
    if not indicator_name:
        return None
    lowered = indicator_name.lower()
    for key, aliases in _INDICATOR_ALIAS:
        for alias in aliases:
            if alias in lowered:
                return key
    return None


def find_guidance(indicator_name: str, question_text: str) -> dict | None:
    """Return the best-matching guidance entry for (indicator, question), or None."""
    key = resolve_indicator_key(indicator_name)
    if not key:
        return None
    entries = GUIDANCE.get(key, [])
    if not entries:
        return None
    q_lower = (question_text or "").lower()
    for entry in entries:
        for kw in entry["question_keywords"]:
            if kw.lower() in q_lower:
                return entry
    return entries[0]
