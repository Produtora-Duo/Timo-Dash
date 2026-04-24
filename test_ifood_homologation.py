"""Tests for iFood homologation helpers and webhook behavior."""

import hashlib
import hmac
import json

import pytest

import dashboardserver
from ifood_homologation_evidence import build_ifood_order_evidence
from ifood_api import IFoodAPI


@pytest.fixture
def client():
    dashboardserver.app.config['TESTING'] = True
    dashboardserver.app.config['SECRET_KEY'] = 'test-secret'
    with dashboardserver.app.test_client() as c:
        yield c


def _signature(secret, body):
    return hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


def test_webhook_accepts_valid_hmac_keepalive(client):
    secret = 'dummysecret'
    old_secret = dashboardserver.IFOOD_WEBHOOK_SECRET
    dashboardserver.IFOOD_WEBHOOK_SECRET = secret
    body = b'{"code":"KEEPALIVE","fullCode":"KEEPALIVE","id":"evt-keepalive"}'
    try:
        resp = client.post(
            '/ifood/webhook',
            data=body,
            content_type='application/json',
            headers={'X-IFood-Signature': _signature(secret, body)},
        )
    finally:
        dashboardserver.IFOOD_WEBHOOK_SECRET = old_secret

    assert resp.status_code == 202
    assert resp.get_json()['message'] == 'keepalive'


def test_webhook_rejects_invalid_signature(client):
    secret = 'dummysecret'
    old_secret = dashboardserver.IFOOD_WEBHOOK_SECRET
    dashboardserver.IFOOD_WEBHOOK_SECRET = secret
    try:
        resp = client.post(
            '/ifood/webhook',
            data=b'{"code":"PLC","id":"evt-1"}',
            content_type='application/json',
            headers={'X-IFood-Signature': 'bad'},
        )
    finally:
        dashboardserver.IFOOD_WEBHOOK_SECRET = old_secret

    assert resp.status_code == 401
    assert resp.get_json()['error'] == 'invalid_signature'


def test_webhook_rejects_missing_signature(client):
    secret = 'dummysecret'
    old_secret = dashboardserver.IFOOD_WEBHOOK_SECRET
    dashboardserver.IFOOD_WEBHOOK_SECRET = secret
    try:
        resp = client.post(
            '/ifood/webhook',
            data=b'{"code":"PLC","id":"evt-1"}',
            content_type='application/json',
        )
    finally:
        dashboardserver.IFOOD_WEBHOOK_SECRET = old_secret

    assert resp.status_code == 401
    assert resp.get_json()['error'] == 'missing_signature'


def test_webhook_validates_exact_raw_body_formatting(client):
    secret = 'dummysecret'
    old_secret = dashboardserver.IFOOD_WEBHOOK_SECRET
    dashboardserver.IFOOD_WEBHOOK_SECRET = secret
    signed_body = b'{"code":"KEEPALIVE","fullCode":"KEEPALIVE","id":"evt-keepalive"}'
    changed_body = b'{ "code":"KEEPALIVE", "fullCode":"KEEPALIVE", "id":"evt-keepalive" }'
    try:
        resp = client.post(
            '/ifood/webhook',
            data=changed_body,
            content_type='application/json',
            headers={'X-IFood-Signature': _signature(secret, signed_body)},
        )
    finally:
        dashboardserver.IFOOD_WEBHOOK_SECRET = old_secret

    assert resp.status_code == 401
    assert resp.get_json()['error'] == 'invalid_signature'


def test_webhook_accepts_order_event_quickly(client):
    secret = 'dummysecret'
    old_secret = dashboardserver.IFOOD_WEBHOOK_SECRET
    dashboardserver.IFOOD_WEBHOOK_SECRET = secret
    payload = {
        'id': 'evt-placed-test',
        'code': 'PLC',
        'fullCode': 'PLACED',
        'merchantId': '00000000-0000-0000-0000-000000000001',
        'orderId': 'order-1',
        'createdAt': '2026-04-24T12:00:00Z',
    }
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    try:
        resp = client.post(
            '/api/ifood/webhook',
            data=body,
            content_type='application/json',
            headers={'X-IFood-Signature': _signature(secret, body)},
        )
    finally:
        dashboardserver.IFOOD_WEBHOOK_SECRET = old_secret

    assert resp.status_code == 202
    data = resp.get_json()
    assert data['received'] == 1
    assert data['queued'] == 1


def test_financial_methods_forward_homologation_header():
    api = IFoodAPI('client', 'secret')
    captured = {}

    def fake_request(method, endpoint, params=None, data=None, headers=None):
        captured['method'] = method
        captured['endpoint'] = endpoint
        captured['headers'] = headers
        return {}

    api._request = fake_request
    api.get_financial_sales(
        'merchant-1',
        begin_sales_date='2026-04-01',
        end_sales_date='2026-04-24',
        headers={'x-request-homologation': 'true'},
    )

    assert captured['headers'] == {'x-request-homologation': 'true'}
    assert captured['endpoint'].endswith('/merchants/merchant-1/sales')


def test_review_list_forwards_homologation_filters():
    api = IFoodAPI('client', 'secret')
    captured = {}

    def fake_request(method, endpoint, params=None, data=None, headers=None):
        captured['params'] = params
        return {}

    api._request = fake_request
    api.list_reviews(
        'merchant-1',
        page=2,
        page_size=10,
        add_count=True,
        date_from='2026-04-01T00:00:00Z',
        date_to='2026-04-24T23:59:59Z',
        status='NOT_REPLIED',
    )

    assert captured['params']['page'] == 2
    assert captured['params']['pageSize'] == 10
    assert captured['params']['addCount'] == 'true'
    assert captured['params']['dateFrom'] == '2026-04-01T00:00:00Z'
    assert captured['params']['dateTo'] == '2026-04-24T23:59:59Z'
    assert captured['params']['status'] == 'NOT_REPLIED'


