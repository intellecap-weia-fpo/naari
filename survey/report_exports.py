import json

from django.contrib.auth.models import AnonymousUser
from django.conf import settings
from django.test import RequestFactory
from django.utils import timezone

from .models import Indicator, Question, QuestionOption, Submission, SubmissionAnswer


REPORT_EXPORT_TYPES = {
    "overview": {"title": "Overview Report", "filename": "overview_report"},
    "module_g2a": {"title": "G2A Decision-Making Report", "filename": "g2a_decision_making_report"},
    "self_efficacy": {"title": "Self Efficacy Report", "filename": "self_efficacy_report"},
    "module_g3a": {"title": "G3A Productive Capital Report", "filename": "g3a_productive_capital_report"},
    "module_g3b": {"title": "G3B Financial Services Report", "filename": "g3b_financial_services_report"},
    "module_g4": {"title": "G4 Time Allocation Report", "filename": "g4_time_allocation_report"},
    "module_g6": {"title": "G6 Physical Mobility Report", "filename": "g6_physical_mobility_report"},
}


def _safe_value(value, fallback="All"):
    if value in (None, ""):
        return fallback
    return value


def _safe_sheet_name(name):
    cleaned = "".join(" " if ch in "\\/?*[]:" else ch for ch in str(name or ""))
    cleaned = cleaned.strip() or "Sheet"
    return cleaned[:31]


def _option_label(option_id, fallback="All"):
    if not option_id:
        return fallback
    if isinstance(option_id, str) and not option_id.isdigit():
        return option_id
    option = QuestionOption.objects.filter(id=option_id).only("text").first()
    return option.text if option else str(option_id)


def _indicator_label(indicator_id, fallback="Default"):
    if not indicator_id:
        return fallback
    indicator = Indicator.objects.filter(id=indicator_id).only("name").first()
    return indicator.name if indicator else str(indicator_id)


def metadata_rows(report_title, filters):
    return [
        ["Report", report_title],
        ["Generated At", timezone.localtime().strftime("%Y-%m-%d %H:%M:%S %Z")],
        ["Report Type", filters.get("report_type", "") or report_title],
    ]


def filter_rows(filters):
    return [
        ["Filter", "Value"],
        ["State", _option_label(filters.get("state"))],
        ["District", _option_label(filters.get("district"))],
        ["Block", _safe_value(filters.get("block"))],
        ["Year", _safe_value(filters.get("year"))],
        ["Date From", _safe_value(filters.get("date_from"))],
        ["Date To", _safe_value(filters.get("date_to"))],
        ["Demographic", _safe_value(filters.get("demographic"))],
        ["Map Indicator", _indicator_label(filters.get("map_indicator"))],
    ]


def stats_rows(stats=None):
    stats = stats or {}
    return [
        ["Metric", "Value"],
        ["Total States", stats.get("total_states_count", 0)],
        ["Total Districts", stats.get("total_districts_count", 0)],
        ["Total Blocks", stats.get("total_blocks_count", 0)],
        ["Total Women Surveyed", stats.get("total_women_surveyed", stats.get("total_women", 0))],
    ]


def counts_with_percent_rows(data=None, first_header="Name", value_header="Count"):
    data = data or {}
    total = sum((count or 0) for count in data.values())
    rows = [[first_header, value_header, "Percent of Total"]]
    for name, count in data.items():
        count = count or 0
        rows.append([name, count, round((count / total * 100), 2) if total else 0])
    rows.append(["Total", total, 100 if total else 0])
    return rows


def indicator_rows(indicators=None):
    indicators = indicators or []
    rows = [["Indicator", "Empowered Count", "Disempowered Count", "Total Count", "Empowerment Percentage"]]
    for indicator in indicators:
        rows.append([
            indicator.get("indicator_name") or indicator.get("name") or "Unknown",
            indicator.get("empowered_count", 0),
            indicator.get("disempowered_count", 0),
            indicator.get("total_count", 0),
            indicator.get("empowerment_percentage", 0),
        ])
    return rows


