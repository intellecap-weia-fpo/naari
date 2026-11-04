from django_components import Component, register
from django.utils.translation import gettext_lazy as _


@register("breadcrumb")
class Breadcrumb(Component):
    template = """
    {% if breadcrumbs %}
    <nav class="w-full overflow-hidden" aria-label="Breadcrumb">
      <ol class="flex items-center text-sm max-w-full overflow-hidden whitespace-nowrap">
        {% for crumb in breadcrumbs %}
          <li class="flex items-center min-w-0">
            {% if not forloop.first %}
              <i class="fa fa-chevron-right text-[10px] text-muted-foreground/60 mx-1.5"></i>
            {% endif %}
            {% if crumb.url and not forloop.last %}
              <a href="{{ crumb.url }}" class="block text-card-foreground/70 hover:text-card-foreground transition-colors truncate max-w-[10rem] sm:max-w-[16rem]">
                {{ crumb.title }}
              </a>
            {% else %}
              <span class="block text-card-foreground font-semibold truncate max-w-[12rem] sm:max-w-[20rem]">{{ crumb.title }}</span>
            {% endif %}
          </li>
        {% endfor %}
      </ol>
    </nav>
    {% endif %}
    """

    def get_template_data(self, args, kwargs, slots, context):
        current_path = kwargs.get("path") or context.get("request").path
        breadcrumbs = self.generate_breadcrumbs(current_path, context)

        return {
            "breadcrumbs": breadcrumbs,
            "current_path": current_path,
        }

    def _humanize_path_part(self, part):
        translation_map = {
            "home": _("Dashboard"),
            "create": _("Create"),
            "edit": _("Edit"),
            "delete": _("Delete"),
            "preview": _("Preview"),
            "clone": _("Clone"),
            "submissions": _("Submissions"),
            "submissions-indicators": _("Submission Indicators"),
            "indicators": _("Indicators"),
            "import-export": _("Import/Export"),
            "user-management": _("User Management"),
            "report": _("Reports"),
            "reports": _("Reports"),
            "reports-v2": _("Reports"),
            "page": _("Page"),
            "question": _("Question"),
            "indicator": _("Indicator"),
        }
        return translation_map.get(part, _(part.replace("-", " ").replace("_", " ").title()))

    def _get_survey_title(self, survey_id):
        try:
            from survey.models import Survey

            survey = Survey.objects.only("title").filter(pk=survey_id).first()
            if survey and survey.title:
                return survey.title
        except Exception:
            pass
        return _("Project %(id)s") % {"id": survey_id}

    def _generate_survey_id_breadcrumbs(self, path_parts, context):
        breadcrumbs = []
        survey_id = path_parts[0]
        survey_title = self._get_survey_title(survey_id)
        survey_url = f"/survey/{survey_id}/"

        if len(path_parts) == 1:
            breadcrumbs.append({"title": survey_title, "url": None})
            return breadcrumbs

        breadcrumbs.append({"title": survey_title, "url": survey_url})

        action = path_parts[1]
        action_title = self._humanize_path_part(action)

        if len(path_parts) == 2:
            breadcrumbs.append({"title": action_title, "url": None})
            return breadcrumbs

        action_url = f"{survey_url}{action}/"
        breadcrumbs.append({"title": action_title, "url": action_url})

        for idx, segment in enumerate(path_parts[2:]):
            is_last = idx == len(path_parts[2:]) - 1
            # Use page title for detail pages when provided by the view.
            if segment.isdigit() and idx == 0 and context.get("page_title"):
                title = context.get("page_title")
            else:
                title = self._humanize_path_part(segment)
            breadcrumbs.append({"title": title, "url": None if is_last else f"{action_url}{segment}/"})

        return breadcrumbs

    def generate_breadcrumbs(self, current_path, context):
        """Generate breadcrumbs dynamically based on current path and app configuration"""

        # For single-level URLs like /cohorts/, /courses/, /users/, only show the title
        path_parts = [part for part in current_path.strip("/").split("/") if part]
        if len(path_parts) == 1:
            part = path_parts[0]
            
            # Handle specific path part translations
            translation_map = {
                "manage": _("Manage"),
                "certificate-templates": _("Certificate Templates"),
                "certificates": _("Certificates"),
                "users": _("Users"),
                "settings": _("Settings"),
                "cohorts": _("Cohorts"),
                "courses": _("Courses"),
                "discussions": _("Discussions"),
                "startups": _("Startups"),
                "mentors": _("Mentor Management"),
                "create": _("Create"),
                "edit": _("Edit"),
                "delete": _("Delete"),
                "preview": _("Preview"),
                "duplicate": _("Duplicate"),
                "survey": _("Projects"),
                "surveys": _("Projects"),
                "submissions": _("Submissions"),
                "reports": _("Reports"),
                "reports-v2": _("Reports"),
                "home": _("Dashboard"),
            }
            
            title = translation_map.get(part, _(part.replace("-", " ").replace("_", " ").title()))
            return [{"title": title, "url": None}]

        # Import app configuration from Django settings
        from django.conf import settings

        # Get apps configuration
        apps_config = getattr(settings, "APPS", [])

        # Find matching app by URL
        for app in apps_config:
            app_url = app.get("url", "")
            if current_path.startswith(app_url) and app_url != "/":  # Don't match root for everything
                root_title = _("Projects") if app_url == "/survey/" else app["name"]
                # Start breadcrumbs with app root
                breadcrumbs = [{"title": root_title, "url": app_url if current_path != app_url else None}]

                # Generate dynamic breadcrumbs for routes within the app
                dynamic_breadcrumbs = self.generate_dynamic_breadcrumbs(current_path, app_url, context)
                breadcrumbs.extend(dynamic_breadcrumbs)

                return breadcrumbs

        # Fallback to path-based breadcrumbs if no app match
        return self.generate_fallback_breadcrumbs(current_path, context)

    def generate_fallback_breadcrumbs(self, current_path, context):
        """Generate basic breadcrumbs when no app match is found"""
        path_parts = [part for part in current_path.strip("/").split("/") if part]

        if not path_parts:
            return []

        # For single-level URLs like /cohorts/, only show the title without parent links
        if len(path_parts) == 1:
            part = path_parts[0]
            
            # Handle specific path part translations
            translation_map = {
                "manage": _("Manage"),
                "certificate-templates": _("Certificate Templates"),
                "certificates": _("Certificates"),
                "users": _("Users"),
                "settings": _("Settings"),
                "cohorts": _("Cohorts"),
                "courses": _("Courses"),
                "discussions": _("Discussions"),
                "startups": _("Startups"),
                "mentors": _("Mentor Management"),
                "create": _("Create"),
                "edit": _("Edit"),
                "delete": _("Delete"),
                "preview": _("Preview"),
                "duplicate": _("Duplicate"),
                "survey": _("Projects"),
                "surveys": _("Projects"),
                "submissions": _("Submissions"),
                "reports": _("Reports"),
                "reports-v2": _("Reports"),
                "home": _("Dashboard"),
            }
            
            title = translation_map.get(part, _(part.replace("-", " ").replace("_", " ").title()))
            return [{"title": title, "url": None}]

        # For multi-level URLs, generate full breadcrumb trail
        breadcrumbs = []
        current_url = "/"

        for i, part in enumerate(path_parts):
            current_url += part + "/"
            is_last = i == len(path_parts) - 1

            # Handle specific path part translations
            translation_map = {
                "manage": _("Manage"),
                "certificate-templates": _("Certificate Templates"),
                "certificates": _("Certificates"),
                "users": _("Users"),
                "settings": _("Settings"),
                "cohorts": _("Cohorts"),
                "courses": _("Courses"),
                "discussions": _("Discussions"),
                "startups": _("Startups"),
                "mentors": _("Mentor Management"),
                "create": _("Create"),
                "edit": _("Edit"),
                "delete": _("Delete"),
                "preview": _("Preview"),
                "duplicate": _("Duplicate"),
                "survey": _("Projects"),
                "surveys": _("Projects"),
                "submissions": _("Submissions"),
                "reports": _("Reports"),
                "reports-v2": _("Reports"),
                "home": _("Dashboard"),
            }

            title = translation_map.get(part, _(part.replace("-", " ").replace("_", " ").title()))
            breadcrumbs.append({"title": title, "url": current_url if not is_last else None})

        return breadcrumbs

    def generate_dynamic_breadcrumbs(self, current_path, app_prefix, context):
        """Generate breadcrumbs for dynamic routes not explicitly in the menu"""
        breadcrumbs = []

        # Remove app prefix to get relative path
        relative_path = current_path[len(app_prefix) :].strip("/")
        path_parts = [part for part in relative_path.split("/") if part]

        if not path_parts:
            return breadcrumbs

        # Survey app uses project-specific breadcrumb labels for ID routes.
        if app_prefix == "/survey/" and path_parts[0].isdigit():
            return self._generate_survey_id_breadcrumbs(path_parts, context)

        # Handle common patterns dynamically
        if len(path_parts) >= 1:
            # Resource-based routes (e.g., users, settings)
            resource = path_parts[0]
            resource_title = resource.replace("_", " ").title()

            # Default behavior for all apps/resources
            if len(path_parts) == 1:
                # Handle specific resource translations
                resource_translation_map = {
                    "certificate-templates": _("Certificate Templates"),
                    "users": _("Users"),
                    "settings": _("Settings"),
                    "cohorts": _("Cohorts"),
                    "courses": _("Courses"),
                    "startups": _("Startups"),
                    "mentors": _("Mentor Management"),
                    "surveys": _("Projects"),
                    "submissions": _("Submissions"),
                    "reports": _("Reports"),
                    "reports-v2": _("Reports"),
                    "home": _("Dashboard"),
                    "create": _("Create Project"),
                    "report": _("Reports"),
                    "user-management": _("User Management"),
                    "import-export": _("Import/Export"),
                    "page": _("Page"),
                    "question": _("Question"),
                    "indicator": _("Indicator"),
                }
                
                translated_title = resource_translation_map.get(resource, _(resource_title))
                # /manage/users/ -> Users (single title, no parent breadcrumb)
                breadcrumbs.append({"title": translated_title, "url": None})
            elif len(path_parts) >= 2:
                # Handle specific resource translations for multi-level breadcrumbs too
                resource_translation_map = {
                    "certificate-templates": _("Certificate Templates"),
                    "users": _("Users"),
                    "settings": _("Settings"),
                    "cohorts": _("Cohorts"),
                    "courses": _("Courses"),
                    "startups": _("Startups"),
                    "mentors": _("Mentor Management"),
                    "surveys": _("Projects"),
                    "submissions": _("Submissions"),
                    "reports": _("Reports"),
                    "reports-v2": _("Reports"),
                    "home": _("Dashboard"),
                    "create": _("Create Project"),
                    "report": _("Reports"),
                    "user-management": _("User Management"),
                    "import-export": _("Import/Export"),
                    "page": _("Page"),
                    "question": _("Question"),
                    "indicator": _("Indicator"),
                }
                
                translated_title = resource_translation_map.get(resource, _(resource_title))
                # Add the resource as a clickable breadcrumb
                breadcrumbs.append({"title": translated_title, "url": f"{app_prefix}{resource}/"})

                action_or_id = path_parts[1]

                if action_or_id == "create":
                    # /manage/users/create/ -> Create User
                    # Handle special cases for create actions
                    if resource == "certificate-templates":
                        breadcrumbs.append({"title": _("Create Certificate Template"), "url": None})
                    else:
                        breadcrumbs.append({"title": _("Create %(resource)s") % {"resource": resource_title.rstrip('s')}, "url": None})
                elif action_or_id.isdigit():
                    # /manage/users/123/ -> User Details
                    # Get more context from the view if available
                    page_title = context.get("page_title")
                    if page_title:
                        detail_title = page_title
                    else:
                        detail_title = _("%(resource)s Details") % {"resource": resource_title.rstrip('s')}

                    if len(path_parts) == 2:
                        breadcrumbs.append({"title": detail_title, "url": None})
                    elif len(path_parts) >= 3:
                        # Add detail page as clickable
                        breadcrumbs.append({"title": detail_title, "url": f"{app_prefix}{resource}/{action_or_id}/"})

                        # Handle sub-actions like edit, delete
                        sub_action = path_parts[2]
                        if sub_action == "edit":
                            if resource == "certificate-templates":
                                breadcrumbs.append({"title": _("Edit Certificate Template"), "url": None})
                            else:
                                breadcrumbs.append({"title": _("Edit %(resource)s") % {"resource": resource_title.rstrip('s')}, "url": None})
                        elif sub_action == "delete":
                            if resource == "certificate-templates":
                                breadcrumbs.append({"title": _("Delete Certificate Template"), "url": None})
                            else:
                                breadcrumbs.append({"title": _("Delete %(resource)s") % {"resource": resource_title.rstrip('s')}, "url": None})
                        else:
                            breadcrumbs.append({"title": _(sub_action.replace("_", " ").title()), "url": None})
                else:
                    # Other actions
                    breadcrumbs.append({"title": _(action_or_id.replace("_", " ").title()), "url": None})

        return breadcrumbs
