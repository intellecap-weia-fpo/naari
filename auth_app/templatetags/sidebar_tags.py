from django import template
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from auth_app.permissions import get_user_role, get_sidebar_menu_for_role, can_access_url

register = template.Library()

# Define your apps with URL patterns and menus
SIDEBAR_APPS = {
    "/settings": {
        "name": _("Dashboard"),
        "url_prefix": "/settings/",
        "menu_items": [
            {"name": _("Home"), "url": "/", "icon_class": "fa fa-home", "is_active": False},
            {"name": _("Settings"), "url": "/settings/", "icon_class": "fa fa-cog"},
        ],
    },
    "/manage/": {
        "name": _("Manage"),
        "url_prefix": "/manage/",
        "menu_items": [
            {"name": _("Dashboard"), "url": "/manage/", "icon_class": "fa fa-home", "is_active": False},
            {
                "name": _("Users"),
                "icon_class": "fa fa-user",
                "children": [
                    {"name": _("All Users"), "url": "/manage/users/", "icon_class": "fa fa-user"},
                    {"name": _("Add User"), "url": "/manage/users/create/", "icon_class": "fa fa-plus"},
                ],
                "default_open": True,
            },
            {"name": _("Settings"), "url": "/manage/settings/", "icon_class": "fa fa-cog"},
        ],
    },
    "/admin/": {
        "name": _("Django Administration"),
        "url_prefix": "/admin/",
        "menu_items": [
            {"name": _("Dashboard"), "url": "/admin/", "icon_class": "fa fa-home", "is_active": False},
            {
                "name": _("Users"),
                "icon_class": "fa fa-user",
                "children": [
                    {"name": _("All Users"), "url": "/admin/auth/user/", "icon_class": "fa fa-user"},
                    {"name": _("Add User"), "url": "/admin/auth/user/add/", "icon_class": "fa fa-plus"},
                    {"name": _("Groups"), "url": "/admin/auth/group/", "icon_class": "fa fa-users"},
                ],
                "default_open": True,
            },
            {"name": _("Settings"), "url": "/admin/neo/", "icon_class": "fa fa-cog"},
        ],
    },
    "/app/": {
        "name": _("App Management"),
        "url_prefix": "/app/",
        "menu_items": [
            {"name": _("Dashboard"), "url": "/app/", "icon_class": "fa fa-home", "is_active": False},
            {
                "name": _("Users"),
                "icon_class": "fa fa-user",
                "children": [
                    {"name": _("All Users"), "url": "/app/users/", "icon_class": "fa fa-user"},
                    {"name": _("Add User"), "url": "/app/users/add/", "icon_class": "fa fa-plus"},
                    {"name": _("User Roles"), "url": "/app/users/roles/"},
                ],
                "default_open": True,
            },
            {"name": _("Settings"), "url": "/app/settings/", "icon_class": "fa fa-cog"},
        ],
    },
    "/survey/": {
        "name": _("Survey Management"),
        "url_prefix": "/survey/",
        "menu_items": [
            {"name": _("Home"), "url": "/survey/home/", "icon_class": "fa fa-home", "is_active": False},
            {"name": _("Surveys"), "url": "/survey/", "icon_class": "fa fa-list-alt", "is_active": False},
            {"name": _("Submissions"), "url": "/survey/submissions/", "icon_class": "fa fa-inbox", "is_active": False},
            {"name": _("Reports"), "url": "/survey/report/", "icon_class": "fa fa-chart-line", "is_active": False},
        ],
    },
    # Cohort and FPO apps removed
}


@register.filter
def get_user_role(user):
    """
    Template filter to get the user's role
    """
    from auth_app.permissions import get_user_role as get_role
    return get_role(user)


@register.simple_tag(takes_context=True)
def url_match_app(context, current_path):
    """
    Match current URL path to an app and return the app configuration
    """
    request = context.get("request")
    user = request.user if request else None

    # Sort by prefix length to match most specific first
    sorted_apps = sorted(SIDEBAR_APPS.items(), key=lambda x: len(x[0]), reverse=True)
    for url_prefix, app_config in sorted_apps:
        if current_path.startswith(url_prefix):
            # Mark active menu items and filter based on permissions
            app_config = app_config.copy()  # Don't modify the original
            app_config["menu_items"] = filter_menu_items_by_permission(
                mark_active_items(app_config["menu_items"], current_path), user, url_prefix
            )
            return app_config

    return None


def filter_menu_items_by_permission(menu_items, user, url_prefix):
    """
    Filter menu items based on user permissions
    """
    if not user or not user.is_authenticated:
        return menu_items

    filtered_items = []
    for item in menu_items:
        item_copy = item.copy()

        # Check if this is the Administration section in cohort app
        if url_prefix == "/" and item.get("name") == _("Administration") and not user.is_staff:
            # Skip Administration section for non-staff users
            continue

        # Filter children if they exist
        if "children" in item_copy:
            filtered_children = []
            for child in item_copy["children"]:
                child_copy = child.copy()
                # Add any child-specific filtering logic here if needed
                filtered_children.append(child_copy)
            item_copy["children"] = filtered_children

        filtered_items.append(item_copy)

    return filtered_items