def geography_rows(map_data=None):
    map_data = map_data or []
    geography_label = "State"
    use_district = False
    if map_data:
        sample = map_data[0]
        if sample.get("district"):
            geography_label = "District"
            use_district = True
    rows = [[geography_label, "Average Empowerment", "Empowerment Percentage", "Empowered Count", "Disempowered Count", "Total Count"]]
    for item in map_data:
        rows.append([
            item.get("district") if use_district else item.get("state") or item.get("district") or "Unknown",
            item.get("value", 0),
            item.get("empowerment_percentage", 0),
            item.get("empowered_count", 0),
            item.get("disempowered_count", 0),
            item.get("count", 0),
        ])
    return rows


def grouped_indicator_rows(grouped_data=None, demographic="age"):
    grouped_data = grouped_data or []
    demographic_labels = {
        "age": "Age",
        "literacy": "Literacy",
        "shg_membership": "SHG Membership",
        "fpo_membership": "FPO Membership",
        "land_ownership": "Land Ownership",
        "house_membership": "House Membership",
        "caste": "Caste",
    }
    label = demographic_labels.get(demographic, demographic)
    rows = [[label, "Indicator", "Empowered Count", "Disempowered Count", "Total Women", "Empowered Percentage", "Disempowered Percentage"]]
    for group in grouped_data:
        for indicator in group.get("indicators", []):
            empowered_count = indicator.get("empowered_count", 0)
            disempowered_count = indicator.get("disempowered_count", 0)
            total = indicator.get("total_count", 0)
            rows.append([
                group.get("group_name") or "Unknown",
                indicator.get("indicator_name") or indicator.get("name") or "Unknown",
                empowered_count,
                disempowered_count,
                total,
                round((empowered_count / total * 100), 2) if total else 0,
                round((disempowered_count / total * 100), 2) if total else 0,
            ])
    if len(rows) == 1:
        rows.append(["No data", "", 0, 0, 0, 0, 0])
    return rows


def object_count_rows(data=None, first_header="Name", value_header="Count"):
    data = data or {}
    return [[first_header, value_header], *[[name, count or 0] for name, count in data.items()]]


def object_matrix_rows(data, first_header, columns):
    data = data or {}
    rows = [[first_header, *columns]]
    for name, counts in data.items():
        rows.append([name, *[(counts or {}).get(column, 0) for column in columns]])
    return rows


def object_matrix_percent_rows(data, first_header, columns):
    data = data or {}
    rows = [[first_header, *columns, "Total"]]
    for name, counts in data.items():
        counts = counts or {}
        total = sum((counts.get(column, 0) or 0) for column in columns)
        rows.append([
            name,
            *[
                round(((counts.get(column, 0) or 0) / total * 100), 2) if total else 0
                for column in columns
            ],
            total,
        ])
    return rows


def object_matrix_long_rows(data, first_header, series_header, columns):
    data = data or {}
    rows = [[first_header, series_header, "Count", "Total for Row", "Percent of Row"]]
    for name, counts in data.items():
        counts = counts or {}
        total = sum((counts.get(column, 0) or 0) for column in columns)
        for column in columns:
            value = counts.get(column, 0) or 0
            rows.append([
                name,
                column,
                value,
                total,
                round((value / total * 100), 2) if total else 0,
            ])
    return rows


def g2a_rows(data=None, first_header="Activity"):
    data = data or []
    rows = [[first_header, "Little or No Input", "Input into Some Decisions", "Input into Most or All Decisions"]]
    for item in data:
        rows.append([
            item.get("activity") or "Unknown",
            item.get("little_no_input", 0),
            item.get("some_input", 0),
            item.get("most_all_input", 0),
        ])
    return rows


