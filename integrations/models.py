from django.db import models


class AccessLog(models.Model):
    """Registro de cada consulta con metadatos del dispositivo."""

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ip_address or 'unknown'} @ {self.created_at}"
