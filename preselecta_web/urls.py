from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import RedirectView
from django.views.static import serve as static_serve


urlpatterns = [
    path('', RedirectView.as_view(url='/preselecta/', permanent=True)),
    path('admin/', admin.site.urls),
    path('api/', include("integrations.api_urls")),
    path('api/', include("api.urls")),
    path('preselecta/', include("integrations.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    # Produccion sin Nginx dedicado para media:
    # exponemos /media de forma explicita con Django.
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", static_serve, {"document_root": settings.MEDIA_ROOT}),
    ]
