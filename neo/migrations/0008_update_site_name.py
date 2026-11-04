from django.db import migrations


def update_site_name(apps, schema_editor):
    SiteConfiguration = apps.get_model("neo", "SiteConfiguration")
    SiteConfiguration.objects.filter(site_name="NAARI Survey Admin App").update(
        site_name="NAARI Admin Panel"
    )


def revert_site_name(apps, schema_editor):
    SiteConfiguration = apps.get_model("neo", "SiteConfiguration")
    SiteConfiguration.objects.filter(
        site_name="NAARI Admin Panel"
    ).update(site_name="NAARI Survey Admin App")


class Migration(migrations.Migration):

    dependencies = [
        ("neo", "0007_siteconfiguration_enabled_apps"),
    ]

    operations = [
        migrations.RunPython(update_site_name, revert_site_name),
    ]
