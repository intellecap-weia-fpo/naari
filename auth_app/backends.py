from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

User = get_user_model()


class CaseSensitiveModelBackend(ModelBackend):
    """
    Custom authentication backend that performs case-sensitive username authentication.
    This fixes the issue where usernames are authenticated in a case-insensitive manner.
    """
    
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)
        if username is None or password is None:
            return None
        
        try:
            # Use case-sensitive lookup instead of the default case-insensitive lookup
            user = User.objects.get(**{User.USERNAME_FIELD: username})
        except User.DoesNotExist:
            # Run the default password hasher once to reduce the timing
            # difference between an existing and a nonexistent user.
            User().set_password(password)
            return None
        
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None