def test_order_evidence_extractor_redacts_and_marks_homologation_fields():
    order = {
        'id': 'order-123',
        'merchantId': 'merchant-1',
        'displayId': '1234',
        'status': 'PLACED',
        'orderType': 'DELIVERY',
        'orderTiming': 'SCHEDULED',
        'schedule': {'startDateTime': '2026-04-24T18:00:00Z', 'endDateTime': '2026-04-24T18:30:00Z'},
        'customer': {'name': 'Cliente Teste', 'documentNumber': '12345678901'},
        'payments': {
            'methods': [
                {'method': 'CREDIT', 'brand': 'VISA', 'changeFor': 100}
            ]
        },
        'benefits': [
            {'value': 12.5, 'sponsorship': 'IFOOD', 'description': 'Cupom homologacao'}
        ],
        'items': [
            {'name': 'Pizza', 'quantity': 1, 'observations': 'Sem cebola'}
        ],
        'delivery': {
            'observations': 'Portaria lateral',
            'deliveryCode': '9876',
        },
        'pickupCode': '1234',
    }
    events = [
        {'id': 'evt-cancel', 'fullCode': 'CANCELLATION_REQUESTED', 'orderId': 'order-123'},
        {'id': 'evt-dispute', 'fullCode': 'DISPUTE_CREATED', 'orderId': 'order-123'},
    ]

    evidence = build_ifood_order_evidence(order, snapshot={'source': 'test'}, events=events)
    fields = evidence['fields']

    assert fields['scheduled_time']['status'] == 'present'
    assert fields['payment_brand']['value'] == 'VISA'
    assert fields['cash_change']['value'] == 100
    assert fields['discounts_subsidies']['status'] == 'present'
    assert fields['item_observations']['value'][0]['observation'] == 'Sem cebola'
    assert fields['customer_document']['value']['document']['masked'].endswith('8901')
    assert '12345678901' not in json.dumps(evidence)
    assert fields['pickup_code']['value']['masked'].endswith('1234')
    assert fields['delivery_code']['value']['masked'].endswith('9876')
    assert fields['delivery_observations']['value'] == 'Portaria lateral'
    assert fields['cancellation_events']['status'] == 'present'
    assert fields['dispute_events']['status'] == 'present'


def test_order_evidence_endpoint_uses_snapshot_and_redacts(monkeypatch, client):
    snapshot = {
        'org_id': 1,
        'merchant_id': 'merchant-1',
        'order_id': 'order-123',
        'source': 'polling',
        'status': 'PLACED',
        'updated_at': '2026-04-24T12:00:00',
        'payload': {
            'id': 'order-123',
            'merchantId': 'merchant-1',
            'status': 'PLACED',
            'customer': {'documentNumber': '12345678901'},
            'payments': {'methods': [{'method': 'CREDIT', 'brand': 'VISA'}]},
        },
    }
    with client.session_transaction() as sess:
        sess['user'] = {'id': 1, 'primary_org_id': 1}
        sess['org_id'] = 1
    monkeypatch.setattr(dashboardserver.db, 'is_platform_admin', lambda user_id: False)
    monkeypatch.setattr(dashboardserver.db, 'get_org_member_role', lambda org_id, user_id: 'admin')
    monkeypatch.setattr(dashboardserver.db, 'get_ifood_order_snapshot', lambda org_id, order_id: snapshot)
    monkeypatch.setattr(dashboardserver.db, 'list_ifood_order_events', lambda **kwargs: [])
    monkeypatch.setattr(dashboardserver, 'get_resilient_api_client', lambda: None)

    resp = client.get('/api/ifood/homologation/orders/order-123/evidence?refresh=0')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    dumped = json.dumps(body)
    assert '12345678901' not in dumped
    assert body['evidence']['fields']['payment_brand']['status'] == 'present'


def test_readiness_order_is_partial_without_order_evidence(monkeypatch, client):
    with client.session_transaction() as sess:
        sess['user'] = {'id': 1, 'primary_org_id': 1}
        sess['org_id'] = 1
    monkeypatch.setattr(dashboardserver.db, 'is_platform_admin', lambda user_id: False)
    monkeypatch.setattr(dashboardserver.db, 'get_org_member_role', lambda org_id, user_id: 'admin')
    monkeypatch.setattr(dashboardserver, '_snapshot_ifood_evidence_entries', lambda limit=500, org_id=None: [])
    monkeypatch.setattr(dashboardserver, '_snapshot_ifood_ingestion_metrics', lambda: {'orders_persisted': 0, 'orders_cached': 0})
    monkeypatch.setattr(dashboardserver.db, 'list_ifood_order_snapshots', lambda org_id=None, limit=10: [])

    resp = client.get('/api/ifood/homologation/readiness')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['modules']['Order']['status'] == 'gap'
    assert 'order_status' in body['modules']['Order']['gaps']


def test_admin_ui_exposes_order_evidence_panel():
    html = open('templates/admin.html', encoding='utf-8').read()
    assert 'Order Evidence' in html
    assert '/api/ifood/homologation/orders/${encodeURIComponent(orderId)}/evidence' in html
    assert 'homologRecentOrders' in html
