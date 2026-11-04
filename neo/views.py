from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.shortcuts import render, redirect
from django.utils import timezone, translation
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from datetime import timedelta
from .models import RecentActivity, SiteConfiguration


@login_required
def home(request):
    # Check if there's a custom landing page configured
    site_config = SiteConfiguration.get_solo()
    if site_config.landing_page_url:
        return redirect(site_config.landing_page_url)
    
    # Get recent activities for the current user
    recent_activities = RecentActivity.objects.filter(user=request.user)[:4]
    
    # Calculate KPIs
    total_users = User.objects.count()
    
    # Active sessions (sessions that haven't expired)
    active_sessions = Session.objects.filter(expire_date__gte=timezone.now()).count()
    
    # Calculate user growth (users created in the last 30 days vs previous 30 days)
    today = timezone.now().date()
    last_30_days = today - timedelta(days=30)
    last_60_days = today - timedelta(days=60)
    
    users_last_30_days = User.objects.filter(date_joined__date__gte=last_30_days).count()
    users_previous_30_days = User.objects.filter(
        date_joined__date__gte=last_60_days,
        date_joined__date__lt=last_30_days
    ).count()
    
    user_growth_percentage = 0
    if users_previous_30_days > 0:
        user_growth_percentage = ((users_last_30_days - users_previous_30_days) / users_previous_30_days) * 100
    elif users_last_30_days > 0:
        user_growth_percentage = 100
    
    # Calculate session growth (compare today vs yesterday)
    yesterday_sessions = Session.objects.filter(
        expire_date__date=today - timedelta(days=1)
    ).count()
    
    session_growth_percentage = 0
    if yesterday_sessions > 0:
        session_growth_percentage = ((active_sessions - yesterday_sessions) / yesterday_sessions) * 100
    elif active_sessions > 0:
        session_growth_percentage = 100
    
    context = {
        'recent_activities': recent_activities,
        'total_users': total_users,
        'active_sessions': active_sessions,
        'user_growth_percentage': round(user_growth_percentage, 1),
        'session_growth_percentage': round(session_growth_percentage, 1),
    }
    
    return render(request, "neo/home.html", context)


@require_POST
@csrf_protect
def set_language(request):
    language = request.POST.get('language')
    next_url = request.POST.get('next', '/')
    
    if language and language in dict(settings.LANGUAGES):
        # Set the language cookie
        response = HttpResponseRedirect(next_url)
        response.set_cookie(
            'django_language',
            language,
            max_age=365 * 24 * 60 * 60,  # 1 year
            path='/',
            domain=None,
            secure=False,
            httponly=False,
            samesite='Lax',
        )
        
        # Save to user profile if authenticated
        if request.user.is_authenticated and hasattr(request.user, 'profile'):
            request.user.profile.preferred_language = language
            request.user.profile.save()
        
        return response
    
    return HttpResponseRedirect(next_url)


def debug_language(request):
    """Debug view to check language settings"""
    from django.http import JsonResponse
    from django.utils import translation
    
    debug_info = {
        'current_language': translation.get_language(),
        'language_from_request': getattr(request, 'LANGUAGE_CODE', 'Not set'),
        'cookie_value': request.COOKIES.get('django_language', 'Not set'),
        'available_languages': dict(settings.LANGUAGES),
        'session_language': request.session.get('django_language', 'Not set'),
    }
    
    return JsonResponse(debug_info)
