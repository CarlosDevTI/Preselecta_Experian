from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0016_useraccessprofile_must_change_password"),
    ]

    operations = [
        migrations.CreateModel(
            name="PreselectaAttemptException",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("id_number", models.CharField(max_length=50)),
                ("id_type", models.CharField(blank=True, default="", max_length=10)),
                ("month_start", models.DateField(help_text="Primer dia del mes al que aplica la excepcion.")),
                ("is_active", models.BooleanField(default=True)),
                ("used", models.BooleanField(default=False)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("granted_by_username", models.CharField(blank=True, max_length=150)),
                ("consumed_by_username", models.CharField(blank=True, max_length=150)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="preselectaattemptexception",
            constraint=models.UniqueConstraint(fields=("id_number", "id_type", "month_start"), name="uniq_preselecta_exception_person_month"),
        ),
    ]
