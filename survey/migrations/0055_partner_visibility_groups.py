from django.conf import settings
from django.db import migrations
from django.db.models import Q

def _partner_rules():
    return list(getattr(settings, "PARTNER_VISIBILITY_RULES", None) or [])


def _rule_group_name(rule):
    return (rule.get("group_name") or "").strip()


def _marker_query(rule):
    query = Q()
    has_criteria = False
    for marker in rule.get("username_markers") or []:
        if marker:
            query |= Q(username__icontains=marker)
            has_criteria = True
    for domain in rule.get("email_domains") or []:
        if domain:
            query |= Q(email__icontains=domain)
            has_criteria = True
    return query if has_criteria else None


def create_partner_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    for rule in _partner_rules():
        group_name = _rule_group_name(rule)
        if not group_name:
            continue
        group = Group.objects.filter(name__iexact=group_name).first()
        if group is None:
            group = Group.objects.create(name=group_name)

        query = _marker_query(rule)
        if query is None:
            continue
        matching_users = User.objects.filter(query).distinct()
        group.user_set.add(*matching_users)


def remove_partner_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for rule in _partner_rules():
        group_name = _rule_group_name(rule)
        if group_name:
            Group.objects.filter(name__iexact=group_name).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("survey", "0054_submission_app_version"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_partner_groups, remove_partner_groups),
    ]