def g2a_percent_rows(data=None, first_header="Activity"):
    data = data or []
    rows = [[first_header, "Little or No Input %", "Input into Some Decisions %", "Input into Most or All Decisions %", "Total"]]
    for item in data:
        little_no_input = item.get("little_no_input", 0)
        some_input = item.get("some_input", 0)
        most_all_input = item.get("most_all_input", 0)
        total = little_no_input + some_input + most_all_input
        rows.append([
            item.get("activity") or "Unknown",
            round((little_no_input / total * 100), 2) if total else 0,
            round((some_input / total * 100), 2) if total else 0,
            round((most_all_input / total * 100), 2) if total else 0,
            total,
        ])
    return rows


def self_efficacy_rows(questions_data=None):
    questions_data = questions_data or {}
    question_titles = {
        "1171": "When facing difficult tasks, they are certain that they will accomplish them",
        "1172": "In general, think that they can obtain outcomes that are important to them",
        "1173": "Are confident that they can perform effectively on many different tasks",
        "1174": "Even when things are tough, they can perform quite well",
        "1175": "Plan tasks carefully",
        "1176": "Look forward to their work",
        "1177": "Continue to work hard even when they face obstacles",
        "1178": "It is important for them to do whatever they are doing as well as they can even if it is not popular with people around them",
        "1179": "I am motivated to do my work better than I have done in the past",
    }
    question_ids = ["1171", "1172", "1173", "1174", "1175", "1176", "1177", "1178", "1179"]
    rows = [["Question", "Strongly Agree", "Agree", "Neither Agree or Disagree", "Disagree", "Strongly Disagree", "Total"]]
    for question_id in question_ids:
        item = questions_data.get(question_id, {})
        values = [
            item.get("strongly_agree", 0),
            item.get("agree", 0),
            item.get("neither", 0),
            item.get("disagree", 0),
            item.get("strongly_disagree", 0),
        ]
        rows.append([question_titles.get(question_id, f"Question {question_id}"), *values, sum(values)])
    return rows


SELF_EFFICACY_CHART_TITLES = {
    "1171": 'Distribution of women who "When facing difficult tasks, they are certain that they will accomplish them"',
    "1172": 'Distribution of women who "In general, think that they can obtain outcomes that are important to them."',
    "1173": 'Distribution of women who "Are confident that they can perform effectively on many different tasks".',
    "1174": 'Distribution of women who think "Even when things are tough, they can perform quite well."',
    "1175": 'Distribution of women who "plan tasks carefully."',
    "1176": 'Distribution of women who "look forward to their work."',
    "1177": 'Distribution of women who "continue to work hard even when they face obstacles."',
    "1178": 'Distribution of women who think "It is important for them to do whatever they\'re doing as well as they can even if it isn\'t popular with people around them."',
    "1179": 'Distribution of women who think "I am motivated to do my work better than I have done in the past."',
}


def self_efficacy_question_rows(question_id, questions_data=None):
    questions_data = questions_data or {}
    item = questions_data.get(str(question_id), {})
    rows = [["Response", "Count", "Percent of Total"]]
    total = (
        item.get("strongly_agree", 0)
        + item.get("agree", 0)
        + item.get("neither", 0)
        + item.get("disagree", 0)
        + item.get("strongly_disagree", 0)
    )
    rows.append(["Strongly Agree", item.get("strongly_agree", 0), round((item.get("strongly_agree", 0) / total * 100), 2) if total else 0])
    rows.append(["Agree", item.get("agree", 0), round((item.get("agree", 0) / total * 100), 2) if total else 0])
    rows.append(["Neither Agree or Disagree", item.get("neither", 0), round((item.get("neither", 0) / total * 100), 2) if total else 0])
    rows.append(["Disagree", item.get("disagree", 0), round((item.get("disagree", 0) / total * 100), 2) if total else 0])
    rows.append(["Strongly Disagree", item.get("strongly_disagree", 0), round((item.get("strongly_disagree", 0) / total * 100), 2) if total else 0])
    rows.append(["Total", total, 100 if total else 0])
    return rows


def chart_index_rows(entries):
    rows = [["Sheet", "Chart Title", "Notes"]]
    rows.extend(entries)
    return rows


