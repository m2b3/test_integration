import unittest
import xml.etree.ElementTree as ET

from base import parse_pubmed_record_xml


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


if __name__ == "__main__":
    unittest.main()
