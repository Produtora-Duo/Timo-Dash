"""Helpers for iFood homologation evidence extraction.

The functions in this module intentionally return reviewer-friendly,
redacted summaries instead of raw customer/order payloads.
"""

from datetime import datetime


ORDER_EVIDENCE_FIELDS = [
    'order_status',
    'order_timing',
    'scheduled_time',
    'payment_method',
    'payment_brand',
    'cash_change',
    'discounts_subsidies',
    'item_observations',
    'customer_document',
    'pickup_code',
    'delivery_code',
    'delivery_observations',
    'cancellation_events',
    'dispute_events',
]


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _first_present(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        elif isinstance(value, (list, dict)):
            if value:
                return value
        else:
            return value
    return None


def _get_path(data, *path):
    current = data
    for part in path:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and isinstance(part, int) and 0 <= part < len(current):
            current = current[part]
        else:
            return None
    return current


def _money_value(value):
    if value is None:
        return None
    if isinstance(value, dict):
        value = _first_present(value.get('value'), value.get('amount'), value.get('price'))
    try:
        number = float(value)
    except Exception:
        return value
    return round(number, 2)


def _mask_digits(value, *, keep=4):
    text = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if not text:
        return None
    keep_n = max(1, int(keep or 1))
    suffix = text[-keep_n:]
    return {
        'present': True,
        'last_digits': suffix,
        'masked': f"{'*' * max(3, len(text) - len(suffix))}{suffix}",
        'length': len(text),
    }


def _redact_text(value, *, keep=36):
    text = str(value or '').strip()
    if not text:
        return None
    if len(text) <= keep:
        return text
    return f"{text[:keep].rstrip()}..."


def _event_code(event):
    event = _as_dict(event)
    return str(_first_present(event.get('fullCode'), event.get('code'), event.get('eventType'), event.get('type')) or '').upper()


def _event_order_id(event):
    event = _as_dict(event)
    return str(_first_present(event.get('orderId'), _get_path(event, 'order', 'id'), event.get('order_id')) or '').strip()


def _event_summary(event):
    event = _as_dict(event)
    return {
        'id': _first_present(event.get('id'), event.get('eventId'), event.get('dedupe_key')),
        'code': _event_code(event),
        'source': event.get('source'),
        'created_at': _first_present(event.get('createdAt'), event.get('event_created_at'), event.get('processed_at'), event.get('created_at')),
    }


def _status_for(value, *, applicable=True):
    if not applicable:
        return 'not_applicable'
    if isinstance(value, bool):
        return 'present' if value else 'missing'
    if value is None:
        return 'missing'
    if isinstance(value, (list, dict)):
        return 'present' if value else 'missing'
    if isinstance(value, str):
        return 'present' if value.strip() else 'missing'
    return 'present'


def _field(status, value=None, label=None):
    payload = {'status': status}
    if label:
        payload['label'] = label
    if value is not None:
        payload['value'] = value
    return payload


def build_ifood_order_evidence(order_payload, *, snapshot=None, events=None):
    """Build a normalized, redacted homologation evidence object for one order."""
    order = _as_dict(order_payload)
    snapshot = _as_dict(snapshot)
    events = [event for event in _as_list(events) if isinstance(event, dict)]

    order_id = str(_first_present(
        order.get('id'),
        order.get('orderId'),
        snapshot.get('order_id'),
        snapshot.get('orderId'),
    ) or '').strip()

    merchant_id = str(_first_present(
        order.get('merchantId'),
        order.get('merchant_id'),
        snapshot.get('merchant_id'),
        snapshot.get('merchantId'),
    ) or '').strip()

    status = _first_present(order.get('status'), order.get('orderStatus'), snapshot.get('status'))
    order_type = _first_present(order.get('type'), order.get('orderType'), _get_path(order, 'delivery', 'deliveredBy'))
    order_timing = _first_present(order.get('orderTiming'), order.get('timing'), order.get('salesChannel'))

    schedule = _first_present(
        order.get('schedule'),
        _get_path(order, 'delivery', 'deliveryDateTime'),
        _get_path(order, 'delivery', 'scheduledTime'),
        order.get('scheduledTo'),
        order.get('scheduled_to'),
    )
    scheduled_value = None
    if isinstance(schedule, dict):
        scheduled_value = {
            'start': _first_present(schedule.get('start'), schedule.get('startDateTime'), schedule.get('deliveryDateTimeStart')),
            'end': _first_present(schedule.get('end'), schedule.get('endDateTime'), schedule.get('deliveryDateTimeEnd')),
        }
        scheduled_value = {k: v for k, v in scheduled_value.items() if v}
    else:
        scheduled_value = schedule

    payments = _as_dict(order.get('payments'))
    payment_methods = _as_list(payments.get('methods')) or _as_list(order.get('paymentMethods'))
    first_payment = _as_dict(payment_methods[0]) if payment_methods else {}
    payment_method = _first_present(
        first_payment.get('method'),
        first_payment.get('type'),
        _get_path(payments, 'prepaid'),
        _get_path(payments, 'pending'),
        order.get('payment_method'),
    )
    payment_brand = _first_present(
        first_payment.get('brand'),
        first_payment.get('cardBrand'),
        first_payment.get('issuer'),
        _get_path(payments, 'brand'),
        order.get('payment_brand'),
    )
    change_for = _money_value(_first_present(
        first_payment.get('changeFor'),
        first_payment.get('cashChangeFor'),
        _get_path(payments, 'changeFor'),
        order.get('change_for'),
    ))

    benefits = _as_list(order.get('benefits')) + _as_list(order.get('discounts'))
    discount_items = []
    for benefit in benefits:
        benefit = _as_dict(benefit)
        amount = _money_value(_first_present(benefit.get('value'), benefit.get('amount'), benefit.get('target')))
        sponsor = _first_present(benefit.get('sponsorship'), benefit.get('sponsor'), benefit.get('owner'), benefit.get('liability'))
        discount_items.append({
            'amount': amount,
            'sponsor': sponsor,
            'description': _redact_text(_first_present(benefit.get('description'), benefit.get('target'), benefit.get('type'))),
        })
    total_benefits = _money_value(_first_present(_get_path(order, 'total', 'benefits'), order.get('totalBenefits')))
    discounts_value = {
        'total': total_benefits,
        'items': [item for item in discount_items if any(item.values())],
    }
    if not discounts_value['total'] and not discounts_value['items']:
        discounts_value = None

    items = _as_list(order.get('items'))
    item_obs = []
    for item in items:
        item = _as_dict(item)
        observation = _first_present(item.get('observations'), item.get('observation'), item.get('notes'), item.get('comment'))
        if observation:
            item_obs.append({
                'name': _redact_text(_first_present(item.get('name'), item.get('description')), keep=48),
                'quantity': item.get('quantity'),
                'observation': _redact_text(observation, keep=80),
            })

    customer_doc = _first_present(
        _get_path(order, 'customer', 'documentNumber'),
        _get_path(order, 'customer', 'document'),
        _get_path(order, 'customer', 'cpf'),
        _get_path(order, 'customer', 'cnpj'),
        order.get('customer_document'),
    )
    document_kind = None
    doc_digits = ''.join(ch for ch in str(customer_doc or '') if ch.isdigit())
    if len(doc_digits) == 11:
        document_kind = 'CPF'
    elif len(doc_digits) == 14:
        document_kind = 'CNPJ'

    pickup_code = _first_present(order.get('pickupCode'), order.get('pickup_code'), _get_path(order, 'takeout', 'pickupCode'))
    delivery_code = _first_present(order.get('deliveryCode'), order.get('delivery_code'), _get_path(order, 'delivery', 'deliveryCode'))
    delivery_obs = _first_present(
        _get_path(order, 'delivery', 'observations'),
        _get_path(order, 'delivery', 'observation'),
        _get_path(order, 'delivery', 'notes'),
        order.get('delivery_observations'),
    )

    cancellation_events = [
        _event_summary(event)
        for event in events
        if 'CANCEL' in _event_code(event) or _event_code(event).startswith('CAN')
    ]
    dispute_events = [
        _event_summary(event)
        for event in events
        if 'DISPUTE' in _event_code(event) or 'NEGOTIATION' in _event_code(event)
    ]

    fields = {
        'order_status': _field(_status_for(status), status, 'Order status'),
        'order_timing': _field(_status_for(_first_present(order_timing, order_type)), {
            'type': order_type,
            'timing': order_timing,
        }, 'Order type/timing'),
        'scheduled_time': _field(_status_for(scheduled_value, applicable=bool(_first_present(scheduled_value, str(order_timing or '').upper() == 'SCHEDULED'))), scheduled_value, 'Scheduled time/window'),
        'payment_method': _field(_status_for(payment_method), payment_method, 'Payment method'),
        'payment_brand': _field(_status_for(payment_brand), payment_brand, 'Card/payment brand'),
        'cash_change': _field(_status_for(change_for, applicable=bool(change_for or str(payment_method or '').upper() in ('CASH', 'DINHEIRO'))), change_for, 'Cash change'),
        'discounts_subsidies': _field(_status_for(discounts_value), discounts_value, 'Discounts/subsidies'),
        'item_observations': _field(_status_for(item_obs, applicable=bool(item_obs or items)), item_obs, 'Item observations'),
        'customer_document': _field(_status_for(customer_doc), {
            'type': document_kind,
            'document': _mask_digits(customer_doc),
        }, 'CPF/CNPJ'),
        'pickup_code': _field(_status_for(pickup_code, applicable=bool(pickup_code or str(order_type or '').upper() in ('TAKEOUT', 'INDOOR'))), _mask_digits(pickup_code), 'Pickup code'),
        'delivery_code': _field(_status_for(delivery_code, applicable=bool(delivery_code or str(order_type or '').upper() == 'DELIVERY')), _mask_digits(delivery_code), 'Delivery code'),
        'delivery_observations': _field(_status_for(delivery_obs, applicable=bool(delivery_obs or str(order_type or '').upper() == 'DELIVERY')), _redact_text(delivery_obs, keep=120), 'Delivery observations'),
        'cancellation_events': _field(_status_for(cancellation_events, applicable=bool(cancellation_events)), cancellation_events, 'Cancellation events'),
        'dispute_events': _field(_status_for(dispute_events, applicable=bool(dispute_events)), dispute_events, 'Dispute events'),
    }

    present_count = sum(1 for item in fields.values() if item.get('status') == 'present')
    missing_count = sum(1 for item in fields.values() if item.get('status') == 'missing')
    not_applicable_count = sum(1 for item in fields.values() if item.get('status') == 'not_applicable')

    return {
        'order_id': order_id or None,
        'merchant_id': merchant_id or None,
        'display_id': _first_present(order.get('displayId'), order.get('display_id')),
        'generated_at': datetime.utcnow().replace(microsecond=0).isoformat() + 'Z',
        'source': {
            'order_payload': 'live_or_snapshot' if order else None,
            'snapshot_source': snapshot.get('source'),
            'snapshot_updated_at': snapshot.get('updated_at'),
            'events_count': len(events),
        },
        'fields': fields,
        'summary': {
            'present': present_count,
            'missing': missing_count,
            'not_applicable': not_applicable_count,
            'total': len(fields),
            'covered': missing_count == 0 and present_count > 0,
        },
    }


def build_order_field_coverage(samples):
    """Aggregate field coverage from a list of order evidence samples."""
    samples = [sample for sample in _as_list(samples) if isinstance(sample, dict)]
    coverage = {}
    for field_name in ORDER_EVIDENCE_FIELDS:
        statuses = [
            _as_dict(_as_dict(sample.get('fields')).get(field_name)).get('status')
            for sample in samples
        ]
        coverage[field_name] = {
            'present': sum(1 for status in statuses if status == 'present'),
            'missing': sum(1 for status in statuses if status == 'missing'),
            'not_applicable': sum(1 for status in statuses if status == 'not_applicable'),
            'covered': any(status == 'present' for status in statuses),
        }
    return coverage

