"""Tests for POST /api/login input validation and error handling."""

import json
import pytest
from unittest.mock import patch, MagicMock
from dashboardserver import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test-secret'
    with app.test_client() as c:
        yield c


def post_login(client, payload=None, raw=None, content_type='application/json'):
    """Helper to POST to /api/login."""
    if raw is not None:
        return client.post('/api/login', data=raw, content_type=content_type)
    return client.post(
        '/api/login',
        data=json.dumps(payload),
        content_type=content_type,
    )


# ── Malformed JSON ──────────────────────────────────────────────────────

class TestMalformedJSON:
    def test_malformed_json_returns_400(self, client):
        resp = post_login(client, raw='{bad json}')
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['success'] is False
        assert 'Invalid JSON' in data['error']


# ── Missing / empty fields ──────────────────────────────────────────────

class TestMissingFields:
    def test_empty_object_returns_400(self, client):
        resp = post_login(client, payload={})
        assert resp.status_code == 400

    def test_missing_password_returns_400(self, client):
        resp = post_login(client, payload={'email': 'a@b.com'})
        assert resp.status_code == 400

    def test_missing_email_returns_400(self, client):
        resp = post_login(client, payload={'password': 'secret'})
        assert resp.status_code == 400

    def test_empty_email_returns_400(self, client):
        resp = post_login(client, payload={'email': '', 'password': 'secret'})
        assert resp.status_code == 400

    def test_whitespace_email_returns_400(self, client):
        resp = post_login(client, payload={'email': '   ', 'password': 'secret'})
        assert resp.status_code == 400

    def test_empty_password_returns_400(self, client):
        resp = post_login(client, payload={'email': 'a@b.com', 'password': ''})
        assert resp.status_code == 400


# ── Non-string types (the bug) ─────────────────────────────────────────

class TestNonStringTypes:
    """Non-string email/password values must return 400, never 503."""

    @pytest.mark.parametrize('email', [None, 123, False, True, [], {}, 0, 1.5])
    def test_non_string_email_returns_400(self, client, email):
        resp = post_login(client, payload={'email': email, 'password': 'test123'})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['success'] is False
        assert 'required' in data['error'].lower()

    @pytest.mark.parametrize('password', [None, 123, False, True, [], {}, 0])
    def test_non_string_password_returns_400(self, client, password):
        resp = post_login(client, payload={'email': 'a@b.com', 'password': password})
        assert resp.status_code == 400
        data = resp.get_json()
        assert data['success'] is False

    def test_both_non_string_returns_400(self, client):
        resp = post_login(client, payload={'email': 123, 'password': True})
        assert resp.status_code == 400


# ── Invalid credentials ────────────────────────────────────────────────

class TestInvalidCredentials:
    def test_wrong_credentials_returns_401(self, client):
        resp = post_login(client, payload={
            'email': 'wrong@example.com',
            'password': 'wrongpassword',
        })
        assert resp.status_code == 401
        data = resp.get_json()
        assert data['success'] is False
        assert 'Invalid email or password' in data['error']


# ── XSS / long strings (safe handling) ─────────────────────────────────

class TestSafeHandling:
    def test_xss_in_email_does_not_crash(self, client):
        resp = post_login(client, payload={
            'email': '<script>alert(1)</script>',
            'password': 'test123',
        })
        assert resp.status_code in (400, 401)

    def test_long_email_does_not_crash(self, client):
        resp = post_login(client, payload={
            'email': 'a' * 10000 + '@example.com',
            'password': 'test123',
        })
        assert resp.status_code in (400, 401)
