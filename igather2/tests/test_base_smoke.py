import unittest
import xml.etree.ElementTree as ET
from io import StringIO

from base import edirect_command, resolve_edirect_prefix, save_records, parse_pubmed_record_xml


class TestBaseSmoke(unittest.TestCase):
    def test_parse_article_date_month_name(self) -> None:
        xml = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID>123</PMID>
    <Article>
      <ArticleTitle>Test Title</ArticleTitle>
      <ArticleDate>
        <Year>2024</Year>
        <Month>Jan</Month>
        <Day>7</Day>
      </ArticleDate>
    </Article>
  </MedlineCitation>
</PubmedArticle>
"""
        record = parse_pubmed_record_xml(ET.fromstring(xml))
        self.assertEqual(record.get("pmid"), "123")
        self.assertEqual(record.get("title"), "Test Title")
        self.assertEqual(record.get("pub_date"), "2024-01-07")

    def test_parse_medline_date_range(self) -> None:
        xml = """\
<PubmedArticle>
  <MedlineCitation>
    <PMID>456</PMID>
    <Article>
      <ArticleTitle>Another Title</ArticleTitle>
      <Journal>
        <JournalIssue>
          <PubDate>
            <MedlineDate>2023 Nov-Dec</MedlineDate>
          </PubDate>
        </JournalIssue>
      </Journal>
    </Article>
  </MedlineCitation>
</PubmedArticle>
"""
        record = parse_pubmed_record_xml(ET.fromstring(xml))
        self.assertEqual(record.get("pmid"), "456")
        self.assertEqual(record.get("pub_date"), "2023-11")

    def test_save_records_jsonl_dedupes_within_run(self) -> None:
        out = StringIO()
        seen_pmids: set[str] = set()
        records = [
            {"pmid": "123", "title": "First"},
            {"pmid": "123", "title": "Duplicate"},
            {"pmid": "456", "title": "Second"},
        ]

        new_records, saved = save_records(
            records,
            conn=None,
            jsonl_handle=out,
            seen_pmids=seen_pmids,
        )

        self.assertEqual(saved, 2)
        self.assertEqual([r["pmid"] for r in new_records], ["123", "456"])
        self.assertEqual(len(out.getvalue().splitlines()), 2)

    def test_edirect_command_accepts_directory_prefix(self) -> None:
        self.assertEqual(edirect_command(["../edirect"], "esearch"), ["../edirect/esearch"])

    def test_resolve_edirect_prefix_falls_back_to_repo_local_copy(self) -> None:
        prefix = resolve_edirect_prefix("")
        self.assertEqual(len(prefix), 1)
        self.assertTrue(prefix[0].endswith("edirect"))


if __name__ == "__main__":
    unittest.main()
