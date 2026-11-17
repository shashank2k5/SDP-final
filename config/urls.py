from django.contrib import admin
from django.urls import include, path

from tracker.views import dashboard_view

admin.site.site_header = "LedgerLite Administration"
admin.site.site_title = "LedgerLite Admin"
admin.site.index_title = "Data management"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("api/auth/", include("accounts.api_urls")),
    path("api/", include("tracker.urls")),
    path("", dashboard_view, name="dashboard"),
]
