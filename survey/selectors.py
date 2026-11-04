"""Shared queryset builders so the submissions list pages and the Celery
export worker filter on identical logic."""

from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q
from django.utils import timezone

from .models import Submission, Survey

# "Name of CLF" question (survey 6 only). Submissions store the CLF either as
# free text or as a selected dropdown option on this question.
CLF_QUESTION_ID = 3383


def _as_marker_list(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    return list(value or [])


_PARTNER_RULE_CACHE_ATTR = "_partner_visibility_rule_cache"
_NO_CACHED_RULE = object()


def _partner_rules() -> list[dict]:
    return list(getattr(settings, "PARTNER_VISIBILITY_RULES", []) or [])


def _rule_group_name(rule) -> str:
    return (rule.get("group_name") or "").strip()


def get_partner_group_names() -> list[str]:
    """Group names defined by the partner rules, in priority order."""
    names = []
    for rule in _partner_rules():
        name = _rule_group_name(rule)
        if name and name not in names:
            names.append(name)
    return names


def _user_matches_rule(user, rule) -> bool:
    group_name = _rule_group_name(rule)
    if group_name and getattr(user, "pk", None):
        if user.groups.filter(name__iexact=group_name).exists():
            return True
    username_lower = (user.username or "").lower()
    email_lower = (getattr(user, "email", "") or "").lower()
    markers = _as_marker_list(rule.get("username_markers"))
    domains = _as_marker_list(rule.get("email_domains"))
    return any(marker.lower() in username_lower for marker in markers) or any(
        domain.lower() in email_lower for domain in domains
    )


def get_partner_visibility_rule(user) -> dict | None:
    """Return the visibility rule matching this user, or None.

    Rules live in settings.PARTNER_VISIBILITY_RULES. Group membership is the
    primary signal so an explicit partner group scopes every member; the
    username/email markers stay as a fallback for accounts provisioned before
    the groups existed.

    The result is memoised on the user instance because callers hit this on
    every submission query.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    cached = getattr(user, _PARTNER_RULE_CACHE_ATTR, _NO_CACHED_RULE)
    if cached is not _NO_CACHED_RULE:
        return cached
    matched = None
    for rule in _partner_rules():
        if _user_matches_rule(user, rule):
            matched = rule
            break
    try:
        setattr(user, _PARTNER_RULE_CACHE_ATTR, matched)
    except (AttributeError, TypeError):
        pass
    return matched


def clear_partner_scope_cache(user) -> None:
    """Drop memoised partner state after changing the user's group membership."""
    if user is None:
        return
    try:
        delattr(user, _PARTNER_RULE_CACHE_ATTR)
    except (AttributeError, TypeError):
        pass


def _rule_user_filter(rule):
    """Build the User queryset filter matching everyone inside one partner."""
    query = Q()
    has_criteria = False
    group_name = _rule_group_name(rule)
    if group_name:
        query |= Q(groups__name__iexact=group_name)
        has_criteria = True
    for marker in _as_marker_list(rule.get("username_markers")):
        if marker:
            query |= Q(username__icontains=marker)
            has_criteria = True
    for domain in _as_marker_list(rule.get("email_domains")):
        if domain:
            query |= Q(email__icontains=domain)
            has_criteria = True
    return query if has_criteria else None


def filter_users_by_partner_scope(users, actor):
    """Limit a User queryset to the actor's partner, when the actor is scoped.

    Partner accounts must never see or manage another partner's users, so this
    is applied to the user-management list, its counters, and every lookup that
    takes a user id from the request.
    """
    rule = get_partner_visibility_rule(actor)
    if rule is None:
        return users
    query = _rule_user_filter(rule)
    if query is None:
        return users.none()
    return users.filter(query).distinct()


def user_in_partner_scope(actor, target) -> bool:
    """True when the actor may see and manage the target user."""
    rule = get_partner_visibility_rule(actor)
    if rule is None or target is None:
        return True
    return _user_matches_rule(target, rule)


def enforce_partner_user_scope(actor, target) -> None:
    """Raise Http404 when a partner user tries to manage another partner."""
    if not user_in_partner_scope(actor, target):
        from django.http import Http404

        raise Http404("User not available")


def assign_partner_group_if_needed(creator, new_user) -> bool:
    """Cascade the creator's partner group onto a user they just created.

    Why: without this a partner admin/manager could create an account that
    falls outside their partner and then lose visibility of it — or, worse,
    create an unscoped account that sees every partner's data. Returns True
    when the group was applied.
    """
    if creator is None or new_user is None:
        return False
    rule = get_partner_visibility_rule(creator)
    if rule is None:
        return False
    group_name = _rule_group_name(rule)
    if not group_name:
        return False
    from django.contrib.auth.models import Group

    group = Group.objects.filter(name__iexact=group_name).first()
    if group is None:
        group = Group.objects.create(name=group_name)
    new_user.groups.add(group)
    clear_partner_scope_cache(new_user)
    return True


def get_visibility_start_date(user):
    """Return the matched partner's earliest visible submission date."""
    rule = get_partner_visibility_rule(user)
    return rule.get("start_date") if rule else None


def has_visibility_cutoff(user) -> bool:
    """True when the user's account is date-restricted by a partner rule."""
    return get_visibility_start_date(user) is not None


def get_visibility_cache_bucket(user) -> str:
    """Cache bucket key so each partner only reads its own cached responses."""
    rule = get_partner_visibility_rule(user)
    name = (rule or {}).get("name")
    return name or "default"


def apply_submission_visibility(submissions, user):
    """Restrict a Submission queryset to the user's partner cutoff when applicable."""
    cutoff = get_visibility_start_date(user)
    if cutoff is None:
        return submissions
    return submissions.filter(submitted_at__date__gte=cutoff)


def apply_answer_visibility(answers, user):
    """Restrict a SubmissionAnswer queryset through the same partner cutoff."""
    cutoff = get_visibility_start_date(user)
    if cutoff is None:
        return answers
    return answers.filter(submission__submitted_at__date__gte=cutoff)


def is_submission_visible(user, submission) -> bool:
    """True when the submission falls inside the user's partner window.

    Use this for records fetched by primary key, which bypass the queryset
    filter. Must agree with apply_submission_visibility's `submitted_at__date`
    semantics: Postgres resolves that lookup in the project timezone, so the
    comparison here converts to local time too.
    """
    cutoff = get_visibility_start_date(user)
    if cutoff is None:
        return True
    submitted_at = getattr(submission, "submitted_at", None)
    if submitted_at is None:
        return True
    if timezone.is_aware(submitted_at):
        submitted_at = timezone.localtime(submitted_at)
    return submitted_at.date() >= cutoff


def enforce_submission_visibility(user, submission):
    """Raise Http404 when a restricted user tries to view a pre-cutoff submission.

    Why: Detail / indicator views fetch submissions by primary key and bypass
    the list-level filter; this guard keeps single-record access consistent
    with apply_submission_visibility.
    """
    if submission is None:
        return
    if not is_submission_visible(user, submission):
        from django.http import Http404
        raise Http404("Submission not available")


def parse_submissions_date_filter(value: str, *, is_end: bool = False):
    """Parse a date / datetime filter string into a timezone-aware datetime.

    Accepts the formats emitted by the reports/submissions flatpickr ("Y-m-d H:i",
    "Y-m-dTH:i") and bare "Y-m-d" from older bookmarked URLs. When only a date is
    given and is_end=True, rolls forward 1 day so __lte captures the whole day.
    Naive datetimes are made aware in the project's current timezone so that
    comparisons against submitted_at (UTC-stored) match the user's local intent.
    Returns None if the value can't be parsed.
    """
    if not value:
        return None
    value = value.strip()
    naive = None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    if naive is None:
        try:
            naive = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return None
        if is_end:
            naive += timedelta(days=1)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def is_survey_access_restricted(user) -> bool:
    """True when the account is limited to its explicitly assigned surveys.

    Two triggers:
    * the user is a partner account (see PARTNER_VISIBILITY_RULES), which is always
      scoped to what was granted — being a superuser does not widen this; and
    * the user has survey_access_restricted ticked, which lets a non-partner
      Manager be pinned to specific projects.

    An account with no grants is therefore limited to nothing, which is the
    intended behaviour: access has to be given explicitly.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if get_partner_visibility_rule(user) is not None:
        return True
    try:
        return bool(user.profile.survey_access_restricted)
    except ObjectDoesNotExist:
        return False


def get_partner_admin_survey_id(user):
    """Survey id a partner admin is pinned to, or None if the rule doesn't apply.

    "Admin" means full-admin role: a superuser, or a member of the `admin`
    group. Plain `is_staff` is deliberately excluded — Managers and Mentors are
    staff too, and they must keep their own (assignment-based) access.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    rule = get_partner_visibility_rule(user)
    if rule is None:
        return None
    is_admin = bool(
        user.is_superuser
        or user.groups.filter(name__iexact="admin").exists()
    )
    if not is_admin:
        return None
    # A rule's admin_survey_id wins over both role access and assigned projects.
    return rule.get("admin_survey_id")


def is_partner_admin(user) -> bool:
    """True when the account is a partner admin pinned to one survey."""
    return get_partner_admin_survey_id(user) is not None


def get_partner_admin_surveys(user):
    """The single survey a partner admin may access, or None when not pinned."""
    survey_id = get_partner_admin_survey_id(user)
    if survey_id is None:
        return None
    return Survey.objects.filter(pk=survey_id)


def get_assigned_surveys_qs(user):
    """Surveys the user owns or is explicitly invited to."""
    return Survey.objects.filter(
        Q(created_by=user) | Q(invitations__user=user, invitations__is_active=True)
    ).distinct()


def get_selectable_surveys(user):
    """Surveys offered in the submissions-list "Filter by Survey" dropdown.

    Partner admins get their hardcoded survey. Other partner accounts see only
    the surveys an admin actually granted them, so the dropdown reflects their
    assignments rather than every survey their role could technically open.
    Everyone else keeps the full role-based list.
    """
    accessible = get_accessible_surveys(user)
    if is_partner_admin(user):
        return accessible
    if get_partner_visibility_rule(user) is None:
        return accessible
    granted = get_assigned_surveys_qs(user)
    return accessible.filter(pk__in=granted.values("pk"))


def get_accessible_surveys(user):
    """Single source of truth for which surveys a user may see.

    Role-based access is the default; an account flagged as survey-restricted
    falls back to its explicit assignments regardless of role. A partner admin
    is hardcoded to a single survey and wins over everything else.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return Survey.objects.filter(visibility="public")

    partner_admin_surveys = get_partner_admin_surveys(user)
    if partner_admin_surveys is not None:
        return partner_admin_surveys

    if is_survey_access_restricted(user):
        return get_assigned_surveys_qs(user)

    is_supervisor = user.groups.filter(name="Supervisor").exists()
    is_manager = user.groups.filter(name="Manager").exists()
    is_dashboard_viewer = user.groups.filter(name="DashboardViewer").exists()
    if user.is_superuser or is_manager or is_supervisor or is_dashboard_viewer:
        return Survey.objects.all()
    return get_assigned_surveys_qs(user)


def _accessible_surveys_qs(user):
    """Surveys the user is allowed to see in the cross-survey submissions list."""
    return get_accessible_surveys(user)


def build_submissions_queryset(user, filters: dict, survey=None):
    """Build a Submission queryset matching the filters shown on the submissions pages.

    filters keys (all optional):
        search, status, archive ('active'|'archived'|'all'),
        show_duplicates (bool), date_from, date_to,
        state, district, clf, survey (id, only used when survey arg is None)
    """

    archive = (filters.get("archive") or "active")
    search = filters.get("search") or ""
    status = filters.get("status") or ""
    show_duplicates = bool(filters.get("show_duplicates"))
    date_from = filters.get("date_from") or ""
    date_to = filters.get("date_to") or ""
    state = filters.get("state") or ""
    district = filters.get("district") or ""
    clf = filters.get("clf") or ""

    if survey is not None:
        submissions = Submission.objects.filter(survey=survey)
    else:
        accessible = _accessible_surveys_qs(user)
        submissions = Submission.objects.filter(survey__in=accessible)
        raw_survey_id = filters.get("survey")
        if raw_survey_id:
            submissions = submissions.filter(survey_id=raw_survey_id)

    submissions = apply_submission_visibility(submissions, user)

    if archive == "active":
        submissions = submissions.filter(is_archived=False)
    elif archive == "archived":
        submissions = submissions.filter(is_archived=True)

    if status:
        submissions = submissions.filter(status=status)

    dt_from = parse_submissions_date_filter(date_from)
    if dt_from:
        submissions = submissions.filter(submitted_at__gte=dt_from)
    dt_to = parse_submissions_date_filter(date_to, is_end=True)
    if dt_to:
        submissions = submissions.filter(submitted_at__lte=dt_to)

    if state:
        submissions = submissions.filter(
            answers__question__key="state",
            answers__text_answer=state,
        )
    if district:
        submissions = submissions.filter(
            answers__question__key="district",
            answers__text_answer=district,
        )

    # CLF (Cluster Level Federation) lives on the "Name of CLF" question (id 3383,
    # survey 6 only). Unlike state/district it has no question `key`, and its value
    # may be stored either as free text (text_answer) or as a selected dropdown
    # option (selected_options), so match both. If the question doesn't exist in
    # this environment, no submission will match and the filter simply returns none.
    if clf:
        submissions = submissions.filter(
            Q(answers__question_id=CLF_QUESTION_ID, answers__text_answer=clf)
            | Q(answers__question_id=CLF_QUESTION_ID, answers__selected_options__text=clf)
        )

    if search:
        submissions = submissions.filter(
            Q(user__username__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(ip_address__icontains=search)
            | Q(survey__title__icontains=search)
        )

    # Only show submissions that actually have answers (matches list views)
    submissions = submissions.annotate(answer_count=Count("answers")).filter(answer_count__gt=0)

    if show_duplicates:
        duplicate_titles = (
            submissions.values("computed_title")
            .annotate(title_count=Count("id"))
            .filter(title_count__gt=1, computed_title__isnull=False)
            .values_list("computed_title", flat=True)
        )
        submissions = submissions.filter(computed_title__in=duplicate_titles)

    # Ordering: duplicates view groups by title so they're adjacent
    if show_duplicates:
        submissions = submissions.order_by("computed_title", "-submitted_at")
    else:
        submissions = submissions.order_by("-submitted_at")

    return submissions.distinct()
