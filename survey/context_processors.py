"""Context processors for survey app"""

def _is_partner_user(user):
    """True when the account matches a rule in PARTNER_VISIBILITY_RULES.

    Imported lazily: this module is loaded during template engine setup, before
    app registry access is safe.
    """
    try:
        from survey.selectors import get_partner_visibility_rule
        return get_partner_visibility_rule(user) is not None
    except Exception:
        return False


def user_role(request):
    """Add user role information to all templates"""
    if request.user.is_authenticated:
        is_manager = request.user.groups.filter(name='Manager').exists()
        is_supervisor = request.user.groups.filter(name='Supervisor').exists()
        is_dashboard_viewer = request.user.groups.filter(name='DashboardViewer').exists()
        is_admin = request.user.is_superuser
        is_staff = request.user.is_staff
        is_partner_user = _is_partner_user(request.user)

        # Determine primary role
        if is_admin:
            user_role_name = 'admin'
        elif is_manager:
            user_role_name = 'manager'
        elif is_supervisor:
            user_role_name = 'supervisor'
        elif is_dashboard_viewer:
            user_role_name = 'dashboard_viewer'
        else:
            user_role_name = 'normal'

        # Check if user can access survey app
        can_access_survey = is_admin or is_manager or is_supervisor or is_dashboard_viewer

        return {
            'is_manager': is_manager,
            'is_supervisor': is_supervisor,
            'is_dashboard_viewer': is_dashboard_viewer,
            'is_admin': is_admin,
            'is_staff': is_staff,
            'user_role_name': user_role_name,
            'can_access_survey': can_access_survey,
            # Partner accounts are limited to consuming data: no project
            # creation and no external report tool.
            'is_partner_user': is_partner_user,
        }
    return {
        'is_manager': False,
        'is_supervisor': False,
        'is_dashboard_viewer': False,
        'is_admin': False,
        'is_staff': False,
        'user_role_name': 'guest',
        'can_access_survey': False,
        'is_partner_user': False,
    }
