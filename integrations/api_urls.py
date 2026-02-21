from django.urls import path
from integrations.api.views import DecisionView

urlpatterns = [
    # Endpoint POST para orquestar la llamada al proveedor
    path("decision/", DecisionView.as_view(), name="decision"),
]
