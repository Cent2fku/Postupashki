"""Collect explicitly disclosed annual amortization of intangible assets."""
import io
import re
import zipfile



_AMOUNT = r"(?P<amount>[0-9]+(?: [0-9]{3})*(?:[,.][0-9]+)?)"
_DISCLOSURE = re.compile(
    r"(?:^|(?<=[.!?]) ) *(?:начислен[ао] )?амортизаци[яию] (?:по )?(?:нематериальных активов|нма) "
    r"за (?P<year>20[0-9]{2})\s*(?:год|г\.)\s*"
    r"(?:(?:составила|составляет|начислена в сумме|в размере)\s*)?[:=\u2013\u2014-]?\s*"
    + _AMOUNT
    + r"\s*(?P<unit>тыс\.?|млн\.?)?\s*(?:руб(?:лей|ля|ль)?\.?|₽)(?!\w)",
    re.IGNORECASE,
)


def extract_amortization(text, year):
    """Return a unique explicit amount in thousands of rubles, or None."""
    normalized = re.sub(r"\s+", " ", text)
    amounts = set()
    for match in _DISCLOSURE.finditer(normalized):
        if int(match['year']) != year:
            continue
        amount = float(match['amount'].replace(' ', '').replace(',', '.'))
        unit = (match['unit'] or '').lower().rstrip('.')
        amounts.add(amount * {'': 0.001, 'тыс': 1, 'млн': 1000}[unit])
    for page in text.split('\f'):
        if not re.search(r'(?i)Наличие и движение нематериальных активов', page):
            continue
        matched = re.search(
            r'(?im)^ *Нематериальные активы [-\u2013\u2014] всего +За (20[0-9]{2}) г\. +([^\n]+)'
            r'(?:\n +За (20[0-9]{2}) г\. +([^\n]+))?',
            page,
        )
        if not matched or 'обесценение' not in page.lower():
            continue
        for label, values in ((matched[1], matched[2]), (matched[3], matched[4])):
            if label != str(year):
                continue
            cells = re.split(r' {2,}', values.strip())
            if len(cells) == 13 and cells[5] in {'-', '\u2013', '\u2014'} and all(
                re.fullmatch(r'[-\u2013\u2014]|\(?[0-9]+(?: [0-9]{3})*\)?', cell) for cell in cells
            ):
                amounts.add(0)
    # In the older form, rows 5100 and 5110 are the current and prior totals.
    # The sixth of the eleven movement cells is amortization charged.
    for page in text.split('\f'):
        if not re.search(r'(?i)Наличие и движение нематериальных активов', page):
            continue
        if not re.search(r'(?i)начислено\s+амортиза', re.sub(r' {2,}', ' ', page)):
            # Multicolumn PDF headers can put the label on different lines.
            if 'начислено' not in page.lower() or 'амортиза-' not in page.lower():
                continue
        for line in page.splitlines():
            row = re.match(
                r'(?i)^ *(?:Нематериальные(?: активы [-\u2013\u2014] всего)?|активы [-\u2013\u2014] всего)'
                r' +(?:5100|5110) +за (20[0-9]{2})\s*г\. +(.+)$', line
            )
            if not row or int(row[1]) != year:
                continue
            cells = re.split(r' {2,}', row[2].strip())
            if len(cells) != 11:
                continue
            if all(cell in {'-', '\u2013', '\u2014'} for cell in cells):
                amounts.add(0)
                continue
            # Thousands of rubles must be stated on the same page.
            if re.search(r'(?i)тыс\.\s*руб\.', page) and all(
                re.fullmatch(r'[-\u2013\u2014]|\(?[0-9]+(?: [0-9]{3})*\)?', cell) for cell in cells
            ):
                charged = cells[5]
                if charged in {'-', '\u2013', '\u2014'}:
                    amounts.add(0)
                else:
                    amounts.add(int(charged.strip('()').replace(' ', '')))
    return next(iter(amounts)) if len(amounts) == 1 else None


def read_text(content):
    if content.startswith(b'%PDF-'):
        from pypdf import PdfReader

        return '\f'.join(page.extract_text(extraction_mode='layout') or '' for page in PdfReader(io.BytesIO(content)).pages)
    if content.startswith(b'PK') and zipfile.is_zipfile(io.BytesIO(content)):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if 'word/document.xml' not in archive.namelist():
                return None
        from docx import Document

        return '\n'.join(p.text for p in Document(io.BytesIO(content)).paragraphs)
    return None
