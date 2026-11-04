from auth_app.permissions import get_user_role, can_access_url
from django.shortcuts import redirect, render
from django.contrib import messages
from django.urls import reverse


class RoleBasedAccessMiddleware:
  """Middleware to handle role-based access control"""

  def __init__(self, get_response):
    self.get_response = get_response

  def __call__(self, request):
    # Add user role to request
    if request.user.is_authenticated:
      request.user_role = get_user_role(request.user)

      # Check URL access permissions
      if request.path.startswith('/'):
        # Skip permission check for common areas
        common_urls = ['/api/', "/survey", "/neo/set-language", "/media/", "/auth/", "/admin", "/graphql"]
        is_common = any(request.path.startswith(url) for url in common_urls)

        if not is_common and not can_access_url(request.user, request.path):
          # Supervisors should always be redirected to submissions
          if request.user_role == 'supervisor':
            return redirect('/survey/submissions/')

          # Special handling for manage URLs - show access denied page
          if request.path.startswith('/manage/'):
            context = {
              'user_role': request.user_role,
              'requested_url': request.path,
              'required_roles': ['admin', 'manager']
            }
            return render(request, 'auth_app/access_denied.html', context, status=403)
          else:
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('neo:home')
    else:
      request.user_role = None

    response = self.get_response(request)
    return response
