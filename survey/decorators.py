"""Custom decorators for survey app"""
from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from guardian.decorators import permission_required as guardian_permission_required
from django.core.cache import caches
from django.http import JsonResponse
import hashlib
import time
import json


# Server start timestamp - used to invalidate cache on server restart
SERVER_START_TIMESTAMP = str(int(time.time()))


def get_survey_report_cache():
    """Get the survey reports cache"""
    try:
        return caches['survey_reports']
    except Exception:
        return caches['default']


def url_cache_decorator(timeout=600):
    """
    Decorator that caches API responses based on the full URL.
    Includes SERVER_START_TIMESTAMP to invalidate cache on server restart.
    
    Usage:
        @url_cache_decorator(timeout=600)  # Cache for 10 minutes
        def my_api_view(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            # Build cache key from full URL + server start timestamp.
            # Partner-restricted users have a capped submission window, so we
            # bucket their cache entries per partner to avoid leaking
            # pre-cutoff data via another bucket's cached response.
            full_url = request.build_absolute_uri()
            from .selectors import get_visibility_cache_bucket
            user_bucket = get_visibility_cache_bucket(getattr(request, "user", None))
            cache_key = f"url_cache_{hashlib.md5((full_url + SERVER_START_TIMESTAMP + user_bucket).encode()).hexdigest()}"
            
            cache = get_survey_report_cache()
            
            # Try to get from cache
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return JsonResponse(cached_result)
            
            # Call the view function
            response = view_func(request, *args, **kwargs)
            
            # Cache the result if it's a successful JSON response
            if isinstance(response, JsonResponse) and response.status_code == 200:
                try:
                    # Extract data from JsonResponse
                    response_data = json.loads(response.content)
                    cache.set(cache_key, response_data, timeout)
                except Exception:
                    pass
            
            return response
        return wrapper
    return decorator


def login_required_json(view_func):
    """login_required for endpoints fetched by JS, returning 401 JSON instead of a redirect.

    Why: a redirect to the sign-in page means a fetch() resolves with an HTML
    login document (or, worse, the client follows it), and the original URL ends
    up appended as ?next= — which then lands the user on a raw JSON endpoint
    after they log back in. Pollers should be told "you are signed out" and
    handle it themselves.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse(
                {"items": [], "active": 0, "authenticated": False},
                status=401,
            )
        return view_func(request, *args, **kwargs)
    return wrapper


def invalidate_survey_cache():
    """Invalidate all survey report cache."""
    cache = get_survey_report_cache()
    cache.clear()


def admin_required(view_func):
    """
    Decorator that restricts access to admin users only (not managers).
    Managers have is_staff=True but is_superuser=False.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('auth_app:sign_in')

        if not request.user.is_superuser:
            return redirect('survey:submissions_list')

        return view_func(request, *args, **kwargs)
    return wrapper


def dashboard_viewer_or_admin_required(view_func):
    """
    Decorator that allows access to admins and dashboard viewers only.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('auth_app:sign_in')

        is_dashboard_viewer = request.user.groups.filter(name='DashboardViewer').exists()
        if not (request.user.is_superuser or is_dashboard_viewer):
            return redirect('survey:submissions_list')

        return view_func(request, *args, **kwargs)
    return wrapper


def staff_or_admin_required(view_func):
    """
    Decorator that restricts access to staff or admin users (managers and admins).
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('auth_app:sign_in')

        if not (request.user.is_staff or request.user.is_superuser):
            return redirect('survey:submissions_list')

        return view_func(request, *args, **kwargs)
    return wrapper


def manager_or_permission_required(perm, *lookup_variables, **kwargs):
    """
    Decorator that checks guardian object-level permissions OR allows privileged roles to bypass.
    Managers, staff, superusers, and supervisors have automatic access.
    Dashboard viewers bypass only read permissions ("view_") for read-only access.
    """
    def decorator(view_func):
        # Get the original guardian decorator
        guardian_decorator = guardian_permission_required(perm, *lookup_variables, **kwargs)

        @wraps(view_func)
        def wrapper(request, *args, **view_kwargs):
            # Check if user is manager, staff, superuser, or supervisor
            if request.user.is_authenticated:
                is_manager = request.user.groups.filter(name='Manager').exists()
                is_supervisor = request.user.groups.filter(name='Supervisor').exists()
                is_dashboard_viewer = request.user.groups.filter(name='DashboardViewer').exists()
                is_read_permission = '.view_' in perm

                if request.user.is_staff or request.user.is_superuser or is_manager or is_supervisor:
                    # Bypass permission check for managers, admins, and supervisors
                    return view_func(request, *args, **view_kwargs)

                if is_dashboard_viewer and is_read_permission:
                    return view_func(request, *args, **view_kwargs)

            # Otherwise, use guardian's permission check
            return guardian_decorator(view_func)(request, *args, **view_kwargs)

        return wrapper
    return decorator
