from django.conf import settings
from .models import SiteConfiguration
from auth_app.permissions import get_user_role


def apps_context(request):
    """Context processor to make APPS available to all templates"""
    all_apps = getattr(settings, "APPS", [])
    
    # Add user role to context
    user_role = None
    if request.user.is_authenticated:
        user_role = get_user_role(request.user)
    
    # Only show apps to authenticated users
    if not request.user.is_authenticated:
        return {"apps": [], "user_role": user_role}
    
    # Get enabled apps from SiteConfiguration
    try:
        site_config = SiteConfiguration.get_solo()
        enabled_apps = site_config.enabled_apps
        
        # If enabled_apps is not empty, filter by enabled apps first
        if enabled_apps:
            all_apps = [app for app in all_apps if app.get('name') in enabled_apps]
        
        # Filter apps based on user role
        filtered_apps = []
        for app in all_apps:
            app_name = app.get('name')
            
            if app_name == 'Admin':
                # Admin app is only available for staff users or admin role
                if request.user.is_staff or user_role == 'admin':
                    filtered_apps.append(app)
            elif app_name == 'Cohort':
                # Cohort app is available to all authenticated users
                filtered_apps.append(app)
            else:
                # Other apps based on role
                if user_role in ['admin', 'cohortmanager']:
                    filtered_apps.append(app)
                elif user_role in ['startupadmin', 'startupuser'] and app_name in ['Cohort', 'Survey']:
                    filtered_apps.append(app)
        
        return {"apps": filtered_apps, "user_role": user_role}
        
    except Exception:
        # Fallback: show apps based on role
        if request.user.is_staff or user_role == 'admin':
            return {"apps": all_apps, "user_role": user_role}
        else:
            return {"apps": [app for app in all_apps if app.get('name') != 'Admin'], "user_role": user_role}


def site_config_context(request):
    """Context processor to make site configuration available to all templates"""
    try:
        site_config = SiteConfiguration.get_solo()
        return {
            "site_config": site_config,
            "primary_color": site_config.primary_color,
            "secondary_color": site_config.secondary_color,
            "theme_mode": site_config.theme_mode,
            "site_name": site_config.site_name,
        }
    except Exception:
        # Fallback values
        return {
            "site_config": None,
            "primary_color": "oklch(0.5770 0.2450 27.3250)",
            "secondary_color": "oklch(0.9702 0 0)",
            "theme_mode": "light",
            "site_name": "NAARI",
        }