def mark_active_items(menu_items, current_path):
    """
    Mark menu items as active based on current path
    """
    items = []
    for item in menu_items:
        item_copy = item.copy()

        # Check if this item has a URL and matches current path
        if "url" in item_copy:
            item_copy["is_active"] = current_path == item_copy["url"] or current_path.startswith(item_copy["url"] + "/")

        # Check children if they exist
        if "children" in item_copy:
            children = []
            has_active_child = False
            for child in item_copy["children"]:
                child_copy = child.copy()
                if "url" in child_copy:
                    child_copy["is_active"] = current_path == child_copy["url"] or current_path.startswith(
                        child_copy["url"] + "/"
                    )
                    if child_copy["is_active"]:
                        has_active_child = True
                        item_copy["default_open"] = True  # Open parent if child is active
                children.append(child_copy)
            item_copy["children"] = children

        items.append(item_copy)

    return items


@register.simple_tag
def generate_breadcrumbs(current_path):
    """
    Generate breadcrumbs based on the current path and sidebar configuration
    """
    print(f"DEBUG BREADCRUMBS: Function called with path '{current_path}'")

    # Simple hardcoded test first
    if current_path.startswith("/manage/"):
        if "/users/" in current_path:
            if current_path.endswith("/create/"):
                return [
                    {"title": _("Manage"), "url": "/manage/"},
                    {"title": _("Users"), "url": "/manage/users/"},
                    {"title": _("Create User"), "url": None},
                ]
            elif "/edit/" in current_path:
                return [
                    {"title": _("Manage"), "url": "/manage/"},
                    {"title": _("Users"), "url": "/manage/users/"},
                    {"title": _("Edit User"), "url": None},
                ]
            elif "/delete/" in current_path:
                return [
                    {"title": _("Manage"), "url": "/manage/"},
                    {"title": _("Users"), "url": "/manage/users/"},
                    {"title": _("Delete User"), "url": None},
                ]
            elif current_path == "/manage/users/":
                return [{"title": _("Manage"), "url": "/manage/"}, {"title": _("Users"), "url": None}]
            else:
                # User detail page
                return [
                    {"title": _("Manage"), "url": "/manage/"},
                    {"title": _("Users"), "url": "/manage/users/"},
                    {"title": _("User Details"), "url": None},
                ]
        else:
            return [{"title": _("Manage"), "url": None}]

    return []


def generate_dynamic_breadcrumbs(current_path, app_prefix):
    """
    Generate breadcrumbs for dynamic routes (e.g., /manage/users/123/edit/)
    """
    breadcrumbs = []

    # Remove app prefix to get relative path
    relative_path = current_path[len(app_prefix) :]
    path_parts = [part for part in relative_path.split("/") if part]

    # Handle common patterns
    if len(path_parts) >= 1:
        if path_parts[0] == "users":
            breadcrumbs.append({"title": _("Users"), "url": f"{app_prefix}users/" if len(path_parts) > 1 else None})

            if len(path_parts) >= 2:
                if path_parts[1] == "create":
                    breadcrumbs.append({"title": _("Create User"), "url": None})
                elif path_parts[1].isdigit():  # User ID
                    user_id = path_parts[1]
                    breadcrumbs.append(
                        {
                            "title": _("User Detail"),
                            "url": f"{app_prefix}users/{user_id}/" if len(path_parts) > 2 else None,
                        }
                    )

                    if len(path_parts) >= 3:
                        if path_parts[2] == "edit":
                            breadcrumbs.append({"title": _("Edit User"), "url": None})
                        elif path_parts[2] == "delete":
                            breadcrumbs.append({"title": _("Delete User"), "url": None})

    return breadcrumbs


def find_breadcrumb_path(menu_items, current_path, path_so_far):
    """
    Recursively find the breadcrumb path for the current URL
    """
    for item in menu_items:
        # Check if this item matches the current path
        if "url" in item and (current_path == item["url"] or current_path.startswith(item["url"] + "/")):
            new_path = path_so_far + [
                {"title": item["name"], "url": item["url"] if current_path != item["url"] else None}
            ]

            # If this is an exact match, return the path
            if current_path == item["url"]:
                return new_path

            # If we have children, search them too
            if "children" in item:
                child_path = find_breadcrumb_path(item["children"], current_path, new_path)
                if child_path:
                    return child_path

            # If current path starts with this URL, it might be a dynamic route
            return new_path

        # Check children even if parent doesn't match URL
        if "children" in item:
            # Check if any child matches
            for child in item["children"]:
                if "url" in child and (current_path == child["url"] or current_path.startswith(child["url"] + "/")):
                    parent_crumb = {"title": item["name"], "url": None}  # Parent groups usually don't have URLs
                    child_crumb = {
                        "title": child["name"],
                        "url": child["url"] if current_path != child["url"] else None,
                    }

                    result = path_so_far + [parent_crumb, child_crumb]

                    # If exact match, return
                    if current_path == child["url"]:
                        return result

                    # If current path starts with this URL, it might be a dynamic route
                    return result

    return []


@register.simple_tag
def get_page_title_from_breadcrumbs(current_path):
    """
    Get the page title from breadcrumbs (last item)
    """
    breadcrumbs = generate_breadcrumbs(current_path)
    if breadcrumbs:
        return breadcrumbs[-1]["title"]
    return None


@register.simple_tag
def test_breadcrumb():
    """
    Simple test function to check if template tags are working
    """
    print("DEBUG: Test breadcrumb function called!")
    return [{"title": _("Manage"), "url": "/manage/"}, {"title": _("Test Page"), "url": None}]
