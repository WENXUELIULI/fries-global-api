from apps.authx.services import (
    create_admin_session,
    verify_admin_session,
    verify_credentials,
)


def test_rejects_bad_credentials(isolated_private):
    assert verify_credentials("nope", "nope") is False


def test_round_trips_session_token(isolated_private):
    token = create_admin_session()
    assert verify_admin_session(token) is True
    assert verify_admin_session("tampered." + token) is False
