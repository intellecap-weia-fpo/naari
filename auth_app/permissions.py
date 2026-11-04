from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from django.db import models


def get_user_role(user):
    """Get the primary role of a user"""
    if not user or not user.is_authenticated:
        return None

    # Check for specific groups in order of priority
    if user.groups.filter(name='admin').exists() or user.is_superuser:
        return 'admin'
    elif user.groups.filter(name='Manager').exists():
        return 'manager'
    elif user.groups.filter(name='Supervisor').exists():
        return 'supervisor'
    elif user.groups.filter(name='DashboardViewer').exists():
        return 'dashboard_viewer'
    elif user.is_staff:
        return 'staff'

    return 'user'


def get_sidebar_menu_for_role(role, user=None):
    """Get sidebar menu items based on user role"""

    if role in ['admin', 'manager']:
        return [
            {
                "name": _("Home"),
                "url": "/survey/home/",
                "icon_class": "fa fa-home",
            },
            {
                "name": _("Survey"),
                "url": "/survey/",
                "icon_class": "fa fa-tasks",
            },
            {
                "name": _("Administration"),
                "icon_class": "fa fa-cogs",
                "children": [
                    {"name": _("User Management"), "url": "/manage/users/", "icon_class": "fa fa-users"},
                    {"name": _("Settings"), "url": "/manage/settings/", "icon_class": "fa fa-cog"},
                ],
                "default_open": False,
            },
        ]

    # Default menu for regular users
    return [
        {
            "name": _("Home"),
            "url": "/survey/home/",
            "icon_class": "fa fa-home",
        },
        {
            "name": _("Survey"),
            "url": "/survey/",
            "icon_class": "fa fa-tasks",
        },
    ]


def can_access_url(user, url):
    """Check if a user can access a specific URL based on their role"""
    if not user or not user.is_authenticated:
        return False

    role = get_user_role(user)

    # Every signed-in dashboard user can manage their own account settings.
    if url == '/settings' or url.startswith('/settings/'):
        return True

    # Admin and manager can access everything
    if role in ['admin', 'manager']:
        return True

    # Survey users can access survey URLs (fine-grained control is in survey middleware)
    if url.startswith('/survey/'):
        return True

    # Restrict management URLs to admin/manager
    if url.startswith('/manage/'):
        return role in ['admin', 'manager']

    return False


def filter_queryset_by_role(queryset, user, model_name):
    """Filter queryset based on user role and ownership"""
    if not user or not user.is_authenticated:
        return queryset.none()

    role = get_user_role(user)

    # Admin and manager can see everything
    if role in ['admin', 'manager']:
        return queryset

    # Regular users see nothing by default
    return queryset.none()
