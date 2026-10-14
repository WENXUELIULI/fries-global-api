from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Inquiry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("company", models.CharField(blank=True, default="", max_length=160)),
                ("country", models.CharField(max_length=120)),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                ("whatsapp", models.CharField(blank=True, default="", max_length=40)),
                ("category", models.CharField(max_length=160)),
                ("specs", models.TextField()),
                ("locale", models.CharField(blank=True, default="en", max_length=8)),
                ("attachment_path", models.CharField(blank=True, default="", max_length=512)),
                ("email_sent", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
