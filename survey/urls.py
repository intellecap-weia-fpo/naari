from django.urls import path
from . import views
from . import translation_views

app_name = "survey"

urlpatterns = [
    # Dashboard
    path("home/", views.home, name="home"),
    # Survey URLs
    path("", views.survey_list, name="survey_list"),
    path("submissions/", views.submissions_list, name="submissions_list"),
    path("submissions/districts/", views.submissions_districts_for_state, name="submissions_districts_for_state"),
    path("submissions/download/", views.download_submissions_list, name="download_submissions_list"),
    # Celery-backed full export for the cross-survey submissions list
    path("submissions/export/", views.create_submissions_export, name="submissions_export_create"),
    # Celery-backed export scoped to a specific survey
    path("<int:survey_pk>/submissions/export/", views.create_submissions_export, name="survey_submissions_export_create"),
    path("submissions/export/active/", views.submissions_exports_active, name="submissions_exports_active"),
    path("submissions/export/clear/", views.submissions_exports_clear, name="submissions_exports_clear"),
    path("submissions/export/<uuid:job_id>/status/", views.submissions_export_status, name="submissions_export_status"),
    path("submissions/export/<uuid:job_id>/download/", views.submissions_export_download, name="submissions_export_download"),
    path("submissions/export/<uuid:job_id>/dismiss/", views.submissions_export_dismiss, name="submissions_export_dismiss"),
    path("submissions/export/<uuid:job_id>/retry/", views.submissions_export_retry, name="submissions_export_retry"),
    path("submissions/<int:pk>/toggle-archive/", views.toggle_submission_archive, name="toggle_submission_archive"),
    path("create/", views.survey_create, name="survey_create"),
    path("<int:pk>/", views.survey_detail, name="survey_detail"),
    path("<int:pk>/form/", views.survey_form, name="survey_form"),
    path("<int:pk>/preview/", views.survey_preview, name="survey_preview"),
    path("<int:pk>/edit/", views.survey_edit, name="survey_edit"),
    path("<int:pk>/delete/", views.survey_delete, name="survey_delete"),
    path("<int:pk>/clone/", views.survey_clone, name="survey_clone"),
    # Page URLs
    path("<int:survey_pk>/page/create/", views.page_create, name="page_create"),
    path("page/<int:pk>/", views.page_detail, name="page_detail"),
    path("page/<int:pk>/edit/", views.page_edit, name="page_edit"),
    path("page/<int:pk>/delete/", views.page_delete, name="page_delete"),
    # Question URLs
    path("page/<int:page_pk>/question/create/", views.question_create, name="question_create"),
    path("question/<int:pk>/edit/", views.question_edit, name="question_edit"),
    path("question/<int:pk>/delete/", views.question_delete, name="question_delete"),
    # API endpoints for dynamic operations
    path("api/<int:survey_pk>/reorder-pages/", views.reorder_pages, name="reorder_pages"),
    path("api/page/<int:page_pk>/reorder-questions/", views.reorder_questions, name="reorder_questions"),
    path("api/page/<int:page_pk>/quick-add-question/", views.quick_add_question, name="quick_add_question"),
    path("api/<int:survey_pk>/quick-add-page/", views.quick_add_page, name="quick_add_page"),
    # Question option management
    path("api/question/<int:question_pk>/add-option/", views.add_question_option, name="add_question_option"),
    path("api/option/<int:option_pk>/update/", views.update_question_option, name="update_question_option"),
    path("api/option/<int:option_pk>/delete/", views.delete_question_option, name="delete_question_option"),
    path(
        "api/question/<int:question_pk>/reorder-options/",
        views.reorder_question_options,
        name="reorder_question_options",
    ),
    path("api/question/<int:question_pk>/details/", views.get_question_details, name="get_question_details"),
    path("api/question/<int:question_pk>/set-filter-question/", views.set_filter_options_question, name="set_filter_options_question"),
    # Translation URLs
    path(
        "api/question/<int:question_pk>/translations/",
        translation_views.question_translations,
        name="question_translations",
    ),
    path("api/option/<int:option_pk>/translations/", translation_views.option_translations, name="option_translations"),
    path("api/option/<int:option_pk>/warning-translations/", translation_views.option_warning_translations, name="option_warning_translations"),
    path("api/question/<int:question_pk>/hint-translations/", translation_views.hint_translations, name="hint_translations"),
    path("api/option/<int:option_pk>/subquestions/", views.option_subquestions, name="option_subquestions"),
    path("api/option/<int:option_pk>/add-subquestion/", views.add_subquestion_to_option, name="add_subquestion_to_option"),
    path("api/subquestion/<int:subquestion_pk>/delete/", views.delete_subquestion, name="delete_subquestion"),
    # Option conditional logic endpoints
    path("api/option/<int:option_pk>/conditions/", views.get_option_conditions, name="get_option_conditions"),
    path("api/option/<int:option_pk>/toggle-conditional/", views.toggle_option_conditional, name="toggle_option_conditional"),
    path("api/option/<int:option_pk>/add-condition/", views.add_option_condition, name="add_option_condition"),
    path("api/option-condition/<int:condition_pk>/delete/", views.delete_option_condition, name="delete_option_condition"),
    path("api/option/<int:option_pk>/warning/", views.option_warning_config, name="option_warning"),
    path("api/<int:survey_pk>/languages/", translation_views.survey_languages, name="survey_languages"),
    path("api/auto-translate/", translation_views.auto_translate, name="auto_translate"),
    path("api/auto-translate-survey/", translation_views.auto_translate_survey, name="auto_translate_survey"),
    # Submission URLs
    path("<int:pk>/submissions/", views.survey_submissions, name="survey_submissions"),
    path("api/<int:survey_pk>/districts/", views.survey_districts_for_state, name="survey_districts_for_state"),
    path("<int:survey_pk>/submissions/<int:submission_pk>/", views.submission_detail, name="submission_detail"),
    path("<int:survey_pk>/submissions/<int:submission_pk>/indicators/", views.submission_indicators, name="submission_indicators"),
    path("api/<int:survey_pk>/submissions/<int:submission_pk>/answer/<int:answer_pk>/update/", views.update_submission_answer, name="update_submission_answer"),
    path("api/<int:survey_pk>/submissions/<int:submission_pk>/question/<int:question_pk>/create-answer/", views.create_submission_answer, name="create_submission_answer"),
    # Invitation URLs
    path("api/<int:survey_pk>/search-users/", views.search_users, name="search_users"),
    path("api/<int:survey_pk>/invite-user/", views.invite_user, name="invite_user"),
    path("api/<int:survey_pk>/invitations/", views.survey_invitations, name="survey_invitations"),
    path("api/<int:survey_pk>/invitations/<int:invitation_id>/remove/", views.remove_invitation, name="remove_invitation"),
    # Test URL
    # Image upload URLs
    path("api/submission/<str:submission_pk>/upload-signature/", views.upload_signature_image, name="upload_signature"),
    path("api/submission/<str:submission_pk>/upload-photo/", views.upload_photo_image, name="upload_photo"),
    path("api/submission/<str:submission_pk>/images/", views.get_submission_images, name="get_submission_images"),
    # Consent translation URL
    path("api/<int:survey_pk>/consent-translations/", translation_views.survey_consent_translations, name="survey_consent_translations"),
    path("api/page/<int:page_pk>/translations/", translation_views.page_translations, name="page_translations"),
    
    # Conditional logic API endpoints
    path("api/question/<int:question_pk>/conditions/", views.get_question_conditions, name="get_question_conditions"),
    path("api/question/<int:question_pk>/toggle-conditional/", views.toggle_question_conditional, name="toggle_question_conditional"),
    path("api/question/<int:question_pk>/add-condition/", views.add_question_condition, name="add_question_condition"),
    path("api/condition/<int:condition_pk>/delete/", views.delete_question_condition, name="delete_question_condition"),
    # Indicators URLs
    path("<int:pk>/indicators/", views.survey_indicators, name="survey_indicators"),
    path("<int:pk>/submissions-indicators/", views.survey_submissions_indicators, name="survey_submissions_indicators"),
    path("<int:survey_pk>/indicator/create/", views.indicator_create, name="indicator_create"),
    path("indicator/<int:pk>/edit/", views.indicator_edit, name="indicator_edit"),
    path("indicator/<int:pk>/execute/", views.indicator_execute, name="indicator_execute"),
    path("<int:survey_pk>/indicator/<int:indicator_pk>/execute-single/", views.indicator_execute_single, name="indicator_execute_single"),
    path("indicator/<int:pk>/delete/", views.indicator_delete, name="indicator_delete"),

    # Survey Dashboard URLs
    path("report/", views.survey_reports_v2, name="reports_v2"),
    path("insights/", views.survey_insights, name="survey_insights"),
    path("insights/<int:survey_pk>/refresh/", views.survey_insights_refresh, name="survey_insights_refresh"),
    path("api/insights-data/", views.api_survey_insights_data, name="api_survey_insights_data"),
    path("api/insights-data/generate/", views.api_survey_insights_generate, name="api_survey_insights_generate"),
    path("api/survey-data/", views.api_survey_data, name="api_survey_data"),
    path("api/map-data/", views.api_map_data, name="api_map_data"),
    path("api/module-g2a-data/", views.api_module_g2a_data, name="api_module_g2a_data"),
    path("api/self-efficacy-data/", views.api_self_efficacy_data, name="api_self_efficacy_data"),
    path("api/g3a-productive-capital-data/", views.api_g3a_productive_capital_data, name="api_g3a_productive_capital_data"),
    path("api/g3b-financial-services-data/", views.api_g3b_financial_services_data, name="api_g3b_financial_services_data"),
    path("api/g4-time-allocation-data/", views.api_g4_time_allocation_data, name="api_g4_time_allocation_data"),
    path("api/g6-physical-mobility-data/", views.api_g6_physical_mobility_data, name="api_g6_physical_mobility_data"),
    path("api/districts-by-state/", views.api_districts_by_state, name="api_districts_by_state"),
    path("report/export/", views.create_report_export, name="report_export_create"),
    path("report/export/<uuid:job_id>/status/", views.report_export_status, name="report_export_status"),
    path("report/export/<uuid:job_id>/download/", views.report_export_download, name="report_export_download"),
    path("api/blocks-by-district/", views.api_blocks_by_district, name="api_blocks_by_district"),
    # Import/Export URLs
    path("<int:pk>/import-export/", views.survey_import_export, name="survey_import_export"),
    path("<int:pk>/export/", views.export_survey_submissions, name="export_survey_submissions"),
    path("<int:pk>/template/", views.download_survey_template, name="download_survey_template"),
    path("api/<int:pk>/question-enums/", views.get_survey_question_enums, name="get_survey_question_enums"),
    path("api/blocks-by-state-and-district/", views.api_blocks_by_state_and_district, name="api_blocks_by_state_and_district"),
    path("api/clfs-by-block/", views.api_clfs_by_block, name="api_clfs_by_block"),
    path("api/grouped-indicator-data/", views.api_grouped_indicator_data, name="api_grouped_indicator_data"),

    # User Management URLs
    path("user-management/", views.survey_user_management, name="user_management"),
    path("user-management/<int:user_id>/", views.survey_user_detail, name="user_detail"),
    path("user-management/create/", views.survey_create_user, name="create_user"),
    path("user-management/<int:user_id>/delete/", views.survey_delete_user, name="delete_user"),
    # User Management API endpoints
    path("api/user/<int:user_id>/search-surveys/", views.search_surveys_for_user, name="search_surveys_for_user"),
    path("api/user/<int:user_id>/assign-survey/", views.assign_user_to_survey, name="assign_user_to_survey"),
    path("api/user/<int:user_id>/remove-survey-access/<int:invitation_id>/", views.remove_user_survey_access, name="remove_user_survey_access"),
    path("api/user/<int:user_id>/change-password/", views.change_user_password, name="change_user_password"),
    path("api/user/<int:user_id>/change-role/", views.change_user_role, name="change_user_role"),
]
