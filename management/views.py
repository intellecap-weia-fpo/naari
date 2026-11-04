from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import User, Group
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Q
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm, UserCreationForm
from django.urls import reverse
from django import forms
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.utils.translation import gettext_lazy as _
from .models import UserProfile
from auth_app.decorators import admin_required
from auth_app.permissions import get_user_role
from survey.selectors import (
    assign_partner_group_if_needed,
    clear_partner_scope_cache,
    enforce_partner_user_scope,
    filter_users_by_partner_scope,
    get_partner_group_names,
)


@admin_required
def manage_dashboard(request):
    """Management dashboard with system statistics"""
    # Partner accounts only ever see their own slice of the user base.
    scoped_users = filter_users_by_partner_scope(User.objects.all(), request.user)

    total_users = scoped_users.count()
    active_users = scoped_users.filter(is_active=True).count()
    admin_users = scoped_users.filter(is_superuser=True).count()
    staff_users = scoped_users.filter(is_staff=True, is_superuser=False).count()

    context = {
        'page_title': _('Management Dashboard'),
        'page_subtitle': _('System overview and statistics'),
        'total_users': total_users,
        'active_users': active_users,
        'admin_users': admin_users,
        'staff_users': staff_users,
    }

    return render(request, 'management/dashboard.html', context)


def get_user_role_display(user):
    """Get display name for user's role based on their groups"""
    if user.is_superuser:
        return "Admin"

    # Check user's groups
    user_groups = user.groups.values_list('name', flat=True)

    if 'admin' in user_groups:
        return "Admin"
    elif 'cohortmanager' in user_groups:
        return "Cohort Manager"
    elif 'mentor' in user_groups:
        return "Mentor"
    elif 'startupadmin' in user_groups:
        return "Startup Admin"
    elif 'startupuser' in user_groups:
        return "Startup User"
    elif user.is_staff:
        return "Staff"
    else:
        return "User"


def _apply_password_form_attrs(form):
    for name, field in form.fields.items():
        field.widget.attrs.update({"class": "form-control"})
        if "password" in name:
            field.widget.attrs.setdefault("autocomplete", "new-password")

    if "old_password" in form.fields:
        form.fields["old_password"].widget.attrs["autocomplete"] = "current-password"


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_password_form_attrs(self)


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_password_form_attrs(self)


def _password_form_error_payload(form):
    errors = {}
    messages_list = []

    for field, field_errors in form.errors.items():
        field_messages = [str(error) for error in field_errors]
        if field == "__all__":
            messages_list.extend(field_messages)
        else:
            errors[field] = field_messages

    return errors, messages_list


def _request_wants_json(request):
    accept = request.headers.get("Accept", "")
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in accept


def _can_change_managed_user_password(actor, target_user):
    actor_role = get_user_role(actor)

    if actor_role == "admin":
        return True

    if actor_role == "manager" and actor.id != target_user.id:
        return get_user_role(target_user) != "admin"

    return False


