from django.utils import translation
from django.conf import settings


class UserLanguageMiddleware:
    """
    Middleware to activate user's preferred language from their profile.
    This should run after LocaleMiddleware.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # This middleware should only run AFTER LocaleMiddleware has done its work
        # We only set user language as a fallback if no language cookie/session exists
        
        if (hasattr(request, 'user') and 
            request.user.is_authenticated and 
            hasattr(request.user, 'profile') and 
            hasattr(request.user.profile, 'preferred_language') and
            request.user.profile.preferred_language):
            
            # Only set user's preferred language if:
            # 1. No language cookie is set
            # 2. Current language is still the default
            current_language = translation.get_language()
            has_language_cookie = 'django_language' in request.COOKIES
            
            if (not has_language_cookie and 
                current_language == settings.LANGUAGE_CODE):
                user_language = request.user.profile.preferred_language
                if user_language in dict(settings.LANGUAGES):
                    translation.activate(user_language)

        response = self.get_response(request)
        return response