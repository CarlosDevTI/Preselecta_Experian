import base64
import os
import requests
from django.core.cache import cache


class PreselectaClient:
    """
    Encapsulates:
      - Retrieving and caching the access_token from Okta.
      - Calling the business service using that token.
    """

    def __init__(self):
        #* ENDPOINTS Y CREDENCIALES
        self.token_url = os.environ["OKTA_TOKEN_URL"]
        self.client_id = os.environ["OKTA_CLIENT_ID"]
        self.client_secret = os.environ["OKTA_CLIENT_SECRET"]

        #* PARAMETROS DE AUTENTICACIÓN
        self.username = os.environ.get("OKTA_USERNAME", "")
        self.password = os.environ.get("OKTA_PASSWORD", "")

        #* CONFIGURACIONES
        self.scope = os.environ["OKTA_SCOPE"]
        self.service_url = os.environ["SERVICE_URL"]

        #* COMO ENVIAR EL TOKEN AL SERVICIO: "access_token" (custom header) o "bearer"
        self.auth_style = os.environ.get("PRESELECTA_AUTH_STYLE", "access_token").lower()

        #* OKTA GRANT TYPE: "PASSWORD" (POR DEFECTO) O "CLIENT_CREDENTIALS"
        self.grant_type = os.environ.get("OKTA_GRANT_TYPE", "password").lower()

        #* VERIFICACIÓN SSL (PERMITE DESACTIVAR SOLO PARA DEPURACIÓN LOCAL/DEV)
        self.verify_ssl = os.environ.get("PRESELECTA_VERIFY_SSL", "True").lower() == "true"

    def _basic_header(self) -> str:
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode() #* ENCODE BASIC AUTH PARA PRUEBAS
        return f"Basic {basic}"

    def get_access_token(self) -> str:
        """
        1) Return cached token if present
        2) Otherwise request a new one from Okta
        3) Cache it for expires_in - 60 seconds
        """
        cache_key = f"preselecta_access_token_{self.client_id}_{self.grant_type}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        headers = {
            "Authorization": self._basic_header(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        #* CONSTRUIR EL CUERPO SEGÚN EL TIPO DE GRANT CONFIGURADO
        if self.grant_type == "client_credentials":
            data = {
                "grant_type": "client_credentials",
                "scope": self.scope,
            }
        else:
            #* PEDIR CONTRASEÑAS DE USUARIO
            #* PENSANDO EN PONER OKTA_GRANT_TYPE="client_credentials" SI SIGUE SIN FUNCIONAR
            data = {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
                "scope": self.scope,
            }

        # --- INICIO DE LOG PARA PROVEEDOR ---
        print("--- REQUEST ---")
        print(f"URL: POST {self.token_url}")
        print(f"HEADERS: {headers}")
        print(f"BODY: {data}")
        print("---------------")

        try:
            resp = requests.post(
                self.token_url, headers=headers, data=data, timeout=15, verify=self.verify_ssl
            )
            print("--- RESPONSE ---")
            print(f"STATUS_CODE: {resp.status_code}")
            print(f"HEADERS: {resp.headers}")
            print(f"BODY: {resp.text}")
            print("----------------")
            # --- FIN DE LOG PARA PROVEEDOR ---
            resp.raise_for_status()
            body = resp.json()
        except requests.exceptions.RequestException as e:
            print("--- RESPONSE (ERROR) ---")
            if e.response is not None:
                print(f"STATUS_CODE: {e.response.status_code}")
                print(f"HEADERS: {e.response.headers}")
                print(f"BODY: {e.response.text}")
            else:
                print(f"Exception: {e}")
            print("------------------------")
            # --- FIN DE LOG PARA PROVEEDOR ---
            raise e
        token = body.get("access_token")
        if not token:
            raise RuntimeError("Okta did not return access_token")

        expires_in = int(body.get("expires_in", 3600))
        cache.set(cache_key, token, timeout=max(60, expires_in - 60))
        return token

    def call_decision(self, payload: dict) -> dict:
        token = self.get_access_token()

        headers = {
            "Content-Type": "application/json",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        #* ENVIAR TOKEN SEGUN EL TIPO DE AUTENTICACIÓN CONFIGURADO
        if self.auth_style == "bearer":
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["access_token"] = token

        resp = requests.post(
            self.service_url, headers=headers, json=payload, timeout=20, verify=self.verify_ssl
        )
        resp.raise_for_status()
        return resp.json()
