from django.urls import path
from . import views

app_name = 'integrations'

urlpatterns = [
    path("login/", views.PreselectaLoginView.as_view(), name="login"),
    path("logout/", views.PreselectaLogoutView.as_view(), name="logout"),
    path("cambiar-contrasena/", views.PreselectaChangePasswordView.as_view(), name="change_password"),
    path('', views.ConsultaView.as_view(), name='consulta'),
    path("historial-pago/", views.HistorialPagoView.as_view(), name="historial_pago"),
    path("admin-auditoria/", views.AdminAuditoriaListView.as_view(), name="admin_auditoria_list"),
    path("admin-auditoria/<int:consent_id>/", views.AdminAuditoriaDetailView.as_view(), name="admin_auditoria_detail"),
]
