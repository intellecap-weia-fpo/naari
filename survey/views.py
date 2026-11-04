import os
from django.utils.translation import gettext_lazy as _t

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import update_session_auth_hash
from graphql_jwt.exceptions import JSONWebTokenError
from graphql_jwt.shortcuts import get_user_by_token
from graphql_jwt.utils import get_credentials
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.contrib import messages
from . import indicator_sandbox
from .decorators import (
  admin_required, dashboard_viewer_or_admin_required, staff_or_admin_required, manager_or_permission_required,
  url_cache_decorator, login_required_json
)
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse, HttpResponse
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.utils.translation import get_language
import json
from django.forms import modelformset_factory, inlineformset_factory
from django.views.generic import CreateView, UpdateView, DeleteView
from guardian.decorators import permission_required
from guardian.shortcuts import assign_perm, get_objects_for_user
from auth_app.permissions import get_user_role
from django.core.exceptions import ValidationError
from packaging.utils import _
from six import StringIO
from django.db.models import Count, Avg, Q, Case, When, Value, IntegerField, FloatField, F
from django.db.models.functions import Coalesce
from django.core.cache import caches
import hashlib

from .models import (
  QuestionCondition,
  OptionCondition,
  ReportExportJob,
  Survey,
  SurveyPage,
  Question,
  QuestionOption,
  QuestionTranslation,
  QuestionOptionTranslation,
  Submission,
  SubmissionAnswer,
  SurveyInvitation,
  IndicatorResult,
  Indicator,
  IndicatorScript,
  SubmissionExportJob,
  SurveyInsightCache,
)
from .insights_cache import (
  INSIGHTS_SURVEY_ID,
  apply_visibility_insight_filters,
  build_filtered_insight_submissions,
  get_or_create_insight_cache,
  normalize_insight_filters,
  serialize_insight_groups,
)
from django import forms

import csv
import pandas as pd
from datetime import datetime, timedelta

SUBMITTED_SURVEY_STATUS = "completed"


def _request_tenant_schema(request):
  tenant = getattr(request, "tenant", None)
  schema_name = getattr(tenant, "schema_name", None)
  return schema_name if schema_name and schema_name != "public" else None


def _enforce_survey_access(user, survey, include_public=False):
  """Raise Http404 when the user may not manage this survey.

  Why: several survey management routes are permission-decorated, and that
  decorator deliberately bypasses the check for Managers and staff. An account
  restricted to its assigned projects must still be blocked here. Public
  visibility is not enough on management pages — only assignment is.
  """
  from django.http import Http404

  if survey is None or survey.is_accessible_by_user(user, include_public=include_public):
    return
  raise Http404("Survey not available")


def _can_change_managed_user_password(actor, target_user=None):
  actor_role = get_user_role(actor)

  if actor_role == "admin":
    return True

  if actor_role == "manager" and target_user is not None:
    if actor.id == target_user.id:
      return False
    return get_user_role(target_user) != "admin"

  return False


try:
  from graphql_relay import from_global_id
except ImportError:
  from_global_id = None


def decode_relay_id(relay_id):
  """
    Decode a relay ID if it appears to be a GraphQL global ID.
    Returns the decoded ID if it's a valid relay ID, otherwise returns the original ID.
    """
  if from_global_id is None:
    return relay_id

  if isinstance(relay_id, str) and len(relay_id) > 10:
    try:
      _, decoded_id = from_global_id(relay_id)
      return decoded_id
    except:
      return relay_id
  return relay_id


REPORT_TYPE_META = {
    "overview": {
        "module_label": "Overview",
        "title": "Overview",
        "description": "Analyzing women's participation and empowerment insights",
        "card_color": "yellow",
        "icon": "bar-chart",
        "card_description": "Indicator-level summary is available in the overview section.",
    },
    "module_g2a": {
        "module_label": "MODULE G2(A)",
        "title": "Role in household decision-making around livelihood activities",
        "description": "Analyzing women's role in household decision-making around livelihood activities",
        "card_color": "yellow",
        "icon": "clipboard",
        "card_description": "Measures agency and influence in livelihood and income-related decisions.",
    },
    "self_efficacy": {
        "module_label": "MODULE G7(B)",
        "title": "New General Self-efficacy scale",
        "description": "Measuring self-efficacy and confidence levels among women",
        "card_color": "green",
        "icon": "hand-rock-o",
        "card_description": "Captures confidence and perceived ability to handle challenging situations.",
    },
    "module_g3a": {
        "module_label": "MODULE G3(A)",
        "title": "Access to Productive Capital",
        "description": "Analyzing women's access to and ownership of productive assets",
        "card_color": "red",
        "icon": "truck",
        "card_description": "Focuses on ownership and access to land and productive assets.",
    },
    "module_g3b": {
        "module_label": "MODULE G3(B)",
        "title": "Access to Financial Services",
        "description": "Analyzing women's access to financial services and credit",
        "card_color": "yellow",
        "icon": "bank",
        "card_description": "Tracks women's participation in and decisions around financial services.",
    },
    "module_g4": {
        "module_label": "MODULE G4",
        "title": "Time allocation and agency",
        "description": "Analyzing how women allocate time across work, leisure, and household tasks",
        "card_color": "red",
        "icon": "clock-o",
        "card_description": "Assesses work burden, unpaid care load, and overall work-life balance.",
    },
    "module_g6": {
        "module_label": "MODULE G6",
        "title": "Physical Mobility",
        "description": "Analyzing women's freedom to travel and access public spaces",
        "card_color": "yellow",
        "icon": "map-marker",
        "card_description": "Measures freedom of movement and ability to visit important places.",
    },
}

# Rich, indicator-specific card descriptions keyed by the indicator name
# (normalised to lowercase). These take precedence over the generic per-report
# fallback so each indicator card surfaces a meaningful summary.
INDICATOR_CARD_DESCRIPTIONS = {
    "input in livelihood decisions": (
        "Measures women's active participation in household decisions on crops, "
        "inputs, and expenditure — a core marker of agricultural agency."
    ),
    "autonomy in income": (
        "Assesses whether women earn independent income from farm or non-farm "
        "activities and have the freedom to decide how that income is earned and used."
    ),
    "control over use of income": (
        "Evaluates women's authority over how household income is spent, revealing "
        "gendered power dynamics in financial decision-making within agricultural households."
    ),
    "ownership of land and other assets": (
        "Tracks formal and informal ownership or use rights over land, livestock, "
        "equipment, and productive assets — revealing stark gender disparities in resource control."
    ),
    "access to and decisions on financial service": (
        "Captures women's access to savings, credit, insurance, and input loans — and "
        "critically, who decides how these financial instruments are used in the household."
    ),
    "work balance": (
        "Quantifies the total time burden of paid agricultural work plus unpaid domestic "
        "and care labour, making visible the \"double burden\" disproportionately carried by farm women."
    ),
    "visiting important locations": (
        "Measures women's ability to travel unaccompanied to markets, banks, health "
        "facilities, and government offices — a key dimension of physical mobility and social agency."
    ),
    "self-efficacy": (
        "Measures women's belief in their own capacity to make autonomous decisions and "
        "handle challenges — a psychological empowerment dimension from the WEAI framework."
    ),
    "group membership": (
        "Tracks active membership in SHGs, FPOs, cooperatives, or community groups — "
        "collective action being a crucial pathway to sustained economic and social empowerment for farm women."
    ),
}

REPORT_EXPORT_FILTER_FIELDS = (
    "programme", "state", "district", "block", "clf", "year", "date_from", "date_to", "demographic", "map_indicator",
)


def get_indicator_report_type(indicator):
  """Map an indicator to one of the existing report sections."""
  indicator_name = (indicator.name or "").lower()
  script_name = (indicator.script.name if indicator.script else "").lower()
  combined = f"{indicator_name} {script_name}"

  if "self-efficacy" in combined or "self efficacy" in combined:
    return "self_efficacy"
  if "financial" in combined or "loan" in combined or "credit" in combined:
    return "module_g3b"
  if "ownership" in combined or "asset" in combined or "land" in combined or "capital" in combined:
    return "module_g3a"
  if "work balance" in combined or "time" in combined:
    return "module_g4"
  if "mobility" in combined or "visiting" in combined or "location" in combined:
    return "module_g6"
  if "group membership" in combined or "membership" in combined:
    return "module_g2a"
  if "livelihood" in combined or "income" in combined or "decision" in combined or "autonomy" in combined:
    return "module_g2a"

  # Indicators without a dedicated module chart fall back to the overview section.
  return "overview"


def build_indicator_report_entries(indicators):
  """Build UI metadata for indicator cards and report selector entries."""
  entries = []

  for indicator in indicators:
    report_type = get_indicator_report_type(indicator)
    report_meta = REPORT_TYPE_META.get(report_type, REPORT_TYPE_META["overview"])
    lookup_name = (indicator.name or "").replace(" Script", "").strip().lower()
    card_description = (
        INDICATOR_CARD_DESCRIPTIONS.get(lookup_name)
        or indicator.description
        or report_meta["card_description"]
    )
    entries.append(
      {
        "indicator_id": indicator.id,
        "title": indicator.name,
        "description": card_description,
        "report_type": report_type,
        "module_label": report_meta["module_label"],
        "report_title": report_meta["title"],
        "report_description": report_meta["description"],
        "color": report_meta["card_color"],
        "icon": report_meta["icon"],
      }
    )

  return entries


@login_required
@dashboard_viewer_or_admin_required
def home(request):
    """Home dashboard page after login - shows stats, recent submissions, and surveys"""
    from django.db.models import Count

    # Question IDs for geographic data (same as report page)
    state_question_id = 709
    district_question_id = 710
    block_question_id = 711

    from .selectors import (
        apply_answer_visibility,
        apply_submission_visibility,
        get_visibility_start_date,
    )

    # Get submissions for survey 6 with successful indicator results (same as report page)
    submissions = apply_submission_visibility(
        Submission.objects.filter(
            survey_id=6,
            status=SUBMITTED_SURVEY_STATUS,
            is_archived=False,
            indicator_results__success=True,
        ),
        request.user,
    ).distinct()

    # Calculate stats using the EXACT same logic as report page
    try:
        state_question = Question.objects.get(id=state_question_id)
        district_question = Question.objects.get(id=district_question_id)
        block_question = Question.objects.get(id=block_question_id)

        # Use the same calculate_survey_stats function as report page
        stats = calculate_survey_stats(
            submissions,
            state_question,
            district_question,
            block_question,
            None,  # selected_state_id
            None,  # selected_district_id
            None   # selected_block_id
        )
    except Question.DoesNotExist:
        # Fallback if questions don't exist
        total_states = apply_answer_visibility(
            SubmissionAnswer.objects.filter(
                question_id=state_question_id,
                text_answer__isnull=False,
            ),
            request.user,
        ).values('text_answer').distinct().count()

        total_districts = apply_answer_visibility(
            SubmissionAnswer.objects.filter(
                question_id=district_question_id,
                text_answer__isnull=False,
            ),
            request.user,
        ).values('text_answer').distinct().count()

        total_blocks = apply_answer_visibility(
            SubmissionAnswer.objects.filter(
                question_id=block_question_id,
                text_answer__isnull=False,
            ),
            request.user,
        ).values('text_answer').distinct().count()

        # Total women surveyed (total submissions with successful indicators)
        total_women_surveyed = submissions.count()

        stats = {
            'total_states_count': total_states,
            'total_districts_count': total_districts,
            'total_blocks_count': total_blocks,
            'total_women_surveyed': total_women_surveyed,
        }

    # Recent submissions (last 10 from survey 6)
    recent_submissions = apply_submission_visibility(
        Submission.objects.filter(
            survey_id=6,
            status='completed',
        ),
        request.user,
    ).select_related('survey', 'user').order_by('-submitted_at')[:10]

    # Get surveys with submission count (top 10 by submission count)
    submission_count_filter = Q(submissions__status='completed')
    cutoff = get_visibility_start_date(request.user)
    if cutoff:
        submission_count_filter &= Q(submissions__submitted_at__date__gte=cutoff)
    surveys = Survey.objects.annotate(
        submission_count=Count('submissions', filter=submission_count_filter)
    ).order_by('-submission_count', '-created_at')[:10]

    # Empowerment Dimensions Data — one card per active indicator
    indicators = (
      Indicator.objects.filter(survey_id=6, is_active=True)
      .select_related("script")
      .order_by("id")
    )
    empowerment_dimensions = build_indicator_report_entries(indicators)

    # Survey types summary.
    # Count responses with the same filters as the Reports page so the numbers match.
    survey_type_ids = [6]
    survey_type_map = {s.id: s for s in Survey.objects.filter(id__in=survey_type_ids)}
    survey_types = []
    for sid in survey_type_ids:
        s = survey_type_map.get(sid)
        if s is None:
            continue
        responses_count = apply_submission_visibility(
            Submission.objects.filter(
                survey_id=sid,
                status=SUBMITTED_SURVEY_STATUS,
                is_archived=False,
                indicator_results__success=True,
            ),
            request.user,
        ).distinct().count()
        survey_types.append({
            'title': s.title,
            'description': s.description,
            'question_count': Question.objects.filter(survey_id=sid).count(),
            'module_count': SurveyPage.objects.filter(survey_id=sid).count(),
            'submission_count': responses_count,
            'indicator_count': Indicator.objects.filter(survey_id=sid, is_active=True).count(),
        })

    context = {
        'stats': stats,
        'recent_submissions': recent_submissions,
        'surveys': surveys,
        'empowerment_dimensions': empowerment_dimensions,
        'survey_types': survey_types,
    }

    return render(request, 'survey/home.html', context)


class SurveyForm(forms.ModelForm):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    # Limit render_field to questions within this survey
    if self.instance and self.instance.pk:
      self.fields['render_field'].queryset = Question.objects.filter(
        survey=self.instance
      ).order_by('page__order', 'order', 'id')
    else:
      # For new surveys, show no questions initially
      self.fields['render_field'].queryset = Question.objects.none()

  class Meta:
    model = Survey
    fields = [
      "title",
      "description",
      "consent_text",
      "end_date",
      "is_active",
      "allow_multiple_submissions",
      "display_mode",
      "visibility",
      "render_field",
    ]
    widgets = {
      "title": forms.TextInput(
        attrs={
          "class": "form-control",
          "maxlength": "255",
          "minlength": "3",
          "required": True,
          "placeholder": "Enter survey title (3-255 characters)",
          "pattern": ".{3,255}",
          "title": "Survey title must be between 3 and 255 characters",
        }
      ),
      "description": forms.Textarea(
        attrs={
          "class": "form-control",
          "rows": 3,
          "maxlength": "2000",
          "placeholder": "Enter survey description (optional, max 2000 characters)",
          "title": "Description cannot exceed 2000 characters",
        }
      ),
      "is_active": forms.CheckboxInput(
        attrs={"class": "form-check-input", "title": "Check to make survey available for submissions"}
      ),
      "allow_multiple_submissions": forms.CheckboxInput(
        attrs={"class": "form-check-input", "title": "Allow users to submit multiple responses"}
      ),
      "display_mode": forms.Select(
        attrs={
          "class": "form-control",
          "required": True,
          "title": "Choose how questions are displayed to users",
        }
      ),
      "visibility": forms.Select(
        attrs={"class": "form-control", "required": True, "title": "Choose who can access this survey"}
      ),
      "end_date": forms.DateInput(
        attrs={"class": "form-control", "type": "date", "title": "Optional end date for the survey"}
      ),
      "consent_text": forms.Textarea(
        attrs={
          "class": "form-control",
          "rows": 5,
          "maxlength": "5000",
          "placeholder": "Enter consent text (optional, max 5000 characters)",
          "title": "Consent text displayed to respondents before they start the survey",
        }
      ),
      "render_field": forms.Select(
        attrs={
          "class": "form-control",
          "title": "Select a question whose answer will be used as the title for submissions",
        }
      ),
    }

  def clean_title(self):
    title = self.cleaned_data.get("title")
    if title and len(title.strip()) < 3:
      raise ValidationError("Survey title must be at least 3 characters long (excluding whitespace).")
    return title.strip() if title else title

  def clean_description(self):
    description = self.cleaned_data.get("description")
    if description and len(description) > 2000:
      raise ValidationError("Description cannot exceed 2000 characters.")
    return description

  def clean_consent_text(self):
    consent_text = self.cleaned_data.get("consent_text")
    if consent_text and len(consent_text) > 5000:
      raise ValidationError("Consent text cannot exceed 5000 characters.")
    return consent_text


class SurveyPageForm(forms.ModelForm):
  class Meta:
    model = SurveyPage
    fields = ["title", "description", "order"]
    widgets = {
      "title": forms.TextInput(
        attrs={
          "class": "form-control",
          "maxlength": "255",
          "minlength": "2",
          "placeholder": "Enter page title (optional, 2-255 characters)",
          "pattern": ".{2,255}",
          "title": "Page title must be between 2 and 255 characters if provided",
        }
      ),
      "description": forms.Textarea(
        attrs={
          "class": "form-control",
          "rows": 2,
          "maxlength": "1000",
          "placeholder": "Enter page description (optional, max 1000 characters)",
          "title": "Description cannot exceed 1000 characters",
        }
      ),
      "order": forms.NumberInput(
        attrs={
          "class": "form-control",
          "min": "0",
          "max": "9999",
          "step": "1",
          "required": True,
          "placeholder": "Enter page order (0-9999)",
          "title": "Page order must be between 0 and 9999",
        }
      ),
    }

  def clean_title(self):
    title = self.cleaned_data.get("title")
    if title and len(title.strip()) < 2:
      raise ValidationError("Page title must be at least 2 characters long (excluding whitespace).")
    return title.strip() if title else title

  def clean_order(self):
    order = self.cleaned_data.get("order")
    if order is not None and order < 0:
      raise ValidationError("Page order cannot be negative.")
    return order


class QuestionForm(forms.ModelForm):
  def __init__(self, *args, **kwargs):
    # Allow passing survey to limit filter options
    survey = kwargs.pop("survey", None)
    super().__init__(*args, **kwargs)
    # Limit filter_options_question choices to questions within same survey and option-based
    if "filter_options_question" in self.fields:
      qs = Question.objects.all()
      instance = kwargs.get("instance")
      if instance and instance.survey_id:
        qs = qs.filter(survey=instance.survey)
      elif survey:
        qs = qs.filter(survey=survey)
      # Only option-based questions are valid sources
      qs = qs.filter(question_type__in=["radio", "checkbox", "dropdown"])
      # Exclude self if instance
      if instance and instance.pk:
        qs = qs.exclude(pk=instance.pk)
      self.fields["filter_options_question"].queryset = qs.order_by("page__order", "order", "id")

  class Meta:
    model = Question
    fields = [
      "key",
      "text",
      "hint",
      "evaluator_condition",
      "question_type",
      "is_required",
      "order",
      "filter_options_question",
      "min_length",
      "max_length",
      "min_value",
      "max_value",
      "validation_pattern",
      "validation_message",
      "group",
    ]
    widgets = {
      "key": forms.TextInput(
        attrs={
          "class": "form-control",
          "maxlength": "100",
          "placeholder": "Question key (optional, for template variables)",
          "title": "Unique identifier for template variable references",
        }
      ),
      "evaluator_condition": forms.TextInput(
        attrs={
          "class": "form-control",
          "maxlength": "255",
          "placeholder": "Optional evaluator condition (primarily for consent)",
          "title": "Optional expression/flag used by evaluators",
        }
      ),
      "filter_options_question": forms.Select(
        attrs={
          "class": "form-control",
          "title": "If set, options for this question are filtered from the selected options of the chosen question",
        }
      ),
      "hint": forms.Textarea(
        attrs={
          "class": "form-control",
          "rows": 2,
          "maxlength": "1000",
          "placeholder": "Optional hint to guide respondents (max 1000 characters)",
          "title": "Hint text to help respondents answer the question",
        }
      ),
      "text": forms.Textarea(
        attrs={
          "class": "form-control",
          "rows": 3,
          "maxlength": "1000",
          "required": True,
          "placeholder": "Enter your question text (up to 1000 characters)",
          "title": "Question text cannot exceed 1000 characters",
        }
      ),
      "question_type": forms.Select(
        attrs={"class": "form-control", "required": True, "title": "Select the type of input for this question"}
      ),
      "is_required": forms.CheckboxInput(
        attrs={"class": "form-check-input", "title": "Check to make this question mandatory for respondents"}
      ),
      "order": forms.NumberInput(
        attrs={
          "class": "form-control",
          "min": "0",
          "max": "9999",
          "step": "1",
          "required": True,
          "placeholder": "Enter question order (0-9999)",
          "title": "Question order must be between 0 and 9999",
        }
      ),
      "min_length": forms.NumberInput(
        attrs={
          "class": "form-control",
          "min": "0",
          "placeholder": "Minimum characters (for text fields)",
          "title": "Minimum number of characters required",
        }
      ),
      "max_length": forms.NumberInput(
        attrs={
          "class": "form-control",
          "min": "1",
          "placeholder": "Maximum characters (for text fields)",
          "title": "Maximum number of characters allowed",
        }
      ),
      "min_value": forms.NumberInput(
        attrs={
          "class": "form-control",
          "step": "0.01",
          "placeholder": "Minimum value (for number fields)",
          "title": "Minimum numeric value allowed",
        }
      ),
      "max_value": forms.NumberInput(
        attrs={
          "class": "form-control",
          "step": "0.01",
          "placeholder": "Maximum value (for number fields)",
          "title": "Maximum numeric value allowed",
        }
      ),
      "validation_pattern": forms.TextInput(
        attrs={
          "class": "form-control",
          "placeholder": "e.g., ^[A-Z]{2}[0-9]{4}$ (for custom validation)",
          "title": "Regular expression pattern for custom validation",
        }
      ),
      "validation_message": forms.TextInput(
        attrs={
          "class": "form-control",
          "placeholder": "Custom error message for validation failure",
          "title": "Error message shown when validation fails",
        }
      ),
      "group": forms.TextInput(
        attrs={
          "class": "form-control",
          "maxlength": "100",
          "placeholder": "Group name (optional, for organizing questions)",
          "title": "Optional group name for organizing related questions",
        }
      ),
    }

  def clean_text(self):
    text = self.cleaned_data.get("text")
    if text and len(text.strip()) < 1:
      raise ValidationError("Question text must be at least 1 characters long (excluding whitespace).")
    import re

    if text and not re.search(r"[a-zA-Z0-9]", text.strip()):
      raise ValidationError("Question text must contain at least one alphanumeric character.")
    return text

  def clean_order(self):
    order = self.cleaned_data.get("order")
    if order is not None and order < 0:
      raise ValidationError("Question order cannot be negative.")
    return order

  def clean(self):
    cleaned_data = super().clean()
    question_type = cleaned_data.get("question_type")
    min_length = cleaned_data.get("min_length")
    max_length = cleaned_data.get("max_length")
    min_value = cleaned_data.get("min_value")
    max_value = cleaned_data.get("max_value")
    validation_pattern = cleaned_data.get("validation_pattern")

    # Validate length constraints for text-based questions
    if question_type in ["text", "textarea", "email"]:
      if min_length is not None and max_length is not None and min_length > max_length:
        raise ValidationError("Minimum length cannot be greater than maximum length.")

    # Validate value constraints for number questions
    if question_type == "number":
      if min_value is not None and max_value is not None and min_value > max_value:
        raise ValidationError("Minimum value cannot be greater than maximum value.")

    # Validate regex pattern
    if validation_pattern:
      try:
        import re

        re.compile(validation_pattern)
      except re.error:
        raise ValidationError({"validation_pattern": "Invalid regular expression pattern."})

    return cleaned_data


class QuestionOptionForm(forms.ModelForm):
  class Meta:
    model = QuestionOption
    fields = ["text", "order"]
    widgets = {
      "text": forms.TextInput(
        attrs={
          "class": "form-control",
          "maxlength": "255",
          "minlength": "1",
          "required": True,
          "placeholder": "Enter option text (1-255 characters)",
          "pattern": ".{1,255}",
          "title": "Option text must be between 1 and 255 characters",
        }
      ),
      "order": forms.NumberInput(
        attrs={
          "class": "form-control",
          "min": "0",
          "max": "9999",
          "step": "1",
          "required": True,
          "placeholder": "Enter option order (0-9999)",
          "title": "Option order must be between 0 and 9999",
        }
      ),
    }

  def clean_text(self):
    text = self.cleaned_data.get("text")
    if not text or len(text.strip()) == 0:
      raise ValidationError("Option text cannot be empty.")
    return text.strip()

  def clean_order(self):
    order = self.cleaned_data.get("order")
    if order is not None and order < 0:
      raise ValidationError("Option order cannot be negative.")
    return order


class QuestionTranslationForm(forms.ModelForm):
  class Meta:
    model = QuestionTranslation
    fields = ["language", "text"]
    widgets = {
      "language": forms.Select(
        attrs={"class": "form-control", "required": True, "title": "Select the language for this translation"}
      ),
      "text": forms.Textarea(
        attrs={
          "class": "form-control",
          "rows": 3,
          "maxlength": "1000",
          "minlength": "5",
          "required": True,
          "placeholder": "Enter question translation (5-1000 characters)",
          "title": "Translation text must be between 5 and 1000 characters",
        }
      ),
    }

  def clean_text(self):
    text = self.cleaned_data.get("text")
    if text and len(text.strip()) < 5:
      raise ValidationError("Translation text must be at least 5 characters long (excluding whitespace).")
    import re

    if text and not re.search(r"[a-zA-Z0-9]", text.strip()):
      raise ValidationError("Translation text must contain at least one alphanumeric character.")
    return text


class QuestionOptionTranslationForm(forms.ModelForm):
  class Meta:
    model = QuestionOptionTranslation
    fields = ["language", "text"]
    widgets = {
      "language": forms.Select(
        attrs={"class": "form-control", "required": True, "title": "Select the language for this translation"}
      ),
      "text": forms.TextInput(
        attrs={
          "class": "form-control",
          "maxlength": "255",
          "minlength": "1",
          "required": True,
          "placeholder": "Enter option translation (1-255 characters)",
          "pattern": ".{1,255}",
          "title": "Translation text must be between 1 and 255 characters",
        }
      ),
    }

  def clean_text(self):
    text = self.cleaned_data.get("text")
    if not text or len(text.strip()) == 0:
      raise ValidationError("Translation text cannot be empty.")
    return text.strip()


class SurveyResponseForm(forms.Form):
  """Dynamic form for survey responses with validation"""

  def __init__(self, survey, *args, **kwargs):
    self.survey = survey
    super().__init__(*args, **kwargs)

    # Dynamically create fields based on survey questions
    for question in survey.questions.all():
      # Skip display-only questions (note/consent) as they don't collect answers
      if question.question_type in ["note", "consent"]:
        continue

      field_name = f"question_{question.id}"

      if question.question_type == "text":
        # Get validation attributes from question
        attrs = question.get_validation_attrs()
        attrs.update({"class": "form-control", "placeholder": "Enter your answer"})

        self.fields[field_name] = forms.CharField(
          label=question.text,
          required=question.is_required,
          min_length=question.min_length,
          max_length=question.max_length or 500,
          widget=forms.TextInput(attrs=attrs),
        )

      elif question.question_type == "textarea":
        attrs = question.get_validation_attrs()
        attrs.update({"class": "form-control", "rows": 4, "placeholder": "Enter your detailed answer"})

        self.fields[field_name] = forms.CharField(
          label=question.text,
          required=question.is_required,
          min_length=question.min_length,
          max_length=question.max_length or 2000,
          widget=forms.Textarea(attrs=attrs),
        )

      elif question.question_type == "email":
        attrs = question.get_validation_attrs()
        attrs.update({"class": "form-control", "placeholder": "Enter a valid email address"})
        # Add default email pattern if no custom pattern is set
        if "pattern" not in attrs:
          attrs["pattern"] = r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$"

        self.fields[field_name] = forms.EmailField(
          label=question.text,
          required=question.is_required,
          min_length=question.min_length,
          max_length=question.max_length or 254,
          widget=forms.EmailInput(attrs=attrs),
        )

      elif question.question_type == "number":
        attrs = question.get_validation_attrs()
        attrs.update({"class": "form-control", "placeholder": "Enter a number"})

        self.fields[field_name] = forms.DecimalField(
          label=question.text,
          required=question.is_required,
          max_digits=15,
          decimal_places=2,
          min_value=question.min_value,
          max_value=question.max_value,
          widget=forms.NumberInput(attrs=attrs),
        )

      elif question.question_type == "date":
        self.fields[field_name] = forms.DateField(
          label=question.text,
          required=question.is_required,
          widget=forms.DateInput(
            attrs={"class": "form-control", "type": "date", "title": "Please select a date"}
          ),
        )

      elif question.question_type == "time":
        self.fields[field_name] = forms.TimeField(
          label=question.text,
          required=question.is_required,
          widget=forms.TimeInput(
            attrs={"class": "form-control", "type": "time", "title": "Please select a time"}
          ),
        )

      elif question.question_type == "datetime":
        self.fields[field_name] = forms.DateTimeField(
          label=question.text,
          required=question.is_required,
          widget=forms.DateTimeInput(
            attrs={
              "class": "form-control",
              "type": "datetime-local",
              "title": "Please select a date and time",
            }
          ),
        )

      elif question.question_type == "radio":
        choices = [(option.id, option.text) for option in question.options.all()]
        self.fields[field_name] = forms.ChoiceField(
          label=question.text,
          required=question.is_required,
          choices=choices,
          widget=forms.RadioSelect(attrs={"class": "form-check-input", "title": "Please select one option"}),
        )

      elif question.question_type == "checkbox":
        choices = [(option.id, option.text) for option in question.options.all()]
        self.fields[field_name] = forms.MultipleChoiceField(
          label=question.text,
          required=question.is_required,
          choices=choices,
          widget=forms.CheckboxSelectMultiple(
            attrs={"class": "form-check-input", "title": "Select one or more options"}
          ),
        )

      elif question.question_type == "dropdown":
        choices = [("", "Select an option")] + [(option.id, option.text) for option in question.options.all()]
        self.fields[field_name] = forms.ChoiceField(
          label=question.text,
          required=question.is_required,
          choices=choices,
          widget=forms.Select(
            attrs={"class": "form-control", "title": "Please select an option from the dropdown"}
          ),
        )

  def clean(self):
    cleaned_data = super().clean()

    # Additional validation for survey responses
    for question in self.survey.questions.all():
      field_name = f"question_{question.id}"
      value = cleaned_data.get(field_name)

      # Validate required fields
      if question.is_required and not value:
        self.add_error(field_name, "This question is required.")

      # Validate email format
      if question.question_type == "email" and value:
        import re

        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_regex, value):
          self.add_error(field_name, "Please enter a valid email address.")

    return cleaned_data


@dashboard_viewer_or_admin_required
def survey_list(request):
  search_query = request.GET.get("search", "")
  status_filter = request.GET.get("status", "")

  from .selectors import get_accessible_surveys
  # Accounts restricted to their assigned projects see only those here too.
  surveys = get_accessible_surveys(request.user).filter(is_deleted=False)

  if search_query:
    surveys = surveys.filter(Q(title__icontains=search_query) | Q(description__icontains=search_query))

  if status_filter == 'active':
    surveys = surveys.filter(is_active=True)
  elif status_filter == 'inactive':
    surveys = surveys.filter(is_active=False)

  paginator = Paginator(surveys, 10)
  page_number = request.GET.get("page")
  page_obj = paginator.get_page(page_number)

  context = {
    "page_obj": page_obj,
    "search_query": search_query,
    "status": status_filter,
  }
  return render(request, "survey/survey_list.html", context)


@admin_required
def survey_create(request):
  if request.method == "POST":
    form = SurveyForm(request.POST)
    if form.is_valid():
      survey = form.save(commit=False)
      survey.created_by = request.user
      survey.save()

      assign_perm("view_survey", request.user, survey)
      assign_perm("change_survey", request.user, survey)
      assign_perm("delete_survey", request.user, survey)

      messages.success(request, f'Survey "{survey.title}" created successfully!')
      return redirect("survey:survey_detail", pk=survey.pk)
  else:
    form = SurveyForm()

  return render(request, "survey/survey_form.html", {
    "form": form,
    "page_title": _t("Create Project"),
    "page_subtitle": _t("Create a new project form"),
    "title": _t("Create Project"),
  })


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "pk"), raise_exception=True)
def survey_detail(request, pk):
  survey = get_object_or_404(Survey, pk=pk)
  _enforce_survey_access(request.user, survey)
  pages = survey.pages.prefetch_related("questions__options__sub_questions").all()

  # Prepare pages data for JavaScript
  import re

  # Collect all keys from questions for variable suggestions
  all_keys = []
  for q in survey.questions.all():
    if q.key:
      all_keys.append(q.key)
  pages_data = []
  for page in pages:
    page_data = {
      "id": page.id,
      "title": page.title or "",
      "description": page.description or "",
      "order": page.order,
      "questions_count": page.questions.count(),
      "questions": [],
    }

    for question in page.questions.all():
      question_data = {
        "id": question.id,
        "key": question.key or "",
        "group": question.group or "",
        "text": question.text,
        "hint": question.hint or "",
        "evaluator_condition": question.evaluator_condition or "",
        "type": question.question_type,
        "type_display": question.get_question_type_display(),
        "is_required": question.is_required,
        "is_conditional": question.is_conditional,
        "order": question.order,
        "requires_options": question.requires_options(),
        "filter_options_question_id": question.filter_options_question_id,
        "min_length": question.min_length,
        "max_length": question.max_length,
        "min_value": float(question.min_value) if question.min_value else None,
        "max_value": float(question.max_value) if question.max_value else None,
        "validation_pattern": question.validation_pattern or "",
        "validation_message": question.validation_message or "",
        # Available keys for template insertion (exclude this question's key)
        "available_keys": [k for k in all_keys if k and k != (question.key or "")],
        # Initial unknown var markers for load (live updates handled client-side)
        "unknown_vars_text": list(
          {
            m.group(1)
            for m in re.finditer(r"{{\s*([A-Za-z_][\w]*)\s*}}", question.text or "")
            if m.group(1) not in all_keys
          }
        ),
        "unknown_vars_hint": list(
          {
            m.group(1)
            for m in re.finditer(r"{{\s*([A-Za-z_][\w]*)\s*}}", question.hint or "")
            if m.group(1) not in all_keys
          }
        ),
        "options": [],
      }

      for option in question.options.all():
        option_data = {
          "id": option.id,
          "text": option.text,
          "value": option.value,
          "order": option.order,
          "is_conditional": option.is_conditional,
          "option_warning": option.option_warning or None,
          "option_warning_text": option.option_warning_text or "",
          "sub_questions": [],
        }

        for sub_question in option.sub_questions.all():
          sub_question_data = {
            "id": sub_question.id,
            "text": sub_question.text,
            "type": sub_question.question_type,
            "is_required": sub_question.is_required,
            "order": sub_question.order,
          }
          option_data["sub_questions"].append(sub_question_data)

        question_data["options"].append(option_data)

      page_data["questions"].append(question_data)

    pages_data.append(page_data)

  import json

  pages_json = json.dumps(pages_data)

  context = {
    "survey": survey,
    "pages": pages,
    "pages_json": pages_json,
    "LANGUAGES": settings.LANGUAGES,
  }
  response = render(request, "survey/survey_detail.html", context)
  # The survey builder uses Alpine expressions in HTML attributes. django-htmlmin
  # entity-encodes operators in those attributes under DEBUG=False, which breaks
  # controls like the language selector in production.
  response.minify_response = False
  return response


@login_required
@admin_required
@manager_or_permission_required("survey.change_survey", (Survey, "pk", "pk"), raise_exception=True)
def survey_edit(request, pk):
  survey = get_object_or_404(Survey, pk=pk)
  _enforce_survey_access(request.user, survey)

  if request.method == "POST":
    form = SurveyForm(request.POST, instance=survey)
    if form.is_valid():
      survey = form.save()
      messages.success(request, f'Survey "{survey.title}" updated successfully!')
      return redirect("survey:survey_detail", pk=survey.pk)
  else:
    form = SurveyForm(instance=survey)

  return render(request, "survey/survey_form.html", {"form": form, "survey": survey, "title": _t("Edit Project")})


@admin_required
@manager_or_permission_required("survey.delete_survey", (Survey, "pk", "pk"), raise_exception=True)
def survey_delete(request, pk):
  survey = get_object_or_404(Survey, pk=pk)
  _enforce_survey_access(request.user, survey)

  if request.method == "POST":
    survey_title = survey.title
    if not survey.is_deleted:
      survey.is_active = False
      survey.is_deleted = True
      survey.save(update_fields=["is_active", "is_deleted"])
      messages.success(request, f'Survey "{survey_title}" deactivated successfully!')
    else:
      messages.info(request, f'Survey "{survey_title}" is already deactivated.')
    return redirect("survey:survey_list")

  return render(request, "survey/survey_confirm_delete.html", {"survey": survey})


@login_required
@require_POST
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "pk"), raise_exception=True)
def survey_clone(request, pk):
  """Clone a survey with a new name"""
  survey = get_object_or_404(Survey, pk=pk)
  _enforce_survey_access(request.user, survey)

  try:
    data = json.loads(request.body)
    new_title = data.get("new_title", "").strip()
  except (json.JSONDecodeError, ValueError):
    new_title = request.POST.get("new_title", "").strip()

  if not new_title:
    new_title = f"{survey.title} (Copy)"

  # Validate title length
  if len(new_title) < 3:
    return JsonResponse({
      "status": "error",
      "message": "Survey title must be at least 3 characters long."
    }, status=400)

  if len(new_title) > 255:
    return JsonResponse({
      "status": "error",
      "message": "Survey title cannot exceed 255 characters."
    }, status=400)

  try:
    # Clone the survey
    new_survey = survey.duplicate(new_title=new_title, created_by=request.user)

    # Assign permissions to the creator
    assign_perm("view_survey", request.user, new_survey)
    assign_perm("change_survey", request.user, new_survey)
    assign_perm("delete_survey", request.user, new_survey)

    messages.success(request, f'Survey "{new_survey.title}" cloned successfully!')

    return JsonResponse({
      "status": "success",
      "message": f'Survey "{new_survey.title}" cloned successfully!',
      "survey_id": new_survey.id,
      "redirect_url": reverse_lazy("survey:survey_detail", kwargs={"pk": new_survey.pk}).__str__()
    })
  except Exception as e:
    return JsonResponse({
      "status": "error",
      "message": f"Failed to clone survey: {str(e)}"
    }, status=500)


@login_required
@manager_or_permission_required("survey.change_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def page_create(request, survey_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)

  if request.method == "POST":
    form = SurveyPageForm(request.POST)
    if form.is_valid():
      page = form.save(commit=False)
      page.survey = survey
      page.save()
      messages.success(request, f'Page "{page.title or "Page " + str(page.order + 1)}" created successfully!')
      return redirect("survey:survey_detail", pk=survey.pk)
  else:
    form = SurveyPageForm()

  return render(request, "survey/page_form.html", {"form": form, "survey": survey, "title": "Create Page"})


@login_required
def page_edit(request, pk):
  page = get_object_or_404(SurveyPage, pk=pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", page.survey):
    return redirect("survey:survey_list")

  if request.method == "POST":
    form = SurveyPageForm(request.POST, instance=page)
    if form.is_valid():
      page = form.save()
      messages.success(request, f'Page "{page.title or "Page " + str(page.order + 1)}" updated successfully!')
      return redirect("survey:survey_detail", pk=page.survey.pk)
  else:
    form = SurveyPageForm(instance=page)

  return render(
    request, "survey/page_form.html", {"form": form, "page": page, "survey": page.survey, "title": "Edit Page"}
  )


@login_required
def page_delete(request, pk):
  page = get_object_or_404(SurveyPage, pk=pk)
  survey = page.survey

  # Check permission
  if not request.user.has_perm("survey.change_survey", page.survey):
    return redirect("survey:survey_list")

  if request.method == "POST":
    page_title = page.title or f"Page {page.order + 1}"
    page.delete()
    messages.success(request, f'Page "{page_title}" deleted successfully!')
    return redirect("survey:survey_detail", pk=survey.pk)

  return render(request, "survey/page_confirm_delete.html", {"page": page, "survey": survey})


@login_required
def page_detail(request, pk):
  page = get_object_or_404(SurveyPage, pk=pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", page.survey):
    return redirect("survey:survey_list")

  questions = page.questions.all()

  context = {
    "page": page,
    "survey": page.survey,
    "questions": questions,
  }
  return render(request, "survey/page_detail.html", context)


@login_required
def question_create(request, page_pk):
  page = get_object_or_404(SurveyPage, pk=page_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", page.survey):
    return redirect("survey:survey_list")

  # Get available questions for conditional logic (all questions in the same survey except current one)
  available_questions = (
    Question.objects.filter(survey=page.survey)
    .exclude(page__isnull=True)
    .select_related("page")
    .order_by("page__order", "order")
  )

  available_questions_data = [
    {"id": str(q.id), "text": f"Page {q.page.order + 1}: {q.text[:50]}{'...' if len(q.text) > 50 else ''}"}
    for q in available_questions
  ]

  if request.method == "POST":
    form = QuestionForm(request.POST, survey=page.survey)
    if form.is_valid():
      question = form.save(commit=False)
      question.survey = page.survey
      question.page = page
      question.save()
      messages.success(request, "Question created successfully!")
      return redirect("survey:page_detail", pk=page.pk)
  else:
    form = QuestionForm(survey=page.survey)

  return render(
    request,
    "survey/question_form.html",
    {
      "form": form,
      "page": page,
      "survey": page.survey,
      "title": "Create Question",
      "available_questions": available_questions,
      "available_questions_json": json.dumps(available_questions_data),
    },
  )


@login_required
def question_edit(request, pk):
  question = get_object_or_404(Question, pk=pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return redirect("survey:survey_list")

  # Get available questions for conditional logic (all questions in the same survey except current one)
  available_questions = (
    Question.objects.filter(survey=question.survey)
    .exclude(id=question.id)
    .exclude(page__isnull=True)
    .select_related("page")
    .order_by("page__order", "order")
  )

  available_questions_data = [
    {"id": str(q.id), "text": f"Page {q.page.order + 1}: {q.text[:50]}{'...' if len(q.text) > 50 else ''}"}
    for q in available_questions
  ]

  if request.method == "POST":
    # Preserve fields that are commonly omitted by inline partial-update requests
    data = request.POST.copy()
    if "filter_options_question" not in data:
      data["filter_options_question"] = question.filter_options_question_id or ""
    if "key" not in data:
      data["key"] = question.key or ""
    if "group" not in data:
      data["group"] = question.group or ""
    form = QuestionForm(data, instance=question)
    if form.is_valid():
      question = form.save()
      messages.success(request, "Question updated successfully!")
      return redirect("survey:page_detail", pk=question.page.pk if question.page else question.survey.pk)
    else:
      # Display form errors
      for field, errors in form.errors.items():
        for error in errors:
          messages.error(request, f"{field.capitalize()}: {error}")
  else:
    form = QuestionForm(instance=question)

  return render(
    request,
    "survey/question_form.html",
    {
      "form": form,
      "question": question,
      "page": question.page,
      "survey": question.survey,
      "title": "Edit Question",
      "available_questions": available_questions,
      "available_questions_json": json.dumps(available_questions_data),
    },
  )


@login_required
def question_delete(request, pk):
  question = get_object_or_404(Question, pk=pk)
  page = question.page
  survey = question.survey

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return redirect("survey:survey_list")

  if request.method == "POST":
    question_text = question.text[:50] + ("..." if len(question.text) > 50 else "")
    question.delete()
    messages.success(request, f'Question "{question_text}" deleted successfully!')
    return redirect("survey:page_detail", pk=page.pk if page else survey.pk)

  return render(
    request, "survey/question_confirm_delete.html", {"question": question, "page": page, "survey": survey}
  )


@login_required
@require_POST
def reorder_pages(request, survey_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    page_orders = data.get("page_orders", [])

    for item in page_orders:
      page_id = item.get("id")
      new_order = item.get("order")

      page = get_object_or_404(SurveyPage, id=page_id, survey=survey)
      page.order = new_order
      page.save(update_fields=["order"])

    return JsonResponse({"status": "success"})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def update_question_option(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_text = data.get("text", "").strip()
    option_value = (
      (data.get("value", None) or "").strip()
      if isinstance(data.get("value", None), str)
      else data.get("value", None)
    )
    option_value = (
      (data.get("value", None) or "").strip()
      if isinstance(data.get("value", None), str)
      else data.get("value", None)
    )

    if not option_text:
      return JsonResponse({"status": "error", "message": "Option text is required"}, status=400)

    option.text = option_text
    option.value = option_value
    option.save(update_fields=["text", "value"])

    return JsonResponse(
      {
        "status": "success",
        "option": {
          "id": option.id,
          "text": option.text,
          "value": option.value,
          "order": option.order,
          "option_warning": option.option_warning or None,
          "option_warning_text": option.option_warning_text or "",
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def submission_detail(request, survey_pk, submission_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Get all answers for this submission
  all_answers = (
    SubmissionAnswer.objects.filter(submission=submission)
    .select_related("question", "question__page")
    .prefetch_related("selected_options__sub_questions")
  )

  # Separate main questions from sub-questions
  # Main questions are those that don't have parent_options or have page/survey directly assigned
  main_questions = []
  sub_question_answers = {}

  # Build lookup of all sub-question IDs
  sub_question_ids = set()
  for answer in all_answers:
    if answer.selected_options.exists():
      for option in answer.selected_options.all():
        for sub_q in option.sub_questions.all():
          sub_question_ids.add(sub_q.id)

  # Organize answers
  for answer in all_answers:
    if answer.question.id in sub_question_ids:
      # This is a sub-question answer
      sub_question_answers[answer.question.id] = answer
    else:
      # This is a main question answer
      main_questions.append(answer)

  # Sort main questions by page order, then by question order within the page
  main_questions.sort(key=lambda a: (
    a.question.page.order if a.question.page else 9999,
    a.question.page.id if a.question.page else 9999,
    a.question.order,
    a.question.id,
  ))

  # Build organized structure grouped by page (module)
  from collections import OrderedDict
  pages_dict = OrderedDict()

  for main_answer in main_questions:
    page = main_answer.question.page
    page_key = page.id if page else 0

    if page_key not in pages_dict:
      pages_dict[page_key] = {
        "page": page,
        "page_title": page.title if page else "Other Questions",
        "page_description": page.description if page else "",
        "answers": [],
      }

    answer_data = {"answer": main_answer, "sub_answers": []}

    # If this answer has selected options, find their sub-questions and answers
    if main_answer.selected_options.exists():
      for option in main_answer.selected_options.all():
        if option.sub_questions.exists():
          for sub_question in option.sub_questions.all():
            sub_answer = sub_question_answers.get(sub_question.id)
            if sub_answer:
              answer_data["sub_answers"].append(
                {"parent_option": option, "sub_question": sub_question, "sub_answer": sub_answer}
              )

    pages_dict[page_key]["answers"].append(answer_data)

  organized_pages = list(pages_dict.values())

  # Also build flat organized_answers for backward compatibility (total count etc.)
  organized_answers = []
  for page_data in organized_pages:
    organized_answers.extend(page_data["answers"])

  context = {
    "survey": survey,
    "submission": submission,
    "organized_answers": organized_answers,
    "organized_pages": organized_pages,
  }
  return render(request, "survey/submission_detail.html", context)


@login_required
@require_POST
def delete_question_option(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    # Check if this is the last option for a question that requires options
    if option.question.requires_options() and option.question.options.count() <= 1:
      return JsonResponse(
        {"status": "error", "message": "This question type requires at least one option"}, status=400
      )

    option_id = option.id
    option.delete()

    return JsonResponse({"status": "success", "option_id": option_id})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def reorder_question_options(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_orders = data.get("option_orders", [])

    for item in option_orders:
      option_id = item.get("id")
      new_order = item.get("order")

      option = get_object_or_404(QuestionOption, id=option_id, question=question)
      option.order = new_order
      option.save(update_fields=["order"])

    return JsonResponse({"status": "success"})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def get_question_details(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    options = []
    if question.requires_options():
      options = [
        {
          "id": option.id,
          "text": option.text,
          "value": option.value,
          "order": option.order,
          "option_warning": option.option_warning or None,
          "option_warning_text": option.option_warning_text or "",
          "sub_questions": [
            {
              "id": sub_question.id,
              "text": sub_question.text,
              "type": sub_question.question_type,
              "is_required": sub_question.is_required,
              "order": sub_question.order,
            }
            for sub_question in option.sub_questions.all()
          ],
        }
        for option in question.options.all()
      ]

    return JsonResponse(
      {
        "status": "success",
        "question": {
          "id": question.id,
          "text": question.text,
          "type": question.question_type,
          "type_display": question.get_question_type_display(),
          "is_required": question.is_required,
          "order": question.order,
          "requires_options": question.requires_options(),
          "filter_options_question_id": question.filter_options_question_id,
          "options": options,
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def submission_detail(request, survey_pk, submission_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Get all answers for this submission
  all_answers = (
    SubmissionAnswer.objects.filter(submission=submission)
    .select_related("question")
    .prefetch_related("selected_options__sub_questions")
  )

  # Separate main questions from sub-questions
  # Main questions are those that don't have parent_options or have page/survey directly assigned
  main_questions = []
  sub_question_answers = {}

  # Build lookup of all sub-question IDs
  sub_question_ids = set()
  for answer in all_answers:
    if answer.selected_options.exists():
      for option in answer.selected_options.all():
        for sub_q in option.sub_questions.all():
          sub_question_ids.add(sub_q.id)

  # Organize answers
  for answer in all_answers:
    if answer.question.id in sub_question_ids:
      # This is a sub-question answer
      sub_question_answers[answer.question.id] = answer
    else:
      # This is a main question answer
      main_questions.append(answer)

  # Build organized structure
  organized_answers = []
  for main_answer in main_questions:
    answer_data = {"answer": main_answer, "sub_answers": []}

    # If this answer has selected options, find their sub-questions and answers
    if main_answer.selected_options.exists():
      for option in main_answer.selected_options.all():
        if option.sub_questions.exists():
          for sub_question in option.sub_questions.all():
            sub_answer = sub_question_answers.get(sub_question.id)
            if sub_answer:
              answer_data["sub_answers"].append(
                {"parent_option": option, "sub_question": sub_question, "sub_answer": sub_answer}
              )

    organized_answers.append(answer_data)

  context = {
    "survey": survey,
    "submission": submission,
    "organized_answers": organized_answers,
  }
  return render(request, "survey/submission_detail.html", context)


@login_required
@require_POST
def reorder_questions(request, page_pk):
  page = get_object_or_404(SurveyPage, pk=page_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", page.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    question_orders = data.get("question_orders", [])

    for item in question_orders:
      question_id = item.get("id")
      new_order = item.get("order")

      question = get_object_or_404(Question, id=question_id, page=page)
      question.order = new_order
      question.save(update_fields=["order"])

    return JsonResponse({"status": "success"})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def update_question_option(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_text = data.get("text", "").strip()
    option_value = (
      (data.get("value", None) or "").strip()
      if isinstance(data.get("value", None), str)
      else data.get("value", None)
    )

    if not option_text:
      return JsonResponse({"status": "error", "message": "Option text is required"}, status=400)

    option.text = option_text
    option.value = option_value
    option.save(update_fields=["text", "value"])

    return JsonResponse(
      {
        "status": "success",
        "option": {
          "id": option.id,
          "text": option.text,
          "value": option.value,
          "order": option.order,
          "option_warning": option.option_warning or None,
          "option_warning_text": option.option_warning_text or "",
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def submission_detail(request, survey_pk, submission_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Get all answers for this submission
  all_answers = (
    SubmissionAnswer.objects.filter(submission=submission)
    .select_related("question")
    .prefetch_related("selected_options__sub_questions")
  )

  # Separate main questions from sub-questions
  # Main questions are those that don't have parent_options or have page/survey directly assigned
  main_questions = []
  sub_question_answers = {}

  # Build lookup of all sub-question IDs
  sub_question_ids = set()
  for answer in all_answers:
    if answer.selected_options.exists():
      for option in answer.selected_options.all():
        for sub_q in option.sub_questions.all():
          sub_question_ids.add(sub_q.id)

  # Organize answers
  for answer in all_answers:
    if answer.question.id in sub_question_ids:
      # This is a sub-question answer
      sub_question_answers[answer.question.id] = answer
    else:
      # This is a main question answer
      main_questions.append(answer)

  # Build organized structure
  organized_answers = []
  for main_answer in main_questions:
    answer_data = {"answer": main_answer, "sub_answers": []}

    # If this answer has selected options, find their sub-questions and answers
    if main_answer.selected_options.exists():
      for option in main_answer.selected_options.all():
        if option.sub_questions.exists():
          for sub_question in option.sub_questions.all():
            sub_answer = sub_question_answers.get(sub_question.id)
            if sub_answer:
              answer_data["sub_answers"].append(
                {"parent_option": option, "sub_question": sub_question, "sub_answer": sub_answer}
              )

    organized_answers.append(answer_data)

  context = {
    "survey": survey,
    "submission": submission,
    "organized_answers": organized_answers,
  }
  return render(request, "survey/submission_detail.html", context)


@login_required
@require_POST
def delete_question_option(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    # Check if this is the last option for a question that requires options
    if option.question.requires_options() and option.question.options.count() <= 1:
      return JsonResponse(
        {"status": "error", "message": "This question type requires at least one option"}, status=400
      )

    option_id = option.id
    option.delete()

    return JsonResponse({"status": "success", "option_id": option_id})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def reorder_question_options(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_orders = data.get("option_orders", [])

    for item in option_orders:
      option_id = item.get("id")
      new_order = item.get("order")

      option = get_object_or_404(QuestionOption, id=option_id, question=question)
      option.order = new_order
      option.save(update_fields=["order"])

    return JsonResponse({"status": "success"})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def get_question_details(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    options = []
    if question.requires_options():
      options = [
        {
          "id": option.id,
          "text": option.text,
          "value": option.value,
          "order": option.order,
          "is_conditional": option.is_conditional,
        }
        for option in question.options.all()
      ]

    return JsonResponse(
      {
        "status": "success",
        "question": {
          "id": question.id,
          "text": question.text,
          "type": question.question_type,
          "type_display": question.get_question_type_display(),
          "is_required": question.is_required,
          "order": question.order,
          "requires_options": question.requires_options(),
          "options": options,
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def submission_detail(request, survey_pk, submission_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Get all answers for this submission
  all_answers = (
    SubmissionAnswer.objects.filter(submission=submission)
    .select_related("question")
    .prefetch_related("selected_options__sub_questions")
  )

  # Separate main questions from sub-questions
  # Main questions are those that don't have parent_options or have page/survey directly assigned
  main_questions = []
  sub_question_answers = {}

  # Build lookup of all sub-question IDs
  sub_question_ids = set()
  for answer in all_answers:
    if answer.selected_options.exists():
      for option in answer.selected_options.all():
        for sub_q in option.sub_questions.all():
          sub_question_ids.add(sub_q.id)

  # Organize answers
  for answer in all_answers:
    if answer.question.id in sub_question_ids:
      # This is a sub-question answer
      sub_question_answers[answer.question.id] = answer
    else:
      # This is a main question answer
      main_questions.append(answer)

  # Build organized structure
  organized_answers = []
  for main_answer in main_questions:
    answer_data = {"answer": main_answer, "sub_answers": []}

    # If this answer has selected options, find their sub-questions and answers
    if main_answer.selected_options.exists():
      for option in main_answer.selected_options.all():
        if option.sub_questions.exists():
          for sub_question in option.sub_questions.all():
            sub_answer = sub_question_answers.get(sub_question.id)
            if sub_answer:
              answer_data["sub_answers"].append(
                {"parent_option": option, "sub_question": sub_question, "sub_answer": sub_answer}
              )

    organized_answers.append(answer_data)

  context = {
    "survey": survey,
    "submission": submission,
    "organized_answers": organized_answers,
  }
  return render(request, "survey/submission_detail.html", context)


@login_required
@require_POST
def quick_add_question(request, page_pk):
  page = get_object_or_404(SurveyPage, pk=page_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", page.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    question_key = data.get("key", "").strip()
    question_text = data.get("text", "").strip()
    question_type = data.get("type", "text")
    hint = data.get("hint", "").strip()
    min_length = data.get("min_length")
    max_length = data.get("max_length")
    min_value = data.get("min_value")
    max_value = data.get("max_value")
    validation_pattern = data.get("validation_pattern", "").strip()
    validation_message = data.get("validation_message", "").strip()

    # Convert empty key to None
    question_key = question_key if question_key else None

    if not question_text:
      return JsonResponse({"status": "error", "message": "Question text is required"}, status=400)

    # Convert empty strings to None for numeric fields
    min_length = int(min_length) if min_length and str(min_length).strip() else None
    max_length = int(max_length) if max_length and str(max_length).strip() else None
    min_value = float(min_value) if min_value and str(min_value).strip() else None
    max_value = float(max_value) if max_value and str(max_value).strip() else None
    validation_pattern = validation_pattern if validation_pattern else None
    validation_message = validation_message if validation_message else None
    hint = hint if hint else None

    # Get the next order number
    last_question = page.questions.order_by("-order").first()
    next_order = (last_question.order + 1) if last_question else 0

    question = Question.objects.create(
      survey=page.survey,
      page=page,
      key=question_key,
      text=question_text,
      hint=hint,
      question_type=question_type,
      order=next_order,
      min_length=min_length,
      max_length=max_length,
      min_value=min_value,
      max_value=max_value,
      validation_pattern=validation_pattern,
      validation_message=validation_message,
    )

    return JsonResponse(
      {
        "status": "success",
        "question": {
          "id": question.id,
          "key": question.key,
          "text": question.text,
          "hint": question.hint or "",
          "type": question.question_type,
          "type_display": question.get_question_type_display(),
          "is_required": question.is_required,
          "order": question.order,
          "requires_options": question.requires_options(),
          "filter_options_question_id": question.filter_options_question_id,
          "options": [],
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def submission_detail(request, survey_pk, submission_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Get all answers for this submission
  all_answers = (
    SubmissionAnswer.objects.filter(submission=submission)
    .select_related("question")
    .prefetch_related("selected_options__sub_questions")
  )

  # Separate main questions from sub-questions
  # Main questions are those that don't have parent_options or have page/survey directly assigned
  main_questions = []
  sub_question_answers = {}

  # Build lookup of all sub-question IDs
  sub_question_ids = set()
  for answer in all_answers:
    if answer.selected_options.exists():
      for option in answer.selected_options.all():
        for sub_q in option.sub_questions.all():
          sub_question_ids.add(sub_q.id)

  # Organize answers
  for answer in all_answers:
    if answer.question.id in sub_question_ids:
      # This is a sub-question answer
      sub_question_answers[answer.question.id] = answer
    else:
      # This is a main question answer
      main_questions.append(answer)

  # Build organized structure
  organized_answers = []
  for main_answer in main_questions:
    answer_data = {"answer": main_answer, "sub_answers": []}

    # If this answer has selected options, find their sub-questions and answers
    if main_answer.selected_options.exists():
      for option in main_answer.selected_options.all():
        if option.sub_questions.exists():
          for sub_question in option.sub_questions.all():
            sub_answer = sub_question_answers.get(sub_question.id)
            if sub_answer:
              answer_data["sub_answers"].append(
                {"parent_option": option, "sub_question": sub_question, "sub_answer": sub_answer}
              )

    organized_answers.append(answer_data)

  context = {
    "survey": survey,
    "submission": submission,
    "organized_answers": organized_answers,
  }
  return render(request, "survey/submission_detail.html", context)


@login_required
@require_POST
def add_question_option(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_text = data.get("text", "").strip()
    option_value = data.get("value", "").strip()

    if not option_text:
      return JsonResponse({"status": "error", "message": "Option text is required"}, status=400)

    # Get the next order number
    last_option = question.options.order_by("-order").first()
    next_order = (last_option.order + 1) if last_option else 0

    option = QuestionOption.objects.create(
      question=question, text=option_text, value=option_value, order=next_order
    )

    return JsonResponse(
      {
        "status": "success",
        "option": {
          "id": option.id,
          "text": option.text,
          "value": option.value,
          "order": option.order,
          "option_warning": option.option_warning or None,
          "option_warning_text": option.option_warning_text or "",
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def add_subquestion_to_option(request, option_pk):
  """Add a subquestion to an option"""
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    text = data.get("text", "").strip()
    question_type = data.get("type", "text")
    is_required = data.get("is_required", False)

    if not text:
      return JsonResponse({"status": "error", "message": "Question text is required"}, status=400)

    # Get the next order number
    last_subquestion = option.sub_questions.order_by("-order").first()
    next_order = (last_subquestion.order + 1) if last_subquestion else 0

    # Create the subquestion
    sub_question = Question.objects.create(
      survey=option.question.survey,
      text=text,
      question_type=question_type,
      is_required=is_required,
      order=next_order,
    )

    # Add the subquestion to the option
    option.sub_questions.add(sub_question)

    return JsonResponse(
      {
        "status": "success",
        "sub_question": {
          "id": sub_question.id,
          "text": sub_question.text,
          "type": sub_question.question_type,
          "is_required": sub_question.is_required,
          "order": sub_question.order,
        },
      }
    )

  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def delete_subquestion(request, subquestion_pk):
  """Delete a subquestion"""
  sub_question = get_object_or_404(Question, pk=subquestion_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", sub_question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    sub_question_id = sub_question.id
    sub_question.delete()

    return JsonResponse({"status": "success", "sub_question_id": sub_question_id})

  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def submission_detail(request, survey_pk, submission_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Get all answers for this submission
  all_answers = (
    SubmissionAnswer.objects.filter(submission=submission)
    .select_related("question")
    .prefetch_related("selected_options__sub_questions")
  )

  # Separate main questions from sub-questions
  # Main questions are those that don't have parent_options or have page/survey directly assigned
  main_questions = []
  sub_question_answers = {}

  # Build lookup of all sub-question IDs
  sub_question_ids = set()
  for answer in all_answers:
    if answer.selected_options.exists():
      for option in answer.selected_options.all():
        for sub_q in option.sub_questions.all():
          sub_question_ids.add(sub_q.id)

  # Organize answers
  for answer in all_answers:
    if answer.question.id in sub_question_ids:
      # This is a sub-question answer
      sub_question_answers[answer.question.id] = answer
    else:
      # This is a main question answer
      main_questions.append(answer)

  # Build organized structure
  organized_answers = []
  for main_answer in main_questions:
    answer_data = {"answer": main_answer, "sub_answers": []}

    # If this answer has selected options, find their sub-questions and answers
    if main_answer.selected_options.exists():
      for option in main_answer.selected_options.all():
        if option.sub_questions.exists():
          for sub_question in option.sub_questions.all():
            sub_answer = sub_question_answers.get(sub_question.id)
            if sub_answer:
              answer_data["sub_answers"].append(
                {"parent_option": option, "sub_question": sub_question, "sub_answer": sub_answer}
              )

    organized_answers.append(answer_data)

  context = {
    "survey": survey,
    "submission": submission,
    "organized_answers": organized_answers,
  }
  return render(request, "survey/submission_detail.html", context)


@login_required
@require_POST
def update_question_option(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_text = data.get("text", "").strip()
    option_value = (
      (data.get("value", None) or "").strip()
      if isinstance(data.get("value", None), str)
      else data.get("value", None)
    )

    if not option_text:
      return JsonResponse({"status": "error", "message": "Option text is required"}, status=400)

    option.text = option_text
    option.value = option_value
    option.save(update_fields=["text", "value"])

    return JsonResponse(
      {
        "status": "success",
        "option": {
          "id": option.id,
          "text": option.text,
          "value": option.value,
          "order": option.order,
          "option_warning": option.option_warning or None,
          "option_warning_text": option.option_warning_text or "",
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def submission_detail(request, survey_pk, submission_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Get all answers for this submission
  all_answers = (
    SubmissionAnswer.objects.filter(submission=submission)
    .select_related("question")
    .prefetch_related("selected_options__sub_questions")
  )

  # Separate main questions from sub-questions
  # Main questions are those that don't have parent_options or have page/survey directly assigned
  main_questions = []
  sub_question_answers = {}

  # Build lookup of all sub-question IDs
  sub_question_ids = set()
  for answer in all_answers:
    if answer.selected_options.exists():
      for option in answer.selected_options.all():
        for sub_q in option.sub_questions.all():
          sub_question_ids.add(sub_q.id)

  # Organize answers
  for answer in all_answers:
    if answer.question.id in sub_question_ids:
      # This is a sub-question answer
      sub_question_answers[answer.question.id] = answer
    else:
      # This is a main question answer
      main_questions.append(answer)

  # Build organized structure
  organized_answers = []
  for main_answer in main_questions:
    answer_data = {"answer": main_answer, "sub_answers": []}

    # If this answer has selected options, find their sub-questions and answers
    if main_answer.selected_options.exists():
      for option in main_answer.selected_options.all():
        if option.sub_questions.exists():
          for sub_question in option.sub_questions.all():
            sub_answer = sub_question_answers.get(sub_question.id)
            if sub_answer:
              answer_data["sub_answers"].append(
                {"parent_option": option, "sub_question": sub_question, "sub_answer": sub_answer}
              )

    organized_answers.append(answer_data)

  context = {
    "survey": survey,
    "submission": submission,
    "organized_answers": organized_answers,
  }
  return render(request, "survey/submission_detail.html", context)


@login_required
@require_POST
def delete_question_option(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    # Check if this is the last option for a question that requires options
    if option.question.requires_options() and option.question.options.count() <= 1:
      return JsonResponse(
        {"status": "error", "message": "This question type requires at least one option"}, status=400
      )

    option_id = option.id
    option.delete()

    return JsonResponse({"status": "success", "option_id": option_id})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def reorder_question_options(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_orders = data.get("option_orders", [])

    for item in option_orders:
      option_id = item.get("id")
      new_order = item.get("order")

      option = get_object_or_404(QuestionOption, id=option_id, question=question)
      option.order = new_order
      option.save(update_fields=["order"])

    return JsonResponse({"status": "success"})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def get_question_details(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    options = []
    if question.requires_options():
      options = [
        {
          "id": option.id,
          "text": option.text,
          "order": option.order,
        }
        for option in question.options.all()
      ]

    return JsonResponse(
      {
        "status": "success",
        "question": {
          "id": question.id,
          "text": question.text,
          "type": question.question_type,
          "type_display": question.get_question_type_display(),
          "is_required": question.is_required,
          "order": question.order,
          "requires_options": question.requires_options(),
          "options": options,
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def quick_add_page(request, survey_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    page_title = data.get("title", "").strip()

    # Get the next order number
    last_page = survey.pages.order_by("-order").first()
    next_order = (last_page.order + 1) if last_page else 0

    page = SurveyPage.objects.create(survey=survey, title=page_title or f"Page {next_order + 1}", order=next_order)

    return JsonResponse(
      {
        "status": "success",
        "page": {
          "id": page.id,
          "title": page.title,
          "description": page.description,
          "order": page.order,
          "questions_count": 0,
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def update_question_option(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_text = data.get("text", "").strip()
    option_value = (
      (data.get("value", None) or "").strip()
      if isinstance(data.get("value", None), str)
      else data.get("value", None)
    )

    if not option_text:
      return JsonResponse({"status": "error", "message": "Option text is required"}, status=400)

    option.text = option_text
    option.value = option_value
    option.save(update_fields=["text", "value"])

    return JsonResponse(
      {
        "status": "success",
        "option": {
          "id": option.id,
          "text": option.text,
          "value": option.value,
          "order": option.order,
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def submission_detail(request, survey_pk, submission_pk):
  survey = get_object_or_404(Survey, pk=survey_pk)
  _enforce_survey_access(request.user, survey)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Get all answers for this submission, indexed by question ID
  all_answers = (
    SubmissionAnswer.objects.filter(submission=submission)
    .select_related("question", "question__page")
    .prefetch_related("selected_options__sub_questions")
  )
  answers_by_question = {answer.question_id: answer for answer in all_answers}

  # Build lookup of all sub-question IDs (questions linked via option.sub_questions)
  sub_question_ids = set()
  for answer in all_answers:
    if answer.selected_options.exists():
      for option in answer.selected_options.all():
        for sub_q in option.sub_questions.all():
          sub_question_ids.add(sub_q.id)

  # Also collect sub-question IDs from all question options in the survey
  all_options = QuestionOption.objects.filter(
    question__survey=survey
  ).prefetch_related("sub_questions")
  for option in all_options:
    for sub_q in option.sub_questions.all():
      sub_question_ids.add(sub_q.id)

  # Build sub-question answers lookup
  sub_question_answers = {}
  for answer in all_answers:
    if answer.question_id in sub_question_ids:
      sub_question_answers[answer.question_id] = answer

  # Get ALL questions from the survey (excluding sub-questions), ordered by page then question order
  all_questions = (
    survey.questions
    .exclude(id__in=sub_question_ids)
    .exclude(question_type__in=["note", "consent"])
    .select_related("page")
    .prefetch_related("options__sub_questions")
    .order_by("page__order", "page__id", "order", "id")
  )

  # Build organized structure grouped by page (module) — using ALL questions
  from collections import OrderedDict
  pages_dict = OrderedDict()

  for question in all_questions:
    page = question.page
    page_key = page.id if page else 0

    if page_key not in pages_dict:
      pages_dict[page_key] = {
        "page": page,
        "page_title": page.title if page else "Other Questions",
        "page_description": page.description if page else "",
        "answers": [],
      }

    # Check if there is an existing answer for this question
    existing_answer = answers_by_question.get(question.id)

    answer_data = {"answer": existing_answer, "question": question, "sub_answers": [], "option_values_json": "[]"}

    # Build option text/value pairs for label-value toggle
    if existing_answer and existing_answer.selected_options.exists():
      option_values = [
        {"text": opt.text, "value": opt.value if opt.value else str(opt.order + 1)}
        for opt in existing_answer.selected_options.all()
      ]
      answer_data["option_values_json"] = json.dumps(option_values)

    # If answer exists and has selected options, find their sub-questions and answers
    if existing_answer and existing_answer.selected_options.exists():
      for option in existing_answer.selected_options.all():
        if option.sub_questions.exists():
          for sub_question in option.sub_questions.all():
            sub_answer = sub_question_answers.get(sub_question.id)
            if sub_answer:
              answer_data["sub_answers"].append(
                {"parent_option": option, "sub_question": sub_question, "sub_answer": sub_answer}
              )

    pages_dict[page_key]["answers"].append(answer_data)

  organized_pages = list(pages_dict.values())

  # Also build flat organized_answers for backward compatibility
  organized_answers = []
  for page_data in organized_pages:
    organized_answers.extend(page_data["answers"])

  # Get indicator results for this submission
  indicator_results = (
    IndicatorResult.objects.filter(submission=submission).select_related("indicator").order_by("indicator__name")
  )

  # Check if user came from user management page
  from_user_id = request.GET.get('from_user')
  from_user = None
  if from_user_id:
    try:
      from_user = User.objects.get(id=from_user_id)
    except User.DoesNotExist:
      pass

  # Check user role for edit permissions
  is_supervisor = request.user.groups.filter(name='Supervisor').exists()

  context = {
    "survey": survey,
    "submission": submission,
    "organized_answers": organized_answers,
    "organized_pages": organized_pages,
    "indicator_results": indicator_results,
    "from_user": from_user,
    "is_supervisor": is_supervisor,
  }
  return render(request, "survey/submission_detail.html", context)


@login_required
@manager_or_permission_required("survey.change_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
@require_POST
def update_submission_answer(request, survey_pk, submission_pk, answer_pk):
  """API endpoint to update a submission answer"""
  try:
    survey = get_object_or_404(Survey, pk=survey_pk)
    submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
    from .selectors import enforce_submission_visibility
    enforce_submission_visibility(request.user, submission)
    answer = get_object_or_404(SubmissionAnswer, pk=answer_pk, submission=submission)

    data = json.loads(request.body)
    question_type = answer.question.question_type

    # Handle different question types
    if question_type in ["radio", "checkbox", "dropdown"]:
      # For option-based questions
      selected_option_ids = data.get("selected_option_ids", [])
      answer.selected_options.clear()

      if selected_option_ids:
        # Convert string IDs to integers
        option_ids = [int(oid) for oid in selected_option_ids]
        options = QuestionOption.objects.filter(id__in=option_ids, question=answer.question)
        answer.selected_options.set(options)

      # Handle "Other" text if provided
      if "other_text" in data:
        answer.other_text = data["other_text"]
    else:
      # For text-based questions (text, textarea, number, email, date, time, datetime)
      answer.text_answer = data.get("text_answer", "")

    answer.save()

    # Prepare response with updated answer details
    response_data = {
      "success": True,
      "message": "Answer updated successfully"
    }

    # Add answer display text for UI update
    if question_type in ["radio", "checkbox", "dropdown"]:
      selected_options = answer.selected_options.all()
      response_data["selected_options"] = [
        {"id": opt.id, "text": opt.text} for opt in selected_options
      ]
      response_data["other_text"] = answer.other_text or ""
    else:
      response_data["text_answer"] = answer.text_answer

    return JsonResponse(response_data)

  except Exception as e:
    return JsonResponse({
      "success": False,
      "error": str(e)
    }, status=400)


@login_required
@manager_or_permission_required("survey.change_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
@require_POST
def create_submission_answer(request, survey_pk, submission_pk, question_pk):
  """API endpoint to create a new answer for an unanswered question in a submission"""
  try:
    survey = get_object_or_404(Survey, pk=survey_pk)
    submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
    from .selectors import enforce_submission_visibility
    enforce_submission_visibility(request.user, submission)
    question = get_object_or_404(Question, pk=question_pk, survey=survey)

    # Check if answer already exists
    existing = SubmissionAnswer.objects.filter(submission=submission, question=question).first()
    if existing:
      return JsonResponse({
        "success": False,
        "error": "Answer already exists for this question. Use the update endpoint instead.",
        "answer_id": existing.pk,
      }, status=400)

    data = json.loads(request.body)
    question_type = question.question_type

    # Create the answer
    answer = SubmissionAnswer.objects.create(
      submission=submission,
      question=question,
    )

    # Handle different question types
    if question_type in ["radio", "checkbox", "dropdown"]:
      selected_option_ids = data.get("selected_option_ids", [])
      if selected_option_ids:
        option_ids = [int(oid) for oid in selected_option_ids]
        options = QuestionOption.objects.filter(id__in=option_ids, question=question)
        answer.selected_options.set(options)

      if "other_text" in data:
        answer.other_text = data["other_text"]
        answer.save()
    else:
      answer.text_answer = data.get("text_answer", "")
      answer.save()

    # Prepare response
    response_data = {
      "success": True,
      "message": "Answer created successfully",
      "answer_id": answer.pk,
    }

    if question_type in ["radio", "checkbox", "dropdown"]:
      selected_options = answer.selected_options.all()
      response_data["selected_options"] = [
        {"id": opt.id, "text": opt.text} for opt in selected_options
      ]
      response_data["other_text"] = answer.other_text or ""
    else:
      response_data["text_answer"] = answer.text_answer

    return JsonResponse(response_data)

  except Exception as e:
    return JsonResponse({
      "success": False,
      "error": str(e)
    }, status=400)


@login_required
def submission_indicators(request, survey_pk, submission_pk):
  """Calculate and display indicators for a specific submission"""
  survey = get_object_or_404(Survey, pk=survey_pk)
  _enforce_survey_access(request.user, survey)
  submission = get_object_or_404(Submission, pk=submission_pk, survey=survey)
  from .selectors import enforce_submission_visibility
  enforce_submission_visibility(request.user, submission)

  # Check if user has permission to view the survey (managers/supervisors have access to all)
  is_manager = request.user.groups.filter(name='Manager').exists()
  is_supervisor = request.user.groups.filter(name='Supervisor').exists()
  if not (request.user.is_staff or request.user.is_superuser or is_manager or is_supervisor or request.user.has_perm("survey.view_survey", survey)):
    from django.core.exceptions import PermissionDenied
    raise PermissionDenied

  # Get all indicators for this survey
  indicators = survey.indicators.filter(is_active=True)
  results = []

  for indicator in indicators:
    # Always execute the indicator (don't use cached results)
    execution_result = indicator.execute(submission)

    # Delete any existing result first
    IndicatorResult.objects.filter(indicator=indicator, submission=submission).delete()

    # Only save the result if execution was successful and result is not None
    if execution_result.get('success', False) and execution_result.get('result') is not None:
      indicator_result = IndicatorResult.objects.create(
        indicator=indicator,
        submission=submission,
        result=execution_result.get('result'),
        success=True,
        error_message=None
      )

      results.append({
        "indicator": indicator,
        "result": indicator_result.result,
        "success": indicator_result.success,
        "error_message": indicator_result.error_message,
        "calculated_at": indicator_result.calculated_at,
        "existing": False,
      })
    else:
      # Don't save failed results, just show them
      results.append({
        "indicator": indicator,
        "result": None,
        "success": False,
        "error_message": execution_result.get('error', 'Unknown error occurred'),
        "calculated_at": None,
        "existing": False,
      })

  context = {
    "survey": survey,
    "submission": submission,
    "results": results,
    "total_indicators": len(indicators),
    "successful_calculations": len([r for r in results if r['success']]),
    "failed_calculations": len([r for r in results if not r['success']]),
  }
  return render(request, "survey/submission_indicators.html", context)


@login_required
@require_POST
def delete_question_option(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    # Check if this is the last option for a question that requires options
    if option.question.requires_options() and option.question.options.count() <= 1:
      return JsonResponse(
        {"status": "error", "message": "This question type requires at least one option"}, status=400
      )

    option_id = option.id
    option.delete()

    return JsonResponse({"status": "success", "option_id": option_id})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def reorder_question_options(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    option_orders = data.get("option_orders", [])

    for item in option_orders:
      option_id = item.get("id")
      new_order = item.get("order")

      option = get_object_or_404(QuestionOption, id=option_id, question=question)
      option.order = new_order
      option.save(update_fields=["order"])

    return JsonResponse({"status": "success"})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def get_question_details(request, question_pk):
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    options = []
    if question.requires_options():
      options = [
        {
          "id": option.id,
          "text": option.text,
          "order": option.order,
        }
        for option in question.options.all()
      ]

    return JsonResponse(
      {
        "status": "success",
        "question": {
          "id": question.id,
          "text": question.text,
          "type": question.question_type,
          "type_display": question.get_question_type_display(),
          "is_required": question.is_required,
          "order": question.order,
          "requires_options": question.requires_options(),
          "options": options,
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def survey_districts_for_state(request, survey_pk):
  """Return districts for the given survey, optionally filtered by state. Used by the submissions page to refresh the district dropdown without a full page reload."""
  survey = get_object_or_404(Survey, pk=survey_pk)
  state = request.GET.get("state", "")
  from .selectors import apply_answer_visibility

  district_qs = apply_answer_visibility(
    SubmissionAnswer.objects.filter(
      submission__survey=survey,
      question__key="district",
      text_answer__isnull=False,
    ).exclude(text_answer=""),
    request.user,
  )

  if state:
    state_submission_ids = apply_answer_visibility(
      SubmissionAnswer.objects.filter(
        submission__survey=survey,
        question__key="state",
        text_answer=state,
      ),
      request.user,
    ).values_list("submission_id", flat=True)
    district_qs = district_qs.filter(submission_id__in=state_submission_ids)

  districts = list(district_qs.values_list("text_answer", flat=True).distinct().order_by("text_answer"))
  return JsonResponse({"districts": districts})


def survey_submissions(request, pk):
  survey = get_object_or_404(Survey, pk=pk)
  if not survey.is_accessible_by_user(request.user, include_public=False):
    from django.http import Http404
    raise Http404("Survey not available")
  search_query = request.GET.get("search", "")
  status_filter = request.GET.get("status", "")
  archive_filter = request.GET.get("archive", "active")
  show_duplicates = request.GET.get("show_duplicates", "") == "1"
  date_from = request.GET.get("date_from", "")
  date_to = request.GET.get("date_to", "")
  page_size = request.GET.get("page_size", "20")
  selected_state = request.GET.get("state", "")
  selected_district = request.GET.get("district", "")
  selected_clf = request.GET.get("clf", "")

  # Validate page_size
  if page_size not in ["10", "20", "25", "50"]:
    page_size = "20"

  from .selectors import apply_answer_visibility, apply_submission_visibility, parse_submissions_date_filter

  # Get distinct state values for this survey (from answers to questions with key='state')
  states = (
    apply_answer_visibility(
      SubmissionAnswer.objects.filter(
        submission__survey=survey,
        question__key="state",
        text_answer__isnull=False,
      ).exclude(text_answer=""),
      request.user,
    )
    .values_list("text_answer", flat=True)
    .distinct()
    .order_by("text_answer")
  )

  # Get distinct district values — filtered by selected state if one is chosen
  district_qs = apply_answer_visibility(
    SubmissionAnswer.objects.filter(
      submission__survey=survey,
      question__key="district",
      text_answer__isnull=False,
    ).exclude(text_answer=""),
    request.user,
  )

  if selected_state:
    # Only show districts for submissions that belong to the selected state
    state_submission_ids = apply_answer_visibility(
      SubmissionAnswer.objects.filter(
        submission__survey=survey,
        question__key="state",
        text_answer=selected_state,
      ),
      request.user,
    ).values_list("submission_id", flat=True)
    district_qs = district_qs.filter(submission_id__in=state_submission_ids)

  districts = district_qs.values_list("text_answer", flat=True).distinct().order_by("text_answer")

  # Distinct CLF values ("Name of CLF" question, id CLF_QUESTION_ID). The value
  # may be stored as free text or as a selected dropdown option, so gather both.
  # Cascade the options by the chosen state/district when set. On environments
  # without this question the list is simply empty and the dropdown is hidden.
  from .selectors import CLF_QUESTION_ID
  clf_answer_qs = apply_answer_visibility(
    SubmissionAnswer.objects.filter(
      submission__survey=survey,
      submission__is_archived=False,
      question_id=CLF_QUESTION_ID,
    ),
    request.user,
  )
  if status_filter:
    clf_answer_qs = clf_answer_qs.filter(submission__status=status_filter)
  clf_date_from = parse_submissions_date_filter(date_from)
  if clf_date_from:
    clf_answer_qs = clf_answer_qs.filter(submission__submitted_at__gte=clf_date_from)
  clf_date_to = parse_submissions_date_filter(date_to, is_end=True)
  if clf_date_to:
    clf_answer_qs = clf_answer_qs.filter(submission__submitted_at__lte=clf_date_to)
  if selected_state:
    clf_answer_qs = clf_answer_qs.filter(
      submission__answers__question__key="state",
      submission__answers__text_answer=selected_state,
    )
  if selected_district:
    clf_answer_qs = clf_answer_qs.filter(
      submission__answers__question__key="district",
      submission__answers__text_answer=selected_district,
    )
  clf_text_values = clf_answer_qs.filter(
    text_answer__isnull=False,
  ).exclude(text_answer="").values_list("text_answer", flat=True)
  clf_option_values = clf_answer_qs.filter(
    selected_options__isnull=False,
  ).values_list("selected_options__text", flat=True)
  clfs = sorted({
    c.strip() for c in list(clf_text_values) + list(clf_option_values) if c and c.strip()
  })

  submissions = Submission.objects.filter(survey=survey)

  submissions = apply_submission_visibility(submissions, request.user)

  # Apply archive filter
  if archive_filter == "active":
    submissions = submissions.filter(is_archived=False)
  elif archive_filter == "archived":
    submissions = submissions.filter(is_archived=True)

  # Apply other filters
  if status_filter:
    submissions = submissions.filter(status=status_filter)

  dt_from = parse_submissions_date_filter(date_from)
  if dt_from:
    submissions = submissions.filter(submitted_at__gte=dt_from)
  dt_to = parse_submissions_date_filter(date_to, is_end=True)
  if dt_to:
    submissions = submissions.filter(submitted_at__lte=dt_to)

  # Show duplicates filter - only show submissions with duplicate computed_titles
  if show_duplicates:
    from django.db.models import Count
    duplicate_titles = submissions.values('computed_title').annotate(
      title_count=Count('id')
    ).filter(title_count__gt=1, computed_title__isnull=False).values_list('computed_title', flat=True)
    submissions = submissions.filter(computed_title__in=duplicate_titles)

  # Keep zero-answer drafts visible and flag them in the UI instead of hiding
  # them; answer_count drives the "No answers" badge in the table.
  from django.db.models import Count
  submissions = submissions.annotate(answer_count=Count('answers'))

  if selected_state:
    submissions = submissions.filter(
      answers__question__key="state",
      answers__text_answer=selected_state,
    )

  if selected_district:
    submissions = submissions.filter(
      answers__question__key="district",
      answers__text_answer=selected_district,
    )

  if selected_clf:
    submissions = submissions.filter(
      Q(answers__question_id=CLF_QUESTION_ID, answers__text_answer=selected_clf)
      | Q(answers__question_id=CLF_QUESTION_ID, answers__selected_options__text=selected_clf)
    ).distinct()

  if search_query:
    submissions = submissions.filter(
      Q(user__username__icontains=search_query)
      | Q(user__first_name__icontains=search_query)
      | Q(user__last_name__icontains=search_query)
      | Q(ip_address__icontains=search_query)
    )

  # Order by most recent first (or by title when showing duplicates)
  if show_duplicates:
    # Sort by title first to group duplicates together, then by date
    submissions = submissions.select_related("survey", "user").prefetch_related(
      "answers", "answers__question", "answers__selected_options"
    ).annotate(answered_count=Count("answers")).order_by("computed_title", "-submitted_at")
  else:
    submissions = submissions.select_related("survey", "user").prefetch_related(
      "answers", "answers__question", "answers__selected_options"
    ).annotate(answered_count=Count("answers")).order_by("-submitted_at")

  paginator = Paginator(submissions, int(page_size))
  page_number = request.GET.get("page")
  page_obj = paginator.get_page(page_number)

  context = {
    "survey": survey,
    "page_obj": page_obj,
    "search_query": search_query,
    "status_filter": status_filter,
    "archive_filter": archive_filter,
    "show_duplicates": show_duplicates,
    "date_from": date_from,
    "date_to": date_to,
    "date_preset": request.GET.get("date_preset", ""),
    "page_size": page_size,
    "states": states,
    "selected_state": selected_state,
    "districts": districts,
    "selected_district": selected_district,
    "clfs": clfs,
    "selected_clf": selected_clf,
  }
  return render(request, "survey/survey_submissions.html", context)


@login_required
@require_POST
def set_filter_options_question(request, question_pk):
  """API endpoint to set or clear the filter_options_question for a question"""
  question = get_object_or_404(Question, pk=question_pk)

  # Permission check
  if not request.user.has_perm("survey.change_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body or "{}")
    raw_id = data.get("filter_question_id")

    # Clear the relation if null/empty
    if not raw_id:
      question.filter_options_question = None
      question.full_clean()
      question.save(update_fields=["filter_options_question"])
      return JsonResponse({"status": "success", "filter_options_question_id": None})

    # Normalize the ID (accepts relay global id or plain int as string)
    try:
      fid = int(raw_id)
    except (TypeError, ValueError):
      fid = decode_relay_id(raw_id)
      try:
        fid = int(fid)
      except (TypeError, ValueError):
        return JsonResponse({"status": "error", "message": "Invalid filter_question_id"}, status=400)

    target = get_object_or_404(Question, pk=fid, survey=question.survey)

    # Validate option-based types
    if question.question_type not in ["radio", "checkbox", "dropdown"]:
      return JsonResponse(
        {"status": "error", "message": "Only option-based questions can use a filter question."}, status=400
      )
    if target.question_type not in ["radio", "checkbox", "dropdown"]:
      return JsonResponse(
        {"status": "error", "message": "Filter question must be radio/checkbox/dropdown."}, status=400
      )
    if target.pk == question.pk:
      return JsonResponse(
        {"status": "error", "message": "A question cannot filter options from itself."}, status=400
      )

    question.filter_options_question = target
    question.full_clean()
    question.save(update_fields=["filter_options_question"])

    return JsonResponse({"status": "success", "filter_options_question_id": target.id})
  except ValidationError as ve:
    # Format validation errors
    msg = (
      "; ".join([f"{k}: {','.join(v)}" for k, v in ve.message_dict.items()])
      if hasattr(ve, "message_dict")
      else str(ve)
    )
    return JsonResponse({"status": "error", "message": msg}, status=400)
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "pk"), raise_exception=True)
def survey_preview(request, pk):
  survey = get_object_or_404(Survey, pk=pk)

  # Get current language from query parameter or use default
  current_language = request.GET.get("lang", survey.default_language)

  # Get all pages with their questions
  pages = survey.pages.prefetch_related("questions__options").all()

  # Prepare translated pages data for template
  translated_pages = []
  for page in pages:
    translated_questions = []
    for question in page.questions.all():
      translated_options = []
      for option in question.options.all():
        translated_options.append(
          {"id": option.id, "text": option.get_text(current_language), "original": option}
        )

      translated_questions.append(
        {
          "id": question.id,
          "text": question.get_text(current_language),
          "question_type": question.question_type,
          "is_required": question.is_required,
          "options": translated_options,
          "original": question,
        }
      )

    translated_pages.append(
      {
        "id": page.id,
        "title": page.get_title(current_language),
        "description": page.get_description(current_language),
        "questions": translated_questions,
        "original": page,
      }
    )

  # Get display mode from query parameter or use survey default
  display_mode = request.GET.get("mode", survey.display_mode)

  # For question-per-page mode, get current question index
  current_question_index = int(request.GET.get("q", 0))
  current_page_index = int(request.GET.get("p", 0))

  # Get available languages for this survey
  available_languages = survey.get_supported_languages()

  # Debug: print available languages
  print(f"Survey ID: {survey.id}")
  print(f"Default language: {survey.default_language}")
  print(f"Available languages: {available_languages}")

  # Get all questions in order for JavaScript
  all_questions = []
  questions_json = []
  for page in pages:
    for question in page.questions.all():
      all_questions.append({"question": question, "page": page, "page_index": list(pages).index(page)})

      # Prepare question data for JavaScript with translation support
      question_data = {
        "id": question.id,
        "text": question.get_text(current_language),
        "type": question.question_type,
        "required": question.is_required,
        "options": [],
      }

      # Add options if the question has them
      if question.requires_options():
        for option in question.options.all():
          question_data["options"].append({"id": option.id, "text": option.get_text(current_language)})

      questions_json.append(question_data)

  # Prepare languages data for the template
  languages_data = []
  language_choices = dict(settings.LANGUAGES)

  for lang_code in available_languages:
    languages_data.append(
      {
        "code": lang_code,
        "name": language_choices.get(lang_code, lang_code.upper()),
        "is_current": lang_code == current_language,
      }
    )

  # Debug: print languages data
  print(f"Languages data: {languages_data}")
  print(f"Current language: {current_language}")

  context = {
    "survey": survey,
    "pages": translated_pages,
    "display_mode": display_mode,
    "current_language": current_language,
    "available_languages": languages_data,
    "current_question": current_question_index,
    "current_page": current_page_index,
    "all_questions": all_questions,
    "questions_json": json.dumps(questions_json) if questions_json else "[]",
    "total_questions": len(all_questions),
    "total_pages": len(translated_pages),
  }

  return render(request, "survey/survey_preview.html", context)


@login_required
@manager_or_permission_required("survey.change_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def search_users(request, survey_pk):
  """Search users to invite to survey"""
  survey = get_object_or_404(Survey, pk=survey_pk)
  search_query = request.GET.get("q", "").strip()

  if len(search_query) < 2:
    return JsonResponse({"status": "error", "message": "Search query must be at least 2 characters"})

  # Get users excluding survey creator and already invited users
  invited_user_ids = survey.invitations.filter(is_active=True).values_list("user_id", flat=True)
  excluded_ids = list(invited_user_ids) + [survey.created_by.id]

  users = User.objects.filter(
    Q(username__icontains=search_query)
    | Q(first_name__icontains=search_query)
    | Q(last_name__icontains=search_query)
    | Q(email__icontains=search_query)
  ).exclude(id__in=excluded_ids)[
    :10
  ]  # Limit to 10 results

  user_data = []
  for user in users:
    full_name = f"{user.first_name} {user.last_name}".strip()
    display_name = full_name if full_name else user.username

    user_data.append(
      {
        "id": user.id,
        "username": user.username,
        "display_name": display_name,
        "email": user.email,
        "full_name": full_name,
      }
    )

  return JsonResponse({"status": "success", "users": user_data, "total": len(user_data)})


@login_required
@manager_or_permission_required("survey.change_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
@require_POST
def invite_user(request, survey_pk):
  """Invite a user to participate in survey"""
  survey = get_object_or_404(Survey, pk=survey_pk)

  try:
    data = json.loads(request.body)
    user_id = data.get("user_id")

    if not user_id:
      return JsonResponse({"status": "error", "message": "User ID is required"})

    user = get_object_or_404(User, id=user_id)

    # Check if user is already invited
    if survey.invitations.filter(user=user, is_active=True).exists():
      return JsonResponse({"status": "error", "message": "User already has access to this survey"})

    # Create invitation
    invitation = survey.invite_user(user, request.user)

    return JsonResponse(
      {
        "status": "success",
        "message": f"Successfully added {user.username}",
        "invitation": {
          "id": invitation.id,
          "user": {
            "id": user.id,
            "username": user.username,
            "display_name": f"{user.first_name} {user.last_name}".strip() or user.username,
            "email": user.email,
          },
          "invited_at": invitation.invited_at.isoformat(),
          "has_responded": invitation.has_responded(),
        },
      }
    )

  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)})


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def survey_invitations(request, survey_pk):
  """View all invitations for a survey"""
  survey = get_object_or_404(Survey, pk=survey_pk)

  invitations = survey.invitations.filter(is_active=True).select_related("user", "invited_by")

  invitation_data = []
  for invitation in invitations:
    user = invitation.user
    invitation_data.append(
      {
        "id": invitation.id,
        "user": {
          "id": user.id,
          "username": user.username,
          "display_name": f"{user.first_name} {user.last_name}".strip() or user.username,
          "email": user.email,
        },
        "invited_by": invitation.invited_by.username,
        "invited_at": invitation.invited_at.isoformat(),
        "has_responded": invitation.has_responded(),
        "responded_at": invitation.responded_at.isoformat() if invitation.responded_at else None,
      }
    )

  return JsonResponse({"status": "success", "invitations": invitation_data, "total": len(invitation_data)})


@login_required
@manager_or_permission_required("survey.change_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
@require_POST
def remove_invitation(request, survey_pk, invitation_id):
  """Remove/deactivate a user invitation"""
  survey = get_object_or_404(Survey, pk=survey_pk)
  invitation = get_object_or_404(SurveyInvitation, id=invitation_id, survey=survey)

  invitation.is_active = False
  invitation.save(update_fields=["is_active"])

  return JsonResponse({"status": "success", "message": f"Removed access for {invitation.user.username}"})


def survey_form(request, pk):
  """Public survey form for responding to surveys"""
  survey = get_object_or_404(Survey, pk=pk)

  # Check if user can access this survey
  if not survey.is_accessible_by_user(request.user if request.user.is_authenticated else None):
    return render(request, "survey/access_denied.html", {"survey": survey})

  if not survey.is_active:
    return render(request, "survey/survey_inactive.html", {"survey": survey})

  # Check if user has already submitted (if multiple submissions not allowed)
  existing_submission = None
  if request.user.is_authenticated and not survey.allow_multiple_submissions:
    existing_submission = Submission.objects.filter(survey=survey, user=request.user).first()
    if existing_submission:
      return render(
        request, "survey/already_submitted.html", {"survey": survey, "submission": existing_submission}
      )

  if request.method == "POST":
    form = SurveyResponseForm(survey, request.POST)
    if form.is_valid():
      # Create submission with geolocation data
      submission_data = {
        "survey": survey,
        "user": request.user if request.user.is_authenticated else None,
        "session_key": request.session.session_key,
        "ip_address": request.META.get("REMOTE_ADDR"),
        "user_agent": request.META.get("HTTP_USER_AGENT"),
      }

      # Add geolocation data if available
      latitude = request.POST.get("latitude")
      longitude = request.POST.get("longitude")

      if latitude and longitude:
        try:
          submission_data["latitude"] = float(latitude)
          submission_data["longitude"] = float(longitude)
        except (ValueError, TypeError):
          # If conversion fails, just skip adding geolocation data
          pass

      submission = Submission.objects.create(**submission_data)

      # Save answers for main questions
      for question in survey.questions.all():
        field_name = f"question_{question.id}"
        value = form.cleaned_data.get(field_name)

        if value:
          answer = SubmissionAnswer.objects.create(submission=submission, question=question)

          if question.question_type in ["radio", "checkbox", "dropdown"]:
            # Handle option-based questions
            if isinstance(value, list):  # Multiple choice (checkbox)
              for option_id in value:
                option = QuestionOption.objects.get(id=option_id)
                answer.selected_options.add(option)

                # Save answers for subquestions if they exist
                _save_subquestion_answers(submission, option, form.cleaned_data)
            else:  # Single choice (radio, dropdown)
              option = QuestionOption.objects.get(id=value)
              answer.selected_options.add(option)

              # Save answers for subquestions if they exist
              _save_subquestion_answers(submission, option, form.cleaned_data)
          else:
            # Handle text-based questions
            answer.text_answer = str(value)

          answer.save()

      # Mark invitation as responded if applicable
      if request.user.is_authenticated:
        invitation = survey.invitations.filter(user=request.user, is_active=True).first()
        if invitation:
          invitation.mark_as_responded()

      return render(request, "survey/submission_success.html", {"survey": survey, "submission": submission})
  else:
    form = SurveyResponseForm(survey)

  # Get all pages with questions for rendering
  pages = survey.pages.prefetch_related("questions__options").all()

  context = {
    "survey": survey,
    "form": form,
    "pages": pages,
  }

  return render(request, "survey/survey_response_form.html", context)


def _save_subquestion_answers(submission, option, cleaned_data):
  """Save answers for subquestions associated with an option"""
  if option.sub_questions.exists():
    for sub_question in option.sub_questions.all():
      field_name = f"subquestion_{sub_question.id}"
      value = cleaned_data.get(field_name)

      if value:
        answer = SubmissionAnswer.objects.create(submission=submission, question=sub_question)

        if sub_question.question_type in ["radio", "checkbox", "dropdown"]:
          # Handle option-based subquestions
          if isinstance(value, list):  # Multiple choice (checkbox)
            for sub_option_id in value:
              sub_option = QuestionOption.objects.get(id=sub_option_id)
              answer.selected_options.add(sub_option)
          else:  # Single choice (radio, dropdown)
            sub_option = QuestionOption.objects.get(id=value)
            answer.selected_options.add(sub_option)
        else:
          # Handle text-based subquestions
          answer.text_answer = str(value)

        answer.save()


def option_subquestions(request, option_pk):
  """API endpoint to get subquestions for an option"""
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check if user can access this survey
  survey = option.question.survey
  if not survey.is_accessible_by_user(request.user if request.user.is_authenticated else None):
    return JsonResponse({"status": "error", "message": "Access denied"}, status=403)

  if not option.sub_questions.exists():
    return JsonResponse({"status": "success", "html": "", "has_subquestions": False})

  # Render subquestions HTML
  from django.template.loader import render_to_string

  html = render_to_string(
    "survey/includes/subquestions.html", {"option": option, "sub_questions": option.sub_questions.all()}
  )

  return JsonResponse({"status": "success", "html": html, "has_subquestions": True})

  context = {
    "survey": survey,
    "form": form,
    "pages": pages,
  }

  return render(request, "survey/survey_response_form.html", context)


@login_required
@login_required
def submissions_districts_for_state(request):
  """Return districts JSON for the submissions_list page, filtered by state (and optionally by survey), scoped to the user's accessible surveys."""
  state = request.GET.get("state", "")
  survey_filter = request.GET.get("survey", "")
  from .selectors import apply_answer_visibility, get_accessible_surveys

  accessible_surveys = get_accessible_surveys(request.user)

  district_qs = apply_answer_visibility(
    SubmissionAnswer.objects.filter(
      submission__survey__in=accessible_surveys,
      question__key="district",
      text_answer__isnull=False,
    ).exclude(text_answer=""),
    request.user,
  )

  if survey_filter:
    district_qs = district_qs.filter(submission__survey_id=survey_filter)

  if state:
    state_submission_ids = apply_answer_visibility(
      SubmissionAnswer.objects.filter(
        submission__survey__in=accessible_surveys,
        question__key="state",
        text_answer=state,
      ),
      request.user,
    )
    if survey_filter:
      state_submission_ids = state_submission_ids.filter(submission__survey_id=survey_filter)
    district_qs = district_qs.filter(submission_id__in=state_submission_ids.values_list("submission_id", flat=True))

  districts = list(district_qs.values_list("text_answer", flat=True).distinct().order_by("text_answer"))
  return JsonResponse({"districts": districts})


def submissions_list(request):
  """List all submissions across all surveys"""
  # Get search parameters
  search_query = request.GET.get("search", "")
  survey_filter = request.GET.get("survey", "")
  status_filter = request.GET.get("status", "")
  archive_filter = request.GET.get("archive", "active")
  show_duplicates = request.GET.get("show_duplicates", "") == "1"
  date_from = request.GET.get("date_from", "")
  date_to = request.GET.get("date_to", "")
  page_size = request.GET.get("page_size", "20")
  state_filter = request.GET.get("state", "")
  district_filter = request.GET.get("district", "")
  clf_filter = request.GET.get("clf", "")

  # Validate page_size
  if page_size not in ["10", "20", "25", "50"]:
    page_size = "20"

  # Get all surveys the user can access
  from .selectors import get_accessible_surveys, get_selectable_surveys
  accessible_surveys = get_accessible_surveys(request.user)
  # The dropdown offers only what was granted; the queryset above keeps the
  # full role-based scope so existing data visibility is unchanged.
  selectable_surveys = get_selectable_surveys(request.user)

  from .selectors import apply_answer_visibility, apply_submission_visibility, parse_submissions_date_filter

  # Get distinct state values across accessible surveys
  state_answer_qs = apply_answer_visibility(
    SubmissionAnswer.objects.filter(
      submission__survey__in=accessible_surveys,
      question__key="state",
      text_answer__isnull=False,
    ).exclude(text_answer=""),
    request.user,
  )

  if survey_filter:
    state_answer_qs = state_answer_qs.filter(submission__survey_id=survey_filter)

  states = state_answer_qs.values_list("text_answer", flat=True).distinct().order_by("text_answer")

  # Get distinct district values — filtered by selected state if one is chosen
  district_answer_qs = apply_answer_visibility(
    SubmissionAnswer.objects.filter(
      submission__survey__in=accessible_surveys,
      question__key="district",
      text_answer__isnull=False,
    ).exclude(text_answer=""),
    request.user,
  )

  if survey_filter:
    district_answer_qs = district_answer_qs.filter(submission__survey_id=survey_filter)

  if state_filter:
    # Only show districts for submissions that belong to the selected state
    state_submission_ids = apply_answer_visibility(
      SubmissionAnswer.objects.filter(
        submission__survey__in=accessible_surveys,
        question__key="state",
        text_answer=state_filter,
      ),
      request.user,
    ).values_list("submission_id", flat=True)
    district_answer_qs = district_answer_qs.filter(submission_id__in=state_submission_ids)

  districts = district_answer_qs.values_list("text_answer", flat=True).distinct().order_by("text_answer")

  # Get distinct CLF values (survey 6 "Name of CLF" question). The value may be
  # stored as free text or as a selected dropdown option, so gather both. On
  # environments without question CLF_QUESTION_ID this simply yields no options.
  from .selectors import CLF_QUESTION_ID
  clf_answer_qs = apply_answer_visibility(
    SubmissionAnswer.objects.filter(
      submission__survey__in=accessible_surveys,
      submission__is_archived=False,
      question_id=CLF_QUESTION_ID,
    ),
    request.user,
  )
  if survey_filter:
    clf_answer_qs = clf_answer_qs.filter(submission__survey_id=survey_filter)
  if status_filter:
    clf_answer_qs = clf_answer_qs.filter(submission__status=status_filter)
  clf_date_from = parse_submissions_date_filter(date_from)
  if clf_date_from:
    clf_answer_qs = clf_answer_qs.filter(submission__submitted_at__gte=clf_date_from)
  clf_date_to = parse_submissions_date_filter(date_to, is_end=True)
  if clf_date_to:
    clf_answer_qs = clf_answer_qs.filter(submission__submitted_at__lte=clf_date_to)
  if state_filter:
    clf_answer_qs = clf_answer_qs.filter(
      submission__answers__question__key="state",
      submission__answers__text_answer=state_filter,
    )
  if district_filter:
    clf_answer_qs = clf_answer_qs.filter(
      submission__answers__question__key="district",
      submission__answers__text_answer=district_filter,
    )

  clf_text_values = clf_answer_qs.filter(
    text_answer__isnull=False,
  ).exclude(text_answer="").values_list("text_answer", flat=True)
  clf_option_values = clf_answer_qs.filter(
    selected_options__isnull=False,
  ).values_list("selected_options__text", flat=True)
  clfs = sorted({
    c.strip() for c in list(clf_text_values) + list(clf_option_values) if c and c.strip()
  })

  submissions = apply_submission_visibility(
    Submission.objects.filter(survey__in=accessible_surveys),
    request.user,
  )

  # Apply archive filter (default to active/non-archived)
  if archive_filter == "active":
    submissions = submissions.filter(is_archived=False)
  elif archive_filter == "archived":
    submissions = submissions.filter(is_archived=True)
  # else "all" - no filter

  # Apply filters
  if search_query:
    submissions = submissions.filter(
      Q(user__username__icontains=search_query)
      | Q(user__first_name__icontains=search_query)
      | Q(user__last_name__icontains=search_query)
      | Q(survey__title__icontains=search_query)
      | Q(ip_address__icontains=search_query)
    )

  if survey_filter:
    submissions = submissions.filter(survey_id=survey_filter)

  if status_filter:
    submissions = submissions.filter(status=status_filter)

  if state_filter:
    submissions = submissions.filter(
      answers__question__key="state",
      answers__text_answer=state_filter,
    )

  if district_filter:
    submissions = submissions.filter(
      answers__question__key="district",
      answers__text_answer=district_filter,
    )

  if clf_filter:
    submissions = submissions.filter(
      Q(answers__question_id=CLF_QUESTION_ID, answers__text_answer=clf_filter)
      | Q(answers__question_id=CLF_QUESTION_ID, answers__selected_options__text=clf_filter)
    ).distinct()

  from .selectors import parse_submissions_date_filter
  dt_from = parse_submissions_date_filter(date_from)
  if dt_from:
    submissions = submissions.filter(submitted_at__gte=dt_from)
  dt_to = parse_submissions_date_filter(date_to, is_end=True)
  if dt_to:
    submissions = submissions.filter(submitted_at__lte=dt_to)

  # Show duplicates filter - only show submissions with duplicate computed_titles
  if show_duplicates:
    from django.db.models import Count
    # Find computed_titles that have duplicates (count > 1)
    duplicate_titles = submissions.values('computed_title').annotate(
      title_count=Count('id')
    ).filter(title_count__gt=1, computed_title__isnull=False).values_list('computed_title', flat=True)
    # Filter submissions to only those with duplicate titles
    submissions = submissions.filter(computed_title__in=duplicate_titles)

  # Keep zero-answer drafts visible and flag them in the UI instead of hiding
  # them; answer_count drives the "No answers" badge in the table.
  from django.db.models import Count
  submissions = submissions.annotate(answer_count=Count('answers'))

  # Order by most recent first (or by title when showing duplicates)
  if show_duplicates:
    # Sort by title first to group duplicates together, then by date
    submissions = submissions.select_related("survey", "user").prefetch_related(
      "answers", "answers__question", "answers__selected_options"
    ).annotate(answered_count=Count("answers")).order_by("computed_title", "-submitted_at")
  else:
    submissions = submissions.select_related("survey", "user").prefetch_related(
      "answers", "answers__question", "answers__selected_options"
    ).annotate(answered_count=Count("answers")).order_by("-submitted_at")

  # Paginate
  paginator = Paginator(submissions, int(page_size))
  page_number = request.GET.get("page")
  page_obj = paginator.get_page(page_number)

  context = {
    "page_title": "Submissions",
    "page_subtitle": "View and manage all survey submissions",
    "page_obj": page_obj,
    "search_query": search_query,
    "survey_filter": survey_filter,
    "status_filter": status_filter,
    "archive_filter": archive_filter,
    "show_duplicates": show_duplicates,
    "date_from": date_from,
    "date_to": date_to,
    "date_preset": request.GET.get("date_preset", ""),
    "accessible_surveys": accessible_surveys,
    "selectable_surveys": selectable_surveys,
    "total_submissions": submissions.count(),
    "page_size": page_size,
    "states": states,
    "state_filter": state_filter,
    "districts": districts,
    "district_filter": district_filter,
    "clfs": clfs,
    "clf_filter": clf_filter,
  }
  return render(request, "survey/submissions_list.html", context)


@login_required
def toggle_submission_archive(request, pk):
  """Toggle archive status of a submission"""
  from django.contrib import messages

  # Get all surveys the user can access
  from .selectors import get_accessible_surveys
  accessible_surveys = get_accessible_surveys(request.user)

  submission = get_object_or_404(Submission, pk=pk, survey__in=accessible_surveys)

  # Check permission
  if not request.user.has_perm("survey.change_submission", submission):
    messages.error(request, _("Permission denied"))
    return redirect("survey:submissions_list")

  submission.is_archived = not submission.is_archived
  submission.save(update_fields=["is_archived"])

  status = "archived" if submission.is_archived else "unarchived"
  messages.success(request, _(f"Submission has been {status}."))

  # Build redirect URL with preserved filter parameters
  from django.urls import reverse
  from urllib.parse import urlencode

  # Use Next parameter if provided, otherwise default to submissions_list
  next_url = request.GET.get("next")
  base_url = next_url if next_url else reverse("survey:submissions_list")
  params = {}

  # Preserve filter parameters from POST
  for param in ["search", "survey", "status", "archive", "show_duplicates", "date_from", "date_to", "page_size", "page", "state", "district"]:
    value = request.POST.get(param, "").strip()
    if value:
      params[param] = value

  if params:
    # If next_url already has query params, we need to append them properly
    if '?' in base_url:
        return redirect(f"{base_url}&{urlencode(params)}")
    return redirect(f"{base_url}?{urlencode(params)}")

  return redirect(base_url)


@login_required
def download_submissions_list(request):
  """Download current page submissions as Excel"""
  from openpyxl import Workbook
  from openpyxl.styles import Font, PatternFill, Alignment

  # Require survey filter
  survey_id = request.GET.get("survey")
  if not survey_id:
    return JsonResponse({"error": "Please select a survey first"}, status=400)

  survey = get_object_or_404(Survey, pk=survey_id)

  # Display mode: 'values' shows option codes, 'labels' (default) shows option text
  display_mode = request.GET.get("display", "labels")

  # Get filter parameters
  search_query = request.GET.get("search", "")
  status_filter = request.GET.get("status", "")
  archive_filter = request.GET.get("archive", "active")
  show_duplicates = request.GET.get("show_duplicates", "") == "1"
  date_from = request.GET.get("date_from", "")
  date_to = request.GET.get("date_to", "")
  page_size = request.GET.get("page_size", "20")
  page_number = request.GET.get("page", 1)

  # Validate page_size
  if page_size not in ["10", "20", "25", "50"]:
    page_size = "20"

  # Build submissions queryset (same logic as submissions_list)
  from .selectors import apply_submission_visibility
  submissions = apply_submission_visibility(
    Submission.objects.filter(survey=survey),
    request.user,
  )

  # Apply archive filter (default to active/non-archived)
  if archive_filter == "active":
    submissions = submissions.filter(is_archived=False)
  elif archive_filter == "archived":
    submissions = submissions.filter(is_archived=True)
  # else "all" - no filter

  # Show duplicates filter - only show submissions with duplicate computed_titles
  if show_duplicates:
    from django.db.models import Count
    duplicate_titles = submissions.values('computed_title').annotate(
      title_count=Count('id')
    ).filter(title_count__gt=1, computed_title__isnull=False).values_list('computed_title', flat=True)
    submissions = submissions.filter(computed_title__in=duplicate_titles)

  if search_query:
    submissions = submissions.filter(
      Q(user__username__icontains=search_query)
      | Q(user__first_name__icontains=search_query)
      | Q(user__last_name__icontains=search_query)
      | Q(ip_address__icontains=search_query)
    )

  if status_filter:
    submissions = submissions.filter(status=status_filter)

  from .selectors import parse_submissions_date_filter
  dt_from = parse_submissions_date_filter(date_from)
  if dt_from:
    submissions = submissions.filter(submitted_at__gte=dt_from)
  dt_to = parse_submissions_date_filter(date_to, is_end=True)
  if dt_to:
    submissions = submissions.filter(submitted_at__lte=dt_to)

  # Exclude submissions with 0 answers
  from django.db.models import Count
  submissions = submissions.annotate(answer_count=Count('answers')).filter(answer_count__gt=0)

  # Order by most recent first (or by title when showing duplicates)
  if show_duplicates:
    submissions = submissions.select_related("survey", "user").prefetch_related(
      'answers', 'answers__question', 'answers__selected_options', 'indicator_results', 'indicator_results__indicator'
    ).order_by("computed_title", "-submitted_at")
  else:
    submissions = submissions.select_related("survey", "user").prefetch_related(
      'answers', 'answers__question', 'answers__selected_options', 'indicator_results', 'indicator_results__indicator'
    ).order_by("-submitted_at")

  # Get current page only
  paginator = Paginator(submissions, int(page_size))
  page_obj = paginator.get_page(page_number)
  current_submissions = list(page_obj.object_list)

  # Get questions ordered by page order, then question order
  questions = survey.questions.select_related('page').prefetch_related(
    'options'
  ).order_by('page__order', 'order', 'id')

  # Precompute which questions have "other" option
  questions_with_other = set()
  for q in questions:
    if q.question_type in ['radio', 'checkbox', 'dropdown']:
      # Check if option text is exactly "Other" or starts with "Other " or "Other("
      if any(
        opt.text.strip().lower() == 'other'
        for opt in q.options.all()
      ):
        questions_with_other.add(q.id)

  # Get indicators for the survey
  indicators = survey.indicators.filter(is_active=True)

  # Build column structure: for option-based questions, add a column for each option
  columns = []
  for q in questions:
    if q.question_type == 'radio':
      columns.append((q, 'radio_single'))
      if q.id in questions_with_other:
        columns.append((q, 'other'))
    elif q.question_type in ['checkbox', 'dropdown']:
      options = q.options.all().order_by('order', 'id')
      for opt in options:
        columns.append((q, opt))
      if q.id in questions_with_other:
        columns.append((q, 'other'))
    else:
      columns.append((q, None))

  # Build Excel
  wb = Workbook()
  ws = wb.active
  ws.title = "Submissions"

  # Row 1: Bold heading indicating display mode
  display_label = "Data in Labels" if display_mode == 'labels' else "Data in Values"
  headers_count = 4 + len(columns) + len(indicators)  # 4 for ID, date, submitter, app version
  ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=headers_count)
  title_cell = ws.cell(row=1, column=1, value=display_label)
  title_cell.font = Font(bold=True, size=14, color="FFFFFF")
  if display_mode == 'values':
    title_cell.fill = PatternFill(start_color="1565C0", end_color="1565C0", fill_type="solid")
  else:
    title_cell.fill = PatternFill(start_color="2E7D32", end_color="2E7D32", fill_type="solid")
  title_cell.alignment = Alignment(horizontal="center", vertical="center")
  ws.row_dimensions[1].height = 30

  # Row 2: Header row
  from django.utils import timezone as tz
  ist_tz = tz.get_fixed_timezone(330)

  headers = ["Submission ID", "Submitted At (IST)", "Submitter", "App Version"]
  for q, opt in columns:
    if opt == 'radio_single':
      headers.append(f"{q.text}")
    elif opt == 'other':
      headers.append(f"{q.text}::Other")
    elif opt:
      headers.append(f"{q.text}::{opt.text}")
    else:
      headers.append(q.text)

  for indicator in indicators:
    headers.append(indicator.name)

  for col_idx, header in enumerate(headers, start=1):
    cell = ws.cell(row=2, column=col_idx, value=header)
    cell.font = Font(bold=True)
    cell.fill = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
    cell.alignment = Alignment(horizontal="center", wrap_text=True)
  ws.row_dimensions[2].height = 40

  # Data rows (starting from row 3)
  for row_idx, submission in enumerate(current_submissions, start=3):
    submitted_at_ist = submission.submitted_at.astimezone(ist_tz)
    ws.cell(row=row_idx, column=1, value=submission.pk)
    ws.cell(row=row_idx, column=2, value=submitted_at_ist.strftime("%b %d, %Y %I:%M %p"))
    ws.cell(row=row_idx, column=3, value=submission.get_submitter_display())
    ws.cell(row=row_idx, column=4, value=submission.app_version or "")

    # Build answers dict for efficient lookup
    answers_dict = {a.question_id: a for a in submission.answers.all()}

    for col_offset, (q, opt) in enumerate(columns):
      col_idx = col_offset + 5  # offset by 4 metadata columns + 1 for 1-indexing
      answer = answers_dict.get(q.id)
      if opt == 'other':
        ws.cell(row=row_idx, column=col_idx, value=answer.other_text if answer and answer.other_text else "")
      elif opt == 'radio_single':
        if answer:
          selected_options = answer.selected_options.all()
          if selected_options:
            if display_mode == 'values':
              opt_obj = selected_options[0]
              ws.cell(row=row_idx, column=col_idx, value=opt_obj.value if opt_obj.value else str(opt_obj.order + 1))
            else:
              ws.cell(row=row_idx, column=col_idx, value=selected_options[0].text)
          else:
            ws.cell(row=row_idx, column=col_idx, value="")
        else:
          ws.cell(row=row_idx, column=col_idx, value="")
      elif opt:
        if answer:
          selected_option_ids = {o.id for o in answer.selected_options.all()}
          ws.cell(row=row_idx, column=col_idx, value="1" if opt.id in selected_option_ids else "0")
        else:
          ws.cell(row=row_idx, column=col_idx, value="0")
      else:
        if answer:
          ws.cell(row=row_idx, column=col_idx, value=answer.text_answer or "")
        else:
          ws.cell(row=row_idx, column=col_idx, value="")

    # Indicator results
    results_dict = {r.indicator_id: r for r in submission.indicator_results.all()}
    for ind_offset, indicator in enumerate(indicators):
      col_idx = 5 + len(columns) + ind_offset
      result = results_dict.get(indicator.id)
      ws.cell(row=row_idx, column=col_idx, value=str(result.result) if result and result.success else "")

  # Return response
  response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
  response['Content-Disposition'] = f'attachment; filename="{survey.title}_submissions_{display_mode}_page{page_number}.xlsx"'
  wb.save(response)
  return response


def _submission_upload_denied(request, submission):
  """Return an error response unless the session or Bearer user may change this submission."""
  user = request.user
  if not user.is_authenticated:
    try:
      token = get_credentials(request)
      user = get_user_by_token(token, request) if token else None
    except JSONWebTokenError:
      user = None
  if user is None:
    return JsonResponse({"status": "error", "message": "Authentication required"}, status=401)
  if submission.user_id != user.pk and not (user.is_staff or user.is_superuser):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)
  return None


@require_POST
@csrf_exempt
def upload_signature_image(request, submission_pk):
  """REST API endpoint to upload signature image for a submission"""
  # Decode relay ID if it's a GraphQL global ID
  decoded_submission_pk = decode_relay_id(submission_pk)
  submission = get_object_or_404(Submission, pk=decoded_submission_pk)

  denied = _submission_upload_denied(request, submission)
  if denied:
    return denied

  try:
    if "signature" not in request.FILES:
      return JsonResponse({"status": "error", "message": "No signature image provided"}, status=400)

    signature_file = request.FILES["signature"]

    # Validate file size (max 5MB)
    if signature_file.size > 5 * 1024 * 1024:
      return JsonResponse({"status": "error", "message": "File size must be less than 5MB"}, status=400)

    # Save the file
    submission.signature_image = signature_file
    submission.save()

    return JsonResponse(
      {
        "status": "success",
        "message": "Signature uploaded successfully",
        "signature_url": request.build_absolute_uri(submission.signature_image.url),
      }
    )

  except Exception as e:
    return JsonResponse({"status": "error", "message": f"Error uploading signature: {str(e)}"}, status=400)


@require_POST
@csrf_exempt
def upload_photo_image(request, submission_pk):
  """REST API endpoint to upload photo image for a submission"""
  # Decode relay ID if it's a GraphQL global ID
  decoded_submission_pk = decode_relay_id(submission_pk)
  submission = get_object_or_404(Submission, pk=decoded_submission_pk)

  denied = _submission_upload_denied(request, submission)
  if denied:
    return denied

  try:
    if "photo" not in request.FILES:
      return JsonResponse({"status": "error", "message": "No photo image provided"}, status=400)

    photo_file = request.FILES["photo"]

    # Validate file type
    # if not photo_file.content_type.startswith('image/'):
    #     return JsonResponse({"status": "error", "message": "Only image files are allowed"}, status=400)

    # Validate file size (max 10MB)
    if photo_file.size > 10 * 1024 * 1024:
      return JsonResponse({"status": "error", "message": "File size must be less than 10MB"}, status=400)

    # Save the file
    submission.photo_image = photo_file
    submission.save()

    return JsonResponse(
      {
        "status": "success",
        "message": "Photo uploaded successfully",
        "photo_url": request.build_absolute_uri(submission.photo_image.url),
      }
    )

  except Exception as e:
    return JsonResponse({"status": "error", "message": f"Error uploading photo: {str(e)}"}, status=400)


@login_required
@csrf_exempt
def get_submission_images(request, submission_pk):
  """REST API endpoint to get image URLs for a submission"""
  # Decode relay ID if it's a GraphQL global ID
  decoded_submission_pk = decode_relay_id(submission_pk)
  submission = get_object_or_404(Submission, pk=decoded_submission_pk)

  # Check permission - user can only view their own submission images (managers can view all)
  is_manager = request.user.groups.filter(name='Manager').exists()
  if not (request.user.is_staff or request.user.is_superuser or is_manager or submission.user == request.user):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  data = {"status": "success", "submission_id": submission.id, "signature_url": None, "photo_url": None}

  if submission.signature_image:
    data["signature_url"] = request.build_absolute_uri(submission.signature_image.url)

  if submission.photo_image:
    data["photo_url"] = request.build_absolute_uri(submission.photo_image.url)

  return JsonResponse(data)


@login_required
def get_question_conditions(request, question_pk):
  """REST API endpoint to get conditions for a question"""
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  conditions = question.conditions.all().order_by("order", "id")
  conditions_data = []

  for condition in conditions:
    conditions_data.append(
      {
        "id": condition.id,
        "target_question": condition.target_question.id,
        "condition_type": condition.condition_type,
        "value": condition.value,
        "logic_operator": condition.logic_operator,
        "order": condition.order,
      }
    )

  return JsonResponse({"status": "success", "conditions": conditions_data})


@login_required
@require_POST
def toggle_question_conditional(request, question_pk):
  """REST API endpoint to toggle conditional logic for a question"""
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)
    is_conditional = data.get("is_conditional", False)

    question.is_conditional = is_conditional
    question.save()

    return JsonResponse({"status": "success", "message": "Conditional logic updated successfully"})

  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def add_question_condition(request, question_pk):
  """REST API endpoint to add a condition to a question"""
  question = get_object_or_404(Question, pk=question_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    data = json.loads(request.body)

    # Validate required fields
    target_question_id = data.get("target_question_id")
    condition_type = data.get("condition_type")

    if not target_question_id:
      return JsonResponse({"status": "error", "message": "Target question is required"}, status=400)

    if not condition_type:
      return JsonResponse({"status": "error", "message": "Condition type is required"}, status=400)

    # Validate condition type
    valid_condition_types = [
      "equals",
      "not_equals",
      "contains",
      "not_contains",
      "greater_than",
      "less_than",
      "is_empty",
      "is_not_empty",
    ]
    if condition_type not in valid_condition_types:
      return JsonResponse({"status": "error", "message": "Invalid condition type"}, status=400)

    # For conditions that require value, validate value is provided
    if condition_type not in ["is_empty", "is_not_empty"] and not data.get("value"):
      return JsonResponse({"status": "error", "message": "Value is required for this condition type"}, status=400)

    # Get target question
    target_question = get_object_or_404(Question, pk=target_question_id)

    # Validate target question belongs to same survey
    if target_question.survey != question.survey:
      return JsonResponse(
        {"status": "error", "message": "Target question must belong to the same survey"}, status=400
      )

    # Create the condition
    condition = QuestionCondition.objects.create(
      question=question,
      target_question=target_question,
      condition_type=condition_type,
      value=data.get("value", ""),
      logic_operator=data.get("logic_operator", "AND"),
      order=question.conditions.count(),  # Add at the end
    )

    return JsonResponse(
      {
        "status": "success",
        "message": "Condition added successfully",
        "condition": {
          "id": condition.id,
          "target_question": condition.target_question.id,
          "condition_type": condition.condition_type,
          "value": condition.value,
          "logic_operator": condition.logic_operator,
          "order": condition.order,
        },
      }
    )

  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def delete_question_condition(request, condition_pk):
  """REST API endpoint to delete a condition"""
  condition = get_object_or_404(QuestionCondition, pk=condition_pk)

  # Check permission
  if not request.user.has_perm("survey.change_survey", condition.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  try:
    condition_id = condition.id
    condition.delete()

    return JsonResponse(
      {"status": "success", "message": "Condition deleted successfully", "condition_id": condition_id}
    )

  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def get_option_conditions(request, option_pk):
  """REST API endpoint to get conditions for an option"""
  option = get_object_or_404(QuestionOption, pk=option_pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  conditions = option.conditions.all().order_by("order", "id")
  conditions_data = []
  for condition in conditions:
    conditions_data.append(
      {
        "id": condition.id,
        "target_question": condition.target_question.id,
        "condition_type": condition.condition_type,
        "value": condition.value,
        "logic_operator": condition.logic_operator,
        "order": condition.order,
      }
    )

  return JsonResponse(
    {
      "status": "success",
      "conditions": conditions_data,
      "is_conditional": option.is_conditional,
    }
  )


@login_required
@require_POST
def toggle_option_conditional(request, option_pk):
  """Toggle conditional logic for an option"""
  option = get_object_or_404(QuestionOption, pk=option_pk)
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)
  try:
    data = json.loads(request.body)
    is_conditional = data.get("is_conditional", False)
    option.is_conditional = bool(is_conditional)
    option.save(update_fields=["is_conditional"])
    return JsonResponse({"status": "success", "message": "Option conditional logic updated"})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def add_option_condition(request, option_pk):
  """Add a condition to an option"""
  option = get_object_or_404(QuestionOption, pk=option_pk)
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)
  try:
    data = json.loads(request.body)
    target_question_id = data.get("target_question_id")
    condition_type = data.get("condition_type")

    if not target_question_id:
      return JsonResponse({"status": "error", "message": "Target question is required"}, status=400)
    if not condition_type:
      return JsonResponse({"status": "error", "message": "Condition type is required"}, status=400)

    valid_condition_types = [
      "equals",
      "not_equals",
      "contains",
      "not_contains",
      "greater_than",
      "less_than",
      "is_empty",
      "is_not_empty",
    ]
    if condition_type not in valid_condition_types:
      return JsonResponse({"status": "error", "message": "Invalid condition type"}, status=400)

    if condition_type not in ["is_empty", "is_not_empty"] and not data.get("value"):
      return JsonResponse({"status": "error", "message": "Value is required for this condition type"}, status=400)

    target_question = get_object_or_404(Question, pk=target_question_id)
    if target_question.survey != option.question.survey:
      return JsonResponse(
        {"status": "error", "message": "Target question must belong to the same survey"}, status=400
      )

    condition = OptionCondition.objects.create(
      option=option,
      target_question=target_question,
      condition_type=condition_type,
      value=data.get("value", ""),
      logic_operator=data.get("logic_operator", "AND"),
      order=option.conditions.count(),
    )
    return JsonResponse(
      {
        "status": "success",
        "message": "Condition added successfully",
        "condition": {
          "id": condition.id,
          "target_question": condition.target_question.id,
          "condition_type": condition.condition_type,
          "value": condition.value,
          "logic_operator": condition.logic_operator,
          "order": condition.order,
        },
      }
    )
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_POST
def delete_option_condition(request, condition_pk):
  """Delete an option condition"""
  condition = get_object_or_404(OptionCondition, pk=condition_pk)
  if not request.user.has_perm("survey.change_survey", condition.option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)
  try:
    cid = condition.id
    condition.delete()
    return JsonResponse({"status": "success", "message": "Condition deleted successfully", "condition_id": cid})
  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def option_warning_config(request, option_pk):
  option = get_object_or_404(QuestionOption, pk=option_pk)

  if request.method == "GET":
    if not request.user.has_perm("survey.view_survey", option.question.survey):
      return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

    raw_warning = option.option_warning if isinstance(option.option_warning, dict) else {}
    if raw_warning and "selected" not in raw_warning and "not_selected" not in raw_warning:
      # Backward compatibility for legacy structure
      raw_warning = {"selected": raw_warning}

    warnings_response = {"selected": None, "not_selected": None}
    target_questions = {}

    for key in ["selected", "not_selected"]:
      entry = raw_warning.get(key)
      if not entry:
        continue
      warnings_response[key] = {
        "target_question_id": entry.get("target_question_id"),
        "condition_type": entry.get("condition_type"),
        "value": entry.get("value", ""),
        "message": (entry.get("message") or option.option_warning_text or "").strip(),
      }
      target_id = entry.get("target_question_id")
      if target_id:
        question = (
          Question.objects.select_related("page")
          .filter(pk=target_id)
          .only("id", "text", "order", "question_type", "page_id", "page__title", "page__order")
          .first()
        )
        if question:
          target_questions[str(question.id)] = {
            "id": question.id,
            "text": question.text,
            "order": question.order,
            "question_type": question.question_type,
            "page_id": question.page_id,
            "page_title": getattr(question.page, "title", ""),
            "page_order": getattr(question.page, "order", None),
          }

    return JsonResponse(
      {
        "status": "success",
        "warnings": warnings_response,
        "target_questions": target_questions,
        "option_warning": option.option_warning,
        "option_warning_text": option.option_warning_text or "",
      }
    )

  # POST and DELETE require change permission
  if not request.user.has_perm("survey.change_survey", option.question.survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  if request.method == "DELETE":
    option.option_warning = None
    option.option_warning_text = ""
    option.save(update_fields=["option_warning", "option_warning_text"])
    return JsonResponse({"status": "success", "message": "Option warnings cleared"})

  try:
    data = json.loads(request.body)
  except json.JSONDecodeError:
    return JsonResponse({"status": "error", "message": "Invalid JSON payload"}, status=400)

  valid_condition_types = [choice[0] for choice in OptionCondition.CONDITION_TYPES]

  def build_entry(type_key, payload):
    if not payload or not payload.get("enabled"):
      return None

    target_question_id = payload.get("target_question_id")
    condition_type = payload.get("condition_type")
    raw_value = payload.get("value", "")
    message = (payload.get("message") or "").strip()

    if not target_question_id:
      raise ValidationError({'option_warning': f"Target question is required for {type_key.replace('_', ' ')} warning."})
    try:
      target_question_id_int = int(target_question_id)
    except (TypeError, ValueError):
      raise ValidationError({'option_warning': f"Invalid target question for {type_key.replace('_', ' ')} warning."})

    target_question = get_object_or_404(Question, pk=target_question_id_int)
    if target_question.survey_id != option.question.survey_id:
      raise ValidationError({'option_warning': 'Warning target question must belong to the same survey.'})

    if condition_type not in valid_condition_types:
      raise ValidationError({'option_warning': 'Invalid condition type provided.'})

    requires_value = condition_type not in ['is_empty', 'is_not_empty']
    normalized_value = ''
    if requires_value:
      # For 'in' and 'not_in' conditions, keep value as list
      if condition_type in ['in', 'not_in', 'is_in', 'is in', 'is-in', 'not in', 'not-in']:
        if isinstance(raw_value, list):
          normalized_value = raw_value
          if not normalized_value:
            raise ValidationError({'option_warning': 'Warning value list cannot be empty for in/not_in conditions.'})
        elif isinstance(raw_value, str):
          raw_value_str = raw_value.strip()
          if not raw_value_str:
            raise ValidationError({'option_warning': 'Warning value is required for this condition type.'})
          # Try to parse as JSON array first
          try:
            parsed = json.loads(raw_value_str)
            if isinstance(parsed, list):
              normalized_value = parsed
            else:
              # Fall back to comma-separated values
              normalized_value = [v.strip() for v in raw_value_str.split(',') if v.strip()]
          except json.JSONDecodeError:
            # Fall back to comma-separated values
            normalized_value = [v.strip() for v in raw_value_str.split(',') if v.strip()]
          if not normalized_value:
            raise ValidationError({'option_warning': 'Warning value list cannot be empty for in/not_in conditions.'})
        else:
          raise ValidationError({'option_warning': 'Warning value must be a list or comma-separated string for in/not_in conditions.'})
      else:
        normalized_value = str(raw_value or '').strip()
        if not normalized_value:
          raise ValidationError({'option_warning': 'Warning value is required for this condition type.'})

    if not message:
      raise ValidationError({'option_warning': 'Warning message is required when configuring a warning.'})

    return {
      'target_question_id': target_question.id,
      'condition_type': condition_type,
      'value': normalized_value,
      'message': message,
    }

  try:
    selected_entry = build_entry('selected', data.get('selected'))
    not_selected_entry = build_entry('not_selected', data.get('not_selected'))
  except ValidationError as exc:
    messages = []
    for field_msgs in exc.message_dict.values():
      messages.extend(field_msgs)
    return JsonResponse({"status": "error", "message": " ".join(messages)}, status=400)

  warnings_payload = {}
  if selected_entry:
    warnings_payload['selected'] = selected_entry
  if not_selected_entry:
    warnings_payload['not_selected'] = not_selected_entry

  if not warnings_payload:
    return JsonResponse({"status": "error", "message": "Configure at least one warning."}, status=400)

  option.option_warning = warnings_payload
  primary_message = selected_entry['message'] if selected_entry else not_selected_entry['message']
  option.option_warning_text = primary_message
  option.save(update_fields=["option_warning", "option_warning_text"])

  target_questions = {}
  for entry in warnings_payload.values():
    tq = (
      Question.objects.select_related("page")
      .filter(pk=entry['target_question_id'])
      .only("id", "text", "order", "question_type", "page_id", "page__title", "page__order")
      .first()
    )
    if tq:
      target_questions[str(tq.id)] = {
        "id": tq.id,
        "text": tq.text,
        "order": tq.order,
        "question_type": tq.question_type,
        "page_id": tq.page_id,
        "page_title": getattr(tq.page, "title", ""),
        "page_order": getattr(tq.page, "order", None),
      }

  return JsonResponse(
    {
      "status": "success",
      "message": "Option warnings saved",
      "warnings": warnings_payload,
      "option_warning": warnings_payload,
      "option_warning_text": option.option_warning_text,
      "target_questions": target_questions,
    }
  )


@login_required
@manager_or_permission_required("survey.view_survey", (Survey, "pk", "pk"), raise_exception=True)
def survey_indicators(request, pk):
  """View for managing survey indicators"""
  survey = get_object_or_404(Survey, pk=pk)
  indicators = survey.indicators.all().select_related("script")

  context = {
    "survey": survey,
    "indicators": indicators,
  }
  return render(request, "survey/survey_indicators.html", context)


class IndicatorForm(forms.ModelForm):
  # Add a field for direct code editing
  code = forms.CharField(
    widget=forms.Textarea(
      attrs={
        "class": "form-control",
        "rows": 10,
        "placeholder": "Enter Python code for the indicator calculation",
        "style": "display: none;",  # Hidden, will be replaced by Monaco editor
      }
    ),
    required=False,
    help_text="Python code that calculates the indicator result. The code should set a 'result' variable.",
  )

  class Meta:
    model = Indicator
    fields = ["name", "description", "script", "is_active"]
    widgets = {
      "name": forms.TextInput(
        attrs={"class": "form-control", "placeholder": "Enter indicator name", "maxlength": "100"}
      ),
      "description": forms.Textarea(
        attrs={"class": "form-control", "placeholder": "Enter indicator description (optional)", "rows": 3}
      ),
      "script": forms.Select(attrs={"class": "form-control", "style": "display: none;"}),  # Hidden
      "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
    }

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    # Make script and code fields not required since we're using Monaco editor
    self.fields["script"].required = False
    self.fields["code"].required = False
    # Set initial code value if editing an existing indicator
    if self.instance and self.instance.pk and self.instance.script:
      self.fields["code"].initial = self.instance.script.code

  def save(self, commit=True):
    # Get or create the script based on the code
    code = self.cleaned_data.get("code", "")
    name = self.cleaned_data.get("name", "")

    # Always create or update a script, even if code is empty (to avoid NULL constraint)
    if self.instance and self.instance.pk and self.instance.script:
      # Update existing script
      script = self.instance.script
      script.code = code or "# Empty script - please add Python code here"
      script.save()
    else:
      # Create new script with indicator name
      script = IndicatorScript.objects.create(
        name=f"{name} Script",
        description=f"Script for {name} indicator",
        code=code or "# Empty script - please add Python code here",
      )
      # Set the script on the instance before saving
      self.instance.script = script

    # Set the script on the instance data before calling parent save
    if hasattr(self.instance, "script") and self.instance.script:
      # Set the script_id directly to ensure it's properly saved
      self.instance.script_id = self.instance.script.id

    # Save the indicator instance
    indicator = super().save(commit=False)

    if commit:
      indicator.save()
      self.save_m2m()

    return indicator


@login_required
@manager_or_permission_required("survey.change_survey", (Survey, "pk", "survey_pk"), raise_exception=True)
def indicator_create(request, survey_pk):
  """Create a new indicator for a survey"""
  survey = get_object_or_404(Survey, pk=survey_pk)
  scripts = IndicatorScript.objects.all()

  if request.method == "POST":
    form = IndicatorForm(request.POST)
    if form.is_valid():
      indicator = form.save(commit=False)
      indicator.survey = survey
      indicator.save()
      messages.success(request, f'Indicator "{indicator.name}" created successfully!')
      return redirect("survey:survey_indicators", pk=survey.pk)
  else:
    form = IndicatorForm()

  context = {
    "survey": survey,
    "form": form,
    "scripts": scripts,
    "title": "Create Indicator"
  }
  return render(request, "survey/indicator_form.html", context)


@login_required
def indicator_edit(request, pk):
  """Edit an existing indicator"""
  try:
    indicator = Indicator.objects.get(pk=pk)
  except Indicator.DoesNotExist:
    messages.error(request, _("The indicator you are looking for does not exist or has been deleted."))
    # Try to redirect back to a reasonable location
    referer = request.META.get('HTTP_REFERER', '')
    if referer:
      return redirect(referer)
    return redirect("survey:survey_list")
  # Check if user has permission to change the survey that this indicator belongs to
  if not request.user.has_perm("survey.change_survey", indicator.survey):
    from django.core.exceptions import PermissionDenied
    raise PermissionDenied
  scripts = IndicatorScript.objects.all()

  if request.method == "POST":
    form = IndicatorForm(request.POST, instance=indicator)
    if form.is_valid():
      indicator = form.save()
      messages.success(request, f'Indicator "{indicator.name}" updated successfully!')
      return redirect("survey:survey_indicators", pk=indicator.survey.pk)
  else:
    form = IndicatorForm(instance=indicator)

  context = {
    "indicator": indicator,
    "survey": indicator.survey,
    "form": form,
    "scripts": scripts,
    "title": "Edit Indicator",
  }
  return render(request, "survey/indicator_form.html", context)


@login_required
def indicator_delete(request, pk):
  """Delete an indicator"""
  try:
    indicator = Indicator.objects.get(pk=pk)
  except Indicator.DoesNotExist:
    messages.error(request, _("The indicator you are looking for does not exist or has been deleted."))
    referer = request.META.get('HTTP_REFERER', '')
    if referer:
      return redirect(referer)
    return redirect("survey:survey_list")
  # Check if user has permission to change the survey that this indicator belongs to
  if not request.user.has_perm("survey.change_survey", indicator.survey):
    from django.core.exceptions import PermissionDenied
    raise PermissionDenied
  survey_pk = indicator.survey.pk

  if request.method == "POST":
    indicator_name = indicator.name
    indicator.delete()
    messages.success(request, f'Indicator "{indicator_name}" deleted successfully!')
    return redirect("survey:survey_indicators", pk=survey_pk)

  return render(request, "survey/indicator_confirm_delete.html", {"indicator": indicator})


@login_required
def indicator_execute(request, pk):
  """Execute an indicator on all submissions"""
  try:
    indicator = Indicator.objects.get(pk=pk)
  except Indicator.DoesNotExist:
    messages.error(request, _("The indicator you are looking for does not exist or has been deleted."))
    referer = request.META.get('HTTP_REFERER', '')
    if referer:
      return redirect(referer)
    return redirect("survey:survey_list")
  # Check if user has permission to view the survey that this indicator belongs to
  if not request.user.has_perm("survey.view_survey", indicator.survey):
    from django.core.exceptions import PermissionDenied

    raise PermissionDenied
  submissions = indicator.survey.submissions.all()

  results = []
  for submission in submissions:
    # Execute the indicator
    execution_result = indicator.execute(submission)

    # Save the result using update_or_create to avoid UNIQUE constraint errors
    result_value = execution_result["result"] if execution_result["result"] is not None else {}
    indicator_result, created = IndicatorResult.objects.update_or_create(
      indicator=indicator,
      submission=submission,
      defaults={
        'result': result_value,
        'success': execution_result["success"],
        'error_message': execution_result["error"],
      }
    )

    results.append(
      {
        "submission": submission,
        "result": indicator_result.result,
        "success": indicator_result.success,
        "error_message": indicator_result.error_message,
        "calculated_at": indicator_result.calculated_at,
        "existing": not created,
      }
    )

  context = {
    "indicator": indicator,
    "results": results,
    "total_submissions": submissions.count(),
    "successful_calculations": len([r for r in results if r["success"]]),
    "failed_calculations": len([r for r in results if not r["success"]]),
  }

  return render(request, "survey/indicator_execute.html", context)


@login_required
def indicator_execute_single(request, survey_pk, indicator_pk):
  """Execute an indicator script and display results for a single submission"""
  survey = get_object_or_404(Survey, pk=survey_pk)
  # Check if user has permission to view the survey
  if not request.user.has_perm("survey.view_survey", survey):
    from django.core.exceptions import PermissionDenied

    raise PermissionDenied
  try:
    indicator = Indicator.objects.get(pk=indicator_pk, survey=survey)
  except Indicator.DoesNotExist:
    messages.error(request, _("The indicator you are looking for does not exist or has been deleted."))
    return redirect("survey:survey_indicators", pk=survey.pk)

  # Get a sample submission for testing (first submission)
  sample_submission = survey.submissions.first()

  if not sample_submission:
    messages.error(request, _("No submissions found for this survey. Please create a submission first."))
    return redirect("survey:survey_indicators", survey_pk=survey.pk)

  # Prepare submission data
  submission_data = {}
  for answer in sample_submission.answers.all():
    question_key = f"q{answer.question.pk}"

    # Get the answer value based on question type
    if answer.question.question_type in ["radio", "checkbox", "dropdown"]:
      # For option-based questions, get the selected option values
      if answer.selected_options.exists():
        if answer.question.question_type == "radio":
          # Single selection - get the first option value
          option = answer.selected_options.first()
          submission_data[question_key] = option.value or option.text
        else:
          # Multiple selections - get list of option values
          options = list(answer.selected_options.all())
          submission_data[question_key] = [opt.value or opt.text for opt in options]
      else:
        submission_data[question_key] = None
    else:
      # For text-based questions, use the text answer
      submission_data[question_key] = answer.text_answer

  # Execute the script
  result = None
  error = None
  output = ""

  try:
    # Create a safe execution environment
    local_vars = {
      "submission": submission_data,
      "result": None,
      "int": int,
      "float": float,
      "str": str,
      "len": len,
      "sum": sum,
      "abs": abs,
      "min": min,
      "max": max,
    }

    result, output = indicator_sandbox.run_isolated(indicator.script.code, local_vars)

  except Exception as e:
    error = str(e)

  context = {
    "survey": survey,
    "indicator": indicator,
    "submission": sample_submission,
    "submission_data": submission_data,
    "result": result,
    "error": error,
    "output": output,
  }

  return render(request, "survey/indicator_execute_single.html", context)


@login_required
def survey_submissions_indicators(request, pk):
  """Display all submissions for a survey with their indicators in a table format"""
  survey = get_object_or_404(Survey, pk=pk)

  # Check if user has permission to view the survey (managers/supervisors have access to all)
  is_manager = request.user.groups.filter(name='Manager').exists()
  is_supervisor = request.user.groups.filter(name='Supervisor').exists()
  if not (request.user.is_staff or request.user.is_superuser or is_manager or is_supervisor or request.user.has_perm("survey.view_survey", survey)):
    from django.core.exceptions import PermissionDenied
    raise PermissionDenied

  # Check if recalculate button was clicked
  recalculate = request.POST.get('recalculate') == 'true'

  # Get only the last 20 submissions for this survey
  from .selectors import apply_submission_visibility
  submissions = apply_submission_visibility(
    survey.submissions.select_related('user').prefetch_related('indicator_results__indicator'),
    request.user,
  )[:20]

  # Get all active indicators for this survey
  indicators = survey.indicators.filter(is_active=True)

  # Prepare table data
  table_data = []
  for submission in submissions:
    row = {
      'submission': submission,
      'indicator_results': {}
    }

    # Process each indicator
    for indicator in indicators:
      if recalculate:
        # Always execute fresh calculation when recalculation is requested
        execution_result = indicator.execute(submission)
        if execution_result.get('success', False) and execution_result.get('result') is not None:
          # Update or create the result
          indicator_result, created = IndicatorResult.objects.update_or_create(
            indicator=indicator,
            submission=submission,
            defaults={
              'result': execution_result.get('result'),
              'success': True,
              'error_message': None
            }
          )
          row['indicator_results'][indicator.id] = {
            'result': indicator_result.result,
            'success': indicator_result.success,
            'error_message': indicator_result.error_message,
            'calculated_at': indicator_result.calculated_at
          }
        else:
          row['indicator_results'][indicator.id] = {
            'result': None,
            'success': False,
            'error_message': execution_result.get('error', 'Unknown error occurred'),
            'calculated_at': None
          }
      else:
        # Get existing results for each indicator (don't execute)
        result = submission.indicator_results.filter(indicator=indicator).first()
        if result:
          row['indicator_results'][indicator.id] = {
            'result': result.result,
            'success': result.success,
            'error_message': result.error_message,
            'calculated_at': result.calculated_at
          }
        else:
          # No cached result exists
          row['indicator_results'][indicator.id] = {
            'result': None,
            'success': False,
            'error_message': 'No cached result available. Click "Recalculate" to compute.',
            'calculated_at': None
          }

    table_data.append(row)

  context = {
    'survey': survey,
    'indicators': indicators,
    'table_data': table_data,
    'total_submissions': len(submissions),
    'total_indicators': len(indicators),
    'recalculated': recalculate,
  }

  return render(request, "survey/survey_submissions_indicators.html", context)


class IndicatorCreateView(CreateView):
  """View for creating a new indicator"""

  model = Indicator
  form_class = IndicatorForm
  template_name = "survey/indicator_form.html"

  def get_survey(self):
    return get_object_or_404(Survey, pk=self.kwargs["survey_pk"])

  def get_form_kwargs(self):
    kwargs = super().get_form_kwargs()
    kwargs["survey"] = self.get_survey()
    return kwargs

  def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["survey"] = self.get_survey()
    context["title"] = _("Create Indicator")
    return context

  def form_valid(self, form):
    form.instance.survey = self.get_survey()
    messages.success(self.request, _("Indicator created successfully!"))
    return super().form_valid(form)

  def get_success_url(self):
    return reverse_lazy("survey:survey_indicators", kwargs={"survey_pk": self.get_survey().pk})


class IndicatorUpdateView(UpdateView):
  """View for updating an existing indicator"""

  model = Indicator
  form_class = IndicatorForm
  template_name = "survey/indicator_form.html"
  pk_url_kwarg = "indicator_pk"

  def get_survey(self):
    return get_object_or_404(Survey, pk=self.kwargs["survey_pk"])

  def get_queryset(self):
    return Indicator.objects.filter(survey=self.get_survey())

  def get_form_kwargs(self):
    kwargs = super().get_form_kwargs()
    kwargs["survey"] = self.get_survey()
    return kwargs

  def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["survey"] = self.get_survey()
    context["title"] = _("Edit Indicator")
    return context

  def form_valid(self, form):
    messages.success(self.request, _("Indicator updated successfully!"))
    return super().form_valid(form)

  def get_success_url(self):
    return reverse_lazy("survey:survey_indicators", kwargs={"survey_pk": self.get_survey().pk})


class IndicatorDeleteView(DeleteView):
  """View for deleting an indicator"""

  model = Indicator
  template_name = "survey/indicator_confirm_delete.html"
  pk_url_kwarg = "indicator_pk"

  def get_survey(self):
    return get_object_or_404(Survey, pk=self.kwargs["survey_pk"])

  def get_queryset(self):
    return Indicator.objects.filter(survey=self.get_survey())

  def get_context_data(self, **kwargs):
    context = super().get_context_data(**kwargs)
    context["survey"] = self.get_survey()
    return context

  def get_success_url(self):
    messages.success(self.request, _("Indicator deleted successfully!"))
    return reverse_lazy("survey:survey_indicators", kwargs={"survey_pk": self.get_survey().pk})


# Survey Dashboard Views

def get_districts_by_state_condition(state_question, district_question, selected_state_id=None):
  """
    Get districts filtered by state based on conditional logic between questions 709 and 710.
    This function looks at the conditional logic set up for district options and returns
    districts that should be shown for the selected state.
    """
  if not selected_state_id or selected_state_id == '' or selected_state_id is None:
    # If no state selected, return all districts
    return QuestionOption.objects.filter(question=district_question).order_by('text')

  # Get the selected state option
  selected_state_option = QuestionOption.objects.filter(id=selected_state_id).first()
  if not selected_state_option:
    return QuestionOption.objects.none()

  # Find districts that have conditional logic matching this state
  matching_district_ids = []

  # Get all district options that have conditions
  district_options = QuestionOption.objects.filter(
    question=district_question
  ).prefetch_related('conditions')

  for district_option in district_options:
    # Check if this district should be shown for the selected state
    conditions = district_option.conditions.all()
    if conditions:
      # Check if any condition matches the selected state
      for condition in conditions:
        if (condition.target_question == state_question and
          condition.condition_type == 'equals' and
          condition.value == selected_state_option.text):
          matching_district_ids.append(district_option.id)
          break
    else:
      # If district has no conditions, show it for all states
      matching_district_ids.append(district_option.id)

  if matching_district_ids:
    # Return districts that have conditional logic for this state or no conditions
    return QuestionOption.objects.filter(
      id__in=matching_district_ids,
      question=district_question
    ).order_by('text')
  else:
    # If no districts found, return all districts as fallback
    return QuestionOption.objects.filter(question=district_question).order_by('text')


def get_districts_by_state_condition_dashboard(state_question, district_question, selected_state_id=None):
  """
  Get districts filtered by state for dashboard statistics.
  This function is specifically designed for dashboard statistics and includes both:
  - Districts with conditional logic for the selected state
  - Districts that have actual survey data for the selected state
  For filter dropdowns, we show all districts that belong to a state based on conditional logic,
  regardless of whether there's survey data, so users can filter by any district.
  """
  if not selected_state_id:
    # If no state selected, return all districts
    return SubmissionAnswer.objects.filter(
      submission__survey_id=6,
      submission__status=SUBMITTED_SURVEY_STATUS,
      submission__is_archived=False,
      question=district_question,
    ).values_list(
      'selected_options__text', flat=True).distinct()

  # Get the selected state option
  selected_state_option = QuestionOption.objects.filter(id=selected_state_id).first()
  if not selected_state_option:
    return QuestionOption.objects.none()

  # Find districts that have conditional logic matching this state
  matching_district_ids = []

  # Get all district options that have conditions
  district_options = QuestionOption.objects.filter(
    question=district_question,
    is_conditional=True
  ).prefetch_related('conditions')

  for district_option in district_options:
    # Check if this district should be shown for the selected state
    conditions = district_option.conditions.all()
    if conditions:
      # Check if any condition matches the selected state
      for condition in conditions:
        if (condition.target_question == state_question and
          condition.condition_type == 'equals' and
          condition.value == selected_state_option.text):
          matching_district_ids.append(district_option.id)
          break

  # Also include districts that have actual survey data for this state
  # even if they don't have conditional logic set up
  data_district_ids = Submission.objects.filter(
    status=SUBMITTED_SURVEY_STATUS,
    is_archived=False,
    answers__question=state_question,
    answers__selected_options__id=selected_state_id
  ).filter(
    answers__question=district_question
  ).values_list('answers__selected_options__id', flat=True).distinct()

  # Combine both lists: conditional districts + districts with actual data
  all_district_ids = set(matching_district_ids) | set(data_district_ids)

  if not all_district_ids:
    # If no districts match this state, return empty result
    return QuestionOption.objects.none()

  # Return all districts that either have conditional logic for this state
  # or have actual survey data for this state
  return QuestionOption.objects.filter(
    id__in=all_district_ids,
    question=district_question
  ).order_by('text')


def get_districts_with_submission_counts(state_question, district_question, selected_state_id=None):
  """
  Get districts with their actual submission counts for dashboard statistics.
  This function returns districts along with the count of submissions for each district,
  properly filtered by state and including only districts with actual survey data.
  """
  if not selected_state_id:
    # If no state selected, return all districts with their counts
    return QuestionOption.objects.filter(
      question=district_question
    ).annotate(
      submission_count=Count(
        'submissionanswer',
        filter=Q(
          submissionanswer__submission__status=SUBMITTED_SURVEY_STATUS,
          submissionanswer__submission__is_archived=False,
        )
      )
    ).order_by('text')

  # Get the selected state option
  selected_state_option = QuestionOption.objects.filter(id=selected_state_id).first()
  if not selected_state_option:
    return QuestionOption.objects.none()

  # Query submissions that match the selected state and have district answers
  districts_with_counts = QuestionOption.objects.filter(
    question=district_question,
    submissionanswer__submission__status=SUBMITTED_SURVEY_STATUS,
    submissionanswer__submission__is_archived=False,
    submissionanswer__submission__answers__question=state_question,
    submissionanswer__submission__answers__selected_options__id=selected_state_id
  ).annotate(
    submission_count=Count(
      'submissionanswer',
      filter=Q(
        submissionanswer__submission__status=SUBMITTED_SURVEY_STATUS,
        submissionanswer__submission__is_archived=False,
        submissionanswer__submission__answers__question=state_question,
        submissionanswer__submission__answers__selected_options__id=selected_state_id
      )
    )
  ).filter(
    submission_count__gt=0  # Only include districts with actual submissions
  ).order_by('text')

  return districts_with_counts


def get_blocks_by_state_and_district_condition(
  state_question,
  district_question,
  block_question,
  selected_state_id=None,
  selected_district_id=None,
  user=None,
):
  """
  Get blocks filtered by state and district based on conditional logic.
  This function returns blocks based on the following conditions:
  - If no state and no district selected: return all distinct blocks from text answers
  - If only state selected: get blocks from submissions where state matches
  - If district selected: get blocks from submissions where district matches
  - If both state and district selected: get blocks from submissions where both match

  Note: Questions 709 (state), 710 (district), 711 (block) are now text type questions.
  Checks both text_answer (new) and selected_options.text (old) for backward compatibility.

  Equivalent SQL:
    SELECT DISTINCT sa.text_answer FROM survey_submissionanswer sa
    WHERE sa.question_id = 711 AND sa.text_answer IS NOT NULL AND sa.text_answer != '';
  """

  # Get all submitted submission IDs for survey 6
  from .selectors import apply_submission_visibility

  all_submission_ids = list(
    apply_submission_visibility(
      Submission.objects.filter(
        survey_id=6,
        status=SUBMITTED_SURVEY_STATUS,
        is_archived=False,
      ),
      user,
    ).values_list('id', flat=True)
  )

  # If no state and no district selected, return all distinct blocks
  if (not selected_state_id or selected_state_id == '') and \
    (not selected_district_id or selected_district_id == ''):
    # Get all distinct block names from text answers for block question
    blocks_from_text = SubmissionAnswer.objects.filter(
      submission_id__in=all_submission_ids,
      question=block_question,
      text_answer__isnull=False
    ).exclude(text_answer='').values_list('text_answer', flat=True).distinct()

    # Also check selected_options.text for old data
    blocks_from_options = SubmissionAnswer.objects.filter(
      submission_id__in=all_submission_ids,
      question=block_question,
      selected_options__isnull=False
    ).values_list('selected_options__text', flat=True).distinct()

    # Combine unique values
    unique_blocks = set(b for b in blocks_from_text if b and b.strip())
    unique_blocks.update(b for b in blocks_from_options if b and b.strip())

    return [(block, block) for block in sorted(unique_blocks)]

  # Start with all submission IDs
  filtered_submission_ids = all_submission_ids

  # If state selected, filter submission IDs by state
  if selected_state_id and selected_state_id != '':
    # Get submission IDs where state matches in text_answer
    state_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=all_submission_ids,
      question=state_question,
      text_answer=selected_state_id
    ).values_list('submission_id', flat=True)

    # Get submission IDs where state matches in selected_options.text
    state_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=all_submission_ids,
      question=state_question,
      selected_options__text=selected_state_id
    ).values_list('submission_id', flat=True)

    # Combine both
    filtered_submission_ids = list(set(state_text_ids) | set(state_option_ids))

  # If district selected, further filter submission IDs by district
  if selected_district_id and selected_district_id != '':
    # Get submission IDs where district matches in text_answer
    district_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=filtered_submission_ids,
      question=district_question,
      text_answer=selected_district_id
    ).values_list('submission_id', flat=True)

    # Get submission IDs where district matches in selected_options.text
    district_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=filtered_submission_ids,
      question=district_question,
      selected_options__text=selected_district_id
    ).values_list('submission_id', flat=True)

    # Combine both
    filtered_submission_ids = list(set(district_text_ids) | set(district_option_ids))

  # Get blocks from filtered submissions
  blocks_from_text = SubmissionAnswer.objects.filter(
    submission_id__in=filtered_submission_ids,
    question=block_question,
    text_answer__isnull=False
  ).exclude(text_answer='').values_list('text_answer', flat=True).distinct()

  # Also check selected_options.text for old data
  blocks_from_options = SubmissionAnswer.objects.filter(
    submission_id__in=filtered_submission_ids,
    question=block_question,
    selected_options__isnull=False
  ).values_list('selected_options__text', flat=True).distinct()

  # Combine unique values
  unique_blocks = set(b for b in blocks_from_text if b and b.strip())
  unique_blocks.update(b for b in blocks_from_options if b and b.strip())

  return [(block, block) for block in sorted(unique_blocks)]


def get_clfs_by_block(
  state_question,
  district_question,
  block_question,
  clf_question,
  selected_state_id=None,
  selected_district_id=None,
  selected_block_id=None,
  user=None,
):
  """
  Get CLFs filtered by state, district and block based on conditional logic.
  Similar to get_blocks_by_state_and_district_condition but adds CLF (question 3383) level.
  Checks both text_answer (new) and selected_options.text (old) for backward compatibility.
  """
  if not clf_question:
    return []

  from .selectors import apply_submission_visibility

  all_submission_ids = list(
    apply_submission_visibility(
      Submission.objects.filter(
        survey_id=6,
        status=SUBMITTED_SURVEY_STATUS,
        is_archived=False,
      ),
      user,
    ).values_list('id', flat=True)
  )

  filtered_submission_ids = all_submission_ids

  # Filter by state if selected
  if selected_state_id and selected_state_id != '':
    state_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=all_submission_ids,
      question=state_question,
      text_answer=selected_state_id
    ).values_list('submission_id', flat=True)
    state_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=all_submission_ids,
      question=state_question,
      selected_options__text=selected_state_id
    ).values_list('submission_id', flat=True)
    filtered_submission_ids = list(set(state_text_ids) | set(state_option_ids))

  # Filter by district if selected
  if selected_district_id and selected_district_id != '':
    district_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=filtered_submission_ids,
      question=district_question,
      text_answer=selected_district_id
    ).values_list('submission_id', flat=True)
    district_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=filtered_submission_ids,
      question=district_question,
      selected_options__text=selected_district_id
    ).values_list('submission_id', flat=True)
    filtered_submission_ids = list(set(district_text_ids) | set(district_option_ids))

  # Filter by block if selected
  if selected_block_id and selected_block_id != '':
    block_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=filtered_submission_ids,
      question=block_question,
      text_answer=selected_block_id
    ).values_list('submission_id', flat=True)
    block_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=filtered_submission_ids,
      question=block_question,
      selected_options__text=selected_block_id
    ).values_list('submission_id', flat=True)
    filtered_submission_ids = list(set(block_text_ids) | set(block_option_ids))

  # Get CLFs from filtered submissions
  clfs_from_text = SubmissionAnswer.objects.filter(
    submission_id__in=filtered_submission_ids,
    question=clf_question,
    text_answer__isnull=False
  ).exclude(text_answer='').values_list('text_answer', flat=True).distinct()

  clfs_from_options = SubmissionAnswer.objects.filter(
    submission_id__in=filtered_submission_ids,
    question=clf_question,
    selected_options__isnull=False
  ).values_list('selected_options__text', flat=True).distinct()

  unique_clfs = set(c for c in clfs_from_text if c and c.strip())
  unique_clfs.update(c for c in clfs_from_options if c and c.strip())

  return [(clf, clf) for clf in sorted(unique_clfs)]


def get_clfs_with_submission_data(submissions, state_question, district_question, block_question, clf_question,
                                  selected_state_id, selected_district_id, selected_block_id):
  """Get only CLFs that have actual submission data, filtered by state, district and block if selected.
  Checks both selected_options (old dropdown data) and text_answer (new text data) for filtering."""
  if not clf_question:
    return []

  submission_ids = list(submissions.values_list('id', flat=True))

  # Filter by state if selected
  if selected_state_id and state_question:
    state_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=submission_ids,
      question=state_question,
      text_answer=selected_state_id
    ).values_list('submission_id', flat=True)
    state_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=submission_ids,
      question=state_question,
      selected_options__text=selected_state_id
    ).values_list('submission_id', flat=True)
    submission_ids = list(set(state_text_ids) | set(state_option_ids))

  # Filter by district if selected
  if selected_district_id and district_question:
    district_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=submission_ids,
      question=district_question,
      text_answer=selected_district_id
    ).values_list('submission_id', flat=True)
    district_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=submission_ids,
      question=district_question,
      selected_options__text=selected_district_id
    ).values_list('submission_id', flat=True)
    submission_ids = list(set(district_text_ids) | set(district_option_ids))

  # Filter by block if selected
  if selected_block_id and block_question:
    block_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=submission_ids,
      question=block_question,
      text_answer=selected_block_id
    ).values_list('submission_id', flat=True)
    block_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=submission_ids,
      question=block_question,
      selected_options__text=selected_block_id
    ).values_list('submission_id', flat=True)
    submission_ids = list(set(block_text_ids) | set(block_option_ids))

  # Get unique CLF text answers from filtered submissions
  seen_texts = set()

  clfs_from_text = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=clf_question,
    text_answer__isnull=False
  ).exclude(text_answer='').values('text_answer').distinct()

  for clf_data in clfs_from_text:
    text_value = clf_data['text_answer']
    if text_value and text_value.strip():
      seen_texts.add(text_value.strip())

  clfs_from_options = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=clf_question,
    selected_options__isnull=False
  ).values('selected_options__text').distinct()

  for clf_data in clfs_from_options:
    text_value = clf_data['selected_options__text']
    if text_value and text_value.strip():
      seen_texts.add(text_value.strip())

  clfs = [{'id': text, 'text': text} for text in seen_texts if text]
  clfs.sort(key=lambda x: x['text'] if x['text'] else '')
  return clfs

def filter_submissions_by_geography(all_submissions, state_question, district_question, block_question,
                                    state=None, district=None, block=None, clf_question=None, clf=None):
  """
  Filter submissions by state/district/block/clf using text_answer (primary) and selected_options.text (fallback).
  Returns the set of submission IDs that match the geographic filters.

  This function filters from ALL submissions first (where text_answer data exists),
  then the caller can intersect with indicator_results__success=True submissions.
  """
  filtered_submission_ids = None

  if state and state_question:
    state_text_ids = all_submissions.filter(
      answers__question=state_question,
      answers__text_answer=state
    ).values_list('id', flat=True)
    state_options_ids = all_submissions.filter(
      answers__question=state_question,
      answers__selected_options__text=state
    ).values_list('id', flat=True)
    filtered_submission_ids = set(state_text_ids) | set(state_options_ids)

  if district and district_question:
    base_ids = filtered_submission_ids if filtered_submission_ids is not None else set(all_submissions.values_list('id', flat=True))
    district_text_ids = all_submissions.filter(
      id__in=base_ids,
      answers__question=district_question,
      answers__text_answer=district
    ).values_list('id', flat=True)
    district_options_ids = all_submissions.filter(
      id__in=base_ids,
      answers__question=district_question,
      answers__selected_options__text=district
    ).values_list('id', flat=True)
    filtered_submission_ids = set(district_text_ids) | set(district_options_ids)

  if block and block_question:
    base_ids = filtered_submission_ids if filtered_submission_ids is not None else set(all_submissions.values_list('id', flat=True))
    block_text_ids = all_submissions.filter(
      id__in=base_ids,
      answers__question=block_question,
      answers__text_answer=block
    ).values_list('id', flat=True)
    block_options_ids = all_submissions.filter(
      id__in=base_ids,
      answers__question=block_question,
      answers__selected_options__text=block
    ).values_list('id', flat=True)
    filtered_submission_ids = set(block_text_ids) | set(block_options_ids)

  if clf and clf_question:
    base_ids = filtered_submission_ids if filtered_submission_ids is not None else set(all_submissions.values_list('id', flat=True))
    # CLF is a free-text question and the dropdown option values are trimmed
    # (see get_clfs_with_submission_data), but stored answers frequently carry
    # stray leading/trailing whitespace and inconsistent casing. Match on the
    # trimmed, case-insensitive value so a selected CLF actually finds its rows.
    from django.db.models.functions import Trim
    clf_norm = clf.strip()
    clf_answers = SubmissionAnswer.objects.filter(
      submission_id__in=base_ids,
      question=clf_question,
    )
    clf_text_ids = clf_answers.annotate(
      _clf_trim=Trim('text_answer')
    ).filter(_clf_trim__iexact=clf_norm).values_list('submission_id', flat=True)
    clf_options_ids = clf_answers.annotate(
      _clf_opt_trim=Trim('selected_options__text')
    ).filter(_clf_opt_trim__iexact=clf_norm).values_list('submission_id', flat=True)
    filtered_submission_ids = set(clf_text_ids) | set(clf_options_ids)

  return filtered_submission_ids


def apply_report_date_filters(queryset, date_from='', date_to=''):
  """Apply date range filters for report endpoints."""
  if date_from:
    try:
      dt_from = None
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
          dt_from = datetime.strptime(date_from, fmt)
          break
        except ValueError:
          continue
      if dt_from:
        queryset = queryset.filter(submitted_at__gte=dt_from)
    except Exception:
      pass

  if date_to:
    try:
      dt_to = None
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
        try:
          dt_to = datetime.strptime(date_to, fmt)
          break
        except ValueError:
          continue
      if not dt_to:
        try:
          dt_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
          pass
      if dt_to:
        queryset = queryset.filter(submitted_at__lte=dt_to)
    except Exception:
      pass

  return queryset


def is_valid_geographic_name(text):
  """Validate if a geographic name is valid (not junk/test data).
  Filters out:
  - Base64 encoded GraphQL IDs (starting with 'UXVlc3Rpb25PcHRpb25Ob2Rl')
  - Very short names (less than 3 characters)
  - Known test/junk values
  """
  if not text:
    return False

  # Filter out Base64 encoded GraphQL IDs
  if text.startswith('UXVlc3Rpb25PcHRpb25Ob2Rl'):
    return False

  # Filter out very short names (likely junk)
  if len(text) < 3:
    return False

  # Filter out known test/junk values (case-insensitive)
  junk_values = {'test', 'hh', 'mh', 'gdhdh', 'hshsbs', 'gshsh', 'abc', 'xyz', 'asdf', 'qwerty'}
  if text.lower() in junk_values:
    return False

  # Filter out strings that are all lowercase and less than 5 chars (likely abbreviations/junk)
  if text.islower() and len(text) < 5:
    return False

  return True


def get_states_with_submission_data(submissions, state_question):
  """Get only states that have actual submission data.
  Uses GROUP BY on text_answer and selected_options.text to get distinct values.
  Filters out invalid/junk values.
  """
  if not state_question:
    return []

  seen_texts = set()
  submission_ids = submissions.values_list('id', flat=True)

  # Query SubmissionAnswer directly with GROUP BY on text_answer
  states_from_text = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=state_question,
    text_answer__isnull=False
  ).exclude(
    text_answer=''
  ).values('text_answer').distinct()

  for state_data in states_from_text:
    text_value = state_data['text_answer']
    if text_value and is_valid_geographic_name(text_value):
      seen_texts.add(text_value)

  # Also check selected_options.text (for old dropdown data)
  states_from_options = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=state_question,
    selected_options__isnull=False
  ).values('selected_options__text').distinct()

  for state_data in states_from_options:
    text_value = state_data['selected_options__text']
    if text_value and is_valid_geographic_name(text_value):
      seen_texts.add(text_value)

  # Convert to list of dicts with text as ID for consistent filtering
  states = [{'id': text, 'text': text} for text in seen_texts if text]

  # Sort by text
  states.sort(key=lambda x: x['text'] if x['text'] else '')
  return states


def get_districts_with_submission_data(submissions, state_question, district_question, selected_state_id):
  """Get only districts that have actual submission data, filtered by state if selected.
  Uses GROUP BY on text_answer and selected_options.text to get distinct values.
  Equivalent SQL:
    SELECT DISTINCT sa.text_answer FROM survey_submissionanswer sa
    WHERE sa.question_id = 710 AND sa.text_answer IS NOT NULL AND sa.text_answer != '';
  """
  if not district_question:
    return []

  submission_ids = list(submissions.values_list('id', flat=True))

  # Filter by state if selected - filter submission_ids by state (question 709)
  if selected_state_id and state_question:
    # Get submission IDs where state matches in text_answer
    state_text_ids = SubmissionAnswer.objects.filter(
      submission_id__in=submission_ids,
      question=state_question,
      text_answer=selected_state_id
    ).values_list('submission_id', flat=True)

    # Get submission IDs where state matches in selected_options.text
    state_option_ids = SubmissionAnswer.objects.filter(
      submission_id__in=submission_ids,
      question=state_question,
      selected_options__text=selected_state_id
    ).values_list('submission_id', flat=True)

    # Combine both
    submission_ids = list(set(state_text_ids) | set(state_option_ids))

  seen_texts = set()

  # Query SubmissionAnswer directly with GROUP BY on text_answer for question 710
  # SELECT DISTINCT text_answer FROM survey_submissionanswer WHERE question_id=710 AND text_answer IS NOT NULL
  districts_from_text = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=district_question,
    text_answer__isnull=False
  ).exclude(
    text_answer=''
  ).values('text_answer').distinct()

  for district_data in districts_from_text:
    text_value = district_data['text_answer']
    if text_value and is_valid_geographic_name(text_value):
      seen_texts.add(text_value)

  # Also check selected_options.text (for old dropdown data)
  districts_from_options = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=district_question,
    selected_options__isnull=False
  ).values('selected_options__text').distinct()

  for district_data in districts_from_options:
    text_value = district_data['selected_options__text']
    if text_value and is_valid_geographic_name(text_value):
      seen_texts.add(text_value)

  # Convert to list of dicts with text as ID for consistent filtering
  districts = [{'id': text, 'text': text} for text in seen_texts if text]

  # Sort by text
  districts.sort(key=lambda x: x['text'] if x['text'] else '')
  return districts


def get_blocks_with_submission_data(submissions, state_question, district_question, block_question, selected_state_id, selected_district_id):
  """Get only blocks that have actual submission data, filtered by state and district if selected.
  Checks both selected_options (old dropdown data) and text_answer (new text data) for filtering."""
  if not block_question:
    return []

  # Start with all submissions
  block_submissions = submissions

  # Filter by state if selected - check both selected_options and text_answer
  if selected_state_id and state_question:
    # Try to filter by selected_options first (numeric ID)
    try:
      # Filter by selected_options.text (text matching)
      state_submission_ids = Submission.objects.filter(
        id__in=block_submissions.values_list('id', flat=True),
        answers__question=state_question,
        answers__selected_options__text=selected_state_id
      ).values_list('id', flat=True)
    except (ValueError, TypeError):
      state_submission_ids = []

    # Also check text_answer
    text_submission_ids = Submission.objects.filter(
      id__in=block_submissions.values_list('id', flat=True),
      answers__question=state_question,
      answers__text_answer=selected_state_id
    ).values_list('id', flat=True)

    all_state_submission_ids = set(state_submission_ids) | set(text_submission_ids)
    if all_state_submission_ids:
      block_submissions = block_submissions.filter(id__in=all_state_submission_ids)

  # Filter by district if selected - check both selected_options and text_answer
  if selected_district_id and district_question:
    # Filter by selected_options.text (text matching)
    try:
      district_submission_ids = Submission.objects.filter(
        id__in=block_submissions.values_list('id', flat=True),
        answers__question=district_question,
        answers__selected_options__text=selected_district_id
      ).values_list('id', flat=True)
    except (ValueError, TypeError):
      district_submission_ids = []

    # Also check text_answer
    text_submission_ids = Submission.objects.filter(
      id__in=block_submissions.values_list('id', flat=True),
      answers__question=district_question,
      answers__text_answer=selected_district_id
    ).values_list('id', flat=True)

    all_district_submission_ids = set(district_submission_ids) | set(text_submission_ids)
    if all_district_submission_ids:
      block_submissions = block_submissions.filter(id__in=all_district_submission_ids)

  # Get unique block text answers from filtered submissions
  blocks_with_data = block_submissions.filter(
    answers__question=block_question,
    answers__text_answer__isnull=False
  ).exclude(
    answers__text_answer=''
  ).values(
    'answers__text_answer'
  ).distinct().order_by('answers__text_answer')

  # Convert to list of dictionaries for template compatibility
  # Filter out invalid/junk values
  blocks = []
  for block_data in blocks_with_data:
    text_value = block_data['answers__text_answer']
    if text_value and is_valid_geographic_name(text_value):
      blocks.append({
        'id': text_value,
        'text': text_value
      })

  return blocks


@dashboard_viewer_or_admin_required
def survey_reports_v2(request):
  """Survey dashboard similar to FPO reports with filters and analytics.
  Optimized for fast initial page load - heavy data is loaded via AJAX after page renders."""

  # Get site configuration
  from neo.models import SiteConfiguration
  site_config = SiteConfiguration.get_solo()

  # Get filter parameters
  selected_programme = request.GET.get('programme', '')
  selected_state = request.GET.get('state', '')
  selected_district = request.GET.get('district', '')
  selected_block = request.GET.get('block', '')
  selected_clf = request.GET.get('clf', '')
  selected_year = request.GET.get('year', '')
  selected_date_from = request.GET.get('date_from', '')
  selected_date_to = request.GET.get('date_to', '')

  # Get state, district, block, clf options for filters
  # Questions 709, 710, 711, 3383 are for state, district, block, clf respectively
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()
  clf_question = Question.objects.filter(id=3383).first()

  from .selectors import apply_submission_visibility

  # Get submitted submissions for survey 6 (for sidebar dropdowns - show states/districts with submitted data)
  all_survey_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Get distinct years from all submissions
  from django.db.models.functions import ExtractYear
  available_years = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).annotate(
    year=ExtractYear('submitted_at')
  ).values_list('year', flat=True).distinct().order_by('-year')

  all_states = []
  all_districts = []
  all_blocks = []
  all_clfs = []

  if state_question:
    # Get ALL states that have any submission data (not filtered by indicator success)
    all_states = get_states_with_submission_data(all_survey_submissions, state_question)

  # Load districts on initial render — scoped to selected state if any, otherwise all districts with data
  if district_question:
    all_districts = get_districts_with_submission_data(all_survey_submissions, state_question, district_question, selected_state or None)

  # Load blocks on initial render — scoped to selected state/district if any
  if block_question:
    all_blocks = get_blocks_with_submission_data(all_survey_submissions, state_question, district_question, block_question, selected_state or None, selected_district or None)

  # Load CLFs on initial render — scoped to selected state/district/block if any
  if clf_question:
    all_clfs = get_clfs_with_submission_data(all_survey_submissions, state_question, district_question, block_question, clf_question, selected_state or None, selected_district or None, selected_block or None)

  # Get selected objects for display
  selected_state_obj = None
  selected_district_obj = None
  selected_block_obj = None
  selected_clf_obj = None

  if selected_state:
    selected_state_obj = QuestionOption.objects.filter(id=selected_state).first()
  if selected_district:
    selected_district_obj = QuestionOption.objects.filter(id=selected_district).first()
  if selected_block:
    selected_block_obj = {'id': selected_block, 'text': selected_block}
  if selected_clf:
    selected_clf_obj = {'id': selected_clf, 'text': selected_clf}

  # Get all available indicators (lightweight query)
  indicators = (
    Indicator.objects.filter(survey_id=6, is_active=True)
    .select_related('script')
    .order_by('id')
  )
  report_indicator_options = build_indicator_report_entries(indicators)

  insights_selected_survey = Survey.objects.filter(id=INSIGHTS_SURVEY_ID).first()


  # NOTE: Stats, indicator_data, and map_data are now loaded via AJAX (fetchSurveyData)
  # after the page renders. This significantly speeds up the initial page load.

  context = {
    'selected_programme': selected_programme,
    'selected_state': selected_state,
    'selected_district': selected_district,
    'selected_block': selected_block,
    'selected_clf': selected_clf,
    'selected_year': selected_year,
    'selected_date_from': selected_date_from,
    'selected_date_to': selected_date_to,
    'selected_state_obj': selected_state_obj,
    'selected_district_obj': selected_district_obj,
    'selected_block_obj': selected_block_obj,
    'selected_clf_obj': selected_clf_obj,
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
    'all_clfs': all_clfs,
    'clf_filter_available': clf_question is not None,
    'available_years': list(available_years),
    'indicators': indicators,
    'total_indicators': len(indicators),
    'report_indicator_options': report_indicator_options,
    'insights_selected_survey': insights_selected_survey,
    'insights_grouped': [],
    'insights_last_generated_on': None,
    'insights_can_refresh': True,
    'maps_guru_api_key': settings.MAPS_GURU_API_KEY,
    'site_config': site_config,
  }

  return render(request, 'survey/reports_v2.html', context)


@login_required
@require_POST
def create_report_export(request):
  from .report_exports import REPORT_EXPORT_TYPES
  from .tasks import generate_report_export

  report_type = request.POST.get("report_type", "overview")
  if report_type not in REPORT_EXPORT_TYPES:
    return JsonResponse({"error": "Unsupported report type"}, status=400)

  filters = {}
  for key in REPORT_EXPORT_FILTER_FIELDS:
    value = request.POST.get(key, "")
    if value not in ("", None):
      filters[key] = value

  existing = ReportExportJob.objects.filter(
    user=request.user,
    report_type=report_type,
    filters=filters,
    status__in=(ReportExportJob.STATUS_PENDING, ReportExportJob.STATUS_RUNNING),
  ).order_by("-created_at").first()
  if existing is not None:
    return JsonResponse({
      "job_id": str(existing.id),
      "status": existing.status,
      "reused": True,
    })

  job = ReportExportJob.objects.create(
    user=request.user,
    report_type=report_type,
    filters=filters,
    status=ReportExportJob.STATUS_PENDING,
  )

  try:
    generate_report_export.delay(str(job.id), schema_name=_request_tenant_schema(request))
  except Exception as exc:  # noqa: BLE001
    if settings.DEBUG:
      try:
        generate_report_export.apply(args=[str(job.id)])
      except Exception as inline_exc:  # noqa: BLE001
        return JsonResponse({"error": "Inline export failed", "detail": str(inline_exc)}, status=500)
      job.refresh_from_db()
      return JsonResponse({"job_id": str(job.id), "status": job.status, "reused": False, "inline": True})
    job.status = ReportExportJob.STATUS_FAILED
    job.error_message = f"Failed to enqueue: {exc}"
    job.save(update_fields=["status", "error_message"])
    return JsonResponse({"error": "Failed to enqueue export", "detail": str(exc)}, status=500)

  return JsonResponse({"job_id": str(job.id), "status": job.status, "reused": False})


@login_required
def report_export_status(request, job_id):
  try:
    job = ReportExportJob.objects.get(pk=job_id)
  except (ReportExportJob.DoesNotExist, ValueError):
    return JsonResponse({"error": "Job not found"}, status=404)

  if job.user_id != request.user.id and not request.user.is_superuser:
    return JsonResponse({"error": "Forbidden"}, status=403)

  download_url = None
  if job.status == ReportExportJob.STATUS_DONE and job.file:
    from django.urls import reverse
    download_url = reverse("survey:report_export_download", kwargs={"job_id": str(job.id)})

  return JsonResponse({
    "job_id": str(job.id),
    "status": job.status,
    "processed": job.completed_steps,
    "total": job.total_steps,
    "percent": job.progress_percent,
    "error": job.error_message or None,
    "download_url": download_url,
  })


@login_required
def report_export_download(request, job_id):
  try:
    job = ReportExportJob.objects.get(pk=job_id)
  except (ReportExportJob.DoesNotExist, ValueError):
    return JsonResponse({"error": "Job not found"}, status=404)

  if job.user_id != request.user.id and not request.user.is_superuser:
    return JsonResponse({"error": "Forbidden"}, status=403)

  if job.status != ReportExportJob.STATUS_DONE or not job.file:
    return JsonResponse({"error": "Export not ready"}, status=409)

  from django.http import FileResponse
  response = FileResponse(
    job.file.open("rb"),
    as_attachment=True,
    filename=os.path.basename(job.file.name),
    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  )
  return response


@login_required
def api_districts_by_state(request):
  """API endpoint to get districts filtered by state with actual submission data"""
  state_id = request.GET.get('state_id')

  try:
    # Get questions for state and district
    state_question = Question.objects.filter(id=709).first()
    district_question = Question.objects.filter(id=710).first()

    if not state_question or not district_question:
      return JsonResponse({
        'success': False,
        'error': 'Questions not found'
      })

    # Get submitted submissions for survey 6 (for sidebar dropdowns - show states/districts with submitted data)
    # Same as main report page to ensure consistent dropdown options
    from .selectors import apply_submission_visibility
    all_survey_submissions = apply_submission_visibility(
      Submission.objects.filter(
        survey_id=6,
        status=SUBMITTED_SURVEY_STATUS,
        is_archived=False,
      ),
      request.user,
    ).distinct()

    # Get districts with submission data for the selected state
    districts = get_districts_with_submission_data(
      all_survey_submissions,
      state_question,
      district_question,
      state_id
    )

    return JsonResponse({
      'success': True,
      'districts': districts
    })

  except Exception as e:
    return JsonResponse({
      'success': False,
      'error': str(e)
    })


# ============================================================================
# CACHING AND OPTIMIZATION HELPERS FOR SURVEY REPORT APIs
# ============================================================================

def get_cache_key(*args):
    """Generate a unique cache key from arguments"""
    key_string = '_'.join(str(arg) for arg in args if arg)
    return hashlib.md5(key_string.encode()).hexdigest()


def get_survey_report_cache():
    """Get the survey reports cache"""
    try:
        return caches['survey_reports']
    except Exception:
        return caches['default']


def invalidate_survey_report_cache():
    """
    Invalidate all cached survey report data.
    Call this when new submissions are added or indicator results are updated.
    """
    cache = get_survey_report_cache()
    cache.clear()


def get_optimized_indicator_results(indicator_ids, submission_ids):
    """
    Fetch all indicator results in a single query and process them efficiently.
    Returns a dict: {indicator_id: {'empowered': count, 'disempowered': count, 'total': count}}
    """
    if not indicator_ids or not submission_ids:
        return {}

    # Fetch all results in one query
    results = IndicatorResult.objects.filter(
        indicator_id__in=indicator_ids,
        submission_id__in=submission_ids,
        success=True
    ).values('indicator_id', 'result')

    # Process results grouped by indicator
    indicator_stats = {ind_id: {'empowered': 0, 'disempowered': 0, 'total': 0} for ind_id in indicator_ids}

    for row in results:
        ind_id = row['indicator_id']
        result = row['result']
        indicator_stats[ind_id]['total'] += 1

        # Determine empowerment status
        is_empowered = False
        if isinstance(result, dict):
            empowerment_keys = ['empowered', 'is_empowered', 'empowered_status', 'status']
            for key in empowerment_keys:
                if key in result:
                    val = result[key]
                    if isinstance(val, bool):
                        is_empowered = val
                    elif isinstance(val, str):
                        is_empowered = val.lower() in ['true', 'empowered', 'yes', '1']
                    elif isinstance(val, (int, float)):
                        is_empowered = val > 0.5
                    break
        elif isinstance(result, bool):
            is_empowered = result
        elif isinstance(result, (int, float)):
            threshold = 50 if result > 1 else 0.5
            is_empowered = result > threshold
        elif isinstance(result, str):
            is_empowered = result.lower() in ['empowered', 'yes', 'true', '1', 'enabled', 'active']

        if is_empowered:
            indicator_stats[ind_id]['empowered'] += 1
        else:
            indicator_stats[ind_id]['disempowered'] += 1

    return indicator_stats


def get_optimized_state_map_data(submissions, state_question, indicator=None):
    """
    Optimized version of get_state_map_data that reduces N+1 queries.
    Fetches all data in bulk and processes in memory.
    """
    if not state_question:
        return []

    submission_ids = list(submissions.values_list('id', flat=True))
    if not submission_ids:
        return []

    # Get all state answers in one query (both text_answer and selected_options)
    state_answers = SubmissionAnswer.objects.filter(
        submission_id__in=submission_ids,
        question=state_question
    ).select_related('submission').prefetch_related('selected_options')

    # Build submission -> state mapping
    submission_states = {}
    for answer in state_answers:
        state_text = answer.text_answer
        if not state_text and answer.selected_options.exists():
            opt = answer.selected_options.first()
            state_text = opt.text if opt else None
        if state_text and state_text.strip():
            submission_states[answer.submission_id] = state_text.strip()

    if not submission_states:
        return []

    # Group submissions by state
    state_submissions = {}
    for sub_id, state in submission_states.items():
        if state not in state_submissions:
            state_submissions[state] = []
        state_submissions[state].append(sub_id)

    # Fetch all indicator results in one query
    if indicator:
        results_query = IndicatorResult.objects.filter(
            indicator=indicator,
            submission_id__in=submission_ids,
            success=True
        ).values('submission_id', 'result')
    else:
        results_query = IndicatorResult.objects.filter(
            submission_id__in=submission_ids,
            success=True
        ).values('submission_id', 'result')

    # Build submission -> results mapping
    submission_results = {}
    for row in results_query:
        sub_id = row['submission_id']
        if sub_id not in submission_results:
            submission_results[sub_id] = []
        submission_results[sub_id].append(row['result'])

    # Calculate stats per state
    state_data = []
    for state_text, sub_ids in state_submissions.items():
        empowered_count = 0
        disempowered_count = 0
        total_value = 0
        count = 0

        for sub_id in sub_ids:
            results = submission_results.get(sub_id, [])
            if not results:
                continue

            # When a specific indicator is queried, only process the first result
            # When no indicator specified (all indicators), check if ALL results are empowered
            if indicator:
                # Specific indicator - process first result only
                result = results[0]
                is_empowered = False
                value = 0

                if isinstance(result, (int, float)):
                    value = result
                    threshold = 50 if result > 1 else 0.5
                    is_empowered = result > threshold
                elif isinstance(result, dict):
                    if 'value' in result:
                        value = float(result['value'])
                    elif 'score' in result:
                        value = float(result['score'])
                    # Check empowerment
                    for key in ['empowered', 'is_empowered']:
                        if key in result:
                            is_empowered = bool(result[key])
                            break
                    else:
                        threshold = 50 if value > 1 else 0.5
                        is_empowered = value > threshold
                elif isinstance(result, bool):
                    value = 1.0 if result else 0.0
                    is_empowered = result
                elif isinstance(result, str):
                    # Handle string results like "Empowered", "Disempowered", "Unempowered"
                    is_empowered = result.lower() in ['empowered', 'true', 'yes']
                    value = 1.0 if is_empowered else 0.0

                total_value += value
                count += 1
                if is_empowered:
                    empowered_count += 1
                else:
                    disempowered_count += 1
            else:
                # No specific indicator - check if empowered in ALL indicators
                all_empowered = True
                empowered_in_any = False
                total_indicators = 0

                for result in results:
                    is_empowered = False

                    if isinstance(result, (int, float)):
                        threshold = 50 if result > 1 else 0.5
                        is_empowered = result > threshold
                    elif isinstance(result, dict):
                        for key in ['empowered', 'is_empowered']:
                            if key in result:
                                is_empowered = bool(result[key])
                                break
                        else:
                            value = 0
                            if 'value' in result:
                                value = float(result['value'])
                            elif 'score' in result:
                                value = float(result['score'])
                            threshold = 50 if value > 1 else 0.5
                            is_empowered = value > threshold
                    elif isinstance(result, bool):
                        is_empowered = result
                    elif isinstance(result, str):
                        is_empowered = result.lower() in ['empowered', 'true', 'yes']

                    total_indicators += 1
                    if is_empowered:
                        empowered_in_any = True
                    else:
                        all_empowered = False

                # Count as empowered only if empowered in ALL indicators
                count += 1
                if all_empowered and total_indicators > 0:
                    empowered_count += 1
                    total_value += 1.0
                else:
                    disempowered_count += 1
                    total_value += 0.0

        if count > 0:
            avg_value = total_value / count
            empowerment_percentage = (empowered_count / count * 100) if count > 0 else 0
            state_coords = get_state_coordinates(state_text)

            state_data.append({
                'state': state_text,
                'value': avg_value * 100 if avg_value <= 1 else avg_value,
                'empowerment_percentage': empowerment_percentage,
                'empowered_count': empowered_count,
                'disempowered_count': disempowered_count,
                'count': count,
                'latitude': state_coords['lat'],
                'longitude': state_coords['lng'],
            })

    return state_data


def get_optimized_district_map_data(indicator, submissions, state_question, district_question, state_filter_name):
    """
    Optimized version of get_indicator_district_map_data that reduces N+1 queries.
    """
    if not state_question or not district_question:
        return []

    submission_ids = list(submissions.values_list('id', flat=True))
    if not submission_ids:
        return []

    # Get all state and district answers in one query
    answers = SubmissionAnswer.objects.filter(
        submission_id__in=submission_ids,
        question__in=[state_question, district_question]
    ).select_related('submission', 'question').prefetch_related('selected_options')

    # Build submission -> state/district mapping
    submission_state = {}
    submission_district = {}

    for answer in answers:
        value = answer.text_answer
        if not value and answer.selected_options.exists():
            opt = answer.selected_options.first()
            value = opt.text if opt else None

        if value and value.strip():
            if answer.question_id == state_question.id:
                submission_state[answer.submission_id] = value.strip()
            elif answer.question_id == district_question.id:
                submission_district[answer.submission_id] = value.strip()

    # Filter submissions for the specified state (with flexible matching)
    # Handle variations like "Maharashtra" vs "Maharashtra (MH)"
    def state_matches(db_state, filter_state):
        if not db_state or not filter_state:
            return False
        db_lower = db_state.lower().strip()
        filter_lower = filter_state.lower().strip()
        # Exact match
        if db_lower == filter_lower:
            return True
        # Check if one contains the other (for abbreviation variations)
        if db_lower.startswith(filter_lower.split('(')[0].strip()) or filter_lower.startswith(db_lower.split('(')[0].strip()):
            return True
        return False

    state_sub_ids = [
        sub_id for sub_id, state in submission_state.items()
        if state_matches(state, state_filter_name)
    ]

    if not state_sub_ids:
        return []

    # Group by district
    district_submissions = {}
    for sub_id in state_sub_ids:
        district = submission_district.get(sub_id)
        if district:
            if district not in district_submissions:
                district_submissions[district] = []
            district_submissions[district].append(sub_id)

    # Fetch all indicator results for this state's submissions
    results_query = IndicatorResult.objects.filter(
        indicator=indicator,
        submission_id__in=state_sub_ids,
        success=True
    ).values('submission_id', 'result')

    submission_results = {}
    for row in results_query:
        sub_id = row['submission_id']
        if sub_id not in submission_results:
            submission_results[sub_id] = []
        submission_results[sub_id].append(row['result'])

    # Calculate stats per district
    district_data = []
    for district_text, sub_ids in district_submissions.items():
        empowered_count = 0
        disempowered_count = 0
        total_value = 0
        count = 0

        for sub_id in sub_ids:
            results = submission_results.get(sub_id, [])
            # Only process the first result for this submission (since we're querying for a specific indicator)
            if results:
                result = results[0]
                is_empowered = False
                value = 0

                if isinstance(result, (int, float)):
                    value = result
                    threshold = 50 if result > 1 else 0.5
                    is_empowered = result > threshold
                elif isinstance(result, dict):
                    if 'value' in result:
                        value = float(result['value'])
                    for key in ['empowered', 'is_empowered']:
                        if key in result:
                            is_empowered = bool(result[key])
                            break
                    else:
                        threshold = 50 if value > 1 else 0.5
                        is_empowered = value > threshold
                elif isinstance(result, bool):
                    value = 1.0 if result else 0.0
                    is_empowered = result
                elif isinstance(result, str):
                    # Handle string results like "Empowered", "Disempowered", "Unempowered"
                    is_empowered = result.lower() in ['empowered', 'true', 'yes']
                    value = 1.0 if is_empowered else 0.0

                total_value += value
                count += 1
                if is_empowered:
                    empowered_count += 1
                else:
                    disempowered_count += 1

        if count > 0:
            avg_value = total_value / count
            empowerment_percentage = (empowered_count / count * 100) if count > 0 else 0

            district_data.append({
                'district': district_text,
                'state': state_filter_name,
                'value': avg_value * 100 if avg_value <= 1 else avg_value,
                'empowerment_percentage': empowerment_percentage,
                'empowered_count': empowered_count,
                'disempowered_count': disempowered_count,
                'count': count,
            })

    return district_data


def get_optimized_grouped_indicator_data(submissions, demographic_question):
    """
    Optimized version of get_grouped_indicator_data that reduces N+1 queries.
    """
    indicators = list(Indicator.objects.filter(survey_id=6, is_active=True).select_related('script'))
    indicator_ids = [ind.id for ind in indicators]
    submission_ids = list(submissions.values_list('id', flat=True))

    if not submission_ids or not indicator_ids:
        return []

    # Pre-fetch all indicator results
    all_results = IndicatorResult.objects.filter(
        indicator_id__in=indicator_ids,
        submission_id__in=submission_ids,
        success=True
    ).values('indicator_id', 'submission_id', 'result')

    # Build submission -> indicator -> results mapping
    submission_indicator_results = {}
    for row in all_results:
        sub_id = row['submission_id']
        ind_id = row['indicator_id']
        if sub_id not in submission_indicator_results:
            submission_indicator_results[sub_id] = {}
        if ind_id not in submission_indicator_results[sub_id]:
            submission_indicator_results[sub_id][ind_id] = []
        submission_indicator_results[sub_id][ind_id].append(row['result'])

    demographic_groups = []

    if demographic_question.question_type in ['radio', 'checkbox', 'dropdown']:
        options = list(demographic_question.options.all().order_by('text'))

        # Pre-fetch demographic answers
        demographic_answers = SubmissionAnswer.objects.filter(
            submission_id__in=submission_ids,
            question=demographic_question
        ).prefetch_related('selected_options')

        # Build option -> submissions mapping
        option_submissions = {opt.id: [] for opt in options}
        for answer in demographic_answers:
            for opt in answer.selected_options.all():
                if opt.id in option_submissions:
                    option_submissions[opt.id].append(answer.submission_id)

        for option in options:
            group_sub_ids = option_submissions.get(option.id, [])
            group_data = {
                'group_name': option.text,
                'group_value': option.value,
                'indicators': []
            }

            if group_sub_ids:
                for indicator in indicators:
                    stats = _calculate_indicator_stats_for_submissions(
                        indicator, group_sub_ids, submission_indicator_results
                    )
                    stats['group_name'] = option.text
                    group_data['indicators'].append(stats)

            demographic_groups.append(group_data)

    elif demographic_question.id == 736:  # Age question
        # Pre-fetch age answers
        age_answers = SubmissionAnswer.objects.filter(
            submission_id__in=submission_ids,
            question=demographic_question
        ).values('submission_id', 'text_answer')

        # Group submissions by age range
        age_groups = [
            {'name': '<18', 'min': 0, 'max': 17},
            {'name': '18-30', 'min': 18, 'max': 30},
            {'name': '30-50', 'min': 31, 'max': 50},
            {'name': '>50', 'min': 51, 'max': 999},
        ]

        age_group_submissions = {ag['name']: [] for ag in age_groups}

        for row in age_answers:
            try:
                age = int(float(row['text_answer']))
                for ag in age_groups:
                    if ag['min'] <= age <= ag['max']:
                        age_group_submissions[ag['name']].append(row['submission_id'])
                        break
            except (ValueError, TypeError):
                continue

        for ag in age_groups:
            group_sub_ids = age_group_submissions[ag['name']]
            group_data = {
                'group_name': ag['name'],
                'group_value': ag['name'],
                'indicators': []
            }

            if group_sub_ids:
                for indicator in indicators:
                    stats = _calculate_indicator_stats_for_submissions(
                        indicator, group_sub_ids, submission_indicator_results
                    )
                    stats['group_name'] = ag['name']
                    group_data['indicators'].append(stats)

            demographic_groups.append(group_data)

    else:
        # For other text-based questions
        text_answers = SubmissionAnswer.objects.filter(
            submission_id__in=submission_ids,
            question=demographic_question
        ).values('submission_id', 'text_answer')

        value_submissions = {}
        for row in text_answers:
            val = row['text_answer']
            if val:
                if val not in value_submissions:
                    value_submissions[val] = []
                value_submissions[val].append(row['submission_id'])

        for value, group_sub_ids in value_submissions.items():
            group_data = {
                'group_name': value,
                'group_value': value,
                'indicators': []
            }

            if group_sub_ids:
                for indicator in indicators:
                    stats = _calculate_indicator_stats_for_submissions(
                        indicator, group_sub_ids, submission_indicator_results
                    )
                    stats['group_name'] = value
                    group_data['indicators'].append(stats)

            demographic_groups.append(group_data)

    return demographic_groups


def _calculate_indicator_stats_for_submissions(indicator, submission_ids, submission_indicator_results):
    """
    Calculate empowered/disempowered stats for an indicator from pre-fetched results.
    """
    empowered_count = 0
    disempowered_count = 0
    total_count = 0

    for sub_id in submission_ids:
        results = submission_indicator_results.get(sub_id, {}).get(indicator.id, [])
        for result in results:
            total_count += 1
            is_empowered = False

            if isinstance(result, dict):
                for key in ['empowered', 'is_empowered', 'empowered_status', 'status']:
                    if key in result:
                        val = result[key]
                        if isinstance(val, bool):
                            is_empowered = val
                        elif isinstance(val, str):
                            is_empowered = val.lower() in ['true', 'empowered', 'yes', '1']
                        elif isinstance(val, (int, float)):
                            is_empowered = val > 0.5
                        break
            elif isinstance(result, bool):
                is_empowered = result
            elif isinstance(result, (int, float)):
                is_empowered = result > 0.5
            elif isinstance(result, str):
                is_empowered = result.lower() in ['empowered', 'yes', 'true', '1', 'enabled', 'active']

            if is_empowered:
                empowered_count += 1
            else:
                disempowered_count += 1

    empowerment_percentage = (empowered_count / total_count * 100) if total_count > 0 else 0

    return {
        'indicator_id': indicator.id,
        'indicator_name': indicator.name,
        'empowered_count': empowered_count,
        'disempowered_count': disempowered_count,
        'total_count': total_count,
        'empowerment_percentage': round(empowerment_percentage, 1)
    }


# ============================================================================
# END OF CACHING AND OPTIMIZATION HELPERS
# ============================================================================


def calculate_survey_stats(submissions, state_question, district_question, block_question, selected_state_id=None,
                           selected_district_id=None, selected_block_id=None, clf_question=None, selected_clf_id=None):
  """Calculate statistics for survey dashboard.
  Checks both selected_options (old dropdown data) and text_answer (new text data).
  Uses the filtered submissions queryset for accurate counts."""

  # Get submission IDs from the filtered submissions queryset
  submission_ids = list(submissions.values_list('id', flat=True))

  # Get unique states, districts, blocks, clfs from filter inputs
  states_count = 0
  districts_count = 0
  blocks_count = 0
  clfs_count = 0
  states_list = []
  districts_list = []
  blocks_list = []
  clfs_list = []

  def _names(items):
    out = []
    for it in items or []:
      if isinstance(it, dict):
        name = it.get('text') or it.get('name') or it.get('title')
      else:
        name = getattr(it, 'text', None) or getattr(it, 'name', None) or str(it)
      if name:
        out.append(name)
    return sorted(out)

  if state_question:
    states = get_states_with_submission_data(submissions, state_question)
    states_list = _names(states)
    if selected_state_id and selected_state_id != '' or selected_district_id and selected_district_id != '' or selected_block_id and selected_block_id != '':
      states_count = 1
    else:
      states_count = len(states_list)

  if district_question:
    districts = get_districts_with_submission_data(
      submissions, state_question, district_question, selected_state_id
    )
    districts_list = _names(districts)
    if selected_district_id and selected_district_id != '' or selected_block_id and selected_block_id != '':
      districts_count = 1
    else:
      districts_count = len(districts_list)

  if block_question:
    blocks = get_blocks_with_submission_data(
      submissions, state_question, district_question, block_question, selected_state_id, selected_district_id
    )
    blocks_list = _names(blocks)
    if selected_block_id and selected_block_id != '':
      blocks_count = 1
    else:
      blocks_count = len(blocks_list)

  if clf_question:
    clfs = get_clfs_with_submission_data(
      submissions, state_question, district_question, block_question, clf_question,
      selected_state_id, selected_district_id, selected_block_id
    )
    clfs_list = _names(clfs)
    if selected_clf_id and selected_clf_id != '':
      clfs_count = 1
    else:
      clfs_count = len(clfs_list)

  # # Count submissions where gender is Female (question ID 1304)
  # female_question_id = 1304
  # women_count = 0
  # try:
  #     female_question = Question.objects.get(id=female_question_id)
  #     female_option = QuestionOption.objects.filter(question=female_question, text__iexact='Female').first()
  #     if female_option:
  #         women_count = submissions.filter(
  #             answers__question=female_question,
  #             answers__selected_options=female_option.id
  #         ).count()
  # except (Question.DoesNotExist, QuestionOption.DoesNotExist):
  #     women_count = submissions.count()  # Fallback to total submissions if question not found

  women_count = submissions.count()  # Fallback to total submissions if question not found

  stats_result = {
    'total_states_count': states_count,
    'total_districts_count': districts_count,
    'total_blocks_count': blocks_count,
    'total_clfs_count': clfs_count,
    'total_women_surveyed': women_count,
    'states_list': states_list,
    'districts_list': districts_list,
    'blocks_list': blocks_list,
    'clfs_list': clfs_list,
  }
  return stats_result


def get_indicator_dashboard_data(indicators, submissions):
  """Get indicator data for dashboard charts using IndicatorResult data"""

  indicator_data = []

  for indicator in indicators:
    # Get results for this indicator
    results = IndicatorResult.objects.filter(
      indicator=indicator,
      submission__in=submissions,
      success=True
    )

    # Count empowered vs disempowered
    empowered_count = 0
    disempowered_count = 0
    total_results = results.count()

    for result in results:
      try:
        if isinstance(result.result, dict):
          # Handle dictionary results - check for various empowerment keys
          empowerment_keys = ['empowered', 'is_empowered', 'empowered_status', 'status']
          is_empowered = False

          for key in empowerment_keys:
            if key in result.result:
              if isinstance(result.result[key], bool):
                is_empowered = result.result[key]
                break
              elif isinstance(result.result[key], str):
                is_empowered = str(result.result[key]).lower() in ['true', 'empowered', 'yes', '1']
                break
              elif isinstance(result.result[key], (int, float)):
                is_empowered = result.result[key] > 0.5
                break

          if is_empowered:
            empowered_count += 1
          else:
            disempowered_count += 1

        elif isinstance(result.result, bool):
          # Handle boolean results directly
          if result.result:
            empowered_count += 1
          else:
            disempowered_count += 1

        elif isinstance(result.result, (int, float)):
          # Handle numeric results - assume threshold of 0.5 or 50
          threshold = 50 if result.result > 1 else 0.5
          if result.result > threshold:
            empowered_count += 1
          else:
            disempowered_count += 1

        elif isinstance(result.result, str):
          # Handle string results
          empowered_values = ['empowered', 'yes', 'true', '1', 'enabled', 'active']
          if result.result.lower() in empowered_values:
            empowered_count += 1
          else:
            disempowered_count += 1

        else:
          # Handle other types - assume disempowered by default
          disempowered_count += 1

      except Exception as e:
        # Log error but continue processing
        print(f"Error processing indicator result for {indicator.name}: {e}")
        disempowered_count += 1

    indicator_data.append({
      'name': indicator.name,
      'empowered_count': empowered_count,
      'disempowered_count': disempowered_count,
      'total_count': total_results,
      'empowerment_percentage': round((empowered_count / total_results * 100) if total_results > 0 else 0, 1)
    })

  return indicator_data


def get_state_map_data(submissions, state_question):
  """Get data for India map visualization.
  Uses text-based state values from both text_answer and selected_options.text."""

  if not state_question:
    return []

  # Group submissions by state and calculate indicator averages
  state_data = []

  # Get unique state text values from submissions (from both text_answer and selected_options.text)
  submission_ids = list(submissions.values_list('id', flat=True))

  # Get states from text_answer
  states_from_text = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=state_question,
    text_answer__isnull=False
  ).exclude(text_answer='').values_list('text_answer', flat=True).distinct()

  # Get states from selected_options.text
  states_from_options = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=state_question,
    selected_options__isnull=False
  ).values_list('selected_options__text', flat=True).distinct()

  # Combine unique state names
  unique_states = set(s for s in states_from_text if s and s.strip())
  unique_states.update(s for s in states_from_options if s and s.strip())

  for state_text in unique_states:
    # Get submissions for this state (check both text_answer and selected_options.text)
    state_text_ids = submissions.filter(
      answers__question=state_question,
      answers__text_answer=state_text
    ).values_list('id', flat=True)

    state_options_ids = submissions.filter(
      answers__question=state_question,
      answers__selected_options__text=state_text
    ).values_list('id', flat=True)

    state_submission_ids = set(state_text_ids) | set(state_options_ids)
    state_submissions = submissions.filter(id__in=state_submission_ids)

    if state_submissions.exists():
      # Calculate average empowerment score for this state
      total_score = 0
      count = 0

      for submission in state_submissions:
        # Get all successful indicator results for this submission
        results = IndicatorResult.objects.filter(
          submission=submission,
          success=True
        )

        for result in results:
          if isinstance(result.result, (int, float)):
            total_score += result.result
            count += 1
          elif isinstance(result.result, dict) and 'score' in result.result:
            total_score += float(result.result['score'])
            count += 1
          elif isinstance(result.result, bool):
            total_score += 1.0 if result.result else 0.0
            count += 1

      avg_score = total_score / count if count > 0 else 0

      # Get coordinates for the state
      state_coords = get_state_coordinates(state_text)

      state_data.append({
        'state': state_text,
        'value': avg_score * 100,  # Convert to percentage
        'count': state_submissions.count(),
        'latitude': state_coords['lat'],
        'longitude': state_coords['lng'],
      })

  return state_data


def get_state_coordinates(state_name):
  """Get approximate coordinates for Indian states"""
  # This is a simplified mapping - you might want to use a more comprehensive approach
  coordinates = {
    'Andhra Pradesh': {'lat': 15.9129, 'lng': 79.7400},
    'Arunachal Pradesh': {'lat': 28.2180, 'lng': 94.7278},
    'Assam': {'lat': 26.2006, 'lng': 92.9376},
    'Bihar': {'lat': 25.0961, 'lng': 85.3131},
    'Chhattisgarh': {'lat': 21.2787, 'lng': 81.8661},
    'Goa': {'lat': 15.2993, 'lng': 74.1240},
    'Gujarat': {'lat': 22.2587, 'lng': 71.1924},
    'Haryana': {'lat': 29.0588, 'lng': 76.0856},
    'Himachal Pradesh': {'lat': 31.1048, 'lng': 77.1734},
    'Jharkhand': {'lat': 23.6102, 'lng': 85.2799},
    'Karnataka': {'lat': 15.3173, 'lng': 75.7139},
    'Kerala': {'lat': 10.8505, 'lng': 76.2711},
    'Madhya Pradesh': {'lat': 22.9734, 'lng': 78.6569},
    'Maharashtra': {'lat': 19.6016, 'lng': 75.3239},
    'Manipur': {'lat': 24.6637, 'lng': 93.9063},
    'Meghalaya': {'lat': 25.4670, 'lng': 91.3662},
    'Mizoram': {'lat': 23.1645, 'lng': 92.9376},
    'Nagaland': {'lat': 26.1584, 'lng': 94.5624},
    'Odisha': {'lat': 20.9517, 'lng': 85.0985},
    'Punjab': {'lat': 31.1471, 'lng': 75.3412},
    'Rajasthan': {'lat': 27.0238, 'lng': 74.2179},
    'Sikkim': {'lat': 27.5330, 'lng': 88.5122},
    'Tamil Nadu': {'lat': 11.1271, 'lng': 78.6569},
    'Telangana': {'lat': 17.1232, 'lng': 78.6262},
    'Tripura': {'lat': 23.8315, 'lng': 91.2868},
    'Uttar Pradesh': {'lat': 26.8467, 'lng': 80.9462},
    'Uttarakhand': {'lat': 30.0668, 'lng': 79.0193},
    'West Bengal': {'lat': 22.9868, 'lng': 87.8550},
  }

  return coordinates.get(state_name, {'lat': 20.5937, 'lng': 78.9629})  # Default to India center


# API Endpoints for Dashboard
@login_required
@url_cache_decorator(timeout=600)
def api_survey_data(request):
  """API endpoint to get survey dashboard data with caching and optimized queries"""

  # Get filter parameters
  programme = request.GET.get('programme', '')
  state = request.GET.get('state', '')
  district = request.GET.get('district', '')
  block = request.GET.get('block', '')
  clf = request.GET.get('clf', '')
  year = request.GET.get('year', '')
  date_from = request.GET.get('date_from', '')
  date_to = request.GET.get('date_to', '')

  # Get questions for filtering
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()
  clf_question = Question.objects.filter(id=3383).first()

  from .selectors import apply_submission_visibility

  # Get submitted submissions for filtering by geography (may be filtered by year for data)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected
  if year:
    all_submissions = all_submissions.filter(submitted_at__year=year)
  all_submissions = apply_report_date_filters(all_submissions, date_from, date_to)

  # Filter by state/district/block/clf from ALL submissions (since state data is in text_answer)
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    state, district, block, clf_question, clf
  )

  # Now get submissions with successful indicator results
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  if year:
    submissions = submissions.filter(submitted_at__year=year)
  submissions = apply_report_date_filters(submissions, date_from, date_to)

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)

  # Calculate statistics
  stats = calculate_survey_stats(submissions, state_question, district_question, block_question, state, district, block,
                                 clf_question, clf)

  # Get indicator data using optimized function
  indicators = list(Indicator.objects.filter(survey_id=6, is_active=True).select_related('script'))
  indicator_ids = [ind.id for ind in indicators]
  submission_ids = list(submissions.values_list('id', flat=True))

  # Use optimized bulk fetch for indicator results
  indicator_stats = get_optimized_indicator_results(indicator_ids, submission_ids)

  indicator_data = []
  for indicator in indicators:
    stats_data = indicator_stats.get(indicator.id, {'empowered': 0, 'disempowered': 0, 'total': 0})
    total = stats_data['total']
    empowered = stats_data['empowered']
    indicator_data.append({
      'name': indicator.name,
      'empowered_count': empowered,
      'disempowered_count': stats_data['disempowered'],
      'total_count': total,
      'empowerment_percentage': round((empowered / total * 100) if total > 0 else 0, 1)
    })

  # Get map data using optimized function
  map_data = get_optimized_state_map_data(submissions, state_question)

  # Build response
  result = {
    'success': True,
    'stats': stats,
    'indicators': indicator_data,
    'map_data': map_data,
  }

  return JsonResponse(result)



@login_required
def api_blocks_by_district(request):
  """API endpoint to get blocks based on selected district"""
  district_id = request.GET.get('district_id', '')

  # Get questions for state, district and block
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  if not district_question or not block_question:
    return JsonResponse({'success': False, 'error': 'Geographic questions not found'})

  # Use the new function to get blocks
  blocks = get_blocks_by_state_and_district_condition(
    state_question, district_question, block_question, None, district_id, request.user
  )

  blocks_data = []
  for block_id, block_text in blocks:
    blocks_data.append({
      'id': block_id,  # Will be None for text answers
      'text': block_text
    })

  return JsonResponse({
    'success': True,
    'blocks': blocks_data
  })


@login_required
def api_blocks_by_state_and_district(request):
  """API endpoint to get blocks based on selected state and/or district"""
  state_id = request.GET.get('state_id', '')
  district_id = request.GET.get('district_id', '')

  # Get questions for state, district and block
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  if not state_question or not district_question or not block_question:
    return JsonResponse({'success': False, 'error': 'Geographic questions not found'})

  # Use the new function to get blocks
  blocks = get_blocks_by_state_and_district_condition(
    state_question, district_question, block_question, state_id, district_id, request.user
  )

  blocks_data = []
  for block_id, block_text in blocks:
    blocks_data.append({
      'id': block_id,  # Will be None for text answers
      'text': block_text
    })

  return JsonResponse({
    'success': True,
    'blocks': blocks_data
  })


@login_required
def api_clfs_by_block(request):
  """API endpoint to get CLFs based on selected state, district and/or block"""
  state_id = request.GET.get('state_id', '')
  district_id = request.GET.get('district_id', '')
  block_id = request.GET.get('block_id', '')

  # Get questions for state, district, block and CLF
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()
  clf_question = Question.objects.filter(id=3383).first()

  if not clf_question:
    return JsonResponse({'success': False, 'error': 'CLF question not found'})

  # Use the CLF function to get CLFs
  clfs = get_clfs_by_block(
    state_question, district_question, block_question, clf_question,
    state_id, district_id, block_id, request.user
  )

  clfs_data = []
  for clf_id, clf_text in clfs:
    clfs_data.append({
      'id': clf_id,
      'text': clf_text
    })

  return JsonResponse({
    'success': True,
    'clfs': clfs_data
  })


@login_required
@url_cache_decorator(timeout=600)
def api_map_data(request):
  """API endpoint to get map data for specific indicator with caching and optimized queries"""

  indicator_id = request.GET.get('indicator_id')
  programme = request.GET.get('programme', '')
  state = request.GET.get('state', '')
  district = request.GET.get('district', '')
  block = request.GET.get('block', '')
  year = request.GET.get('year', '')
  date_from = request.GET.get('date_from', '')
  date_to = request.GET.get('date_to', '')
  state_filter = request.GET.get('state_filter', '')  # New parameter for district-level data

  if not indicator_id:
    return JsonResponse({'success': False, 'error': 'Indicator ID required'})

  try:
    indicator = Indicator.objects.get(id=indicator_id, is_active=True)
  except Indicator.DoesNotExist:
    return JsonResponse({'success': False, 'error': 'Indicator not found'})

  # Get questions for filtering
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  from .selectors import apply_submission_visibility

  # Get submitted submissions for survey 6 (for sidebar dropdowns - NOT filtered by year)
  # Same as main report page to ensure consistent dropdown options
  all_survey_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Get submitted submissions for filtering by geography (may be filtered by year for data)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected (only for data filtering, not sidebar dropdowns)
  if year:
    all_submissions = all_submissions.filter(submitted_at__year=year)

  # Apply date range filters for geography matching
  if date_from:
    from datetime import datetime
    try:
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
          dt_from = datetime.strptime(date_from, fmt)
          break
        except ValueError:
          continue
      else:
        dt_from = None
      if dt_from:
        all_submissions = all_submissions.filter(submitted_at__gte=dt_from)
    except Exception:
      pass
  if date_to:
    from datetime import datetime, timedelta
    try:
      dt_to = None
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
        try:
          dt_to = datetime.strptime(date_to, fmt)
          break
        except ValueError:
          continue
      if not dt_to:
        try:
          dt_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
          pass
      if dt_to:
        all_submissions = all_submissions.filter(submitted_at__lte=dt_to)
    except Exception:
      pass

  # Filter by geography from ALL submissions (since state data is in text_answer)
  clf = request.GET.get('clf', '')
  clf_question = Question.objects.filter(id=3383).first()
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    state, district, block, clf_question, clf
  )

  # Get submissions with successful indicator results for THIS specific indicator
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__indicator=indicator,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  if year:
    submissions = submissions.filter(submitted_at__year=year)

  # Apply date range filters to indicator submissions too
  if date_from:
    from datetime import datetime
    try:
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
          dt_from = datetime.strptime(date_from, fmt)
          break
        except ValueError:
          continue
      else:
        dt_from = None
      if dt_from:
        submissions = submissions.filter(submitted_at__gte=dt_from)
    except Exception:
      pass
  if date_to:
    from datetime import datetime, timedelta
    try:
      dt_to = None
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
        try:
          dt_to = datetime.strptime(date_to, fmt)
          break
        except ValueError:
          continue
      if not dt_to:
        try:
          dt_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
          pass
      if dt_to:
        submissions = submissions.filter(submitted_at__lte=dt_to)
    except Exception:
      pass

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)

  # Get map data for this specific indicator using optimized functions
  if state_filter and state_question:
    # When state_filter is provided, return district-level data for that state
    map_data = get_optimized_district_map_data(indicator, submissions, state_question, district_question, state_filter)
  else:
    # Default: return state-level data
    map_data = get_optimized_state_map_data(submissions, state_question, indicator)

  # Get sidebar dropdown options from ALL submissions (NOT filtered by year)
  # This matches the main report page behavior to show all available states/districts/blocks
  all_states = get_states_with_submission_data(all_survey_submissions, state_question)
  all_districts = get_districts_with_submission_data(all_survey_submissions, state_question, district_question, state)
  all_blocks = get_blocks_with_submission_data(all_survey_submissions, state_question, district_question, block_question, state, district)

  # Build response with no-cache headers
  result = {
    'success': True,
    'map_data': map_data,
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
  }

  response = JsonResponse(result)
  response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
  response['Pragma'] = 'no-cache'
  response['Expires'] = '0'
  return response


def get_indicator_state_map_data(indicator, submissions, state_question):
  """Get state-wise data for a specific indicator.
  Uses text-based state values from both text_answer and selected_options.text."""

  if not state_question:
    return []

  state_data = []

  # Get unique state text values from submissions (from both text_answer and selected_options.text)
  submission_ids = list(submissions.values_list('id', flat=True))

  # Get states from text_answer
  states_from_text = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=state_question,
    text_answer__isnull=False
  ).exclude(text_answer='').values_list('text_answer', flat=True).distinct()

  # Get states from selected_options.text
  states_from_options = SubmissionAnswer.objects.filter(
    submission_id__in=submission_ids,
    question=state_question,
    selected_options__isnull=False
  ).values_list('selected_options__text', flat=True).distinct()

  # Combine unique state names
  unique_states = set(s for s in states_from_text if s and s.strip())
  unique_states.update(s for s in states_from_options if s and s.strip())

  for state_text in unique_states:
    # Get submissions for this state (check both text_answer and selected_options.text)
    state_text_ids = submissions.filter(
      answers__question=state_question,
      answers__text_answer=state_text
    ).values_list('id', flat=True)

    state_options_ids = submissions.filter(
      answers__question=state_question,
      answers__selected_options__text=state_text
    ).values_list('id', flat=True)

    state_submission_ids = set(state_text_ids) | set(state_options_ids)
    state_submissions = submissions.filter(id__in=state_submission_ids)

    if state_submissions.exists():
      # Get results for this specific indicator
      results = IndicatorResult.objects.filter(
        indicator=indicator,
        submission__in=state_submissions,
        success=True
      )

      if results.exists():
        # Calculate empowered/disempowered counts and average value
        empowered_count = 0
        disempowered_count = 0
        total_value = 0
        count = 0

        for result in results:
          if isinstance(result.result, (int, float)):
            total_value += result.result
            count += 1
            # Determine if empowered based on threshold
            threshold = 50 if result.result > 1 else 0.5
            if result.result > threshold:
              empowered_count += 1
            else:
              disempowered_count += 1
          elif isinstance(result.result, dict) and 'value' in result.result:
            value = float(result.result['value'])
            total_value += value
            count += 1
            # Check if empowered
            if 'empowered' in result.result:
              is_empowered = result.result['empowered']
            elif 'is_empowered' in result.result:
              is_empowered = result.result['is_empowered']
            else:
              # Use threshold for numeric values
              threshold = 50 if value > 1 else 0.5
              is_empowered = value > threshold

            if is_empowered:
              empowered_count += 1
            else:
              disempowered_count += 1
          elif isinstance(result.result, bool):
            total_value += 1.0 if result.result else 0.0
            count += 1
            if result.result:
              empowered_count += 1
            else:
              disempowered_count += 1
          else:
            # Handle string results
            is_empowered = False
            if isinstance(result.result, str):
              empowered_values = ['empowered', 'yes', 'true', '1', 'enabled', 'active']
              is_empowered = result.result.lower() in empowered_values

            if is_empowered:
              empowered_count += 1
            else:
              disempowered_count += 1

        avg_value = total_value / count if count > 0 else 0
        empowerment_percentage = (empowered_count / count * 100) if count > 0 else 0

        # Get coordinates
        state_coords = get_state_coordinates(state_text)

        state_data.append({
          'state': state_text,
          'value': avg_value * 100,  # Convert to percentage
          'empowerment_percentage': empowerment_percentage,
          'empowered_count': empowered_count,
          'disempowered_count': disempowered_count,
          'count': count,
          'latitude': state_coords['lat'],
          'longitude': state_coords['lng'],
        })

  return state_data


def get_indicator_district_map_data(indicator, submissions, state_question, district_question, state_filter_name):
  """Get district-wise data for a specific indicator within a specific state.
  Uses text-based state/district values from both text_answer and selected_options.text."""

  if not state_question or not district_question:
    return []

  # Filter submissions for this specific state (check both text_answer and selected_options.text)
  state_text_ids = submissions.filter(
    answers__question=state_question,
    answers__text_answer=state_filter_name
  ).values_list('id', flat=True)

  state_options_ids = submissions.filter(
    answers__question=state_question,
    answers__selected_options__text=state_filter_name
  ).values_list('id', flat=True)

  state_submission_ids = set(state_text_ids) | set(state_options_ids)
  state_submissions = submissions.filter(id__in=state_submission_ids)

  if not state_submissions.exists():
    return []

  # Get unique district text values from state_submissions
  state_sub_ids = list(state_submissions.values_list('id', flat=True))

  # Get districts from text_answer
  districts_from_text = SubmissionAnswer.objects.filter(
    submission_id__in=state_sub_ids,
    question=district_question,
    text_answer__isnull=False
  ).exclude(text_answer='').values_list('text_answer', flat=True).distinct()

  # Get districts from selected_options.text
  districts_from_options = SubmissionAnswer.objects.filter(
    submission_id__in=state_sub_ids,
    question=district_question,
    selected_options__isnull=False
  ).values_list('selected_options__text', flat=True).distinct()

  # Combine unique district names
  unique_districts = set(d for d in districts_from_text if d and d.strip())
  unique_districts.update(d for d in districts_from_options if d and d.strip())

  district_data = []

  for district_text in unique_districts:
    # Get submissions for this district within the filtered state
    district_text_ids = state_submissions.filter(
      answers__question=district_question,
      answers__text_answer=district_text
    ).values_list('id', flat=True)

    district_options_ids = state_submissions.filter(
      answers__question=district_question,
      answers__selected_options__text=district_text
    ).values_list('id', flat=True)

    district_submission_ids = set(district_text_ids) | set(district_options_ids)
    district_submissions = state_submissions.filter(id__in=district_submission_ids)

    if district_submissions.exists():
      # Get results for this specific indicator
      results = IndicatorResult.objects.filter(
        indicator=indicator,
        submission__in=district_submissions,
        success=True
      )

      if results.exists():
        # Calculate empowered/disempowered counts and average value
        empowered_count = 0
        disempowered_count = 0
        total_value = 0
        count = 0

        for result in results:
          if isinstance(result.result, (int, float)):
            total_value += result.result
            count += 1
            # Determine if empowered based on threshold
            threshold = 50 if result.result > 1 else 0.5
            if result.result > threshold:
              empowered_count += 1
            else:
              disempowered_count += 1
          elif isinstance(result.result, dict) and 'value' in result.result:
            value = float(result.result['value'])
            total_value += value
            count += 1
            # Check if empowered
            if 'empowered' in result.result:
              is_empowered = result.result['empowered']
            elif 'is_empowered' in result.result:
              is_empowered = result.result['is_empowered']
            else:
              # Use threshold for numeric values
              threshold = 50 if value > 1 else 0.5
              is_empowered = value > threshold

            if is_empowered:
              empowered_count += 1
            else:
              disempowered_count += 1
          elif isinstance(result.result, bool):
            total_value += 1.0 if result.result else 0.0
            count += 1
            if result.result:
              empowered_count += 1
            else:
              disempowered_count += 1
          else:
            # Handle string results
            is_empowered = False
            if isinstance(result.result, str):
              empowered_values = ['empowered', 'yes', 'true', '1', 'enabled', 'active']
              is_empowered = result.result.lower() in empowered_values

            if is_empowered:
              empowered_count += 1
            else:
              disempowered_count += 1

        avg_value = total_value / count if count > 0 else 0
        empowerment_percentage = (empowered_count / count * 100) if count > 0 else 0

        district_data.append({
          'district': district_text,
          'state': state_filter_name,
          'value': avg_value * 100,  # Convert to percentage
          'empowerment_percentage': empowerment_percentage,
          'empowered_count': empowered_count,
          'disempowered_count': disempowered_count,
          'count': count,
        })

  return district_data


@login_required
@url_cache_decorator(timeout=600)
def api_grouped_indicator_data(request):
  """API endpoint for grouped indicator data by demographic questions with caching and optimized queries"""

  # Get filter parameters
  selected_programme = request.GET.get('programme', '')
  selected_state = request.GET.get('state', '')
  selected_district = request.GET.get('district', '')
  selected_block = request.GET.get('block', '')
  selected_year = request.GET.get('year', '')
  selected_date_from = request.GET.get('date_from', '')
  selected_date_to = request.GET.get('date_to', '')
  selected_demographic = request.GET.get('demographic', 'age')  # Default to age

  # Apply location filters
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  from .selectors import apply_submission_visibility

  # Get submitted submissions for survey 6 (for sidebar dropdowns - NOT filtered by year)
  # Same as main report page to ensure consistent dropdown options
  all_survey_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Get submitted submissions for filtering by geography (may be filtered by year for data)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected (only for data filtering, not sidebar dropdowns)
  if selected_year:
    all_submissions = all_submissions.filter(submitted_at__year=selected_year)

  # Apply date range filters for geography matching
  if selected_date_from:
    from datetime import datetime
    try:
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
          dt_from = datetime.strptime(selected_date_from, fmt)
          break
        except ValueError:
          continue
      else:
        dt_from = None
      if dt_from:
        all_submissions = all_submissions.filter(submitted_at__gte=dt_from)
    except Exception:
      pass
  if selected_date_to:
    from datetime import datetime, timedelta
    try:
      dt_to = None
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
        try:
          dt_to = datetime.strptime(selected_date_to, fmt)
          break
        except ValueError:
          continue
      if not dt_to:
        try:
          dt_to = datetime.strptime(selected_date_to, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
          pass
      if dt_to:
        all_submissions = all_submissions.filter(submitted_at__lte=dt_to)
    except Exception:
      pass

  # Filter by geography from ALL submissions (since state data is in text_answer)
  selected_clf = request.GET.get('clf', '')
  clf_question = Question.objects.filter(id=3383).first()
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    selected_state, selected_district, selected_block, clf_question, selected_clf
  )

  # Get submissions with successful indicator results
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  if selected_year:
    submissions = submissions.filter(submitted_at__year=selected_year)

  # Apply date range filters to indicator submissions too
  if selected_date_from:
    from datetime import datetime
    try:
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
          dt_from = datetime.strptime(selected_date_from, fmt)
          break
        except ValueError:
          continue
      else:
        dt_from = None
      if dt_from:
        submissions = submissions.filter(submitted_at__gte=dt_from)
    except Exception:
      pass
  if selected_date_to:
    from datetime import datetime, timedelta
    try:
      dt_to = None
      for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
        try:
          dt_to = datetime.strptime(selected_date_to, fmt)
          break
        except ValueError:
          continue
      if not dt_to:
        try:
          dt_to = datetime.strptime(selected_date_to, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
          pass
      if dt_to:
        submissions = submissions.filter(submitted_at__lte=dt_to)
    except Exception:
      pass

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)

  # Get demographic question mapping
  demographic_questions = {
    'age': 736,  # Age question
    'literacy': 737,  # Literacy question
    'shg_membership': 1319,  # SHG Membership question
    'fpo_membership': 719,  # FPO Membership question
    'land_ownership': 1298,  # Type of land ownership question (changed from 1383 which has no data)
    'house_membership': 740,  # House Membership question
    'caste': 738,  # Caste of Household question
  }

  demographic_question_id = demographic_questions.get(selected_demographic, 736)
  demographic_question = Question.objects.filter(id=demographic_question_id).first()

  if not demographic_question:
    return JsonResponse({'success': False, 'error': 'Demographic question not found'})

  # Get grouped data using optimized function
  grouped_data = get_optimized_grouped_indicator_data(submissions, demographic_question)

  # Get sidebar dropdown options from ALL submissions (NOT filtered by year)
  # This matches the main report page behavior to show all available states/districts/blocks
  all_states = get_states_with_submission_data(all_survey_submissions, state_question)
  all_districts = get_districts_with_submission_data(all_survey_submissions, state_question, district_question, selected_state)
  all_blocks = get_blocks_with_submission_data(all_survey_submissions, state_question, district_question, block_question, selected_state, selected_district)

  # Build response
  result = {
    'success': True,
    'grouped_data': grouped_data,
    'demographic': selected_demographic,
    'demographic_question': {
      'id': demographic_question.id,
      'text': demographic_question.text,
    },
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
  }

  return JsonResponse(result)


def get_grouped_indicator_data(submissions, demographic_question):
  """Get indicator data grouped by demographic question options"""

  # Get all indicators
  indicators = Indicator.objects.filter(survey_id=6, is_active=True).select_related('script')

  # Get demographic groups
  demographic_groups = []

  if demographic_question.question_type in ['radio', 'checkbox', 'dropdown']:
    # For option-based questions
    options = demographic_question.options.all().order_by('text')

    for option in options:
      group_data = {
        'group_name': option.text,
        'group_value': option.value,
        'indicators': []
      }

      # Get submissions for this demographic group
      group_submissions = submissions.filter(
        answers__question=demographic_question,
        answers__selected_options=option
      )

      if group_submissions.exists():
        # Calculate indicator results for this group
        for indicator in indicators:
          indicator_data = calculate_indicator_for_submissions(indicator, group_submissions)
          indicator_data['group_name'] = option.text
          group_data['indicators'].append(indicator_data)

      demographic_groups.append(group_data)

  else:
    # For text-based questions (like Age), create custom groups
    if demographic_question.id == 736:  # Age question
      age_groups = [
        {'name': '<18', 'condition': lambda x: x < 18},
        {'name': '18-30', 'condition': lambda x: 18 <= x <= 30},
        {'name': '30-50', 'condition': lambda x: 30 < x <= 50},
        {'name': '>50', 'condition': lambda x: x > 50},
      ]

      for age_group in age_groups:
        group_data = {
          'group_name': age_group['name'],
          'group_value': age_group['name'],
          'indicators': []
        }

        # Get submissions for this age group
        group_submissions = []
        for submission in submissions:
          age_answer = submission.answers.filter(question=demographic_question).first()
          if age_answer and age_answer.text_answer:
            try:
              age = int(age_answer.text_answer)
              if age_group['condition'](age):
                group_submissions.append(submission)
            except (ValueError, TypeError):
              continue

        if group_submissions:
          # Calculate indicator results for this group
          for indicator in indicators:
            indicator_data = calculate_indicator_for_submissions(indicator, group_submissions)
            indicator_data['group_name'] = age_group['name']
            group_data['indicators'].append(indicator_data)

        demographic_groups.append(group_data)
    else:
      # For other text-based questions, get unique values
      unique_values = submissions.filter(
        answers__question=demographic_question
      ).values_list('answers__text_answer', flat=True).distinct()

      for value in unique_values:
        if value:  # Skip empty values
          group_data = {
            'group_name': value,
            'group_value': value,
            'indicators': []
          }

          # Get submissions for this value
          group_submissions = submissions.filter(
            answers__question=demographic_question,
            answers__text_answer=value
          )

          if group_submissions.exists():
            # Calculate indicator results for this group
            for indicator in indicators:
              indicator_data = calculate_indicator_for_submissions(indicator, group_submissions)
              indicator_data['group_name'] = value
              group_data['indicators'].append(indicator_data)

          demographic_groups.append(group_data)

  return demographic_groups


def calculate_indicator_for_submissions(indicator, submissions):
  """Calculate empowered/disempowered counts for an indicator for specific submissions"""

  # Get results for this indicator
  results = IndicatorResult.objects.filter(
    indicator=indicator,
    submission__in=submissions,
    success=True
  )

  empowered_count = 0
  disempowered_count = 0
  total_count = results.count()

  for result in results:
    is_empowered = False

    if isinstance(result.result, dict):
      # Handle dictionary results
      empowerment_keys = ['empowered', 'is_empowered', 'empowered_status', 'status']
      for key in empowerment_keys:
        if key in result.result:
          if isinstance(result.result[key], bool):
            is_empowered = result.result[key]
            break
          elif isinstance(result.result[key], str):
            is_empowered = str(result.result[key]).lower() in ['true', 'empowered', 'yes', '1']
            break
          elif isinstance(result.result[key], (int, float)):
            is_empowered = result.result[key] > 0.5
            break
    elif isinstance(result.result, bool):
      is_empowered = result.result
    elif isinstance(result.result, (int, float)):
      is_empowered = result.result > 0.5
    elif isinstance(result.result, str):
      empowered_values = ['empowered', 'yes', 'true', '1', 'enabled', 'active']
      is_empowered = result.result.lower() in empowered_values

    if is_empowered:
      empowered_count += 1
    else:
      disempowered_count += 1

  empowerment_percentage = (empowered_count / total_count * 100) if total_count > 0 else 0

  return {
    'indicator_id': indicator.id,
    'indicator_name': indicator.name,
    'empowered_count': empowered_count,
    'disempowered_count': disempowered_count,
    'total_count': total_count,
    'empowerment_percentage': round(empowerment_percentage, 1)
  }

@login_required
def survey_import_export(request, pk):
  """View for importing and exporting survey submissions"""
  survey = get_object_or_404(Survey, pk=pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", survey):
    messages.error(request, _("Permission denied"))
    return redirect("survey:survey_list")

  if request.method == "GET":
    return render(request, "survey/survey_import_export.html", {"survey": survey})

  # Handle POST requests for import
  if "import_file" not in request.FILES:
    messages.error(request, _("Please select a file to import"))
    return render(request, "survey/survey_import_export.html", {"survey": survey})

  import_file = request.FILES["import_file"]

  if not import_file.name.endswith(('.csv', '.xlsx', '.xls')):
    messages.error(request, _("File must be a CSV or Excel file"))
    return render(request, "survey/survey_import_export.html", {"survey": survey})

  try:
    # Read the file
    if import_file.name.endswith('.csv'):
      # For CSV, handle comment lines and skip headers properly
      df = pd.read_csv(import_file, comment='#', header=None)
    else:
      # For Excel, read all rows
      df = pd.read_excel(import_file, header=None)

    # Validate structure
    if len(df) < 3:
      messages.error(request, _("File must have at least 3 rows: Question IDs, Question Text, and Data"))
      return render(request, "survey/survey_import_export.html", {"survey": survey})

    # Extract headers and data
    question_id_row = df.iloc[0]  # First row: Question IDs
    question_text_row = df.iloc[1]  # Second row: Question Text
    data_rows = df.iloc[2:]  # Third row onwards: Actual data

    # Parse question IDs and create mapping
    question_id_map = {}
    questions_map = {}

    for col_idx, cell_value in question_id_row.items():
      if pd.notna(cell_value):
        # Extract question ID from formats like "Q1", "Q23", or just "1", "23"
        question_id_text = str(cell_value).strip()
        if question_id_text.startswith('Q'):
          question_id_text = question_id_text[1:]  # Remove 'Q' prefix

        try:
          question_id = int(question_id_text)
          question_id_map[col_idx] = question_id
        except ValueError:
          continue

    # Get all survey questions and create mapping
    survey_questions = {q.id: q for q in survey.questions.all()}

    # Validate that all question IDs in file exist in survey
    invalid_question_ids = []
    for col_idx, question_id in question_id_map.items():
      if question_id not in survey_questions:
        invalid_question_ids.append(question_id)

    if invalid_question_ids:
      messages.error(request, _("Invalid question IDs found: {ids}").format(ids=", ".join(map(str, invalid_question_ids))))
      return render(request, "survey/survey_import_export.html", {"survey": survey})

    # Import submissions
    imported_count = 0
    errors = []

    for index, row in data_rows.iterrows():
      try:
        # Skip empty rows
        if row.isna().all():
          continue

        answers_data = {}
        has_data = False

        # Process each column
        for col_idx, question_id in question_id_map.items():
          if col_idx >= len(row) or pd.isna(row.iloc[col_idx]):
            continue

          question = survey_questions[question_id]
          answer_value = row.iloc[col_idx]

          if pd.isna(answer_value) or str(answer_value).strip() == '':
            continue

          has_data = True

          if question.question_type in ['radio', 'checkbox', 'dropdown']:
            # Handle option-based questions
            answer_text = str(answer_value).strip()
            selected_options = []

            # Split by comma for multiple selections (checkbox)
            option_texts = [opt.strip() for opt in answer_text.split(',')]

            for option_text in option_texts:
              if option_text:
                option = question.options.filter(text__iexact=option_text).first()
                if option:
                  selected_options.append(option.id)
                else:
                  # If option not found, add to errors but continue
                  errors.append(f"Row {index + 3}: Option '{option_text}' not found for question '{question.text}'")

            if selected_options:
              answers_data[question.id] = {
                'selected_options': selected_options
              }

          else:
            # Handle text-based questions
            answers_data[question.id] = {
              'text_answer': str(answer_value).strip()
            }

        # Create submission if we have data
        if has_data and answers_data:
          submission = Submission.objects.create(
            survey=survey,
            user=request.user,
            status='completed',
            submitted_at=datetime.now()
          )

          # Create answers
          for question_id, answer_data in answers_data.items():
            question = Question.objects.get(id=question_id)
            answer = SubmissionAnswer.objects.create(
              submission=submission,
              question=question,
              text_answer=answer_data.get('text_answer')
            )

            if 'selected_options' in answer_data:
              answer.selected_options.set(answer_data['selected_options'])

          imported_count += 1

      except Exception as e:
        errors.append(f"Row {index + 3}: {str(e)}")

    if imported_count > 0:
      messages.success(request, _("Successfully imported {count} submissions").format(count=imported_count))
    else:
      messages.warning(request, _("No submissions were imported"))

    if errors:
      messages.error(request, _("Some errors occurred: {errors}").format(errors=", ".join(errors[:5])))

  except Exception as e:
    messages.error(request, _("Error importing file: {error}").format(error=str(e)))

  return render(request, "survey/survey_import_export.html", {"survey": survey})


@login_required
def export_survey_submissions(request, pk):
  """Export survey submissions as CSV/Excel with proper format"""
  survey = get_object_or_404(Survey, pk=pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", survey):
    messages.error(request, _("Permission denied"))
    return redirect("survey:survey_list")


  # Get all submissions
  from .selectors import apply_submission_visibility
  submissions = apply_submission_visibility(
    survey.submissions.all().prefetch_related('answers', 'answers__question', 'answers__selected_options'),
    request.user,
  )

  # Get all questions ordered by their order
  questions = survey.questions.all().order_by('order', 'id')

  # Precompute which questions have "other" option
  questions_with_other = set()
  for question in questions:
    if question.question_type in ['radio', 'checkbox', 'dropdown']:
      # Check if option text is exactly "Other" or starts with "Other " or "Other("
      if any(
        opt.text.strip().lower() == 'other' or
        opt.text.strip().lower().startswith('other ') or
        opt.text.strip().lower().startswith('other(')
        for opt in question.options.all()
      ):
        questions_with_other.add(question.id)

  # Create data with proper header format
  # Row 1: Question IDs
  # Row 2: Question Text
  # Row 3+: Submission data

  from django.utils import timezone as tz
  ist_tz = tz.get_fixed_timezone(330)

  # Header rows - add metadata columns before question columns
  header_data = {'_submission_id': 'Submission ID', '_submitted_at': 'Submitted At', '_submitter': 'Submitter', '_status': 'Status', '_language': 'Language', '_app_version': 'App Version'}
  question_text_data = {'_submission_id': 'Submission ID', '_submitted_at': 'Submitted At (IST)', '_submitter': 'Submitter', '_status': 'Status', '_language': 'Language', '_app_version': 'App Version'}

  for question in questions:
    header_data[question.id] = f"Q{question.id}"
    question_text_data[question.id] = question.text
    if question.id in questions_with_other:
      header_data[f"{question.id}_other"] = f"Q{question.id}_other"
      question_text_data[f"{question.id}_other"] = f"{question.text}::Other"

  # Submission data
  submission_data = []
  for submission in submissions:
    submitted_at_ist = submission.submitted_at.astimezone(ist_tz)
    row = {
      '_submission_id': submission.pk,
      '_submitted_at': submitted_at_ist.strftime("%b %d, %Y %I:%M %p"),
      '_submitter': submission.get_submitter_display(),
      '_status': submission.get_status_display(),
      '_language': submission.language or survey.default_language,
      '_app_version': submission.app_version or "",
    }
    for question in questions:
      answer = submission.answers.filter(question=question).first()
      if answer:
        if answer.selected_options.exists():
          row[question.id] = ", ".join([opt.text for opt in answer.selected_options.all()])
        else:
          row[question.id] = answer.text_answer or ""
        if question.id in questions_with_other:
          row[f"{question.id}_other"] = answer.other_text or ""
      else:
        row[question.id] = ""
        if question.id in questions_with_other:
          row[f"{question.id}_other"] = ""
    submission_data.append(row)

  # Create Excel workbook with data validation
  if request.GET.get('format', 'csv') == 'excel':
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Survey Submissions"

    # Set up the data
    all_data = [header_data, question_text_data] + submission_data

    # Write data to worksheet
    for row_idx, row_data in enumerate(all_data, start=1):
      for col_idx, (key, value) in enumerate(row_data.items(), start=1):
        cell = ws.cell(row=row_idx, column=col_idx, value=value)

        # Style the header rows
        if row_idx == 1:  # Question IDs row
          cell.font = Font(bold=True)
          cell.fill = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
          cell.alignment = Alignment(horizontal="center")
        elif row_idx == 2:  # Question text row
          cell.font = Font(bold=True)
          cell.fill = PatternFill(start_color="F3E5F5", end_color="F3E5F5", fill_type="solid")
          cell.alignment = Alignment(wrap_text=True)

    # Add data validation for option-based questions (offset for metadata columns)
    for col_idx, question in enumerate(questions, start=7):
      if question.question_type in ['radio', 'checkbox', 'dropdown']:
        options = question.options.all()
        if options:
          dv = DataValidation(type="list", formula1=f'"{",".join([opt.text for opt in options])}"', allow_blank=True)
          dv.add(f"{get_column_letter(col_idx)}3:{get_column_letter(col_idx)}{len(all_data) + 1}")
          ws.add_data_validation(dv)

    # Auto-adjust column widths
    all_keys = list(header_data.keys())
    for col_idx, key in enumerate(all_keys, start=1):
      column_letter = get_column_letter(col_idx)
      max_length = len(str(header_data[key]))
      max_length = max(max_length, len(str(question_text_data[key])))

      for row_data in submission_data:
        if key in row_data and row_data[key]:
          max_length = max(max_length, len(str(row_data[key])))

      ws.column_dimensions[column_letter].width = min(max_length + 2, 50)

    # Set row height for question text row
    ws.row_dimensions[2].height = 40

    # Create response
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{survey.title}_submissions.xlsx"'
    wb.save(response)

  else:
    # CSV export (without data validation)
    all_data = [header_data, question_text_data] + submission_data
    df = pd.DataFrame(all_data)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{survey.title}_submissions.csv"'
    df.to_csv(response, index=False, header=False)

  return response


@login_required
def download_survey_template(request, pk):
  """Download a template file for importing survey submissions with proper format"""
  survey = get_object_or_404(Survey, pk=pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", survey):
    messages.error(request, _("Permission denied"))
    return redirect("survey:survey_list")

  # Get all questions ordered by their order
  questions = survey.questions.all().order_by('order', 'id')

  # Create template data with proper header format
  # Row 1: Question IDs
  # Row 2: Question Text
  # Row 3+: Example data

  # Header rows
  header_data = {}
  question_text_data = {}

  for question in questions:
    header_data[question.id] = f"Q{question.id}"
    question_text_data[question.id] = question.text

  # Example data rows
  example_data = []

  # Create 2-3 example rows
  for example_idx in range(2):
    row = {}
    for question in questions:
      if question.question_type in ['radio', 'checkbox', 'dropdown']:
        # For option-based questions, use the first available option
        options = question.options.all()
        if options:
          if question.question_type == 'checkbox' and example_idx == 1:
            # For checkbox, show multiple options in second example
            if len(options) >= 2:
              row[question.id] = f"{options[0].text}, {options[1].text}"
            else:
              row[question.id] = options[0].text
          else:
            row[question.id] = options[min(example_idx, len(options) - 1)].text
        else:
          row[question.id] = f"Select option"
      else:
        # For text-based questions, provide example answers
        if question.question_type == 'text':
          row[question.id] = f"Example text answer {example_idx + 1}"
        elif question.question_type == 'textarea':
          row[question.id] = f"Example longer text answer for textarea {example_idx + 1}"
        elif question.question_type == 'number':
          row[question.id] = str(25 + example_idx * 5)
        elif question.question_type == 'email':
          row[question.id] = f"example{example_idx + 1}@email.com"
        elif question.question_type == 'date':
          row[question.id] = f"2024-0{example_idx + 1}-15"
        elif question.question_type == 'time':
          row[question.id] = f"1{example_idx}:00"
        elif question.question_type == 'datetime':
          row[question.id] = f"2024-0{example_idx + 1}-15 1{example_idx}:00:00"
        else:
          row[question.id] = f"Example {question.question_type} answer {example_idx + 1}"

    example_data.append(row)

  # Create Excel workbook with data validation
  if request.GET.get('format', 'csv') == 'excel':
    from openpyxl import Workbook
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Template"

    # Set up the data
    all_data = [header_data, question_text_data] + example_data

    # Define border style
    thin_border = Border(
      left=Side(style='thin'),
      right=Side(style='thin'),
      top=Side(style='thin'),
      bottom=Side(style='thin')
    )

    # Write data to worksheet
    for row_idx, row_data in enumerate(all_data, start=1):
      for col_idx, (question_id, value) in enumerate(row_data.items(), start=1):
        cell = ws.cell(row=row_idx, column=col_idx, value=value)

        # Style the header rows
        if row_idx == 1:  # Question IDs row
          cell.font = Font(bold=True, color="FFFFFF")
          cell.fill = PatternFill(start_color="2196F3", end_color="2196F3", fill_type="solid")
          cell.alignment = Alignment(horizontal="center")
          cell.border = thin_border
        elif row_idx == 2:  # Question text row
          cell.font = Font(bold=True)
          cell.fill = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
          cell.alignment = Alignment(wrap_text=True, vertical="center")
          cell.border = thin_border
        elif row_idx >= 3:  # Example data rows
          if row_idx == 3:  # First example row
            cell.fill = PatternFill(start_color="F8F9FA", end_color="F8F9FA", fill_type="solid")
          cell.border = thin_border

    # Add data validation for option-based questions
    for col_idx, question in enumerate(questions, start=1):
      if question.question_type in ['radio', 'checkbox', 'dropdown']:
        options = question.options.all()
        if options:
          # Create dropdown validation
          dv = DataValidation(type="list", formula1=f'"{",".join([opt.text for opt in options])}"', allow_blank=True)

          # Apply validation to example rows and beyond (row 3 onwards)
          dv.add(f"{get_column_letter(col_idx)}3:{get_column_letter(col_idx)}1048576")  # Apply to entire column from row 3
          ws.add_data_validation(dv)

    # Auto-adjust column widths
    for col_idx, question in enumerate(questions, start=1):
      column_letter = get_column_letter(col_idx)
      max_length = len(str(question.id)) + 3  # Question ID with "Q" prefix
      max_length = max(max_length, len(question.text))  # Question text

      # Check example data
      for row_data in example_data:
        if question.id in row_data and row_data[question.id]:
          max_length = max(max_length, len(str(row_data[question.id])))

      ws.column_dimensions[column_letter].width = min(max_length + 2, 60)

    # Set row height for question text row
    ws.row_dimensions[2].height = 60

    # Add a comments row at the top
    ws.insert_rows(1)
    ws.cell(row=1, column=1, value="Instructions:")
    ws.cell(row=1, column=2, value="Row 1: Question IDs | Row 2: Question Text | Row 3+: Your Data")
    ws.cell(row=1, column=1).font = Font(bold=True, color="FF5722")
    ws.cell(row=1, column=2).font = Font(italic=True, color="666666")

    # Merge instruction cells
    ws.merge_cells(start_row=1, start_column=2, end_row=1, end_column=len(questions))

    # Create response
    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{survey.title}_template.xlsx"'
    wb.save(response)

  else:
    # CSV export (without data validation)
    all_data = [header_data, question_text_data] + example_data
    df = pd.DataFrame(all_data)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{survey.title}_template.csv"'

    # Create CSV with custom format
    output = StringIO()
    output.write("# Template for importing survey submissions\n")
    output.write(f"# Survey: {survey.title}\n")
    output.write("# Format: Row 1 = Question IDs, Row 2 = Question Text, Row 3+ = Your data\n")
    output.write("# For multiple choice questions, use exact option text separated by commas\n\n")

    df.to_csv(output, index=False, header=False)
    response.write(output.getvalue())

  return response


@login_required
def get_survey_question_enums(request, pk):
  """Get available options for survey questions (AJAX endpoint)"""
  survey = get_object_or_404(Survey, pk=pk)

  # Check permission
  if not request.user.has_perm("survey.view_survey", survey):
    return JsonResponse({"status": "error", "message": "Permission denied"}, status=403)

  questions_data = {}

  for question in survey.questions.all():
    question_data = {
      'id': question.id,
      'text': question.text,
      'type': question.question_type,
      'required': question.is_required,
      'options': []
    }

    if question.question_type in ['radio', 'checkbox', 'dropdown']:
      question_data['options'] = [
        {'text': opt.text, 'value': opt.value or opt.text}
        for opt in question.options.all()
      ]

    # Add validation info
    if question.question_type == 'number':
      if question.min_value is not None:
        question_data['min_value'] = float(question.min_value)
      if question.max_value is not None:
        question_data['max_value'] = float(question.max_value)

    if question.question_type in ['text', 'textarea']:
      if question.min_length is not None:
        question_data['min_length'] = question.min_length
      if question.max_length is not None:
        question_data['max_length'] = question.max_length

    # Use question text as primary key, but also include key if available
    questions_data[question.text] = question_data
    if question.key:
      questions_data[question.key] = question_data

  return JsonResponse({
    "status": "success",
    "questions": questions_data,
    "question_types": dict(Question.QUESTION_TYPES)
  })


@login_required
@url_cache_decorator(timeout=600)
def api_module_g2a_data(request):
  """API endpoint to get MODULE G2(A) decision-making data"""
  import logging
  logger = logging.getLogger(__name__)

  # Get filter parameters
  programme = request.GET.get('programme', '')
  state = request.GET.get('state', '')
  district = request.GET.get('district', '')
  block = request.GET.get('block', '')
  year = request.GET.get('year', '')
  date_from = request.GET.get('date_from', '')
  date_to = request.GET.get('date_to', '')

  logger.info(f"MODULE G2(A) API called with filters - state: {state}, district: {district}, block: {block}, year: {year}, date_from: {date_from}, date_to: {date_to}")

  from .selectors import apply_submission_visibility

  # Get ALL submissions for filtering by geography (since state data is in text_answer)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected
  if year:
    all_submissions = all_submissions.filter(submitted_at__year=year)
  all_submissions = apply_report_date_filters(all_submissions, date_from, date_to)

  # Get questions for filtering
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  # Filter by geography from ALL submissions (since state data is in text_answer)
  clf = request.GET.get('clf', '')
  clf_question = Question.objects.filter(id=3383).first()
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    state, district, block, clf_question, clf
  )

  # Get submissions with successful indicator results
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  logger.info(f"Base submissions count: {submissions.count()}")

  if year:
    submissions = submissions.filter(submitted_at__year=year)
    logger.info(f"After year filter: {submissions.count()} submissions")
  submissions = apply_report_date_filters(submissions, date_from, date_to)

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)
    logger.info(f"After geographic filter: {submissions.count()} submissions")

  # Question 909 options mapping with their sub-questions
  activities_mapping = [
    {
      "option": "Staple grain farming, processing, trading or marketing: grains that are grown primarily for food consumption- (Wheat, Paddy, Mustard, Millet, Gram, Maize, Pulses)",
      "short_name": "Staple Grain (rice, corn etc.) farming, processing & marketing",
      "make_decision": 911,
      "decision_output": 1294,
      "decision_income": 915
    },
    {
      "option": "[Vegetables, Fruits, Flowers, Other Crops] farming, processing, trading or marketing",
      "short_name": "Horticulture (fruits, flowers etc.) farming, processing & marketing",
      "make_decision": 917,
      "decision_output": 919,
      "decision_income": 920
    },
    {
      "option": "Large livestock raising (cattle, buffaloes) and Other small livestock raising (goats) or processing, trading or marketing of milk, meat or meat products",
      "short_name": "Large Livestock (cow, buffaloes etc.) & Small Ruminant (goat etc.) raising, processing & marketing",
      "make_decision": 922,
      "decision_output": 924,
      "decision_income": 925,
      "additional_options": ["Other small livestock raising (goats) or processing, trading or marketing of milk, meat or meat products"],
      "additional_questions": {
        "make_decision": [927],
        "decision_output": [929],
        "decision_income": [930]
      }
    },
    {
      "option": "Poultry and other small animals raising (chickens, ducks, turkeys) and processing, trading or marketing of eggs, meat or meat products",
      "short_name": "Poultry & Small Animal (ducks, turkeys etc.) farming, processing & marketing",
      "make_decision": 932,
      "decision_output": 935,
      "decision_income": 936
    },
    {
      "option": "Non-farm economic activities in other sectors (running a small business, self-employment, buy-and-sell)",
      "short_name": "Non-Farm Economic Activities: small business & self-employment",
      "make_decision": 938,
      "decision_output": None,
      "decision_income": 940
    },
    {
      "option": "Wage and salary employment (work that is paid for in cash or in-kind, including both agriculture and other wage work)",
      "short_name": "Wage & Salary Employment: agriculture & other sectors",
      "make_decision": 942,
      "decision_output": None,
      "decision_income": 944
    },
    {
      "option": "FPO work activities (selling of produce, aggregating of the produce, village level meetings, capacity building training)",
      "short_name": "FPO Activities: produce sales, aggregation, meetings & training",
      "make_decision": 946,
      "decision_output": None,
      "decision_income": 948
    }
  ]

  # Initialize result structure
  output_decision_data = []
  income_decision_data = []
  participation_data = []
  activity_distribution_data = []

  # Get question 909 for activity selection
  question_909 = Question.objects.filter(id=909).first()

  # Process each activity
  for activity in activities_mapping:
    # Filter submissions to only those who selected this activity in question 909
    # Get the option from question 909 that matches this activity
    activity_options_list = []
    if question_909:
      # Try to find the matching option for this activity
      all_options = QuestionOption.objects.filter(question=question_909)

      # Find main option
      for opt in all_options:
        if activity['option'].lower() in opt.text.lower() or opt.text.lower() in activity['option'].lower():
          activity_options_list.append(opt)
          break

      # If there are additional options (for merged categories like livestock)
      if 'additional_options' in activity:
        for additional_opt_text in activity['additional_options']:
          for opt in all_options:
            if additional_opt_text.lower() in opt.text.lower() or opt.text.lower() in additional_opt_text.lower():
              activity_options_list.append(opt)
              break

    # Filter submissions where this activity was selected in question 909
    filtered_submissions = submissions
    if activity_options_list:
      # Combine submissions from all matching options
      filtered_submissions = submissions.filter(
        answers__question=question_909,
        answers__selected_options__in=activity_options_list
      ).distinct()

    # Count responses for output decision (only if decision_output is not None)
    if activity['decision_output']:
      output_counts = get_decision_counts(filtered_submissions, activity['decision_output'])

      # Add counts from additional questions if they exist
      if 'additional_questions' in activity and 'decision_output' in activity['additional_questions']:
        for add_q_id in activity['additional_questions']['decision_output']:
          add_counts = get_decision_counts(filtered_submissions, add_q_id)
          output_counts['little_no_input'] += add_counts['little_no_input']
          output_counts['some_input'] += add_counts['some_input']
          output_counts['most_all_input'] += add_counts['most_all_input']

      output_decision_data.append({
        'activity': activity['short_name'],
        'little_no_input': output_counts['little_no_input'],
        'some_input': output_counts['some_input'],
        'most_all_input': output_counts['most_all_input']
      })

    # Count responses for income decision
    income_counts = get_decision_counts(filtered_submissions, activity['decision_income'])

    # Add counts from additional questions if they exist
    if 'additional_questions' in activity and 'decision_income' in activity['additional_questions']:
      for add_q_id in activity['additional_questions']['decision_income']:
        add_counts = get_decision_counts(filtered_submissions, add_q_id)
        income_counts['little_no_input'] += add_counts['little_no_input']
        income_counts['some_input'] += add_counts['some_input']
        income_counts['most_all_input'] += add_counts['most_all_input']

    income_decision_data.append({
      'activity': activity['short_name'],
      'little_no_input': income_counts['little_no_input'],
      'some_input': income_counts['some_input'],
      'most_all_input': income_counts['most_all_input']
    })

    # Count responses for participation/make_decision
    participation_counts = get_decision_counts(filtered_submissions, activity['make_decision'])

    # Add counts from additional questions if they exist
    if 'additional_questions' in activity and 'make_decision' in activity['additional_questions']:
      for add_q_id in activity['additional_questions']['make_decision']:
        add_counts = get_decision_counts(filtered_submissions, add_q_id)
        participation_counts['little_no_input'] += add_counts['little_no_input']
        participation_counts['some_input'] += add_counts['some_input']
        participation_counts['most_all_input'] += add_counts['most_all_input']

    participation_data.append({
      'activity': activity['short_name'],
      'little_no_input': participation_counts['little_no_input'],
      'some_input': participation_counts['some_input'],
      'most_all_input': participation_counts['most_all_input']
    })

    # Count total women who selected this activity for distribution chart
    if activity_options_list:
      activity_count = submissions.filter(
        answers__question=question_909,
        answers__selected_options__in=activity_options_list
      ).distinct().count()
    else:
      activity_count = 0

    activity_distribution_data.append({
      'activity': activity['short_name'],  # Use short name for consistency
      'count': activity_count
    })

  # Calculate stats
  total_women = submissions.count()
  total_activities = len([item for item in activity_distribution_data if item['count'] > 0])

  # Calculate dashboard stats (states, districts, blocks, women surveyed)
  dashboard_stats = calculate_survey_stats(
    submissions, state_question, district_question, block_question,
    state, district, block
  )

  # Get sidebar dropdown options from ALL submissions (text_answer data)
  all_states = get_states_with_submission_data(all_submissions, state_question)
  all_districts = get_districts_with_submission_data(all_submissions, state_question, district_question, state)
  all_blocks = get_blocks_with_submission_data(all_submissions, state_question, district_question, block_question, state, district)

  return JsonResponse({
    'success': True,
    'output_decision_data': output_decision_data,
    'income_decision_data': income_decision_data,
    'participation_data': participation_data,
    'activity_distribution_data': activity_distribution_data,
    'stats': {
      'total_women': total_women,
      'total_activities': total_activities,
      **dashboard_stats  # Merge dashboard stats (total_states_count, total_districts_count, etc.)
    },
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
  })


def get_decision_counts(submissions, question_id):
  """Helper function to count decision responses for a question"""
  import logging
  logger = logging.getLogger(__name__)

  # Initialize counts
  counts = {
    'little_no_input': 0,
    'some_input': 0,
    'most_all_input': 0
  }

  if not question_id:
    logger.info(f"No question_id provided")
    return counts

  # Get the question
  question = Question.objects.filter(id=question_id).first()
  if not question:
    logger.warning(f"Question {question_id} not found")
    return counts

  logger.info(f"Processing question {question_id}: {question.text[:50]}")

  # Get all answers for this question in the filtered submissions
  answers = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question
  ).prefetch_related('selected_options')

  logger.info(f"Found {answers.count()} answers for question {question_id}")

  # Count responses based on the option text
  for answer in answers:
    for option in answer.selected_options.all():
      option_text_lower = option.text.lower()
      logger.debug(f"Processing option: {option.text}")

      # Map options to categories
      if 'little' in option_text_lower or 'no' in option_text_lower:
        counts['little_no_input'] += 1
      elif 'some' in option_text_lower:
        counts['some_input'] += 1
      elif 'most' in option_text_lower or 'all' in option_text_lower:
        counts['most_all_input'] += 1

  logger.info(f"Question {question_id} counts: {counts}")
  return counts


@url_cache_decorator(timeout=600)
def api_self_efficacy_data(request):
  """API endpoint to get Self Efficacy data for questions 1171-1179"""
  import logging
  logger = logging.getLogger(__name__)

  # Get filter parameters
  programme = request.GET.get('programme', '')
  state = request.GET.get('state', '')
  district = request.GET.get('district', '')
  block = request.GET.get('block', '')
  year = request.GET.get('year', '')
  date_from = request.GET.get('date_from', '')
  date_to = request.GET.get('date_to', '')

  logger.info(f"Self Efficacy API called with filters - state: {state}, district: {district}, block: {block}, year: {year}")

  from .selectors import apply_submission_visibility

  # Get ALL submissions for filtering by geography (since state data is in text_answer)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected
  if year:
    all_submissions = all_submissions.filter(submitted_at__year=year)
  all_submissions = apply_report_date_filters(all_submissions, date_from, date_to)

  # Get questions for filtering
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  # Filter by geography from ALL submissions (since state data is in text_answer)
  clf = request.GET.get('clf', '')
  clf_question = Question.objects.filter(id=3383).first()
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    state, district, block, clf_question, clf
  )

  # Get submissions with successful indicator results
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  logger.info(f"Base submissions count: {submissions.count()}")

  if year:
    submissions = submissions.filter(submitted_at__year=year)
    logger.info(f"After year filter: {submissions.count()} submissions")
  submissions = apply_report_date_filters(submissions, date_from, date_to)

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)
    logger.info(f"After geographic filter: {submissions.count()} submissions")

  # Self Efficacy questions mapping
  questions_data = {}

  # Question IDs for Self Efficacy
  question_ids = [1171, 1172, 1173, 1174, 1175, 1176, 1177, 1178, 1179]

  for question_id in question_ids:
    question_data = get_self_efficacy_counts(submissions, question_id)
    questions_data[str(question_id)] = question_data
    logger.info(f"Question {question_id} data: {question_data}")

  # Calculate stats
  total_women = submissions.count()

  # Calculate dashboard stats (states, districts, blocks, women surveyed)
  dashboard_stats = calculate_survey_stats(
    submissions, state_question, district_question, block_question,
    state, district, block
  )

  # Get sidebar dropdown options from ALL submissions (text_answer data)
  all_states = get_states_with_submission_data(all_submissions, state_question)
  all_districts = get_districts_with_submission_data(all_submissions, state_question, district_question, state)
  all_blocks = get_blocks_with_submission_data(all_submissions, state_question, district_question, block_question, state, district)

  return JsonResponse({
    'success': True,
    'questions_data': questions_data,
    'stats': {
      'total_women': total_women,
      'total_questions': len(question_ids),
      **dashboard_stats  # Merge dashboard stats (total_states_count, total_districts_count, etc.)
    },
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
  })


def get_self_efficacy_counts(submissions, question_id):
  """Helper function to count self efficacy responses for a question"""
  import logging
  logger = logging.getLogger(__name__)

  # Initialize counts for the 5 options
  counts = {
    'strongly_agree': 0,
    'agree': 0,
    'neither': 0,
    'disagree': 0,
    'strongly_disagree': 0
  }

  if not question_id:
    logger.info(f"No question_id provided")
    return counts

  # Get the question
  question = Question.objects.filter(id=question_id).first()
  if not question:
    logger.warning(f"Question {question_id} not found")
    return counts

  logger.info(f"Processing question {question_id}: {question.text[:50] if question.text else 'No text'}")

  # Get all answers for this question in the filtered submissions
  answers = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question
  ).prefetch_related('selected_options')

  logger.info(f"Found {answers.count()} answers for question {question_id}")

  # Count responses based on the option text
  for answer in answers:
    for option in answer.selected_options.all():
      option_text_lower = option.text.lower().strip()
      logger.debug(f"Processing option: '{option.text}'")

      # Map options to categories - check most specific first
      # Must check "strongly" variants before plain variants
      if 'strongly agree' in option_text_lower:
        counts['strongly_agree'] += 1
        logger.debug(f"Matched: strongly_agree")
      elif 'strongly disagree' in option_text_lower:
        counts['strongly_disagree'] += 1
        logger.debug(f"Matched: strongly_disagree")
      elif 'neither' in option_text_lower:
        counts['neither'] += 1
        logger.debug(f"Matched: neither")
      elif 'agree' in option_text_lower:
        counts['agree'] += 1
        logger.debug(f"Matched: agree")
      elif 'disagree' in option_text_lower:
        counts['disagree'] += 1
        logger.debug(f"Matched: disagree")
      else:
        logger.warning(f"Unmatched option text: '{option.text}'")

  logger.info(f"Question {question_id} counts: {counts}")
  return counts


@url_cache_decorator(timeout=600)
def api_g3b_financial_services_data(request):
  """API endpoint to get G3B Financial Services data"""
  import logging
  logger = logging.getLogger(__name__)

  # Get filter parameters
  programme = request.GET.get('programme', '')
  state = request.GET.get('state', '')
  district = request.GET.get('district', '')
  block = request.GET.get('block', '')
  year = request.GET.get('year', '')
  date_from = request.GET.get('date_from', '')
  date_to = request.GET.get('date_to', '')

  logger.info(f"G3B API called with filters - state: {state}, district: {district}, block: {block}, year: {year}")

  from .selectors import apply_submission_visibility

  # Get ALL submissions for filtering by geography (since state data is in text_answer)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected
  if year:
    all_submissions = all_submissions.filter(submitted_at__year=year)
  all_submissions = apply_report_date_filters(all_submissions, date_from, date_to)

  # Get questions for filtering
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  # Filter by geography from ALL submissions (since state data is in text_answer)
  clf = request.GET.get('clf', '')
  clf_question = Question.objects.filter(id=3383).first()
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    state, district, block, clf_question, clf
  )

  # Get submissions with successful indicator results
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  logger.info(f"Base submissions count: {submissions.count()}")

  if year:
    submissions = submissions.filter(submitted_at__year=year)
    logger.info(f"After year filter: {submissions.count()} submissions")
  submissions = apply_report_date_filters(submissions, date_from, date_to)

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)
    logger.info(f"After geographic filter: {submissions.count()} submissions")

  # Question 984 - main question with 8 loan source options
  loan_sources = [
    {"name": "Non-governmental organization (NGO)", "borrowed": 985, "madeDecision": 1199, "makeDecision": 1232},
    {"name": "Formal lender (bank/financial institution)", "borrowed": 987, "madeDecision": 988, "makeDecision": 989},
    {"name": "Informal lender", "borrowed": 991, "madeDecision": 1233, "makeDecision": 1235},
    {"name": "Friends or relatives", "borrowed": 993, "madeDecision": 1236, "makeDecision": 1237},
    {"name": "Group based micro-finance or lending including SHG's (Funding, credit facilities, etc.)", "borrowed": 995, "madeDecision": 1238, "makeDecision": 1239},
    {"name": "Informal credit / savings groups (e.g., merry-go-rounds, tontines, funeral societies, etc.)", "borrowed": 997, "madeDecision": 1240, "makeDecision": 1241},
    {"name": "FPO (Credit facilities)", "borrowed": 999, "madeDecision": 1242, "makeDecision": 1243},
    {"name": "Other", "borrowed": 1001, "madeDecision": 1244, "makeDecision": 1245}
  ]

  # Chart 1: Distribution of HH based on type of loan taken
  loan_type_counts = {}
  total_submissions = submissions.count()

  # Get question 984
  question_984 = Question.objects.filter(id=984).first()
  if question_984:
    # Get all options for question 984
    for idx, source in enumerate(loan_sources):
      # Count how many submissions selected this option
      count = SubmissionAnswer.objects.filter(
        submission__in=submissions,
        question=question_984,
        selected_options__text__icontains=source["name"].split("(")[0].strip()[:20]
      ).distinct().count()

      loan_type_counts[source["name"]] = count
      logger.info(f"Loan source '{source['name']}': {count} submissions")

  # Count "None" - submissions that didn't select any option for question 984
  answered_count = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question_984
  ).exclude(selected_options__isnull=True).values('submission').distinct().count()

  loan_type_counts["None"] = total_submissions - answered_count
  logger.info(f"None selected: {loan_type_counts['None']} submissions")

  # Charts 2, 3, 4: Decision-making data for each loan source
  # Y-axis: Loan sources, X-axis: Decision options

  # Chart 2: Borrowed type data - each loan source by borrowed type
  borrowed_by_source = {}
  for source in loan_sources:
    borrowed_data = get_borrowed_type_counts(submissions, source["borrowed"])
    borrowed_by_source[source["name"]] = borrowed_data

  # Chart 3: makeDecision data - each loan source by decision maker
  make_decision_by_source = {}
  for source in loan_sources:
    make_data = get_decision_maker_counts(submissions, source["makeDecision"])
    make_decision_by_source[source["name"]] = make_data

  # Chart 4: madeDecision data - each loan source by decision maker (grouped for display)
  made_decision_by_source = {}
  for source in loan_sources:
    made_data = get_grouped_decision_maker_counts(submissions, source["madeDecision"])
    made_decision_by_source[source["name"]] = made_data

  # Aggregate borrowed types per loan source (total across Cash, Kind, Cash And Kind)
  aggregated_borrowed_by_source = {}
  for source_name, borrowed_data in borrowed_by_source.items():
    total = borrowed_data.get('Cash', 0) + borrowed_data.get('Kind', 0) + borrowed_data.get('Cash And Kind', 0)
    aggregated_borrowed_by_source[source_name] = total

  # Aggregate decision makers per loan source (total across all decision types)
  aggregated_decision_by_source = {}
  for source_name, decision_data in made_decision_by_source.items():
    total = (decision_data.get('Self', 0) +
             decision_data.get('Spouse or partner', 0) +
             decision_data.get('Other HH members', 0) +
             decision_data.get('Others', 0))
    aggregated_decision_by_source[source_name] = total

  # Calculate dashboard stats (states, districts, blocks, women surveyed)
  dashboard_stats = calculate_survey_stats(
    submissions, state_question, district_question, block_question,
    state, district, block
  )

  # Get sidebar dropdown options from ALL submissions (text_answer data)
  all_states = get_states_with_submission_data(all_submissions, state_question)
  all_districts = get_districts_with_submission_data(all_submissions, state_question, district_question, state)
  all_blocks = get_blocks_with_submission_data(all_submissions, state_question, district_question, block_question, state, district)

  return JsonResponse({
    'success': True,
    'loan_type_counts': loan_type_counts,
    'borrowed_by_source': borrowed_by_source,
    'aggregated_borrowed_by_source': aggregated_borrowed_by_source,
    'make_decision_by_source': make_decision_by_source,
    'made_decision_by_source': made_decision_by_source,
    'aggregated_decision_by_source': aggregated_decision_by_source,
    'stats': dashboard_stats,
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
  })


def get_borrowed_type_counts(submissions, question_id):
  """Helper function to count borrowed type responses (Cash, Kind, Cash And Kind)"""
  import logging
  logger = logging.getLogger(__name__)

  counts = {
    'Cash': 0,
    'Kind': 0,
    'Cash And Kind': 0
  }

  if not question_id:
    return counts

  # Get the question
  question = Question.objects.filter(id=question_id).first()
  if not question:
    logger.warning(f"Question {question_id} not found")
    return counts

  # Get all answers for this question in the filtered submissions
  answers = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question
  ).prefetch_related('selected_options')

  # Count responses based on the option text
  for answer in answers:
    for option in answer.selected_options.all():
      option_text = option.text.strip()

      if 'Cash And Kind' in option_text or 'Cash and Kind' in option_text:
        counts['Cash And Kind'] += 1
      elif 'Cash' in option_text:
        counts['Cash'] += 1
      elif 'Kind' in option_text:
        counts['Kind'] += 1

  logger.info(f"Question {question_id} borrowed type counts: {counts}")
  return counts


def get_decision_maker_counts(submissions, question_id):
  """Helper function to count decision-maker responses"""
  import logging
  logger = logging.getLogger(__name__)

  counts = {
    "Self": 0,
    "Spouse (or Partner) In HH": 0,
    "Spouse (or Partner) Outside HH": 0,
    "Other HH Member": 0,
    "Non-HH Member, Relative": 0,
    "Non-HH Member, No Relation": 0,
    "Government or Other Institution": 0,
    "Clan (Group of Relatives)": 0
  }

  if not question_id:
    return counts

  # Get the question
  question = Question.objects.filter(id=question_id).first()
  if not question:
    logger.warning(f"Question {question_id} not found")
    return counts

  # Get all answers for this question in the filtered submissions
  answers = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question
  ).prefetch_related('selected_options')

  # Count responses based on the option text
  for answer in answers:
    for option in answer.selected_options.all():
      option_text = option.text.strip()

      # Match against the known decision-maker types
      if 'Self' in option_text and 'Non' not in option_text:
        counts['Self'] += 1
      elif 'Spouse (or Partner) In HH' in option_text or 'Spouse In HH' in option_text:
        counts['Spouse (or Partner) In HH'] += 1
      elif 'Spouse (or Partner) Outside HH' in option_text or 'Spouse Outside HH' in option_text:
        counts['Spouse (or Partner) Outside HH'] += 1
      elif 'Other HH Member' in option_text:
        counts['Other HH Member'] += 1
      elif 'Non-HH Member, Relative' in option_text or 'Non HH Member, Relative' in option_text:
        counts['Non-HH Member, Relative'] += 1
      elif 'Non-HH Member, No Relation' in option_text or 'Non HH Member, No Relation' in option_text:
        counts['Non-HH Member, No Relation'] += 1
      elif 'Government or Other Institution' in option_text:
        counts['Government or Other Institution'] += 1
      elif 'Clan' in option_text:
        counts['Clan (Group of Relatives)'] += 1

  logger.info(f"Question {question_id} decision-maker counts: {counts}")
  return counts


def get_grouped_decision_maker_counts(submissions, question_id):
  """Helper function to count decision-maker responses grouped for display"""
  import logging
  logger = logging.getLogger(__name__)

  # Get detailed counts first
  detailed_counts = get_decision_maker_counts(submissions, question_id)

  # Group according to display requirements
  grouped_counts = {
    "Self": detailed_counts["Self"],
    "Spouse or partner": (
      detailed_counts["Spouse (or Partner) In HH"] +
      detailed_counts["Spouse (or Partner) Outside HH"]
    ),
    "Other HH members": detailed_counts["Other HH Member"],
    "Others": (
      detailed_counts["Non-HH Member, Relative"] +
      detailed_counts["Non-HH Member, No Relation"] +
      detailed_counts["Government or Other Institution"] +
      detailed_counts["Clan (Group of Relatives)"]
    )
  }

  logger.info(f"Question {question_id} grouped decision-maker counts: {grouped_counts}")
  return grouped_counts


def get_ownership_counts(submissions, question_id):
  """Helper function to count ownership responses"""
  import logging
  logger = logging.getLogger(__name__)

  counts = {
    'Solely': 0,
    'Jointly': 0,
    'Solely And Jointly': 0,
    'No ownership': 0,
    'Not in the household': 0
  }

  if not question_id:
    logger.warning(f"Question ID is None")
    return counts

  # Get the question
  question = Question.objects.filter(id=question_id).first()
  if not question:
    logger.warning(f"Question {question_id} not found")
    return counts

  # Get total submissions count
  total_submissions = submissions.count()

  # Get all answers for this question in the filtered submissions
  answers = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question
  ).prefetch_related('selected_options')

  # Track submissions that answered this question
  answered_submissions = set()

  # Count responses based on the option text
  for answer in answers:
    answered_submissions.add(answer.submission_id)
    for option in answer.selected_options.all():
      option_text = option.text.strip()

      if 'Solely And Jointly' in option_text:
        counts['Solely And Jointly'] += 1
      elif 'Solely' in option_text:
        counts['Solely'] += 1
      elif 'Jointly' in option_text:
        counts['Jointly'] += 1
      elif 'None' in option_text or 'No ownership' in option_text:
        counts['No ownership'] += 1

  # Count "Not in the household" - submissions that didn't answer this question
  counts['Not in the household'] = total_submissions - len(answered_submissions)

  logger.info(f"Question {question_id} ownership counts: {counts}")
  return counts


@url_cache_decorator(timeout=600)
def api_g3a_productive_capital_data(request):
  """API endpoint to get G3A Access to Productive Capital data"""
  import logging
  logger = logging.getLogger(__name__)

  # Get filter parameters
  programme = request.GET.get('programme', '')
  state = request.GET.get('state', '')
  district = request.GET.get('district', '')
  block = request.GET.get('block', '')
  year = request.GET.get('year', '')
  date_from = request.GET.get('date_from', '')
  date_to = request.GET.get('date_to', '')

  logger.info(f"G3A API called with filters - state: {state}, district: {district}, block: {block}, year: {year}")

  from .selectors import apply_submission_visibility

  # Get ALL submissions for filtering by geography (since state data is in text_answer)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected
  if year:
    all_submissions = all_submissions.filter(submitted_at__year=year)
  all_submissions = apply_report_date_filters(all_submissions, date_from, date_to)

  # Get questions for filtering
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  # Filter by geography from ALL submissions (since state data is in text_answer)
  clf = request.GET.get('clf', '')
  clf_question = Question.objects.filter(id=3383).first()
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    state, district, block, clf_question, clf
  )

  # Get submissions with successful indicator results
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  logger.info(f"Base submissions count: {submissions.count()}")

  if year:
    submissions = submissions.filter(submitted_at__year=year)
    logger.info(f"After year filter: {submissions.count()} submissions")
  submissions = apply_report_date_filters(submissions, date_from, date_to)

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)
    logger.info(f"After geographic filter: {submissions.count()} submissions")

  # Question 959 - main question with 7 income generating item options
  items = [
    {"name": "Agricultural Land (including fishpond) (Pieces/plots)", "ownership": 960, "mostlyUses": 961},
    {"name": "Mechanized farm equipment (tractor-plough, power tiller, treadle pump)", "ownership": 963, "mostlyUses": 964},
    {"name": "Non-farm business equipment (solar panels used for recharging, sewing machine, brewing equipment, fryers)", "ownership": 966, "mostlyUses": 967},
    {"name": "House or building", "ownership": 969, "mostlyUses": 970},
    {"name": "Cell phone", "ownership": 972, "mostlyUses": 973},
    {"name": "Other land not used for agricultural purposes (pieces/plots, residential or commercial land)", "ownership": 975, "mostlyUses": 976},
    {"name": "Means of transportation (bicycle, motorcycle, car)", "ownership": 978, "mostlyUses": 979}
  ]

  # Chart 1: Ownership data - each item by ownership type
  ownership_by_item = {}
  for item in items:
    ownership_data = get_ownership_counts(submissions, item["ownership"])
    ownership_by_item[item["name"]] = ownership_data

  # Chart 2: Mostly uses data - each item by who mostly uses (grouped for display)
  mostly_uses_by_item = {}
  for item in items:
    uses_data = get_grouped_decision_maker_counts(submissions, item["mostlyUses"])
    mostly_uses_by_item[item["name"]] = uses_data

  # Aggregate ownership per item (total across all ownership types)
  aggregated_ownership_by_item = {}
  for item_name, ownership_data in ownership_by_item.items():
    total = (ownership_data.get('Solely', 0) +
             ownership_data.get('Jointly', 0) +
             ownership_data.get('Solely And Jointly', 0) +
             ownership_data.get('No ownership', 0) +
             ownership_data.get('Not in the household', 0))
    aggregated_ownership_by_item[item_name] = total

  # Aggregate mostly uses per item (total across all decision types)
  aggregated_uses_by_item = {}
  for item_name, uses_data in mostly_uses_by_item.items():
    total = (uses_data.get('Self', 0) +
             uses_data.get('Spouse or partner', 0) +
             uses_data.get('Other HH members', 0) +
             uses_data.get('Others', 0))
    aggregated_uses_by_item[item_name] = total

  # Calculate dashboard stats (states, districts, blocks, women surveyed)
  dashboard_stats = calculate_survey_stats(
    submissions, state_question, district_question, block_question,
    state, district, block
  )

  # Get sidebar dropdown options from ALL submissions (text_answer data)
  all_states = get_states_with_submission_data(all_submissions, state_question)
  all_districts = get_districts_with_submission_data(all_submissions, state_question, district_question, state)
  all_blocks = get_blocks_with_submission_data(all_submissions, state_question, district_question, block_question, state, district)

  return JsonResponse({
    'success': True,
    'ownership_by_item': ownership_by_item,
    'aggregated_ownership_by_item': aggregated_ownership_by_item,
    'mostly_uses_by_item': mostly_uses_by_item,
    'aggregated_uses_by_item': aggregated_uses_by_item,
    'stats': dashboard_stats,
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
  })


def get_paid_unpaid_counts(submissions, question_id):
  """Helper function to count paid/unpaid responses"""
  import logging
  logger = logging.getLogger(__name__)

  counts = {
    'Paid': 0,
    'Unpaid': 0
  }

  if not question_id:
    logger.warning(f"Question ID is None")
    return counts

  # Get the question
  question = Question.objects.filter(id=question_id).first()
  if not question:
    logger.warning(f"Question {question_id} not found")
    return counts

  # Get all answers for this question in the filtered submissions
  answers = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question
  ).prefetch_related('selected_options')

  # Count responses based on the option text
  for answer in answers:
    for option in answer.selected_options.all():
      option_text = option.text.strip()

      if 'Paid' in option_text:
        counts['Paid'] += 1
      elif 'Unpaid' in option_text:
        counts['Unpaid'] += 1

  logger.info(f"Question {question_id} paid/unpaid counts: {counts}")
  return counts


def get_fpo_impact_counts(submissions, question_id):
  """Helper function to count FPO impact responses (grouped)"""
  import logging
  logger = logging.getLogger(__name__)

  counts = {
    'Decreased': 0,
    'No change': 0,
    'Increased': 0
  }

  if not question_id:
    logger.warning(f"Question ID is None")
    return counts
  # Get the question
  question = Question.objects.filter(id=question_id).first()
  if not question:
    logger.warning(f"Question {question_id} not found")
    return counts

  # Get all answers for this question in the filtered submissions
  answers = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question
  ).prefetch_related('selected_options')
  print(question_id)
  print(answers)
  # Count responses based on the option text and group them
  for answer in answers:
    for option in answer.selected_options.all():
      option_text = option.text.strip()

      if 'Significantly decreased' in option_text or 'Slightly decreased' in option_text:
        counts['Decreased'] += 1
      elif 'No change' in option_text:
        counts['No change'] += 1
      elif 'Significantly increased' in option_text or 'Slightly increased' in option_text:
        counts['Increased'] += 1

  logger.info(f"Question {question_id} FPO impact counts: {counts}")
  return counts


@url_cache_decorator(timeout=600)
def api_g4_time_allocation_data(request):
  """API endpoint to get G4 Time Allocation and Agency data"""
  import logging
  logger = logging.getLogger(__name__)

  # Get filter parameters
  programme = request.GET.get('programme', '')
  state = request.GET.get('state', '')
  district = request.GET.get('district', '')
  block = request.GET.get('block', '')
  year = request.GET.get('year', '')
  date_from = request.GET.get('date_from', '')
  date_to = request.GET.get('date_to', '')

  logger.info(f"G4 API called with filters - state: {state}, district: {district}, block: {block}, year: {year}")

  from .selectors import apply_submission_visibility

  # Get ALL submissions for filtering by geography (since state data is in text_answer)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected
  if year:
    all_submissions = all_submissions.filter(submitted_at__year=year)
  all_submissions = apply_report_date_filters(all_submissions, date_from, date_to)

  # Get questions for filtering
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  # Filter by geography from ALL submissions (since state data is in text_answer)
  clf = request.GET.get('clf', '')
  clf_question = Question.objects.filter(id=3383).first()
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    state, district, block, clf_question, clf
  )

  # Get submissions with successful indicator results
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  logger.info(f"Base submissions count: {submissions.count()}")

  if year:
    submissions = submissions.filter(submitted_at__year=year)
    logger.info(f"After year filter: {submissions.count()} submissions")
  submissions = apply_report_date_filters(submissions, date_from, date_to)

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)
    logger.info(f"After geographic filter: {submissions.count()} submissions")

  # Question 1002 - main question with 18 activity options
  activities = [
    {"name": "Sleeping and resting", "paidQuestion": None, "fpoQuestion": 1038},
    {"name": "Farm-related activities", "paidQuestion": 1257, "fpoQuestion": 1258},
    {"name": "Domestic work (Including fetching water and fuel)", "paidQuestion": 1252, "fpoQuestion": 1251},
    {"name": "Cooking", "paidQuestion": 1248, "fpoQuestion": 1250},
    {"name": "Work as employed/own business/self-employment", "paidQuestion": 1260, "fpoQuestion": 1261},
    {"name": "School/College/etc (including homework)", "paidQuestion": 1268, "fpoQuestion": 1269},
    {"name": "Commuting for any purpose (to/from school or work)", "paidQuestion": 1277, "fpoQuestion": 1279},
    {"name": "Weaving/sewing/textile care", "paidQuestion": 1254, "fpoQuestion": 1255},
    {"name": "Training or meetings related to agriculture or other livelihoods(includes FPO training)", "paidQuestion": 1271, "fpoQuestion": 1273},
    {"name": "Social activities and hobbies", "paidQuestion": 1280, "fpoQuestion": 1282},
    {"name": "Religious activities", "paidQuestion": 1283, "fpoQuestion": 1285},
    {"name": "Care work for adults", "paidQuestion": 1263, "fpoQuestion": 1264},
    {"name": "Care work for children", "paidQuestion": 1266, "fpoQuestion": 1267},
    {"name": "Personal care", "paidQuestion": None, "fpoQuestion": 1041},
    {"name": "Shopping/ receiving services (including healthcare)", "paidQuestion": 1274, "fpoQuestion": 1276},
    {"name": "Eating and drinking", "paidQuestion": None, "fpoQuestion": 1247},
    {"name": "Entertainment/Social Media", "paidQuestion": 1286, "fpoQuestion": 1288},
    {"name": "Other", "paidQuestion": 1289, "fpoQuestion": 1291}
  ]

  # Chart 1: Paid/Unpaid data - each activity by paid/unpaid
  # Exclude: Sleeping and resting, Personal care, Eating and drinking
  excluded_activities = ["Sleeping and resting", "Personal care", "Eating and drinking"]
  paid_unpaid_by_activity = {}
  for activity in activities:
    if activity["name"] in excluded_activities:
      continue  # Skip excluded activities for Chart 1
    if activity["paidQuestion"]:
      paid_unpaid_data = get_paid_unpaid_counts(submissions, activity["paidQuestion"])
      paid_unpaid_by_activity[activity["name"]] = paid_unpaid_data
    else:
      # Activities without paid questions are considered unpaid only
      paid_unpaid_by_activity[activity["name"]] = {'Paid': 0, 'Unpaid': 0}

  # Chart 2: FPO impact data - each activity by impact (Decreased, No change, Increased)
  fpo_impact_by_activity = {}
  for activity in activities:
    fpo_data = get_fpo_impact_counts(submissions, activity["fpoQuestion"])
    fpo_impact_by_activity[activity["name"]] = fpo_data

  # Aggregate paid/unpaid per activity (keep same structure, just copy)
  aggregated_paid_unpaid_by_activity = {}
  for activity_name, paid_unpaid_data in paid_unpaid_by_activity.items():
    aggregated_paid_unpaid_by_activity[activity_name] = paid_unpaid_data.copy()

  # Aggregate FPO impact per activity (keep same structure, just copy)
  aggregated_fpo_impact_by_activity = {}
  for activity_name, fpo_data in fpo_impact_by_activity.items():
    aggregated_fpo_impact_by_activity[activity_name] = fpo_data.copy()

  # Calculate dashboard stats (states, districts, blocks, women surveyed)
  dashboard_stats = calculate_survey_stats(
    submissions, state_question, district_question, block_question,
    state, district, block
  )

  # Get sidebar dropdown options from ALL submissions (text_answer data)
  all_states = get_states_with_submission_data(all_submissions, state_question)
  all_districts = get_districts_with_submission_data(all_submissions, state_question, district_question, state)
  all_blocks = get_blocks_with_submission_data(all_submissions, state_question, district_question, block_question, state, district)

  return JsonResponse({
    'success': True,
    'paid_unpaid_by_activity': paid_unpaid_by_activity,
    'aggregated_paid_unpaid_by_activity': aggregated_paid_unpaid_by_activity,
    'fpo_impact_by_activity': fpo_impact_by_activity,
    'aggregated_fpo_impact_by_activity': aggregated_fpo_impact_by_activity,
    'stats': dashboard_stats,
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
  })


@url_cache_decorator(timeout=600)
def api_g6_physical_mobility_data(request):
  """API endpoint to get G6 Physical Mobility data"""
  import logging
  logger = logging.getLogger(__name__)

  # Get filter parameters
  programme = request.GET.get('programme', '')
  state = request.GET.get('state', '')
  district = request.GET.get('district', '')
  block = request.GET.get('block', '')
  year = request.GET.get('year', '')
  date_from = request.GET.get('date_from', '')
  date_to = request.GET.get('date_to', '')

  logger.info(f"G6 API called with filters - state: {state}, district: {district}, block: {block}, year: {year}")

  from .selectors import apply_submission_visibility

  # Get ALL submissions for filtering by geography (since state data is in text_answer)
  all_submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
    ),
    request.user,
  ).distinct()

  # Apply year filter if selected
  if year:
    all_submissions = all_submissions.filter(submitted_at__year=year)
  all_submissions = apply_report_date_filters(all_submissions, date_from, date_to)

  # Get questions for filtering
  state_question = Question.objects.filter(id=709).first()
  district_question = Question.objects.filter(id=710).first()
  block_question = Question.objects.filter(id=711).first()

  # Filter by geography from ALL submissions (since state data is in text_answer)
  clf = request.GET.get('clf', '')
  clf_question = Question.objects.filter(id=3383).first()
  filtered_submission_ids = filter_submissions_by_geography(
    all_submissions, state_question, district_question, block_question,
    state, district, block, clf_question, clf
  )

  # Get submissions with successful indicator results
  submissions = apply_submission_visibility(
    Submission.objects.filter(
      survey_id=6,
      status=SUBMITTED_SURVEY_STATUS,
      is_archived=False,
      indicator_results__success=True,
    ),
    request.user,
  ).distinct()

  logger.info(f"Base submissions count: {submissions.count()}")

  if year:
    submissions = submissions.filter(submitted_at__year=year)
    logger.info(f"After year filter: {submissions.count()} submissions")
  submissions = apply_report_date_filters(submissions, date_from, date_to)

  # If geographic filters were applied, intersect with the filtered submission IDs
  if filtered_submission_ids is not None:
    submissions = submissions.filter(id__in=filtered_submission_ids)
    logger.info(f"After geographic filter: {submissions.count()} submissions")

  # Question 1126 - main question with 5 place options
  places = [
    {"name": "Market / haat / bazaar", "oftenGo": 1127, "whoDecide": 1128},
    {"name": "Visiting family or relatives", "oftenGo": 1133, "whoDecide": 1134},
    {"name": "Attending Public village gathering / community meeting / training for NGO or programs/SHG's/VOs/ CLFs", "oftenGo": 1145, "whoDecide": 1146},
    {"name": "Attending village level FPO meetings and trainings", "oftenGo": 1151, "whoDecide": 1152},
    {"name": "Attending cluster level FPO meetings and trainings", "oftenGo": 1157, "whoDecide": 1158}
  ]

  # Chart 1: Distribution of places visited by women (count of selections)
  place_counts = {}
  total_submissions = submissions.count()

  # Get question 1126
  question_1126 = Question.objects.filter(id=1126).first()
  if question_1126:
    # Get all options for question 1126
    for place in places:
      # Count how many submissions selected this option
      count = SubmissionAnswer.objects.filter(
        submission__in=submissions,
        question=question_1126,
        selected_options__text__icontains=place["name"].split("/")[0].strip()[:20]
      ).distinct().count()

      place_counts[place["name"]] = count
      logger.info(f"Place '{place['name']}': {count} submissions")

  # Chart 2: Frequency by place (oftenGo)
  # Y-axis: Places, X-axis: Frequency options
  frequency_by_place = {}
  for place in places:
    frequency_data = get_frequency_counts(submissions, place["oftenGo"])
    frequency_by_place[place["name"]] = frequency_data

  # Chart 3: Decision maker by place (whoDecide) - grouped for display
  decision_by_place = {}
  for place in places:
    decision_data = get_grouped_decision_maker_counts(submissions, place["whoDecide"])
    decision_by_place[place["name"]] = decision_data

  # Aggregate decision makers per place (total across all decision types)
  aggregated_decision_by_place = {}
  for place_name, decision_data in decision_by_place.items():
    total = (decision_data.get('Self', 0) +
             decision_data.get('Spouse or partner', 0) +
             decision_data.get('Other HH members', 0) +
             decision_data.get('Others', 0))
    aggregated_decision_by_place[place_name] = total

  # Calculate dashboard stats (states, districts, blocks, women surveyed)
  dashboard_stats = calculate_survey_stats(
    submissions, state_question, district_question, block_question,
    state, district, block
  )

  # Get sidebar dropdown options from ALL submissions (text_answer data)
  all_states = get_states_with_submission_data(all_submissions, state_question)
  all_districts = get_districts_with_submission_data(all_submissions, state_question, district_question, state)
  all_blocks = get_blocks_with_submission_data(all_submissions, state_question, district_question, block_question, state, district)

  return JsonResponse({
    'success': True,
    'place_counts': place_counts,
    'frequency_by_place': frequency_by_place,
    'decision_by_place': decision_by_place,
    'aggregated_decision_by_place': aggregated_decision_by_place,
    'stats': dashboard_stats,
    'all_states': all_states,
    'all_districts': all_districts,
    'all_blocks': all_blocks,
  })


def get_frequency_counts(submissions, question_id):
  """Helper function to count frequency responses (Everyday, Every Week, etc.)"""
  import logging
  logger = logging.getLogger(__name__)

  counts = {
    'Everyday': 0,
    'Every Week At Least Once': 0,
    'Every 2 Weeks At Least Once': 0,
    'Every Month At Least Once': 0,
    'Sometimes': 0
  }

  if not question_id:
    return counts

  # Get the question
  question = Question.objects.filter(id=question_id).first()
  if not question:
    logger.warning(f"Question {question_id} not found")
    return counts

  logger.info(f"Processing frequency question {question_id}")

  # Get all answers for this question
  answers = SubmissionAnswer.objects.filter(
    submission__in=submissions,
    question=question
  ).prefetch_related('selected_options')

  logger.info(f"Found {answers.count()} answers for question {question_id}")

  # Count responses based on option text
  for answer in answers:
    for option in answer.selected_options.all():
      option_text = option.text.strip()

      # Match against frequency types
      if 'Everyday' in option_text:
        counts['Everyday'] += 1
      elif 'Every Week At Least Once' in option_text or 'Every week at least once' in option_text:
        counts['Every Week At Least Once'] += 1
      elif 'Every 2 Weeks At Least Once' in option_text or 'Every 2 weeks at least once' in option_text:
        counts['Every 2 Weeks At Least Once'] += 1
      elif 'Every Month At Least Once' in option_text or 'Every month at least once' in option_text:
        counts['Every Month At Least Once'] += 1
      elif 'Sometimes' in option_text:
        counts['Sometimes'] += 1

  logger.info(f"Question {question_id} frequency counts: {counts}")
  return counts


# ============================================================================
# User Management Views
# ============================================================================

@login_required
def survey_user_management(request):
  """View for managing users in the Survey system"""
  from .selectors import filter_users_by_partner_scope

  search_query = request.GET.get("search", "")
  status_filter = request.GET.get("status", "")

  # Get all users, narrowed to the actor's partner when the account is scoped
  users = filter_users_by_partner_scope(
    User.objects.all(), request.user
  ).prefetch_related("survey_invitations__survey", "groups")

  # Apply search filter
  if search_query:
    users = users.filter(
      Q(username__icontains=search_query)
      | Q(first_name__icontains=search_query)
      | Q(last_name__icontains=search_query)
      | Q(email__icontains=search_query)
    )

  # Apply status filter
  if status_filter:
    if status_filter == "active":
      users = users.filter(is_active=True)
    elif status_filter == "inactive":
      users = users.filter(is_active=False)

  # Order by date joined (newest first)
  users = users.order_by('-date_joined')

  # Add role information to each user
  users_list = []
  for user in users:
    user.is_manager = user.groups.filter(name='Manager').exists()
    user.is_supervisor = user.groups.filter(name='Supervisor').exists()
    user.is_dashboard_viewer = user.groups.filter(name='DashboardViewer').exists()
    users_list.append(user)

  # Pagination
  paginator = Paginator(users_list, 20)
  page_number = request.GET.get("page")
  page_obj = paginator.get_page(page_number)

  context = {
    "page_obj": page_obj,
    "search_query": search_query,
    "status_filter": status_filter,
    "use_survey_sidebar": True,
  }
  return render(request, "survey/user_management.html", context)


@login_required
def survey_user_detail(request, user_id):
  """Detailed view for a single user and their survey access"""
  from datetime import date, datetime
  from .selectors import enforce_partner_user_scope

  user_obj = get_object_or_404(User.objects.prefetch_related("survey_invitations__survey"), id=user_id)
  enforce_partner_user_scope(request.user, user_obj)

  # Check if viewed user is manager, admin, or supervisor
  viewed_user_is_admin = user_obj.is_staff or user_obj.is_superuser
  viewed_user_is_manager_role = user_obj.groups.filter(name='Manager').exists()
  viewed_user_is_supervisor = user_obj.groups.filter(name='Supervisor').exists()
  viewed_user_is_dashboard_viewer = user_obj.groups.filter(name='DashboardViewer').exists()
  is_admin_or_manager = viewed_user_is_admin or viewed_user_is_manager_role

  # Get invitations
  # For all users, show only their specific invitations
  invitations = list(user_obj.survey_invitations.select_related("survey", "invited_by").all())
  active_invitations = [inv for inv in invitations if inv.is_active]

  # Prepare invitations as a list for json_script template tag
  invitations_data = [
    {
      "id": inv.id,
      "survey": {
        "id": inv.survey.id,
        "title": inv.survey.title,
      },
      "invited_by": inv.invited_by.get_full_name() or inv.invited_by.username if inv.invited_by else "Unknown",
      "invited_at": inv.invited_at.isoformat() if inv.invited_at else None,
      "is_active": inv.is_active,
    }
    for inv in invitations
  ]

  # Get filter parameters
  search_query = request.GET.get('search', '').strip()
  start_date = request.GET.get('start_date', '').strip()
  end_date = request.GET.get('end_date', '').strip()

  # Get user's submissions
  from .selectors import apply_submission_visibility
  submissions = apply_submission_visibility(
    Submission.objects.filter(user=user_obj).select_related('survey'),
    request.user,
  )

  # Apply search filter (search by submission title or survey title)
  if search_query:
    # Get answers with text matching search query
    matching_answer_ids = SubmissionAnswer.objects.filter(
      submission__user=user_obj,
      text_answer__icontains=search_query
    ).values_list('submission_id', flat=True)

    submissions = submissions.filter(
      Q(survey__title__icontains=search_query) |
      Q(id__in=matching_answer_ids)
    )

  # Apply date filters
  if start_date:
    try:
      start_datetime = datetime.strptime(start_date, '%Y-%m-%d')
      submissions = submissions.filter(submitted_at__gte=start_datetime)
    except ValueError:
      pass

  if end_date:
    try:
      end_datetime = datetime.strptime(end_date, '%Y-%m-%d')
      # Add 1 day and subtract 1 second to include the entire end_date
      from datetime import timedelta
      end_datetime = end_datetime + timedelta(days=1) - timedelta(seconds=1)
      submissions = submissions.filter(submitted_at__lte=end_datetime)
    except ValueError:
      pass

  submissions = submissions.order_by('-submitted_at').annotate(
    answer_count=Count('answers', distinct=True)
  )

  # Calculate stats (on filtered results). Counts are reported per status so
  # the cards read plainly; note that "no answers" overlaps with "draft" — an
  # empty draft is both, which is why it is shown as its own warning figure.
  # total_submissions still counts every row so the pagination footer matches
  # the list.
  total_submissions = submissions.count()
  completed_submissions = submissions.filter(status='completed').count()
  draft_submissions = submissions.filter(status='draft').count()
  empty_submissions = submissions.filter(answer_count=0).count()
  answered_submissions = total_submissions - empty_submissions
  today = date.today()
  today_submissions = submissions.filter(
    submitted_at__date=today, answer_count__gt=0
  ).count()

  # Paginate submissions
  paginator = Paginator(submissions, 20)
  page_number = request.GET.get('page')
  page_obj = paginator.get_page(page_number)

  context = {
    "page_title": f"User - {user_obj.get_full_name() or user_obj.username}",
    "user_obj": user_obj,
    "viewed_user_is_manager": viewed_user_is_manager_role,
    "viewed_user_is_supervisor": viewed_user_is_supervisor,
    "viewed_user_is_dashboard_viewer": viewed_user_is_dashboard_viewer,
    "is_admin_or_manager": is_admin_or_manager,
    "invitations": invitations,
    "active_invitations": active_invitations,
    "invitations_data": invitations_data,
    "submissions": page_obj,
    "total_submissions": total_submissions,
    "answered_submissions": answered_submissions,
    "completed_submissions": completed_submissions,
    "draft_submissions": draft_submissions,
    "today_submissions": today_submissions,
    "empty_submissions": empty_submissions,
    "search_query": search_query,
    "start_date": start_date,
    "end_date": end_date,
    "use_survey_sidebar": True,
    "can_change_user_password": _can_change_managed_user_password(request.user, user_obj),
  }
  return render(request, "survey/user_detail.html", context)


@login_required
@require_GET
def search_surveys_for_user(request, user_id):
  """Search surveys to assign to a user or get all available surveys"""
  from .selectors import enforce_partner_user_scope

  user_obj = get_object_or_404(User, id=user_id)
  enforce_partner_user_scope(request.user, user_obj)
  search_query = request.GET.get("q", "").strip()

  # Get survey IDs where the user already has active invitations
  already_assigned_survey_ids = SurveyInvitation.objects.filter(
    user=user_obj, is_active=True
  ).values_list("survey_id", flat=True)

  # Start with base queryset - all active surveys not already assigned
  surveys = Survey.objects.filter(is_active=True).exclude(id__in=already_assigned_survey_ids)

  # Apply search filter if query provided
  if search_query:
    surveys = surveys.filter(
      Q(title__icontains=search_query) | Q(description__icontains=search_query)
    )

  # Limit results
  surveys = surveys.order_by('-created_at')[:50]

  survey_data = [
    {
      "id": survey.id,
      "title": survey.title,
      "description": survey.description[:100] + "..." if survey.description and len(survey.description) > 100 else survey.description or "",
      "visibility": survey.get_visibility_display(),
      "created_by": survey.created_by.get_full_name() or survey.created_by.username,
    }
    for survey in surveys
  ]

  return JsonResponse({"status": "success", "surveys": survey_data, "total": len(survey_data)})


@login_required
@require_POST
def assign_user_to_survey(request, user_id):
  """Assign a user to a survey from the user management page"""
  from .selectors import enforce_partner_user_scope

  user_obj = get_object_or_404(User, id=user_id)
  enforce_partner_user_scope(request.user, user_obj)

  try:
    data = json.loads(request.body)
    survey_id = data.get("survey_id")

    if not survey_id:
      return JsonResponse({"status": "error", "message": "Survey ID is required"})

    survey = get_object_or_404(Survey, id=survey_id)

    # Check if user is already invited
    if survey.invitations.filter(user=user_obj, is_active=True).exists():
      return JsonResponse({"status": "error", "message": "User already has access to this survey"})

    # Create invitation
    invitation = survey.invite_user(user_obj, request.user)
    from .selectors import clear_partner_scope_cache
    clear_partner_scope_cache(user_obj)

    return JsonResponse({
      "status": "success",
      "message": f"Successfully assigned {user_obj.username} to {survey.title}",
      "invitation": {
        "id": invitation.id,
        "survey": {
          "id": survey.id,
          "title": survey.title,
        },
        "invited_at": invitation.invited_at.isoformat(),
        "invited_by": request.user.get_full_name() or request.user.username,
      },
    })

  except Exception as e:
    return JsonResponse({"status": "error", "message": str(e)})


@login_required
def remove_user_survey_access(request, user_id, invitation_id):
  """Remove survey access from a user from the user management page"""
  from .selectors import enforce_partner_user_scope

  user_obj = get_object_or_404(User, id=user_id)
  enforce_partner_user_scope(request.user, user_obj)
  invitation = get_object_or_404(SurveyInvitation, id=invitation_id, user=user_obj)

  invitation.is_active = False
  invitation.save(update_fields=["is_active"])
  from .selectors import clear_partner_scope_cache
  clear_partner_scope_cache(user_obj)

  return JsonResponse({
    "status": "success",
    "message": f"Removed survey access for {user_obj.username}"
  })


@login_required
def survey_create_user(request):
  """View for creating new users"""
  # Check if current user is admin or manager
  is_current_user_admin = request.user.is_superuser
  is_current_user_manager = request.user.groups.filter(name='Manager').exists()

  if request.method == "POST":
    username = request.POST.get("username")
    email = request.POST.get("email", "").strip()
    first_name = request.POST.get("first_name")
    last_name = request.POST.get("last_name")
    password = request.POST.get("password")
    confirm_password = request.POST.get("confirm_password")
    user_role = request.POST.get("user_role", "manager")

    # Role-based validation: Managers cannot create admin users
    if is_current_user_manager and not is_current_user_admin and user_role == "admin":
      messages.error(request, "You do not have permission to create admin users.")
      return render(request, "survey/create_user.html", {
        "form_data": request.POST,
        "use_survey_sidebar": True,
        "is_admin": is_current_user_admin
      }, status=422)

    # Basic validation (email is now optional)
    if not all([username, password, confirm_password]):
      messages.error(request, "Please fill in all required fields.")
      return render(request, "survey/create_user.html", {
        "form_data": request.POST,
        "use_survey_sidebar": True,
        "is_admin": is_current_user_admin
      }, status=422)

    if password != confirm_password:
      messages.error(request, "Passwords do not match.")
      return render(request, "survey/create_user.html", {
        "form_data": request.POST,
        "use_survey_sidebar": True,
        "is_admin": is_current_user_admin
      }, status=422)

    # Validate username is lowercase
    if username != username.lower():
      messages.error(request, "Username must be in lowercase letters only.")
      return render(request, "survey/create_user.html", {
        "form_data": request.POST,
        "use_survey_sidebar": True,
        "is_admin": is_current_user_admin
      }, status=422)

    # Check if username already exists
    if User.objects.filter(username=username).exists():
      messages.error(request, "Username already exists.")
      return render(request, "survey/create_user.html", {
        "form_data": request.POST,
        "use_survey_sidebar": True,
        "is_admin": is_current_user_admin
      }, status=422)

    # Check if email already exists (only if email is provided)
    if email and User.objects.filter(email=email).exists():
      messages.error(request, "Email already exists.")
      return render(request, "survey/create_user.html", {
        "form_data": request.POST,
        "use_survey_sidebar": True,
        "is_admin": is_current_user_admin
      }, status=422)

    try:
      from django.contrib.auth.models import Group

      # Create user
      user = User.objects.create_user(
        username=username,
        email=email or "",
        password=password,
        first_name=first_name or "",
        last_name=last_name or "",
      )

      # Set privileges based on user role
      if user_role == "admin":
        user.is_staff = True
        user.is_superuser = True
        role_label = "Admin"
      elif user_role == "manager":
        user.is_staff = True
        user.is_superuser = False
        # Create or get the Manager group
        manager_group, created = Group.objects.get_or_create(name='Manager')
        user.groups.add(manager_group)
        role_label = "Manager"
      elif user_role == "supervisor":
        user.is_staff = False
        user.is_superuser = False
        # Create or get the Supervisor group
        supervisor_group, created = Group.objects.get_or_create(name='Supervisor')
        user.groups.add(supervisor_group)
        role_label = "Supervisor"
      elif user_role == "dashboard_viewer":
        user.is_staff = False
        user.is_superuser = False
        dashboard_viewer_group, created = Group.objects.get_or_create(name='DashboardViewer')
        user.groups.add(dashboard_viewer_group)
        role_label = "Readonly Admin User"
      else:
        user.is_staff = False
        user.is_superuser = False
        role_label = "Normal"
      user.save()

      # A partner admin/manager's new accounts join the same partner so they
      # remain inside the creator's visibility scope.
      from .selectors import assign_partner_group_if_needed
      assign_partner_group_if_needed(request.user, user)

      # Handle survey assignments explicitly instead of auto-assigning
      from survey.models import Survey, SurveyInvitation
      assigned_surveys_ids = request.POST.getlist("assigned_surveys")
      surveys_assigned = 0

      if assigned_surveys_ids:
        surveys_to_assign = Survey.objects.filter(id__in=assigned_surveys_ids)
        for survey in surveys_to_assign:
          if survey.created_by != user:
            invitation, created = SurveyInvitation.objects.get_or_create(
              survey=survey,
              user=user,
              defaults={'invited_by': request.user, 'is_active': True}
            )
            if created or not invitation.is_active:
              if not invitation.is_active:
                invitation.is_active = True
                invitation.save(update_fields=['is_active'])
              surveys_assigned += 1

      from .selectors import clear_partner_scope_cache
      clear_partner_scope_cache(user)

      if surveys_assigned > 0:
        messages.success(request, f"{role_label} user '{username}' created successfully with access to {surveys_assigned} surveys.")
      else:
        messages.success(request, f"{role_label} user '{username}' created successfully.")

      return redirect("survey:user_management")
    except Exception as e:
      messages.error(request, f"Error creating user: {str(e)}")
      return render(request, "survey/create_user.html", {
        "form_data": request.POST,
        "use_survey_sidebar": True,
        "is_admin": is_current_user_admin
      }, status=422)

  from survey.models import Survey
  context = {
    "page_title": "Create User",
    "use_survey_sidebar": True,
    "is_admin": is_current_user_admin,
    "available_surveys": Survey.objects.filter(is_active=True).order_by('-created_at'),
  }
  return render(request, "survey/create_user.html", context)


@login_required
@require_POST
def survey_delete_user(request, user_id):
  """Delete a user"""
  from .selectors import enforce_partner_user_scope

  user_obj = get_object_or_404(User, id=user_id)
  enforce_partner_user_scope(request.user, user_obj)

  # Prevent deleting yourself
  if user_obj == request.user:
    messages.error(request, "You cannot delete your own account.")
    return redirect("survey:user_management")

  username = user_obj.username
  user_obj.delete()
  messages.success(request, f"User '{username}' has been deleted.")
  return redirect("survey:user_management")


@login_required
@require_POST
def change_user_password(request, user_id):
  """Allow a superuser to set a user's password from Survey User Management."""
  from .selectors import enforce_partner_user_scope

  user_obj = get_object_or_404(User, id=user_id)
  enforce_partner_user_scope(request.user, user_obj)
  if not _can_change_managed_user_password(request.user, user_obj):
    return JsonResponse(
      {
        "status": "error",
        "message": "Only admins can change admin passwords. Managers can change non-admin user passwords.",
      },
      status=403,
    )

  form = SetPasswordForm(user_obj, request.POST)

  if form.is_valid():
    changed_user = form.save()
    if changed_user.id == request.user.id:
      update_session_auth_hash(request, changed_user)

    return JsonResponse({
      "status": "success",
      "message": f"Password changed successfully for {user_obj.username}.",
    })

  errors = {}
  non_field_errors = []
  for field, field_errors in form.errors.items():
    field_messages = [str(error) for error in field_errors]
    if field == "__all__":
      non_field_errors.extend(field_messages)
    else:
      errors[field] = field_messages

  return JsonResponse(
    {
      "status": "error",
      "message": non_field_errors[0] if non_field_errors else "Please correct the password errors below.",
      "errors": errors,
      "non_field_errors": non_field_errors,
    },
    status=400,
  )


@login_required
@require_POST
def change_user_role(request, user_id):
  """Change a user's role"""
  from django.contrib.auth.models import Group
  from .selectors import enforce_partner_user_scope

  user_obj = get_object_or_404(User, id=user_id)
  enforce_partner_user_scope(request.user, user_obj)

  # Check if current user is admin or manager
  is_current_user_admin = request.user.is_superuser
  is_current_user_manager = request.user.groups.filter(name='Manager').exists()

  try:
    data = json.loads(request.body)
    new_role = data.get("role")

    if not new_role or new_role not in ["admin", "manager", "supervisor", "dashboard_viewer", "normal"]:
      return JsonResponse({"status": "error", "message": "Invalid role specified"})

    # Role-based validation: Managers cannot change users to admin role
    if is_current_user_manager and not is_current_user_admin and new_role == "admin":
      return JsonResponse({"status": "error", "message": "You do not have permission to assign admin role"})

    # Remove from role groups that are managed by this endpoint if they exist
    manager_group = Group.objects.filter(name='Manager').first()
    if manager_group:
      user_obj.groups.remove(manager_group)
    supervisor_group = Group.objects.filter(name='Supervisor').first()
    if supervisor_group:
      user_obj.groups.remove(supervisor_group)
    dashboard_viewer_group = Group.objects.filter(name='DashboardViewer').first()
    if dashboard_viewer_group:
      user_obj.groups.remove(dashboard_viewer_group)

    # Set new role
    if new_role == "admin":
      user_obj.is_staff = True
      user_obj.is_superuser = True
      role_label = "Admin"
    elif new_role == "manager":
      user_obj.is_staff = True
      user_obj.is_superuser = False
      # Add to Manager group
      manager_group, created = Group.objects.get_or_create(name='Manager')
      user_obj.groups.add(manager_group)
      role_label = "Manager"
    elif new_role == "supervisor":
      user_obj.is_staff = False
      user_obj.is_superuser = False
      # Add to Supervisor group
      supervisor_group, created = Group.objects.get_or_create(name='Supervisor')
      user_obj.groups.add(supervisor_group)
      role_label = "Supervisor"
    elif new_role == "dashboard_viewer":
      user_obj.is_staff = False
      user_obj.is_superuser = False
      dashboard_viewer_group, created = Group.objects.get_or_create(name='DashboardViewer')
      user_obj.groups.add(dashboard_viewer_group)
      role_label = "Readonly Admin User"
    else:  # normal
      user_obj.is_staff = False
      user_obj.is_superuser = False
      role_label = "Add (Mobile app users)"

    user_obj.save()
    from .selectors import clear_partner_scope_cache
    clear_partner_scope_cache(user_obj)

    return JsonResponse({
      "status": "success",
      "message": f"User role changed to {role_label} successfully"
    })

  except Exception as e:
    return JsonResponse({"status": "error", "message": f"Error changing role: {str(e)}"})


# -------------------- Insights page --------------------

@login_required
@dashboard_viewer_or_admin_required
def survey_insights(request):
  """Insights are surfaced inside the Reports page; keep this route as a compatibility redirect."""
  return redirect("survey:reports_v2")


@login_required
@require_POST
def survey_insights_refresh(request, survey_pk):
    """Staff-only: enqueue a Celery task to refresh insights for one survey.

    Runs asynchronously because each question fires an LLM call + pacing delay
    that together blow past browser/proxy request timeouts on larger surveys.
    """
    if not request.user.is_staff:
        return JsonResponse({"status": "error", "message": "Staff only"}, status=403)

    from .tasks import refresh_survey_insights

    try:
        refresh_survey_insights.delay(int(survey_pk), True, schema_name=_request_tenant_schema(request))
    except Exception as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)

    return JsonResponse({
        "status": "queued",
        "message": "Insights refresh started in the background. This usually takes 1–2 minutes; reload the page to see new results.",
    }, status=202)


@login_required
@dashboard_viewer_or_admin_required
@require_GET
def api_survey_insights_data(request):
    survey = Survey.objects.filter(id=INSIGHTS_SURVEY_ID).first()
    if survey is None:
        return JsonResponse({"success": False, "error": "Insights survey not found"}, status=404)

    raw_filters = apply_visibility_insight_filters(request.GET, request.user)
    raw_filters["language"] = get_language() or "en"
    filters = normalize_insight_filters(raw_filters)
    submissions = build_filtered_insight_submissions(survey, filters)
    submission_count = submissions.count()

    cache_entry, created = get_or_create_insight_cache(survey, filters)
    if created and cache_entry.status == SurveyInsightCache.STATUS_PENDING:
        cache_entry.status = SurveyInsightCache.STATUS_MISSING
        cache_entry.save(update_fields=["status", "updated_at"])

    status = cache_entry.status
    if status == SurveyInsightCache.STATUS_DONE:
        if cache_entry.submission_count == 0 and submission_count > 0:
            cache_entry.status = SurveyInsightCache.STATUS_MISSING
            cache_entry.submission_count = submission_count
            cache_entry.result_count = 0
            cache_entry.error_message = ""
            cache_entry.completed_at = None
            cache_entry.save(
                update_fields=[
                    "status",
                    "submission_count",
                    "result_count",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ]
            )
            return JsonResponse({
                "success": True,
                "status": SurveyInsightCache.STATUS_MISSING,
                "submission_count": submission_count,
                "result_count": 0,
                "generated_at": None,
                "insights_grouped": [],
                "message": "Insights are not cached for the current filters yet.",
            })
        payload = {
            "success": True,
            "status": status,
            "submission_count": cache_entry.submission_count,
            "result_count": cache_entry.result_count,
            "generated_at": cache_entry.completed_at.isoformat() if cache_entry.completed_at else None,
            "insights_grouped": serialize_insight_groups(cache_entry),
            "message": (
                "No questions crossed the poor-performance threshold for these filters."
                if cache_entry.result_count == 0 and cache_entry.submission_count > 0
                else ""
            ),
        }
        return JsonResponse(payload)

    if status in (SurveyInsightCache.STATUS_PENDING, SurveyInsightCache.STATUS_RUNNING):
        # Return whatever indicator cards have already been generated so the UI
        # can render them progressively — each per-indicator task writes its card
        # as soon as it finishes, so the user sees results stream in instead of
        # staring at a spinner until the whole batch completes.
        return JsonResponse({
            "success": True,
            "status": status,
            "submission_count": cache_entry.submission_count or submission_count,
            "result_count": cache_entry.result_count,
            "generated_at": cache_entry.completed_at.isoformat() if cache_entry.completed_at else None,
            "insights_grouped": serialize_insight_groups(cache_entry),
            "message": "Insights are being generated for the current filters. It usually take 10-15mins.",
        })

    if submission_count == 0:
        return JsonResponse({
            "success": True,
            "status": SurveyInsightCache.STATUS_DONE,
            "submission_count": 0,
            "result_count": 0,
            "generated_at": None,
            "insights_grouped": [],
            "message": "No submitted responses match the current filters.",
        })

    return JsonResponse({
        "success": True,
        "status": SurveyInsightCache.STATUS_MISSING,
        "submission_count": submission_count,
        "result_count": 0,
        "generated_at": cache_entry.completed_at.isoformat() if cache_entry.completed_at else None,
        "insights_grouped": [],
        "message": "Insights are not cached for the current filters yet.",
    })


@login_required
@dashboard_viewer_or_admin_required
@require_POST
def api_survey_insights_generate(request):
    survey = Survey.objects.filter(id=INSIGHTS_SURVEY_ID).first()
    if survey is None:
        return JsonResponse({"success": False, "error": "Insights survey not found"}, status=404)

    from .tasks import generate_filtered_survey_insights

    try:
        payload = json.loads(request.body or "{}") if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON payload"}, status=400)
    raw_filters = apply_visibility_insight_filters(payload, request.user)
    raw_filters["language"] = get_language() or "en"
    filters = normalize_insight_filters(raw_filters)
    force = bool(payload.get("force"))
    cache_entry, created = get_or_create_insight_cache(survey, filters)

    submission_count = build_filtered_insight_submissions(survey, filters).count()
    if (
        not force
        and cache_entry.status == SurveyInsightCache.STATUS_DONE
        and cache_entry.submission_count == 0
        and cache_entry.result_count == 0
    ):
        cache_entry.status = SurveyInsightCache.STATUS_MISSING
        cache_entry.submission_count = submission_count
        cache_entry.error_message = ""
        cache_entry.completed_at = None
        cache_entry.save(update_fields=["status", "submission_count", "error_message", "completed_at", "updated_at"])

    if submission_count == 0:
        cache_entry.status = SurveyInsightCache.STATUS_DONE
        cache_entry.submission_count = 0
        cache_entry.result_count = 0
        cache_entry.error_message = ""
        cache_entry.completed_at = None
        cache_entry.save(update_fields=["status", "submission_count", "result_count", "error_message", "completed_at", "updated_at"])
        return JsonResponse({
            "success": True,
            "status": SurveyInsightCache.STATUS_DONE,
            "queued": False,
            "message": "No submitted responses match the current filters.",
        })

    if (
        not force
        and cache_entry.status in (SurveyInsightCache.STATUS_PENDING, SurveyInsightCache.STATUS_RUNNING)
    ):
        return JsonResponse({
            "success": True,
            "status": cache_entry.status,
            "queued": False,
            "reused": True,
            "message": "Insights generation is already in progress for these filters.",
        }, status=202)

    if (
        force
        or created
        or cache_entry.status in (SurveyInsightCache.STATUS_MISSING, SurveyInsightCache.STATUS_FAILED)
        or (cache_entry.status == SurveyInsightCache.STATUS_DONE and cache_entry.submission_count == 0)
    ):
        cache_entry.status = SurveyInsightCache.STATUS_PENDING
        cache_entry.submission_count = submission_count
        cache_entry.error_message = ""
        cache_entry.save(update_fields=["status", "submission_count", "error_message", "updated_at"])
    elif cache_entry.status == SurveyInsightCache.STATUS_DONE and not force:
        return JsonResponse({
            "success": True,
            "status": cache_entry.status,
            "queued": False,
            "reused": True,
            "message": "Cached insights are already available for these filters.",
        })
    else:
        cache_entry.status = SurveyInsightCache.STATUS_PENDING
        cache_entry.submission_count = submission_count
        cache_entry.error_message = ""
        cache_entry.save(update_fields=["status", "submission_count", "error_message", "updated_at"])

    try:
        generate_filtered_survey_insights.delay(cache_entry.id, schema_name=_request_tenant_schema(request))
    except Exception as exc:
        cache_entry.status = SurveyInsightCache.STATUS_FAILED
        cache_entry.error_message = f"Failed to enqueue: {exc}"
        cache_entry.save(update_fields=["status", "error_message", "updated_at"])
        return JsonResponse({"success": False, "error": "Failed to enqueue insights generation", "detail": str(exc)}, status=500)

    return JsonResponse({
        "success": True,
        "status": SurveyInsightCache.STATUS_PENDING,
        "queued": True,
        "reused": False,
        "message": "Generating insights for the current filters. Results will appear here automatically.",
    }, status=202)


# ---------------------------------------------------------------------------
# Submission export jobs (Celery-backed)
# ---------------------------------------------------------------------------

_EXPORT_FILTER_FIELDS = (
    "search", "status", "archive", "show_duplicates",
    "date_from", "date_to", "state", "district", "clf",
    "survey", "display",
)


def _collect_export_filters(request):
    """Pull only the filter keys we care about so the dedup key is stable."""
    filters = {}
    for key in _EXPORT_FILTER_FIELDS:
        if key in request.GET:
            filters[key] = request.GET.get(key)
        elif key in request.POST:
            filters[key] = request.POST.get(key)
    # Normalize show_duplicates to a bool so dedup matches regardless of "1"/"true"/"on"
    if "show_duplicates" in filters:
        filters["show_duplicates"] = str(filters["show_duplicates"]).lower() in ("1", "true", "on", "yes")
    if "display" in filters and filters["display"] not in ("labels", "values"):
        filters["display"] = "labels"
    return filters


@login_required
@require_POST
def create_submissions_export(request, survey_pk=None):
    """Create (or reuse) a SubmissionExportJob and enqueue it on Celery.
    survey_pk is passed when exporting from a specific survey's submissions page;
    for the cross-survey submissions list the argument is None."""
    from .selectors import build_submissions_queryset
    from .tasks import generate_submissions_export

    survey = None
    if survey_pk is not None:
        survey = get_object_or_404(Survey, pk=survey_pk)

    filters = _collect_export_filters(request)

    # Reuse in-flight job with matching filters → prevents duplicate work when user double-clicks.
    existing = SubmissionExportJob.objects.filter(
        user=request.user,
        survey=survey,
        filters=filters,
        status__in=(SubmissionExportJob.STATUS_PENDING, SubmissionExportJob.STATUS_RUNNING),
    ).order_by("-created_at").first()

    if existing is not None:
        return JsonResponse({
            "job_id": str(existing.id),
            "status": existing.status,
            "reused": True,
        })

    # Safety cap: refuse if the filtered queryset would produce more rows than the configured limit.
    max_rows = getattr(settings, "SUBMISSIONS_EXPORT_MAX_ROWS", 100_000)
    try:
        row_count = build_submissions_queryset(request.user, filters, survey=survey).count()
    except Exception as exc:  # noqa: BLE001 — don't block the whole endpoint on a query error
        row_count = None
    if row_count is not None and row_count > max_rows:
        return JsonResponse({
            "error": (
                f"This filter would export {row_count:,} rows, which exceeds the "
                f"{max_rows:,} row limit. Narrow the filters and try again."
            ),
            "row_count": row_count,
            "max_rows": max_rows,
        }, status=400)

    job = SubmissionExportJob.objects.create(
        user=request.user,
        survey=survey,
        filters=filters,
        status=SubmissionExportJob.STATUS_PENDING,
    )

    try:
        generate_submissions_export.delay(str(job.id), schema_name=_request_tenant_schema(request))
    except Exception as exc:  # noqa: BLE001 — surface broker outages immediately
        job.status = SubmissionExportJob.STATUS_FAILED
        job.error_message = f"Failed to enqueue: {exc}"
        job.save(update_fields=["status", "error_message"])
        return JsonResponse({"error": "Failed to enqueue export", "detail": str(exc)}, status=500)

    return JsonResponse({"job_id": str(job.id), "status": job.status, "reused": False})


@login_required_json
def submissions_export_latest(request, survey_pk=None):
    """Return the user's most recent export job for this survey (or cross-survey if no pk).

    The UI calls this on page load to auto-resume: if a job is still running the
    progress bar reattaches; if a job finished while the user was away, the UI
    surfaces a 'your last export is ready' link without re-running the task.
    """
    from datetime import timedelta
    from django.utils import timezone as tz

    survey = None
    if survey_pk is not None:
        survey = get_object_or_404(Survey, pk=survey_pk)

    recent_cutoff = tz.now() - timedelta(hours=1)

    qs = SubmissionExportJob.objects.filter(user=request.user, survey=survey)
    # Either still running, or finished within the last hour
    qs = qs.filter(
        Q(status__in=[SubmissionExportJob.STATUS_PENDING, SubmissionExportJob.STATUS_RUNNING])
        | Q(status__in=[SubmissionExportJob.STATUS_DONE, SubmissionExportJob.STATUS_FAILED], completed_at__gte=recent_cutoff)
    )
    job = qs.order_by("-created_at").first()
    if job is None:
        return JsonResponse({"job": None})

    download_url = None
    if job.status == SubmissionExportJob.STATUS_DONE and job.file:
        from django.urls import reverse
        download_url = reverse("survey:submissions_export_download", kwargs={"job_id": str(job.id)})

    return JsonResponse({
        "job": {
            "job_id": str(job.id),
            "status": job.status,
            "processed": job.processed_rows,
            "total": job.total_rows,
            "percent": job.progress_percent,
            "error": job.error_message or None,
            "download_url": download_url,
            "filters": job.filters or {},
        }
    })


@login_required_json
def submissions_exports_active(request):
    """Return the user's recent export jobs for the navbar widget.
    Includes anything pending/running plus anything completed/failed within the last hour,
    so users can still click 'Download' on a file that finished while they were on another page.
    """
    from datetime import timedelta
    from django.utils import timezone as tz
    from django.urls import reverse

    recent_cutoff = tz.now() - timedelta(hours=1)
    jobs = (
        SubmissionExportJob.objects.filter(user=request.user)
        .filter(
            Q(status__in=[SubmissionExportJob.STATUS_PENDING, SubmissionExportJob.STATUS_RUNNING])
            | Q(
                status__in=[SubmissionExportJob.STATUS_DONE, SubmissionExportJob.STATUS_FAILED],
                completed_at__gte=recent_cutoff,
            )
        )
        .select_related("survey")
        .order_by("-created_at")[:20]
    )

    items = []
    for job in jobs:
        items.append({
            "job_id": str(job.id),
            "status": job.status,
            "processed": job.processed_rows,
            "total": job.total_rows,
            "percent": job.progress_percent,
            "error": job.error_message or None,
            "download_url": (
                reverse("survey:submissions_export_download", kwargs={"job_id": str(job.id)})
                if job.status == SubmissionExportJob.STATUS_DONE and job.file else None
            ),
            "label": (
                f"{job.survey.title} — {(job.filters or {}).get('display', 'labels').title()}"
                if job.survey else "Submissions export"
            ),
            "created_at": job.created_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        })

    active_count = sum(1 for it in items if it["status"] in ("pending", "running"))
    return JsonResponse({"items": items, "active": active_count})


@login_required_json
def submissions_export_status(request, job_id):
    """Return the current state of an export job (polled by the UI progress bar)."""
    try:
        job = SubmissionExportJob.objects.get(pk=job_id)
    except (SubmissionExportJob.DoesNotExist, ValueError):
        return JsonResponse({"error": "Job not found"}, status=404)

    if job.user_id != request.user.id and not request.user.is_superuser:
        return JsonResponse({"error": "Forbidden"}, status=403)

    download_url = None
    if job.status == SubmissionExportJob.STATUS_DONE and job.file:
        from django.urls import reverse
        download_url = reverse("survey:submissions_export_download", kwargs={"job_id": str(job.id)})

    return JsonResponse({
        "job_id": str(job.id),
        "status": job.status,
        "processed": job.processed_rows,
        "total": job.total_rows,
        "percent": job.progress_percent,
        "error": job.error_message or None,
        "download_url": download_url,
    })


@login_required_json
@require_POST
def submissions_export_retry(request, job_id):
    """Re-queue a failed export using the original job's filters/survey.
    Creates a fresh job so the old failed one can still be reviewed or dismissed."""
    from .tasks import generate_submissions_export

    try:
        original = SubmissionExportJob.objects.get(pk=job_id)
    except (SubmissionExportJob.DoesNotExist, ValueError):
        return JsonResponse({"error": "Job not found"}, status=404)

    if original.user_id != request.user.id and not request.user.is_superuser:
        return JsonResponse({"error": "Forbidden"}, status=403)

    if original.status != SubmissionExportJob.STATUS_FAILED:
        return JsonResponse({"error": "Only failed exports can be retried."}, status=409)

    new_job = SubmissionExportJob.objects.create(
        user=original.user,
        survey=original.survey,
        filters=original.filters or {},
        status=SubmissionExportJob.STATUS_PENDING,
    )
    try:
        generate_submissions_export.delay(str(new_job.id), schema_name=_request_tenant_schema(request))
    except Exception as exc:  # noqa: BLE001
        new_job.status = SubmissionExportJob.STATUS_FAILED
        new_job.error_message = f"Failed to enqueue: {exc}"
        new_job.save(update_fields=["status", "error_message"])
        return JsonResponse({"error": "Failed to enqueue retry", "detail": str(exc)}, status=500)

    # Remove the old failed job so the navbar list doesn't accumulate duplicates.
    if original.file:
        original.file.delete(save=False)
    original.delete()

    return JsonResponse({"job_id": str(new_job.id), "status": new_job.status})


@login_required_json
@require_POST
def submissions_export_dismiss(request, job_id):
    """Remove a finished/failed export from the user's navbar list (hard delete).
    Running/pending jobs can't be dismissed — they'll self-complete and then be clearable."""
    try:
        job = SubmissionExportJob.objects.get(pk=job_id)
    except (SubmissionExportJob.DoesNotExist, ValueError):
        return JsonResponse({"error": "Job not found"}, status=404)

    if job.user_id != request.user.id and not request.user.is_superuser:
        return JsonResponse({"error": "Forbidden"}, status=403)

    if job.status in (SubmissionExportJob.STATUS_PENDING, SubmissionExportJob.STATUS_RUNNING):
        return JsonResponse(
            {"error": "Cannot dismiss a running export. Wait for it to finish."},
            status=409,
        )

    if job.file:
        job.file.delete(save=False)
    job.delete()
    return JsonResponse({"ok": True})


@login_required_json
@require_POST
def submissions_exports_clear(request):
    """Clear all finished/failed jobs from the user's navbar list (hard delete).
    Leaves running jobs untouched."""
    qs = SubmissionExportJob.objects.filter(
        user=request.user,
        status__in=[SubmissionExportJob.STATUS_DONE, SubmissionExportJob.STATUS_FAILED],
    )
    removed = 0
    for job in qs:
        if job.file:
            job.file.delete(save=False)
        job.delete()
        removed += 1
    return JsonResponse({"ok": True, "removed": removed})


@login_required
def submissions_export_download(request, job_id):
    """Stream the generated XLSX back as an attachment download."""
    try:
        job = SubmissionExportJob.objects.get(pk=job_id)
    except (SubmissionExportJob.DoesNotExist, ValueError):
        return JsonResponse({"error": "Job not found"}, status=404)

    if job.user_id != request.user.id and not request.user.is_superuser:
        return JsonResponse({"error": "Forbidden"}, status=403)

    if job.status != SubmissionExportJob.STATUS_DONE or not job.file:
        return JsonResponse({"error": "Export not ready"}, status=409)

    from django.http import FileResponse
    response = FileResponse(
        job.file.open("rb"),
        as_attachment=True,
        filename=os.path.basename(job.file.name),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return response
