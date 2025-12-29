from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from .serializers import DecisionPayloadSerializer
from integrations.services.preselecta import PreselectaClient
import requests
import logging

logger = logging.getLogger(__name__)

class DecisionView(APIView):
    """
    Orquesta el flujo:
        1 - Valida el payload de entrada con DRF Serializer
        2 - Obtiene token (cacheado) y llama al servicio externo
        3 - Devuelve al cliente la respuesta tal cual (o un error controlado)
    """
    def post(self, request):
        print("--- DecisionView: Petición POST recibida ---")
        print("--- DecisionView: Datos del request recibido ---", request.data)
        #? Validación de entrada (lanza 400 con detalle si falla)
        ser = DecisionPayloadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        print("--- DecisionView: Payload validado correctamente ---")

        client = PreselectaClient()
        try:
            # print(f"--- DecisionView: Llamando a call_decision con: {ser.validated_data} ---")
            #? Llama al proveedor con el payload validado
            data = client.call_decision(ser.validated_data)
            print("--- DecisionView: Llamada a call_decision exitosa ---")
            return Response(data, status=status.HTTP_200_OK)
        except requests.HTTPError as e:
            print(f"--- DecisionView: ERROR - requests.HTTPError: {e} ---")
            #? Muestra detalle del proveedor si viene en JSON/texto
            try:
                detail = e.response.json()
                print(f"--- DecisionView: Detalle del error (JSON): {detail} ---")
            except Exception:
                detail = {"detail": e.response.text}
                print(f"--- DecisionView: Detalle del error (texto): {detail} ---")
            return Response(detail, status=e.response.status_code)
        except Exception as e:
            print(f"--- DecisionView: ERROR - Excepción general: {e} ---")
            logger.exception("Error no esperado en DecisionView")
            #* Errores no previstos (DNS, timeout, bugs, etc.)
            return Response({"detail": str(e)}, status=500)
