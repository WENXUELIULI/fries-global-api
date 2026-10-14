from django.db import migrations, models
from django.utils.text import slugify


def fill_category_i18n(apps, schema_editor):
    ProductCategory = apps.get_model("catalog", "ProductCategory")
    used: set[str] = set()
    for category in ProductCategory.objects.order_by("id"):
        base = slugify(category.title) or "category"
        slug = base
        n = 2
        while slug in used:
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        category.slug = slug
        category.titles = {"en": category.title, "ru": category.title}
        category.descriptions = {}
        category.save(update_fields=["slug", "titles", "descriptions"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_productcategory_href"),
    ]

    operations = [
        migrations.AddField(
            model_name="productcategory",
            name="slug",
            field=models.SlugField(blank=True, default="", max_length=140),
        ),
        migrations.AddField(
            model_name="productcategory",
            name="titles",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="productcategory",
            name="descriptions",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(fill_category_i18n, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="productcategory",
            name="slug",
            field=models.SlugField(max_length=140, unique=True),
        ),
    ]
