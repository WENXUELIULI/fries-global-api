from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_category_slug_i18n"),
    ]

    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sku", models.CharField(blank=True, default="", max_length=80)),
                ("slug", models.SlugField(max_length=140)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("published", "Published")], db_index=True, default="draft", max_length=16)),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=0)),
                ("titles", models.JSONField(blank=True, default=dict)),
                ("descriptions", models.JSONField(blank=True, default=dict)),
                ("origin", models.CharField(blank=True, default="", max_length=160)),
                ("size", models.CharField(blank=True, default="", max_length=160)),
                ("moq", models.CharField(blank=True, default="", max_length=160)),
                ("packing", models.CharField(blank=True, default="", max_length=160)),
                ("lead_time", models.CharField(blank=True, default="", max_length=160)),
                ("specs", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="products", to="catalog.productcategory")),
            ],
        ),
        migrations.CreateModel(
            name="ProductImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("image_path", models.CharField(max_length=512)),
                ("alt", models.CharField(blank=True, default="", max_length=200)),
                ("sort_order", models.PositiveIntegerField(db_index=True, default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="images", to="catalog.product")),
            ],
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(fields=("category", "slug"), name="catalog_product_category_slug_uniq"),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                condition=models.Q(("sku", ""), _negated=True),
                fields=("category", "sku"),
                name="catalog_product_category_sku_uniq",
            ),
        ),
    ]
