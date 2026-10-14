from __future__ import annotations

import re

from django.db import models
from django.utils.text import slugify

LOCALES = ("en", "ru", "zh", "es", "fr", "de", "ar")
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def pick_locale(raw: str | None) -> str:
    locale = (raw or "en").strip().lower()
    return locale if locale in LOCALES else "en"


def localized_text(mapping, locale: str, *, fallback: str = "") -> str:
    data = mapping if isinstance(mapping, dict) else {}
    value = data.get(locale) or data.get("en") or fallback
    return str(value).strip() if value else ""


class ProductCategory(models.Model):
    title = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    titles = models.JSONField(default=dict, blank=True)
    descriptions = models.JSONField(default=dict, blank=True)
    href = models.CharField(max_length=500, blank=True, default="#contact")
    image_path = models.CharField(max_length=512, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.title

    @classmethod
    def unique_slug(cls, base: str, *, exclude_id: int | None = None) -> str:
        slug = slugify(base) or "category"
        candidate = slug
        n = 2
        qs = cls.objects.all()
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        while qs.filter(slug=candidate).exists():
            candidate = f"{slug}-{n}"
            n += 1
        return candidate

    def save(self, *args, **kwargs):
        titles = dict(self.titles) if isinstance(self.titles, dict) else {}
        if self.title and not (titles.get("en") or "").strip():
            titles["en"] = self.title
        en = (titles.get("en") or self.title or "").strip()
        if en:
            self.title = en[:120]
            titles["en"] = en[:120]
        if titles.get("en") and not (titles.get("ru") or "").strip():
            titles["ru"] = titles["en"]
        self.titles = titles
        if not isinstance(self.descriptions, dict):
            self.descriptions = {}
        if not self.slug:
            self.slug = self.unique_slug(en or "category", exclude_id=self.pk)
        super().save(*args, **kwargs)


class Product(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_CHOICES = (
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
    )

    category = models.ForeignKey(ProductCategory, on_delete=models.CASCADE, related_name="products")
    sku = models.CharField(max_length=80, blank=True, default="")
    slug = models.SlugField(max_length=140)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    titles = models.JSONField(default=dict, blank=True)
    descriptions = models.JSONField(default=dict, blank=True)
    origin = models.CharField(max_length=160, blank=True, default="")
    size = models.CharField(max_length=160, blank=True, default="")
    moq = models.CharField(max_length=160, blank=True, default="")
    packing = models.CharField(max_length=160, blank=True, default="")
    lead_time = models.CharField(max_length=160, blank=True, default="")
    specs = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["category", "slug"], name="catalog_product_category_slug_uniq"),
            models.UniqueConstraint(
                fields=["category", "sku"],
                condition=~models.Q(sku=""),
                name="catalog_product_category_sku_uniq",
            ),
        ]

    def __str__(self) -> str:
        return localized_text(self.titles, "en") or self.slug

    def unique_slug(self, base: str, *, exclude_id: int | None = None) -> str:
        slug = slugify(base) or "product"
        candidate = slug
        n = 2
        qs = Product.objects.filter(category_id=self.category_id)
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        while qs.filter(slug=candidate).exists():
            candidate = f"{slug}-{n}"
            n += 1
        return candidate

    def save(self, *args, **kwargs):
        titles = dict(self.titles) if isinstance(self.titles, dict) else {}
        en = (titles.get("en") or "").strip()
        if en:
            titles["en"] = en[:120]
        if titles.get("en") and not (titles.get("ru") or "").strip():
            titles["ru"] = titles["en"]
        self.titles = titles
        if not isinstance(self.descriptions, dict):
            self.descriptions = {}
        self.sku = (self.sku or "").strip()
        if not self.slug:
            self.slug = self.unique_slug(en or "product", exclude_id=self.pk)
        super().save(*args, **kwargs)


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image_path = models.CharField(max_length=512)
    alt = models.CharField(max_length=200, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.image_path

