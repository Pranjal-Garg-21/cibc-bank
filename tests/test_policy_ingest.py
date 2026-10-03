from trustissues.ask.rag.policy_ingest import MAX_WORDS, WHOLE, chunk_document

DOC = {"doc_id": "D1", "doc_type": "policy", "title": "T", "language": "EN", "version": "1.0",
       "effective_date": "2026-01-01", "superseded_by": None, "owner": "x", "confidentiality": "internal",
       "applies_to_products": '["card"]', "applies_to_provinces": '["ALL"]'}


def test_heading_sections_become_chunks_with_ids():
    md = "> banner\n\n# T\n\n## §1.1 Hours\nCalls 7 to 9.\n\n## §1.2 Frequency\nThree per week.\n"
    ch = chunk_document(DOC, md)
    assert [c.section_id for c in ch] == ["§1.1", "§1.2"]
    assert "Hours" in ch[0].text and ch[0].meta["applies_to_products"] == ["card"]


def test_long_section_splits_inside_the_section_with_overlap():
    sents = [f"Sentence number {i} says something useful about hardship." for i in range(60)]
    md = "## §2 Long\n" + " ".join(sents)
    ch = chunk_document(DOC, md)
    assert len(ch) > 1 and {c.section_id for c in ch} == {"§2"}
    assert all(len(c.text.split()) <= MAX_WORDS + 20 for c in ch)
    assert all(c.section_text == ch[0].section_text for c in ch)  # parent kept for expansion
    assert ch[0].text.split(". ")[-1][:30] in ch[1].text  # one-sentence overlap


def test_faq_and_table_and_headingless_shapes():
    faq = chunk_document(DOC, "**Q1. How long?**\nSix months.\n\n**Q2. Why?**\nBecause.\n")
    assert [c.section_id for c in faq] == ["Q1", "Q2"]
    tbl = chunk_document(DOC, "| P | T |\n|---|---|\n| HP-A | a |\n| HP-B | b |\n")
    assert [c.section_id for c in tbl] == ["HP-A", "HP-B"] and "| P | T |" in tbl[1].text
    assert chunk_document(DOC, "Just a paragraph.")[0].section_id == WHOLE
