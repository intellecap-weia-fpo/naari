"""Custom template filters for displaying times in Indian Standard Time (IST)"""

from django import template
from django.utils import timezone
from datetime import datetime

register = template.Library()


@register.filter
def ist_time(value):
    """Convert datetime to Indian Standard Time (IST) and format it."""
    if not value:
        return ""
    
    # Ensure the datetime is timezone-aware
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_default_timezone())
    
    # Convert to IST (Asia/Kolkata)
    ist_tz = timezone.get_fixed_timezone(330)  # IST is UTC+5:30 (330 minutes)
    ist_time = value.astimezone(ist_tz)
    
    return ist_time.strftime("%b %d, %Y %I:%M %p")


@register.filter
def ist_date(value):
    """Convert datetime to Indian Standard Time (IST) and return date only."""
    if not value:
        return ""
    
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_default_timezone())
    
    ist_tz = timezone.get_fixed_timezone(330)
    ist_time = value.astimezone(ist_tz)
    
    return ist_time.strftime("%b %d, %Y")


@register.filter
def ist_time_only(value):
    """Convert datetime to Indian Standard Time (IST) and return time only."""
    if not value:
        return ""
    
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_default_timezone())
    
    ist_tz = timezone.get_fixed_timezone(330)
    ist_time = value.astimezone(ist_tz)
    
    return ist_time.strftime("%I:%M %p")
