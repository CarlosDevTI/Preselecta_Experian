from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm


class PreselectaAuthenticationForm(AuthenticationForm):
    """
    Formulario de login del modulo Preselecta con mensajes en espanol.
    """

    error_messages = {
        "invalid_login": "Usuario o contrasena incorrectos. Verifica mayusculas/minusculas.",
        "inactive": "Tu usuario esta inactivo. Contacta a TI.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)
        self.fields["username"].label = "Usuario"
        self.fields["password"].label = "Contrasena"
        self.fields["username"].error_messages["required"] = "El usuario es obligatorio."
        self.fields["password"].error_messages["required"] = "La contrasena es obligatoria."


class PreselectaPasswordChangeForm(PasswordChangeForm):
    """
    Cambio de contrasena con mensajes en espanol para forzar primer acceso seguro.
    """

    error_messages = {
        "password_incorrect": "La contrasena actual es incorrecta.",
        "password_mismatch": "Las nuevas contrasenas no coinciden.",
    }

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields["old_password"].label = "Contrasena actual"
        self.fields["new_password1"].label = "Nueva contrasena"
        self.fields["new_password2"].label = "Confirmar nueva contrasena"
        self.fields["old_password"].error_messages["required"] = "La contrasena actual es obligatoria."
        self.fields["new_password1"].error_messages["required"] = "La nueva contrasena es obligatoria."
        self.fields["new_password2"].error_messages["required"] = "Debes confirmar la nueva contrasena."
