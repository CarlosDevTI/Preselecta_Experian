from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0015_otpchallenge_blocked_until_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="useraccessprofile",
            name="must_change_password",
            field=models.BooleanField(
                default=False,
                help_text="Si esta activo, el usuario debe actualizar su contrasena antes de usar el modulo.",
            ),
        ),
    ]

