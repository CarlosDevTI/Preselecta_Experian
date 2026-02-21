from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0012_consentotp_admin_observation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="accesslog",
            name="requested_by_agency",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="accesslog",
            name="requested_by_area",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="accesslog",
            name="requested_by_username",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="requested_by_agency",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="requested_by_area",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="requested_by_username",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="preselectaquery",
            name="requested_by_agency",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="preselectaquery",
            name="requested_by_area",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="preselectaquery",
            name="requested_by_username",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.CreateModel(
            name="UserAccessProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("area", models.CharField(choices=[("AGENCIA", "Agencia"), ("ADMINISTRATIVO", "Administrativo"), ("TALENTO_HUMANO", "Talento Humano"), ("CARTERA", "Cartera")], default="AGENCIA", max_length=40)),
                ("agency", models.CharField(blank=True, max_length=120)),
                ("can_choose_place", models.BooleanField(default=False)),
                ("can_view_rejected_history", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="access_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["user__username"]},
        ),
    ]