def _apply_common_submission_filters(queryset, filters, user=None):
    from .selectors import apply_submission_visibility

    queryset = queryset.filter(
        survey_id=6,
        status="completed",
        is_archived=False,
    ).distinct()
    queryset = apply_submission_visibility(queryset, user)

    year = filters.get("year")
    if year:
        queryset = queryset.filter(submitted_at__year=year)

    date_from = filters.get("date_from")
    if date_from:
        from datetime import datetime
        try:
            for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    dt_from = datetime.strptime(date_from, fmt)
                    break
                except ValueError:
                    continue
            else:
                dt_from = None
            if dt_from:
                queryset = queryset.filter(submitted_at__gte=dt_from)
        except Exception:
            pass

    date_to = filters.get("date_to")
    if date_to:
        from datetime import datetime, timedelta
        try:
            dt_to = None
            for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
                try:
                    dt_to = datetime.strptime(date_to, fmt)
                    break
                except ValueError:
                    continue
            if not dt_to:
                try:
                    dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
                except ValueError:
                    pass
            if dt_to:
                queryset = queryset.filter(submitted_at__lte=dt_to)
        except Exception:
            pass

    return queryset


def _count_submissions_for_text_value(submissions, question, value):
    if not question or not value:
        return 0
    text_ids = submissions.filter(
        answers__question=question,
        answers__text_answer=value,
    ).values_list("id", flat=True)
    option_ids = submissions.filter(
        answers__question=question,
        answers__selected_options__text=value,
    ).values_list("id", flat=True)
    return len(set(text_ids) | set(option_ids))


def geography_breakdown_rows(filters, user=None):
    from . import views

    state_question = Question.objects.filter(id=709).first()
    district_question = Question.objects.filter(id=710).first()
    block_question = Question.objects.filter(id=711).first()

    all_submissions = _apply_common_submission_filters(Submission.objects.all(), filters, user)
    filtered_submission_ids = views.filter_submissions_by_geography(
        all_submissions,
        state_question,
        district_question,
        block_question,
        filters.get("state"),
        filters.get("district"),
        filters.get("block"),
    )
    submissions = all_submissions
    if filtered_submission_ids is not None:
        submissions = submissions.filter(id__in=filtered_submission_ids)

    total = submissions.count()
    if filters.get("district"):
        level = "Block"
        values = views.get_blocks_with_submission_data(
            submissions,
            state_question,
            district_question,
            block_question,
            filters.get("state"),
            filters.get("district"),
        )
        question = block_question
    elif filters.get("state"):
        level = "District"
        values = views.get_districts_with_submission_data(
            submissions,
            state_question,
            district_question,
            filters.get("state"),
        )
        question = district_question
    else:
        level = "State"
        values = views.get_states_with_submission_data(submissions, state_question)
        question = state_question

    rows = [[level, "Women Surveyed", "Percent of Filtered Total"]]
    for item in values:
        name = item.get("text") if isinstance(item, dict) else str(item)
        count = _count_submissions_for_text_value(submissions, question, name)
        rows.append([name, count, round((count / total * 100), 2) if total else 0])

    if filters.get("block"):
        block_value = filters.get("block")
        rows = [["Block", "Women Surveyed", "Percent of Filtered Total"], [block_value, total, 100 if total else 0]]
    elif len(rows) == 1:
        rows.append(["No data", 0, 0])

    rows.append(["Total", total, 100 if total else 0])
    return rows