class UserForm(forms.ModelForm):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('cohortmanager', 'Cohort Manager'),
        ('startupadmin', 'Startup Admin'),
        ('startupuser', 'Startup User'),
        ('mentor', 'Mentor'),
    ]
    
    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        label="Select Role",
        required=True
    )
    survey_access = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        label="Project Access",
        help_text="Select projects this user should have access to"
    )
    survey_access_restricted = forms.BooleanField(
        required=False,
        label="Restrict to assigned projects only",
        help_text=(
            "When checked, this account sees only the projects selected above, "
            "even if their role is Admin, Manager or Supervisor."
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "is_active", "role"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Import here to avoid circular imports
        from survey.models import Survey, SurveyInvitation
        self.fields["survey_access"].queryset = Survey.objects.filter(is_active=True).order_by('-created_at')
        
        # Set initial role value based on user's current groups
        if self.instance and self.instance.pk:
            user_groups = self.instance.groups.all()
            if user_groups:
                # Set role based on the first group (assuming one role per user)
                self.initial['role'] = user_groups.first().name
            
            # Set initial survey access
            self.initial['survey_access'] = Survey.objects.filter(
                id__in=SurveyInvitation.objects.filter(user=self.instance).values('survey_id')
            )

            try:
                self.initial['survey_access_restricted'] = self.instance.profile.survey_access_restricted
            except ObjectDoesNotExist:
                self.initial['survey_access_restricted'] = False

class UserCreateForm(UserCreationForm):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('cohortmanager', 'Cohort Manager'),
        ('startupadmin', 'Startup Admin'),
        ('startupuser', 'Startup User'),
        ('mentor', 'Mentor'),
    ]
    
    email = forms.EmailField(required=True, widget=forms.EmailInput(attrs={"class": "form-control"}))
    first_name = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    last_name = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs={"class": "form-control"}))
    role = forms.ChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        label="Select Role",
        required=True
    )
    survey_access = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        label="Project Access",
        help_text="Select projects this user should have access to"
    )
    survey_access_restricted = forms.BooleanField(
        required=False,
        label="Restrict to assigned projects only",
        help_text=(
            "When checked, this account sees only the projects selected above, "
            "even if their role is Admin, Manager or Supervisor."
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = User
        fields = ("username", "email", "first_name", "last_name", "password1", "password2", "role")
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].widget.attrs.update({"class": "form-control"})
        self.fields["password2"].widget.attrs.update({"class": "form-control"})
        # Import here to avoid circular imports
        from survey.models import Survey
        self.fields["survey_access"].queryset = Survey.objects.filter(is_active=True).order_by('-created_at')


@login_required
def user_list(request):
    search_query = request.GET.get("search", "")
    scoped_users = filter_users_by_partner_scope(User.objects.all(), request.user)
    users = scoped_users

    if search_query:
        users = users.filter(
            Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
        )

    users = users.order_by("-date_joined")

    paginator = Paginator(users, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # Add role information for each user
    for user in page_obj:
        user.user_role = get_user_role_display(user)

    context = {
        "page_obj": page_obj,
        "search_query": search_query,
        "total_users": scoped_users.count(),
        "page_title": _("User Management"),
        "page_subtitle": _("Manage system users and their permissions"),
        "page_actions": [
            {
                "text": _("Add User"),
                "url": reverse("management:user_create"),
                "icon": "fa fa-plus",
                "class": "text-primary-foreground bg-primary hover:bg-primary/80",
            }
        ],
    }

    return render(request, "management/user_list.html", context)


@login_required
def user_create(request):
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            
            # Handle role assignment
            selected_role = form.cleaned_data.get('role')
            if selected_role:
                # Remove user from all groups first
                user.groups.clear()
                
                # Create the group if it doesn't exist and add user to it
                group, created = Group.objects.get_or_create(name=selected_role)
                user.groups.add(group)
                
                # Set is_staff based on role
                if selected_role == 'admin':
                    user.is_staff = True
                    user.is_superuser = True
                elif selected_role in ['cohortmanager', 'mentor']:
                    user.is_staff = True
                    user.is_superuser = False
                else:
                    user.is_staff = False
                    user.is_superuser = False
                
                user.save()

            profile, _created = UserProfile.objects.get_or_create(user=user)
            profile.survey_access_restricted = bool(form.cleaned_data.get('survey_access_restricted'))
            profile.save(update_fields=['survey_access_restricted'])

            # Partner admins/managers create users inside their own partner so
            # the new account never lands outside their visibility scope.
            assign_partner_group_if_needed(request.user, user)

            # Handle survey assignment
            selected_surveys = form.cleaned_data.get('survey_access')
            if selected_surveys:
                from survey.models import SurveyInvitation
                for survey in selected_surveys:
                    SurveyInvitation.objects.create(
                        survey=survey,
                        user=user,
                        invited_by=request.user
                    )
            clear_partner_scope_cache(user)
            
            messages.success(request, f'User "{user.username}" created successfully with role "{selected_role}".')
            return redirect("management:user_detail", user_id=user.id)
        else:
            messages.error(request, "Please correct the errors below and try again.")
    else:
        form = UserCreateForm()

    context = {
        "form": form,
        "page_title": _("Create New User"),
        "page_subtitle": _("Add a new user to the system"),
        "back_url": reverse("management:user_list"),
    }
    return render(request, "management/user_create.html", context)


@login_required
def user_detail(request, user_id):
    user = get_object_or_404(User, id=user_id)
    enforce_partner_user_scope(request.user, user)

    # Handle DELETE request
    if request.method == "POST" and request.POST.get('action') == 'delete':
        if user == request.user:
            messages.error(request, "You cannot delete your own account.")
            return JsonResponse(
                {'success': False, 'message': 'You cannot delete your own account.'},
                status=400,
            )
        username = user.username
        user.delete()
        messages.success(request, f'User "{username}" deleted successfully.')
        return JsonResponse({'success': True, 'message': f'User "{username}" deleted successfully.', 'redirect': reverse('management:user_list')})

    # Ensure user has a profile
    profile, created = UserProfile.objects.get_or_create(user=user)

    # Check if user is a mentor
    is_mentor = user.groups.filter(name='mentor').exists()

    context = {
        "user_obj": user,
        "is_mentor": is_mentor,
        "page_title": user.get_full_name() or user.username,
        "page_subtitle": _(f"User details for @{user.username}"),
        "page_actions": [],
    }

    return render(request, "management/user_detail.html", context)


@login_required
def user_edit(request, user_id):
    user = get_object_or_404(User, id=user_id)
    enforce_partner_user_scope(request.user, user)
    can_change_user_password = _can_change_managed_user_password(request.user, user)

    if request.method == "POST" and request.POST.get("action") == "change_password":
        form = UserForm(instance=user)

        if not can_change_user_password:
            messages.error(
                request,
                "Only admins can change admin passwords. Managers can change non-admin user passwords.",
            )
            return redirect("management:user_edit", user_id=user.id)

        password_form = StyledSetPasswordForm(user, request.POST)
        if password_form.is_valid():
            changed_user = password_form.save()
            if changed_user.id == request.user.id:
                update_session_auth_hash(request, changed_user)

            messages.success(request, f'Password changed successfully for "{user.username}".')
            return redirect("management:user_edit", user_id=user.id)

        messages.error(request, "Please correct the password errors below and try again.")
    elif request.method == "POST":
        form = UserForm(request.POST, instance=user)
        password_form = StyledSetPasswordForm(user)
        if form.is_valid():
            form.save()

            # These are wiped by groups.clear() below, so capture them first:
            # a partner user must stay inside their partner after any edit.
            preserved_partner_groups = list(user.groups.filter(name__in=get_partner_group_names()))
            
            # Handle role assignment
            selected_role = form.cleaned_data.get('role')
            if selected_role:
                # Remove user from all groups first
                user.groups.clear()
                
                # Create the group if it doesn't exist and add user to it
                group, created = Group.objects.get_or_create(name=selected_role)
                user.groups.add(group)
                
                # Set is_staff based on role
                if selected_role == 'admin':
                    user.is_staff = True
                    user.is_superuser = True
                elif selected_role in ['cohortmanager', 'mentor']:
                    user.is_staff = True
                    user.is_superuser = False
                else:
                    user.is_staff = False
                    user.is_superuser = False
                
                user.save()

            if preserved_partner_groups:
                user.groups.add(*preserved_partner_groups)
            assign_partner_group_if_needed(request.user, user)
            clear_partner_scope_cache(user)

            profile, _created = UserProfile.objects.get_or_create(user=user)
            profile.survey_access_restricted = bool(form.cleaned_data.get('survey_access_restricted'))
            profile.save(update_fields=['survey_access_restricted'])
            
            # Handle survey assignment
            selected_surveys = form.cleaned_data.get('survey_access')
            from survey.models import SurveyInvitation
            
            if selected_surveys is not None:
                # Remove surveys that are no longer selected
                SurveyInvitation.objects.filter(user=user).exclude(survey__in=selected_surveys).delete()
                
                # Add new survey invitations
                current_survey_ids = list(SurveyInvitation.objects.filter(user=user).values_list('survey_id', flat=True))
                for survey in selected_surveys:
                    if survey.id not in current_survey_ids:
                        SurveyInvitation.objects.create(
                            survey=survey,
                            user=user,
                            invited_by=request.user
                        )
            # Assignments changed the auto-restriction result, so drop the cache
            # again now that the invitations are final.
            clear_partner_scope_cache(user)
            
            messages.success(request, f'User "{user.username}" updated successfully with role "{selected_role}".')
            return redirect("management:user_detail", user_id=user.id)
        else:
            messages.error(request, "Please correct the errors below and try again.")
    else:
        form = UserForm(instance=user)
        password_form = StyledSetPasswordForm(user)

    context = {
        "form": form,
        "password_form": password_form,
        "user_obj": user,
        "can_change_user_password": can_change_user_password,
        "page_title": _(f"Edit {user.get_full_name() or user.username}"),
        "page_subtitle": _(f"Update user information for @{user.username}"),
    }
    return render(request, "management/user_edit.html", context)




@login_required
@require_POST
def upload_profile_image(request, user_id):
    user = get_object_or_404(User, id=user_id)
    enforce_partner_user_scope(request.user, user)

    if "profile_image" not in request.FILES:
        return JsonResponse({"error": "No image file provided"}, status=400)

    image_file = request.FILES["profile_image"]

    # Validate file type
    if not image_file.content_type.startswith("image/"):
        return JsonResponse({"error": "File must be an image"}, status=400)

    # Validate file size (5MB limit)
    if image_file.size > 5 * 1024 * 1024:
        return JsonResponse({"error": "Image file too large (max 5MB)"}, status=400)

    # Get or create user profile
    profile, created = UserProfile.objects.get_or_create(user=user)

    # Delete old profile image if it exists
    if profile.profile_image:
        profile.profile_image.delete(save=False)

    # Save new profile image
    profile.profile_image = image_file
    profile.save()

    messages.success(request, "Profile image updated successfully.")
    return JsonResponse({"success": True, "image_url": profile.profile_image.url if profile.profile_image else None})


@login_required
@require_POST
def upload_cover_image(request, user_id):
    user = get_object_or_404(User, id=user_id)
    enforce_partner_user_scope(request.user, user)

    if "cover_image" not in request.FILES:
        return JsonResponse({"error": "No image file provided"}, status=400)

    image_file = request.FILES["cover_image"]

    # Validate file type
    if not image_file.content_type.startswith("image/"):
        return JsonResponse({"error": "File must be an image"}, status=400)

    # Validate file size (10MB limit for cover images)
    if image_file.size > 10 * 1024 * 1024:
        return JsonResponse({"error": "Image file too large (max 10MB)"}, status=400)

    # Get or create user profile
    profile, created = UserProfile.objects.get_or_create(user=user)

    # Delete old cover image if it exists
    if profile.cover_image:
        profile.cover_image.delete(save=False)

    # Save new cover image
    profile.cover_image = image_file
    profile.save()

    messages.success(request, "Cover image updated successfully.")
    return JsonResponse({"success": True, "image_url": profile.cover_image.url if profile.cover_image else None})


@login_required
def settings_view(request):
    """Account settings for the signed-in user."""
    wants_json = _request_wants_json(request)

    if request.method == "POST":
        password_form = StyledPasswordChangeForm(request.user, request.POST)
        if password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            if wants_json:
                return JsonResponse({"status": "success", "message": "Your password has been changed successfully."})

            messages.success(request, "Your password has been changed successfully.")
            return redirect(request.path)

        if wants_json:
            errors, non_field_errors = _password_form_error_payload(password_form)
            return JsonResponse(
                {
                    "status": "error",
                    "message": non_field_errors[0] if non_field_errors else "Please correct the password errors below.",
                    "errors": errors,
                    "non_field_errors": non_field_errors,
                },
                status=400,
            )

        messages.error(request, "Please correct the password errors below and try again.")
    else:
        password_form = StyledPasswordChangeForm(request.user)

    return render(
        request,
        "management/settings.html",
        {
            "password_form": password_form,
            "page_title": _("Settings"),
            "page_subtitle": _("Change your account password"),
        },
    )
