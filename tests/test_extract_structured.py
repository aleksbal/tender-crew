from extract_text import extract_text_structured


def test_extract_realistic_docx_pages():
    structured, diag = extract_text_structured('realistic_cv.docx')
    assert isinstance(structured, dict)
    assert 'pages' in structured
    assert len(structured['pages']) >= 1
    # Expect at least the header on page 1
    p1 = structured['pages'][0]
    assert 'page_text' in p1 and len(p1['page_text']) > 0


def test_blocks_and_lines_present():
    structured, diag = extract_text_structured('realistic_cv.docx')
    p1 = structured['pages'][0]
    assert isinstance(p1['blocks'], list)
    for b in p1['blocks']:
        assert 'lines' in b and isinstance(b['lines'], list)