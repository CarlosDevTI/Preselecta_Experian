from django.shortcuts import render
from django.views import View
import requests
import json

from .models import AccessLog


class ConsultaView(View):
    template_name = 'integrations/consulta.html'  # Ruta de la plantilla

    @staticmethod
    def _get_client_ip(request):
        """Devuelve la IP del cliente respetando X-Forwarded-For si existe."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def get(self, request, *args, **kwargs):
        # Paso 1 inicial
        return render(
            request,
            self.template_name,
            {"step": "1", "show_step2": False, "step1_data": {}, "step2_data": {}},
        )

    def post(self, request, *args, **kwargs):
        step = request.POST.get("step", "1")

        # Datos capturados en el paso 1
        id_number = (request.POST.get('id_number') or "").strip()
        id_type = (request.POST.get('id_type') or "").strip()
        first_last_name = (request.POST.get('first_last_name') or "").strip()
        step1_data = {
            "idNumber": id_number,
            "idType": id_type,
            "firstLastName": first_last_name,
        }

        # Paso 1: solo valida y muestra el siguiente paso, sin llamar al proveedor
        if step == "1":
            if not id_number or not id_type or not first_last_name:
                return render(request, self.template_name, {
                    "step": "1",
                    "show_step2": False,
                    "error_message": "Completa Tipo de identificación, Número y Primer apellido.",
                    "step1_data": step1_data,
                    "step2_data": {},
                })
            return render(request, self.template_name, {
                "step": "2",
                "show_step2": True,
                "step1_data": step1_data,
                "step2_data": {},
            })

        # Paso 2: variables adicionales de la estrategia
        linea_credito = (request.POST.get('linea_credito') or "").strip()
        tipo_asociado = (request.POST.get('tipo_asociado') or "").strip()
        medio_pago = (request.POST.get('medio_pago') or "").strip()
        actividad = (request.POST.get('actividad') or "").strip()
        step2_data = {
            "linea_credito": linea_credito,
            "tipo_asociado": tipo_asociado,
            "medio_pago": medio_pago,
            "actividad": actividad,
        }

        # Si falta algo del paso 2, no se llama al proveedor
        if not all(step2_data.values()):
            return render(request, self.template_name, {
                "step": "2",
                "show_step2": True,
                "error_message": "Completa las 4 variables de la estrategia antes de consultar.",
                "step1_data": step1_data,
                "step2_data": step2_data,
            })

        # Registro de acceso con metadatos del dispositivo
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")

        # Construye la carga útil final para PRECREDITO_CONGENTE
        payload = {
            "idNumber": id_number,
            "idType": id_type,
            "firstLastName": first_last_name,
            "inquiryClientId": "892000373",
            "inquiryClientType": "2",
            "inquiryUserId": "892000373",
            "inquiryUserType": "2",
            "inquiryParameters": [
                {"paramType": "STRAID", "keyvalue": {"key": "T", "value": "25674"}},
                {"paramType": "STRNAM", "keyvalue": {"key": "T", "value": "PRECREDITO_CONGENTE"}},
                {"paramType": "LINEA_CREDITO", "keyvalue": {"key": "T", "value": linea_credito}},
                {"paramType": "TIPO_ASOCIADO", "keyvalue": {"key": "T", "value": tipo_asociado}},
                {"paramType": "MEDIO_PAGO", "keyvalue": {"key": "T", "value": medio_pago}},
                {"paramType": "ACTIVIDAD", "keyvalue": {"key": "T", "value": actividad}},
            ]
        }

        # Llama a la API interna y maneja la respuesta
        api_url = request.build_absolute_uri('/api/decision/')
        response_data = {}
        response_pretty = None
        error_message = None
        try:
            response = requests.post(api_url, json=payload)
            response.raise_for_status()
            response_data = response.json()
            if response_data:
                response_pretty = json.dumps(response_data, indent=4, ensure_ascii=False)
        except requests.exceptions.RequestException as e:
            error_message = f"Error calling API: {e}"
            if e.response:
                try:
                    error_message += f" - {e.response.text}"
                except Exception:
                    pass

        AccessLog.objects.create(
            ip_address=self._get_client_ip(request) or None,
            forwarded_for=x_forwarded_for,
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            consulted_id_number=id_number,
            consulted_name=first_last_name,
        )

        print("DEBUG >> response_data =", response_data)
        return render(request, self.template_name, {
            'response_json': response_data if response_data else None,
            'response_pretty': response_pretty,
            'error_message': error_message,
            'submitted_data': payload,
            "step": "2",
            "show_step2": True,
            "step1_data": step1_data,
            "step2_data": step2_data,
        })