def _per_indicator_map_rows(user, filters, indicator):
    """Build rows for one indicator's map sheet. Always falls back through
    state_filter -> default -> national so the sheet never ends up blank
    when the user-selected geography has no data for that indicator."""
    from . import views

    base = {k: v for k, v in (filters or {}).items() if k != "indicator_id"}
    base["indicator_id"] = str(indicator.id)

    attempts = []
    if base.get("district") or base.get("block"):
        return geography_breakdown_rows(filters, user)
    if base.get("state"):
        with_state_filter = dict(base)
        with_state_filter["state_filter"] = base["state"]
        attempts.append(with_state_filter)
        attempts.append(base)
    else:
        attempts.append(base)
    national = {"indicator_id": str(indicator.id)}
    if national not in attempts:
        attempts.append(national)

    for params in attempts:
        try:
            payload = _call_json_view(views.api_map_data, user, params)
        except Exception:
            continue
        map_data = payload.get("map_data") or payload.get("data") or []
        if map_data:
            return geography_rows(map_data)

    return [
        ["State", "Average Empowerment", "Empowerment Percentage", "Empowered Count", "Disempowered Count", "Total Count"],
        ["No data for the selected filters", 0, 0, 0, 0, 0],
    ]


def geography_map_export_rows(user, filters, indicator_id=None, fallback_map_data=None):
    from . import views

    fallback_map_data = fallback_map_data or []
    if filters.get("district") or filters.get("block"):
        scope_rows = geography_breakdown_rows(filters, user)
        return [
            ["Scope", "Block-wise scoped geography export"],
            ["Note", "Map API data is only available down to district level, so this view falls back to the filtered block breakdown."],
            [],
            *scope_rows,
        ]

    if filters.get("state") and indicator_id:
        map_filters = dict(filters)
        map_filters["indicator_id"] = indicator_id
        map_filters["state_filter"] = filters.get("state")
        map_payload = _call_json_view(views.api_map_data, user, map_filters)
        map_data = map_payload.get("map_data") or map_payload.get("data") or []
        if map_data:
            return geography_rows(map_data)

    return geography_rows(fallback_map_data)


def grouped_indicator_export_rows(filters, demographic="age", user=None):
    from . import views

    demographic_questions = {
        "age": 736,
        "literacy": 737,
        "shg_membership": 1319,
        "fpo_membership": 719,
        "land_ownership": 1298,
        "house_membership": 740,
        "caste": 738,
    }

    state_question = Question.objects.filter(id=709).first()
    district_question = Question.objects.filter(id=710).first()
    block_question = Question.objects.filter(id=711).first()
    demographic_question = Question.objects.filter(id=demographic_questions.get(demographic, 736)).first()

    if not demographic_question:
        return grouped_indicator_rows([], demographic)

    all_submissions = _apply_common_submission_filters(Submission.objects.all(), filters, user)
    filtered_submission_ids = views.filter_submissions_by_geography(
        all_submissions,
        state_question,
        district_question,
        block_question,
        filters.get("state"),
        filters.get("district"),
        filters.get("block"),
    )

    submissions = _apply_common_submission_filters(
        Submission.objects.filter(indicator_results__success=True),
        filters,
        user,
    )
    if filtered_submission_ids is not None:
        submissions = submissions.filter(id__in=filtered_submission_ids)

    grouped_data = views.get_optimized_grouped_indicator_data(submissions, demographic_question)
    return grouped_indicator_rows(grouped_data, demographic)


def _call_json_view(view_func, user, params):
    from . import views

    allowed_hosts = [host for host in getattr(settings, "ALLOWED_HOSTS", []) if host and host != "*"]
    host = allowed_hosts[0] if allowed_hosts else "localhost"
    request = RequestFactory(HTTP_HOST=host).get("/survey/report/export/internal/", data=params)
    request.user = user if user is not None else AnonymousUser()
    response = view_func(request)
    if response.status_code >= 400:
        raise RuntimeError(f"{view_func.__name__} returned {response.status_code}")
    payload = json.loads(response.content.decode("utf-8"))
    if not payload.get("success", False):
        raise RuntimeError(payload.get("error") or f"{view_func.__name__} returned success=false")
    return payload


