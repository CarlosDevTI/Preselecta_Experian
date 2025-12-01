from django.db import models


class AccessLog(models.Model):
    """Registro de cada consulta con metadatos del dispositivo."""

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    forwarded_for = models.TextField(blank=True, help_text="Cabecera X-Forwarded-For completa")
    real_ip = models.GenericIPAddressField(null=True, blank=True, help_text="Cabecera X-Real-IP si llega")
    remote_addr = models.GenericIPAddressField(null=True, blank=True, help_text="IP de entrada al contenedor")
    user_agent = models.TextField(blank=True)
    consulted_id_number = models.CharField(max_length=50, blank=True)
    consulted_name = models.CharField(max_length=200, blank=True)
    response_full_name = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ip_address or 'unknown'} @ {self.created_at}"
