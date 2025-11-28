import re
import unicodedata
from rest_framework import serializers

# Mapeo de tipos de identificación según la tabla del proveedor.
_DOC_TYPE_MAP = {
    "1": "1",  # Cédula de ciudadanía
    "ceduladeciudadania": "1",
    "cedula": "1",
    "cc": "1",
    "cedulaciudadania": "1",
    "2": "2",  # NIT
    "nit": "2",
    "numerodeidentificaciontributaria": "2",
    "3": "3",  # NIT de extranjería
    "nitdeextranjeria": "3",
    "nitextranjeria": "3",
    "4": "4",  # Cédula de extranjería
    "ceduladeextranjeria": "4",
    "cce": "4",
    "5": "5",  # Pasaporte
    "pasaporte": "5",
    "6": "6",  # Carné diplomático
    "carnediplomatico": "6",
}


def _normalize_doc_type(value: str) -> str:
    """Normaliza texto (sin tildes/espacios) para mapearlo a los códigos 1..6."""
    ascii_value = (
        unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]", "", ascii_value.lower())

class KeyValueSerializer(serializers.Serializer):
    #? Estructura para cada keyvalue del arreglo inquiryParameters
    key = serializers.CharField()
    value = serializers.CharField()

class InquiryParameterSerializer(serializers.Serializer):
    #? Cada parámetro de negocio que el servicio espera
    paramType = serializers.CharField()
    keyvalue = KeyValueSerializer()

class DecisionPayloadSerializer(serializers.Serializer):
    """
    Valida que el cuerpo que recibimos desde tu frontend/cliente
    cumpla el contrato esperado por el servicio externo.
    Esto evita enviar basura y recibir 400 del proveedor.
    """
    idNumber = serializers.CharField()
    idType = serializers.CharField()
    firstLastName = serializers.CharField(required=False, allow_blank=True)
    inquiryClientId = serializers.CharField()
    inquiryClientType = serializers.CharField()
    inquiryUserId = serializers.CharField()
    inquiryUserType = serializers.CharField()
    inquiryParameters = serializers.ListField(child=InquiryParameterSerializer())

    def validate(self, attrs):
        """
        Permite recibir tanto el código numérico (1..6) como el nombre del documento
        y lo traduce al código que espera el proveedor.
        """
        for field in ("idType", "inquiryClientType", "inquiryUserType"):
            raw = attrs.get(field)
            if raw is None:
                continue
            normalized = _normalize_doc_type(raw)
            mapped = _DOC_TYPE_MAP.get(normalized)
            if not mapped:
                raise serializers.ValidationError(
                    {
                        field: "Tipo de documento no soportado. Usa 1-6 o su nombre (CC, NIT, Pasaporte, etc.)."
                    }
                )
            attrs[field] = mapped
        return attrs
