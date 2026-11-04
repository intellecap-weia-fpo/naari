from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.utils.html import escape

from .models import Client


@staff_member_required
def tenant_list(request):
    rows = []
    for tenant in Client.objects.prefetch_related("domains").all():
        domains = ", ".join(domain.domain for domain in tenant.domains.all()) or "-"
        status = "active" if tenant.is_active else "inactive"
        rows.append(
            "<tr>"
            f"<td>{escape(tenant.name)}</td>"
            f"<td><code>{escape(tenant.schema_name)}</code></td>"
            f"<td>{escape(domains)}</td>"
            f"<td>{status}</td>"
            "</tr>"
        )

    body = "\n".join(rows) or '<tr><td colspan="4">No tenants yet.</td></tr>'
    return HttpResponse(
        f"""
        <!doctype html>
        <html>
        <head>
          <title>Tenants</title>
          <style>
            body {{ font-family: system-ui, sans-serif; margin: 32px; }}
            table {{ border-collapse: collapse; width: 100%; max-width: 1000px; }}
            th, td {{ border-bottom: 1px solid #ddd; padding: 10px; text-align: left; }}
            th {{ background: #f6f7f8; }}
            a {{ color: #0b5cad; }}
          </style>
        </head>
        <body>
          <h1>Tenants</h1>
          <p><a href="/admin/customers/client/add/">Create tenant</a> · <a href="/admin/">Admin</a></p>
          <table>
            <thead><tr><th>Name</th><th>Schema</th><th>Domains</th><th>Status</th></tr></thead>
            <tbody>{body}</tbody>
          </table>
        </body>
        </html>
        """
    )
