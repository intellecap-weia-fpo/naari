from django.db import connection
from django_tenants.utils import get_public_schema_name
from django.db import models
from django.contrib.auth.models import User
from django.urls import reverse
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
import uuid
from django.utils import timezone
from datetime import timedelta


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    profile_image = models.ImageField(upload_to="profile_images/", null=True, blank=True)
    cover_image = models.ImageField(upload_to="cover_images/", null=True, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    website = models.URLField(blank=True)
    location = models.CharField(max_length=100, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    preferred_language = models.CharField(
        max_length=10,
        choices=settings.LANGUAGES,
        default=settings.LANGUAGE_CODE,
        help_text="User's preferred language for the interface"
    )
    # Email verification fields
    email_verified = models.BooleanField(default=False)
    email_verification_token = models.UUIDField(default=uuid.uuid4, editable=False)
    email_verification_sent_at = models.DateTimeField(null=True, blank=True)
    # When set, this account only sees surveys it is explicitly assigned to,
    # even if its role (admin/manager/supervisor/viewer) normally grants all
    # surveys. Off by default so existing accounts keep their current access.
    survey_access_restricted = models.BooleanField(
        default=False,
        help_text=(
            "Limit this account to the projects explicitly assigned to it, "
            "instead of every project its role normally grants."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"

    def get_absolute_url(self):
        return reverse("management:user_detail", kwargs={"user_id": self.user.id})

    @property
    def has_profile_image(self):
        return bool(self.profile_image)

    @property
    def has_cover_image(self):
        return bool(self.cover_image)
    
    def generate_verification_token(self):
        """Generate new verification token"""
        self.email_verification_token = uuid.uuid4()
        self.email_verification_sent_at = timezone.now()
        self.save()
        return self.email_verification_token
    
    def is_verification_token_expired(self):
        """Check if verification token is expired (24 hours)"""
        if not self.email_verification_sent_at:
            return True
        expiry_time = self.email_verification_sent_at + timedelta(hours=24)
        return timezone.now() > expiry_time
    
    def verify_email(self):
        """Mark email as verified"""
        self.email_verified = True
        self.email_verification_token = uuid.uuid4()  # Invalidate token
        self.save()


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    # guardian creates its anonymous user during the first shared migration, and
    # the public schema has no profile table.
    if connection.schema_name == get_public_schema_name():
        return
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if connection.schema_name == get_public_schema_name():
        return
    if hasattr(instance, "profile"):
        instance.profile.save()
    else:
        UserProfile.objects.create(user=instance)
