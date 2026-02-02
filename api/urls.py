from django.urls import path

from .views import HC2SoapJuridicaPdfView, HC2SoapNaturalPdfView

urlpatterns = [
    path("datacredito/soap/hc2", HC2SoapNaturalPdfView.as_view(), name="hc2-soap"),
    path("datacredito/soap/hc2/", HC2SoapNaturalPdfView.as_view(), name="hc2-soap-trailing"),
    path("datacredito/soap/hc2pj", HC2SoapJuridicaPdfView.as_view(), name="hc2pj-soap"),
    path("datacredito/soap/hc2pj/", HC2SoapJuridicaPdfView.as_view(), name="hc2pj-soap-trailing"),
]
