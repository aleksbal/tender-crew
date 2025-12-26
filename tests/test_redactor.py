import json
from redactor import redact_structured


def make_structured(pages_blocks):
    pages = []
    for pi, blocks in enumerate(pages_blocks):
        page_blocks = []
        for b in blocks:
            page_blocks.append({
                'bbox': None,
                'text': b,
                'lines': [{'text': b, 'char_start': None, 'char_end': None}],
            })
        pages.append({'page_number': pi + 1, 'width': None, 'height': None, 'page_text': b, 'blocks': page_blocks})
    return {'file_type': 'docx', 'pages': pages}


def test_phone_and_year_range():
    blocks = [
        'Phone: +49 30 12345678',
        'Company A, Senior Engineer (2020-2024)'
    ]
    s = make_structured([blocks])
    out, redactions = redact_structured(s)

    # phone should be redacted
    phone_redactions = [r for r in redactions if r['kind'] == 'phone']
    assert any('+49 30 12345678' in r['original'] for r in phone_redactions)

    # year range should NOT be redacted as phone
    assert not any('2020-2024' in r['original'] and r['kind'] == 'phone' for r in redactions)

    # company line should preserve the year-range text
    company_line = out['pages'][0]['blocks'][1]['lines'][0]['text']
    assert '2020-2024' in company_line


def test_email_and_address_redaction():
    blocks = [
        'Adresse: Hauptstraße 5, 12345 Stadt',
        'Contact: alice@example.com'
    ]
    s = make_structured([blocks])
    out, redactions = redact_structured(s)

    assert any(r['kind'] == 'address' for r in redactions)
    assert any(r['kind'] == 'email' for r in redactions)
    # outputs should contain redaction placeholders
    assert '[REDACTED_ADDRESS]' in out['pages'][0]['blocks'][0]['lines'][0]['text'] or out['pages'][0]['blocks'][0]['text'].startswith('[REDACTED_ADDRESS]')
    assert '[REDACTED_EMAIL]' in out['pages'][0]['blocks'][1]['lines'][0]['text']
