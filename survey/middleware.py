"""Middleware for survey app"""
import re
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import resolve


class SurveyAccessMiddleware:
    """
    Middleware to restrict access to survey app based on user role.
    Only admins and managers can access the full survey app.
    Dashboard viewers can access survey pages in read-only mode.
    Supervisors can only access submission-related URLs (read-only).
    Normal users are blocked from accessing survey URLs.
    """

    # Dashboard viewers are read-only users. These action paths are blocked even for GET
    # because a few legacy endpoints mutate state without strict HTTP method checks.
    DASHBOARD_VIEWER_BLOCKED_PATTERNS = [
        re.compile(r'^/survey/create/$'),
        re.compile(r'^/survey/\d+/edit/$'),
        re.compile(r'^/survey/\d+/delete/$'),
        re.compile(r'^/survey/\d+/clone/$'),
        re.compile(r'^/survey/submissions/\d+/toggle-archive/$'),
        re.compile(r'^/survey/\d+/page/create/$'),
        re.compile(r'^/survey/page/\d+/edit/$'),
        re.compile(r'^/survey/page/\d+/delete/$'),
        re.compile(r'^/survey/page/\d+/question/create/$'),
        re.compile(r'^/survey/question/\d+/edit/$'),
        re.compile(r'^/survey/question/\d+/delete/$'),
        re.compile(r'^/survey/\d+/indicator/create/$'),
        re.compile(r'^/survey/indicator/\d+/edit/$'),
        re.compile(r'^/survey/indicator/\d+/execute/$'),
        re.compile(r'^/survey/\d+/indicator/\d+/execute-single/$'),
        re.compile(r'^/survey/indicator/\d+/delete/$'),
        re.compile(r'^/survey/user-management/create/$'),
        re.compile(r'^/survey/user-management/\d+/delete/$'),
        re.compile(r'^/survey/api/user/\d+/assign-survey/$'),
        re.compile(r'^/survey/api/user/\d+/remove-survey-access/\d+/$'),
        re.compile(r'^/survey/api/user/\d+/change-role/$'),
    ]

    # URL patterns that supervisors are allowed to access
    SUPERVISOR_ALLOWED_PATTERNS = [
        re.compile(r'^/survey/submissions/$'),                          # submissions list
        re.compile(r'^/survey/submissions/download/$'),                 # download submissions
        re.compile(r'^/survey/\d+/submissions/$'),                      # survey-specific submissions
        re.compile(r'^/survey/\d+/submissions/\d+/$'),                  # submission detail
        re.compile(r'^/survey/\d+/submissions/\d+/indicators/$'),       # submission indicators
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if the request is for a survey URL
        if request.path.startswith('/survey/'):
            # Allow access to public endpoints (like API endpoints used by mobile app)
            public_endpoints = ['/survey/api/']
            is_public = any(request.path.startswith(endpoint) for endpoint in public_endpoints)

            # If user is authenticated
            if request.user.is_authenticated:
                # Check if user is admin or manager
                is_admin = request.user.is_superuser
                is_manager = request.user.groups.filter(name='Manager').exists()
                is_supervisor = request.user.groups.filter(name='Supervisor').exists()
                is_dashboard_viewer = request.user.groups.filter(name='DashboardViewer').exists()

                if is_admin or is_manager:
                    # Full access for admins and managers
                    pass
                elif is_dashboard_viewer:
                    # Dashboard viewers can see all survey pages, but only in read-only mode.
                    if request.method not in ("GET", "HEAD", "OPTIONS"):
                        messages.error(
                            request,
                            "Access denied. Dashboard viewers have read-only access."
                        )
                        return redirect('/survey/home/')

                    if any(pattern.match(request.path) for pattern in self.DASHBOARD_VIEWER_BLOCKED_PATTERNS):
                        messages.error(
                            request,
                            "Access denied. Dashboard viewers cannot access create/edit/delete actions."
                        )
                        return redirect('/survey/home/')
                elif is_supervisor:
                    # Supervisors can only access submission-related URLs
                    if not any(pattern.match(request.path) for pattern in self.SUPERVISOR_ALLOWED_PATTERNS):
                        messages.error(
                            request,
                            "Access denied. Supervisors can only access submissions."
                        )
                        return redirect('/survey/submissions/')
                else:
                    # Block access for normal users
                    if not is_public:
                        messages.error(
                            request,
                            "Access denied. You don't have permission to access the survey management system. "
                            "Only administrators and managers can access this area."
                        )
                        return redirect('/')

        response = self.get_response(request)
        return response