def workbook_spec_for_report(user, report_type, filters):
    from . import views

    if report_type not in REPORT_EXPORT_TYPES:
        raise ValueError(f"Unsupported report type: {report_type}")

    meta = REPORT_EXPORT_TYPES[report_type]
    filters = filters or {}
    report_title = meta["title"]
    normalized_filters = dict(filters)
    normalized_filters["report_type"] = report_type
    sheets = [
        ("Summary", metadata_rows(report_title, normalized_filters)),
        ("Filters", filter_rows(normalized_filters)),
        ("Geography Breakdown", geography_breakdown_rows(normalized_filters, user)),
    ]

    if report_type == "overview":
        overview = _call_json_view(views.api_survey_data, user, filters)
        all_indicators = list(
            Indicator.objects.filter(survey_id=6, is_active=True).only("id", "name").order_by("id")
        )
        demographics = [
            ("age", "Age"),
            ("literacy", "Literacy"),
            ("shg_membership", "SHG Membership"),
            ("fpo_membership", "FPO Membership"),
            ("land_ownership", "Land Ownership"),
            ("house_membership", "House Membership"),
            ("caste", "Caste"),
        ]

        chart_index = [
            ["Statistics", "Summary statistics cards", "Counts shown in the KPI cards"],
            ["Pct Empowered", "Percentage of Empowered Women", "Indicator bar chart"],
        ]
        for ind in all_indicators:
            chart_index.append([
                _safe_sheet_name(f"Map - {ind.name}"),
                "Empowered women by Geography",
                f"Map indicator: {ind.name}",
            ])
        for key, label in demographics:
            chart_index.append([
                _safe_sheet_name(f"Dist - {label}"),
                "Distribution by Women",
                f"Demographic: {label}",
            ])

        sheets.append(("Chart Index", chart_index_rows(chart_index)))
        sheets.append(("Statistics", stats_rows(overview.get("stats"))))
        sheets.append(("Pct Empowered", indicator_rows(overview.get("indicators"))))

        for ind in all_indicators:
            sheets.append((
                _safe_sheet_name(f"Map - {ind.name}"),
                _per_indicator_map_rows(user, filters, ind),
            ))

        for key, label in demographics:
            sheets.append((
                _safe_sheet_name(f"Dist - {label}"),
                grouped_indicator_export_rows(filters, key, user),
            ))

        total_steps = 3 + len(all_indicators) + len(demographics)
        return {"title": report_title, "filename": meta["filename"], "sheets": sheets, "total_steps": total_steps}

    if report_type == "module_g2a":
        data = _call_json_view(views.api_module_g2a_data, user, filters)
        sheets.append(("Chart Index", chart_index_rows([
            ["Women by Activities", "Distribution of Women by Activities", ""],
            ["Output Decision Input", "Distribution of Women by input in decisions on how much output from activity to keep for home consumption than selling?", ""],
            ["Income Decision Input", "Distribution of women over decision making on what to do with the income generated from the activity", ""],
            ["Participation in Decision", "Distribution of women feeling like they could participate in decision making for following activities if they wanted to.", ""],
        ])))
        sheets.append(("Statistics", stats_rows(data.get("stats"))))
        sheets.append(("Women by Activities", counts_with_percent_rows(
            {item.get("activity"): item.get("count", 0) for item in data.get("activity_distribution_data", [])},
            "Activity",
            "Count",
        )))
        sheets.append(("Output Decision Input", g2a_rows(data.get("output_decision_data"))))
        sheets.append(("Output Decision %", g2a_percent_rows(data.get("output_decision_data"))))
        sheets.append(("Income Decision Input", g2a_rows(data.get("income_decision_data"))))
        sheets.append(("Income Decision %", g2a_percent_rows(data.get("income_decision_data"))))
        sheets.append(("Participation in Decision", g2a_rows(data.get("participation_data"))))
        sheets.append(("Participation %", g2a_percent_rows(data.get("participation_data"))))
        return {"title": report_title, "filename": meta["filename"], "sheets": sheets, "total_steps": 1}

    if report_type == "self_efficacy":
        data = _call_json_view(views.api_self_efficacy_data, user, filters)
        sheets.append(("Chart Index", chart_index_rows([
            [f"Q{question_id}", title, "One sheet per rendered chart"]
            for question_id, title in SELF_EFFICACY_CHART_TITLES.items()
        ])))
        sheets.append(("Statistics", stats_rows(data.get("stats"))))
        sheets.append(("Self Efficacy Questions", self_efficacy_rows(data.get("questions_data"))))
        for question_id, title in SELF_EFFICACY_CHART_TITLES.items():
            sheets.append((f"Q{question_id}", [["Chart Title", title], [], *self_efficacy_question_rows(question_id, data.get("questions_data"))]))
        return {"title": report_title, "filename": meta["filename"], "sheets": sheets, "total_steps": 1}

    if report_type == "module_g3a":
        data = _call_json_view(views.api_g3a_productive_capital_data, user, filters)
        sheets.append(("Chart Index", chart_index_rows([
            ["Ownership by Item", "Distribution of Women across type of ownership of income generating item", ""],
            ["Who Owns the Item", "Distribution of Women across who owns the item", ""],
        ])))
        sheets.append(("Statistics", stats_rows(data.get("stats"))))
        sheets.append(("Ownership by Item", counts_with_percent_rows(
            data.get("aggregated_ownership_by_item"),
            "Item",
            "Count",
        )))
        sheets.append(("Ownership Detail", object_matrix_rows(
            data.get("ownership_by_item"),
            "Item",
            ["Solely", "Jointly", "Solely And Jointly", "No ownership", "Not in the household"],
        )))
        sheets.append(("Ownership Detail %", object_matrix_percent_rows(
            data.get("ownership_by_item"),
            "Item",
            ["Solely", "Jointly", "Solely And Jointly", "No ownership", "Not in the household"],
        )))
        sheets.append(("Who Owns the Item", counts_with_percent_rows(
            data.get("aggregated_uses_by_item"),
            "Item",
            "Count",
        )))
        sheets.append(("Who Owns Detail", object_matrix_rows(
            data.get("mostly_uses_by_item"),
            "Item",
            ["Self", "Spouse or partner", "Other HH members", "Others"],
        )))
        sheets.append(("Who Owns Detail %", object_matrix_percent_rows(
            data.get("mostly_uses_by_item"),
            "Item",
            ["Self", "Spouse or partner", "Other HH members", "Others"],
        )))
        return {"title": report_title, "filename": meta["filename"], "sheets": sheets, "total_steps": 1}

    if report_type == "module_g3b":
        data = _call_json_view(views.api_g3b_financial_services_data, user, filters)
        sheets.append(("Chart Index", chart_index_rows([
            ["Loan Borrowed Type", "Distribution of HH based on type of loan taken", "Rendered chart"],
            ["Borrow Loan Decision", "Distribution of women based on the ability to take the decision to borrow loan", "Rendered chart"],
            ["Supporting Loan Type", "Distribution of HH based on type of loan taken", "Supporting counts"],
            ["Supporting Make Dec", "Detailed decision maker breakdown", "Supporting detail"],
        ])))
        sheets.append(("Statistics", stats_rows(data.get("stats"))))
        sheets.append(("Loan Borrowed Type", counts_with_percent_rows(
            data.get("aggregated_borrowed_by_source"),
            "Loan Source",
            "Count",
        )))
        sheets.append(("Borrow Loan Decision", counts_with_percent_rows(
            data.get("aggregated_decision_by_source"),
            "Loan Source",
            "Count",
        )))
        sheets.append(("Supporting Loan Type", counts_with_percent_rows(data.get("loan_type_counts"), "Loan Source", "Count")))
        sheets.append(("Supporting Borrowed", object_matrix_rows(
            data.get("borrowed_by_source"),
            "Loan Source",
            ["Cash", "Kind", "Cash And Kind"],
        )))
        sheets.append(("Supporting Borrow %", object_matrix_percent_rows(
            data.get("borrowed_by_source"),
            "Loan Source",
            ["Cash", "Kind", "Cash And Kind"],
        )))
        sheets.append(("Supporting Make Dec", object_matrix_rows(
            data.get("make_decision_by_source"),
            "Loan Source",
            [
                "Self",
                "Spouse (or Partner) In HH",
                "Spouse (or Partner) Outside HH",
                "Other HH Member",
                "Non-HH Member, Relative",
                "Non-HH Member, No Relation",
                "Government or Other Institution",
                "Clan (Group of Relatives)",
            ],
        )))
        sheets.append(("Supporting Made Dec", object_matrix_rows(
            data.get("made_decision_by_source"),
            "Loan Source",
            ["Self", "Spouse or partner", "Other HH members", "Others"],
        )))
        sheets.append(("Supporting Made %", object_matrix_percent_rows(
            data.get("made_decision_by_source"),
            "Loan Source",
            ["Self", "Spouse or partner", "Other HH members", "Others"],
        )))
        return {"title": report_title, "filename": meta["filename"], "sheets": sheets, "total_steps": 1}

    if report_type == "module_g4":
        data = _call_json_view(views.api_g4_time_allocation_data, user, filters)
        sheets.append(("Chart Index", chart_index_rows([
            ["Paid and Unpaid", "Distribution of women involved in paid and unpaid activities", ""],
            ["FPO Impact on Time", "Impact of FPO membership on time allocation in women's workdays", ""],
        ])))
        sheets.append(("Statistics", stats_rows(data.get("stats"))))
        sheets.append(("Paid and Unpaid", object_matrix_rows(data.get("paid_unpaid_by_activity"), "Activity", ["Paid", "Unpaid"])))
        sheets.append(("Paid and Unpaid %", object_matrix_percent_rows(data.get("paid_unpaid_by_activity"), "Activity", ["Paid", "Unpaid"])))
        sheets.append(("FPO Impact on Time", object_matrix_rows(data.get("fpo_impact_by_activity"), "Activity", ["Decreased", "No change", "Increased"])))
        sheets.append(("FPO Impact %", object_matrix_percent_rows(data.get("fpo_impact_by_activity"), "Activity", ["Decreased", "No change", "Increased"])))
        return {"title": report_title, "filename": meta["filename"], "sheets": sheets, "total_steps": 1}

    data = _call_json_view(views.api_g6_physical_mobility_data, user, filters)
    sheets.append(("Chart Index", chart_index_rows([
        ["Places Mobility Chart", "Distribution of frequency of places visited by women", "Rendered chart uses decision-by-place dataset"],
        ["Supporting Place Count", "Place count summary", "Supporting detail"],
        ["Supporting Visit Freq", "Visit frequency by place", "Supporting detail"],
    ])))
    sheets.append(("Statistics", stats_rows(data.get("stats"))))
    sheets.append(("Places Mobility Chart", counts_with_percent_rows(
        data.get("aggregated_decision_by_place"),
        "Place",
        "Count",
    )))
    sheets.append(("Places Mobility Detail", object_matrix_rows(
        data.get("decision_by_place"),
        "Place",
        ["Self", "Spouse or partner", "Other HH members", "Others"],
    )))
    sheets.append(("Places Mobility %", object_matrix_percent_rows(
        data.get("decision_by_place"),
        "Place",
        ["Self", "Spouse or partner", "Other HH members", "Others"],
    )))
    sheets.append(("Supporting Place Count", counts_with_percent_rows(data.get("place_counts"), "Place", "Count")))
    sheets.append(("Supporting Visit Freq", object_matrix_rows(
        data.get("frequency_by_place"),
        "Place",
        ["Everyday", "Every Week At Least Once", "Every 2 Weeks At Least Once", "Every Month At Least Once", "Sometimes"],
    )))
    sheets.append(("Supporting Visit %", object_matrix_percent_rows(
        data.get("frequency_by_place"),
        "Place",
        ["Everyday", "Every Week At Least Once", "Every 2 Weeks At Least Once", "Every Month At Least Once", "Sometimes"],
    )))
    return {"title": report_title, "filename": meta["filename"], "sheets": sheets, "total_steps": 1}
