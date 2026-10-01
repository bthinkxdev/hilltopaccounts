from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
urlpatterns = [path('admin/', admin.site.urls), path('accounts/', include('apps.accounts.urls')), path('businesses/', include('apps.businesses.urls')), path('', include('apps.villas.urls')), path('tenants/', include('apps.tenancy.urls')), path('billing/', include('apps.billing.urls')), path('expenses/', include('apps.expenses.urls')), path('cash/', include('apps.cash.urls')), path('audit/', include('apps.audit.urls')), path('notifications/', include('apps.notifications.urls')), path('', include('apps.dashboard.urls'))]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
handler400 = 'config.views.bad_request'
handler403 = 'config.views.permission_denied'
handler404 = 'config.views.page_not_found'
handler500 = 'config.views.server_error'
