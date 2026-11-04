from django.db import models
from django.contrib.auth.models import User
from solo.models import SingletonModel


class SiteConfiguration(SingletonModel):
    site_name = models.CharField(max_length=255, default="NAARI Admin Panel")
    maintenance_mode = models.BooleanField(default=False)

    # SMTP Settings
    smtp_host = models.CharField(max_length=255, blank=True, help_text="SMTP server hostname")
    smtp_port = models.PositiveIntegerField(default=587, help_text="SMTP server port")
    smtp_username = models.CharField(max_length=255, blank=True, help_text="SMTP username")
    smtp_password = models.CharField(max_length=255, blank=True, help_text="SMTP password")
    smtp_use_tls = models.BooleanField(default=True, help_text="Use TLS encryption")
    smtp_use_ssl = models.BooleanField(default=False, help_text="Use SSL encryption")
    email_from = models.EmailField(blank=True, help_text="Default 'from' email address")

    # Login Page Settings
    login_background_image = models.ImageField(
        upload_to="login_backgrounds/", blank=True, null=True, help_text="Background image for login page"
    )
    login_logo = models.ImageField(upload_to="logos/", blank=True, null=True, help_text="Logo for login page")
    login_page_title = models.CharField(max_length=255, default="Sign In", help_text="Title shown on login page")
    login_welcome_message = models.TextField(blank=True, help_text="Welcome message on login page")

    # Theme Settings
    primary_color = models.CharField(
        max_length=50, default="oklch(0.5770 0.2450 27.3250)", help_text="Primary theme color (hex)"
    )
    secondary_color = models.CharField(
        max_length=50, default="oklch(0.9702 0 0)", help_text="Secondary theme color (hex)"
    )
    theme_mode = models.CharField(
        max_length=10,
        choices=[("light", "Light"), ("dark", "Dark"), ("auto", "Auto")],
        default="light",
        help_text="Default theme mode",
    )

    # General Settings
    company_name = models.CharField(max_length=255, default="Company Name", help_text="Organization name")
    company_logo = models.ImageField(upload_to="company_logos/", blank=True, null=True, help_text="Company logo")
    favicon = models.ImageField(upload_to="favicons/", blank=True, null=True, help_text="Website favicon")
    timezone = models.CharField(max_length=50, default="UTC", help_text="Default timezone")
    date_format = models.CharField(max_length=20, default="Y-m-d", help_text="Date format")
    time_format = models.CharField(max_length=20, default="H:i", help_text="Time format")

    # Landing Page Settings
    landing_page_url = models.CharField(
        max_length=255,
        blank=True,
        help_text="Custom landing page URL (e.g., '/', '/survey/', or external URL). Leave empty to use default homepage."
    )
    redirect_authenticated_users = models.BooleanField(
        default=False,
        help_text="Redirect authenticated users to landing page instead of dashboard"
    )

    # Apps Settings
    enabled_apps = models.JSONField(
        default=list,
        blank=True,
        help_text="List of enabled app names. Leave empty to show all apps. Example: ['Admin', 'Survey', 'Cohort']"
    )

    def __str__(self):
        return "Site Configuration"

    class Meta:
        verbose_name = "Site Configuration"
        verbose_name_plural = "Site Configuration"


class RecentActivity(models.Model):
    ACTIVITY_TYPES = [
        ('user_management', 'User Management'),
        ('reports', 'Reports'),
        ('system_settings', 'System Settings'),
        ('notifications', 'Notifications'),
        ('data_import', 'Data Import'),
        ('other', 'Other'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='recent_activities')
    activity_type = models.CharField(max_length=50, choices=ACTIVITY_TYPES)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    url = models.URLField(blank=True, help_text="Link to the activity")
    icon_class = models.CharField(max_length=100, default="fa fa-activity", help_text="Font Awesome icon class")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Recent Activity"
        verbose_name_plural = "Recent Activities"

    def __str__(self):
        return f"{self.user.username} - {self.title}"
