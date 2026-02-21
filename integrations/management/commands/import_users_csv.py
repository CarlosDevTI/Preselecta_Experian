import csv
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from integrations.models import UserAccessProfile


def _to_bool(raw: str, default: bool = False) -> bool:
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value == "":
        return default
    return value in {"1", "true", "t", "yes", "y", "si", "s"}


class Command(BaseCommand):
    help = "Importa usuarios y perfiles de acceso desde CSV."

    REQUIRED_COLUMNS = {
        "username",
        "password",
        "first_name",
        "last_name",
        "email",
        "area",
        "agency",
        "can_view_rejected_history",
        "is_active",
    }

    AREA_ALIASES = {
        "ADMIN_AUDITORIA": UserAccessProfile.AREA_ADMINISTRATIVO,
        "ADMINISTRATIVO": UserAccessProfile.AREA_ADMINISTRATIVO,
        "AGENCIA": UserAccessProfile.AREA_AGENCIA,
        "TALENTO_HUMANO": UserAccessProfile.AREA_TALENTO_HUMANO,
        "CARTERA": UserAccessProfile.AREA_CARTERA,
    }

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str, help="Ruta al CSV de usuarios")
        parser.add_argument("--dry-run", action="store_true", help="Valida sin guardar cambios")

    def handle(self, *args, **options):
        csv_path = Path(options["csv_path"]).expanduser()
        dry_run = options["dry_run"]

        if not csv_path.exists():
            raise CommandError(f"No existe el archivo CSV: {csv_path}")

        User = get_user_model()
        created = 0
        updated = 0
        errors = 0

        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                raise CommandError("El CSV no tiene encabezados.")

            columns = {c.strip() for c in reader.fieldnames if c}
            missing = self.REQUIRED_COLUMNS - columns
            if missing:
                raise CommandError(f"Faltan columnas requeridas: {', '.join(sorted(missing))}")

            with transaction.atomic():
                for idx, row in enumerate(reader, start=2):
                    try:
                        username = (row.get("username") or "").strip()
                        password = (row.get("password") or "").strip()
                        if not username:
                            raise ValueError("username vacio")
                        if not password:
                            raise ValueError("password vacio")

                        area_raw = (row.get("area") or "").strip().upper()
                        area = self.AREA_ALIASES.get(area_raw, area_raw)
                        valid_areas = {key for key, _ in UserAccessProfile.AREA_CHOICES}
                        if area not in valid_areas:
                            raise ValueError(f"area invalida: {area_raw}")

                        agency = (row.get("agency") or "").strip()
                        first_name = (row.get("first_name") or "").strip()
                        last_name = (row.get("last_name") or "").strip()
                        email = (row.get("email") or "").strip()
                        is_active = _to_bool(row.get("is_active"), default=True)
                        can_view_rejected = _to_bool(row.get("can_view_rejected_history"), default=False)
                        can_choose_place = _to_bool(row.get("can_choose_place"), default=False)
                        must_change_value = row.get("must_change_password")

                        user, was_created = User.objects.get_or_create(
                            username=username,
                            defaults={
                                "first_name": first_name,
                                "last_name": last_name,
                                "email": email,
                                "is_active": is_active,
                            },
                        )
                        user.first_name = first_name
                        user.last_name = last_name
                        user.email = email
                        user.is_active = is_active
                        user.set_password(password)
                        user.save()

                        profile, profile_created = UserAccessProfile.objects.get_or_create(
                            user=user,
                            defaults={
                                "area": area,
                                "agency": agency,
                                "can_choose_place": can_choose_place,
                                "can_view_rejected_history": can_view_rejected,
                                "is_active": is_active,
                                "must_change_password": True,
                            },
                        )

                        profile.area = area
                        profile.agency = agency
                        profile.can_choose_place = can_choose_place
                        profile.can_view_rejected_history = can_view_rejected
                        profile.is_active = is_active
                        if must_change_value is not None and str(must_change_value).strip() != "":
                            profile.must_change_password = _to_bool(must_change_value, default=True)
                        elif profile_created:
                            profile.must_change_password = True
                        profile.save()

                        if was_created:
                            created += 1
                        else:
                            updated += 1

                    except Exception as exc:  # noqa: BLE001
                        errors += 1
                        self.stderr.write(f"[fila {idx}] Error: {exc}")

                if dry_run:
                    transaction.set_rollback(True)

        mode = "DRY-RUN" if dry_run else "APLICADO"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: creados={created}, actualizados={updated}, errores={errors}"
            )
        )

