import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.authx.services import ADMIN_COOKIE, create_admin_session

ORIGIN = {"HTTP_ORIGIN": "http://127.0.0.1:3000", "HTTP_HOST": "127.0.0.1:3000"}


def _payload(**overrides):
    body = {
        "name": "Alex Petrov",
        "company": "Silk Road Trade",
        "country": "Uzbekistan",
        "email": "alex@example.com",
        "whatsapp": "",
        "category": "Valves & Pipes",
        "specs": "DN150 gate valve, PN16, 200 pcs",
        "locale": "en",
        "website": "",
    }
    body.update(overrides)
    return body


def _post(client, payload, **headers):
    return client.post(
        "/api/inquiries",
        data=json.dumps(payload),
        content_type="application/json",
        **{**ORIGIN, **headers},
    )


def test_inquiry_requires_name(db, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    response = _post(Client(), _payload(name=""))
    assert response.status_code == 400
    assert response.json()["error"] == "name"


def test_inquiry_email_only_is_accepted(db, settings, mailoutbox):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from apps.inquiries.models import Inquiry

    response = _post(Client(), _payload(email="buyer@example.com", whatsapp=""))
    assert response.status_code == 201
    assert Inquiry.objects.count() == 1
    assert mailoutbox[0].subject == "[Website Inquiry] Valves & Pipes – Uzbekistan – Silk Road Trade"


def test_inquiry_whatsapp_only_is_accepted(db, settings, mailoutbox):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from apps.inquiries.models import Inquiry

    response = _post(Client(), _payload(email="", whatsapp="+998901234567"))
    assert response.status_code == 201
    assert Inquiry.objects.get().whatsapp == "+998901234567"
    assert mailoutbox


def test_inquiry_requires_email_or_whatsapp(db, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    response = _post(Client(), _payload(email="", whatsapp=""))
    assert response.status_code == 400
    assert response.json()["error"] == "contact"


def test_inquiry_honeypot_is_ignored(db, settings, mailoutbox):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from apps.inquiries.models import Inquiry

    response = _post(Client(), _payload(website="http://spam.test"))
    assert response.status_code == 201
    assert Inquiry.objects.count() == 0
    assert mailoutbox == []


def test_inquiry_turnstile_required_in_production(db, settings):
    settings.DEBUG = False
    settings.CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:3000"]
    settings.TURNSTILE_SECRET_KEY = "test-secret"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    response = _post(Client(), _payload())
    assert response.status_code == 403


def test_admin_inquiries_list_and_csv(db, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from apps.inquiries.models import Inquiry

    Inquiry.objects.create(
        name="Alex Petrov",
        company="Silk Road Trade",
        country="Uzbekistan",
        email="alex@example.com",
        category="Valves & Pipes",
        specs="DN150",
        locale="en",
    )
    client = Client()
    client.cookies[ADMIN_COOKIE] = create_admin_session()
    listed = client.get("/admin/api/inquiries")
    assert listed.status_code == 200
    assert listed.json()["inquiries"][0]["name"] == "Alex Petrov"
    csv_response = client.get("/admin/api/inquiries?format=csv")
    assert csv_response.status_code == 200
    assert b"Alex Petrov" in csv_response.content


def test_inquiry_rejects_huge_attachment(db, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    huge = SimpleUploadedFile("spec.pdf", b"a" * (10 * 1024 * 1024 + 1), content_type="application/pdf")
    response = Client().post(
        "/api/inquiries",
        data={**_payload(), "attachment": huge},
        **ORIGIN,
    )
    assert response.status_code == 400
    assert response.json()["error"] == "attachment"
