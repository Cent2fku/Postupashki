"""Извлечь амортизацию НМА из пояснений и собрать архив для анализа."""
import argparse
from collections import Counter
import csv
from pathlib import Path
import re
import shutil
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile

from amortization import extract_amortization, read_text
from collect_fns import write_csv

CANDIDATE = re.compile(
    r'нематер[иі]аль|\bнма\b|начислено\s+амортиза|(?<!\d)(?:5100|5110)(?!\d)', re.IGNORECASE
)


def excerpts(text):
    result = []
    for page, content in enumerate(text.split('\f'), 1):
        lines = content.splitlines()
        indices = set()
        for index, line in enumerate(lines):
            if CANDIDATE.search(line):
                indices.update(range(max(0, index - 2), min(len(lines), index + 9)))
        if indices:
            result.append(f'--- Страница {page} ---')
            for index in sorted(indices):
                result.append(f'{index + 1:04d} {lines[index]}')
    return '\n'.join(result)


def cached_ocr(path, output, executable, options, dpi):
    if output.exists():
        return output.read_text(encoding='utf-8')
    from ocr_clarifications import recognize

    pages = []
    for page in recognize(path, executable, options, dpi):
        _, _, body = page.partition('\n')
        pages.append(body)
    text = '\f'.join(pages)
    output.write_text(text, encoding='utf-8')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('fns_data'))
    parser.add_argument('--ocr', action='store_true', help='Распознать PDF без пригодного текста')
    parser.add_argument('--tesseract', default='tesseract')
    parser.add_argument('--tessdata', type=Path)
    parser.add_argument('--dpi', type=int, default=250)
    args = parser.parse_args()
    if not 72 <= args.dpi <= 600:
        parser.error('--dpi должен быть от 72 до 600.')
    ocr_exe = None
    options = ['--tessdata-dir', str(args.tessdata.resolve())] if args.tessdata else []
    if args.ocr:
        ocr_exe = shutil.which(args.tesseract)
        if not ocr_exe:
            parser.error('Tesseract не найден. Укажите --tesseract.')
        check = subprocess.run([ocr_exe, *options, '--list-langs'],
                               capture_output=True, text=True, errors='replace')
        if check.returncode or 'rus' not in check.stdout.splitlines():
            parser.error('Tesseract не видит rus.traineddata. Проверьте --tessdata.')

    filenames = ['companies.csv', 'financials.csv', 'collection_status.csv']
    for filename in filenames:
        if not (args.data / filename).is_file():
            parser.error(f'Нет {args.data / filename}. Сначала запустите collect_fns.py.')
    with (args.data / 'financials.csv').open(encoding='utf-8-sig', newline='') as stream:
        forms = list(csv.DictReader(stream))
    ocr_dir = args.data / 'ocr'
    ocr_dir.mkdir(exist_ok=True)
    values, statuses = [], []
    archive_path = args.data / 'fns_for_analysis.zip'
    temporary = archive_path.with_suffix('.tmp')
    with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as archive:
        for index, form in enumerate(forms, 1):
            detail_id = int(form['detail_id'])
            year = int(form['year'])
            source = form['clarification_url']
            path = args.data / 'cache' / f'clarification_{detail_id}.bin'
            report = {'inn': form['inn'], 'form_year': year, 'detail_id': detail_id, 'source': source}
            status = form['clarification_status']
            if source and path.is_file():
                print(f'{index}/{len(forms)}: INN {form["inn"]}, {year}, {path.name}', flush=True)
                try:
                    content = path.read_bytes()
                    text = read_text(content) or ''
                    if len(re.findall(r'[А-Яа-яЁё]', text)) < 40 and content.startswith(b'%PDF-'):
                        del content
                        ocr_output = ocr_dir / f'{detail_id}.txt'
                        if ocr_output.exists():
                            text = ocr_output.read_text(encoding='utf-8')
                            status = 'OCR: фрагменты для сверки' if text.strip() else 'OCR: нет текста'
                        elif ocr_exe:
                            text = cached_ocr(path, ocr_output, ocr_exe, options, args.dpi)
                            status = 'OCR: фрагменты для сверки' if text.strip() else 'OCR: нет текста'
                        else:
                            status = 'Нужен OCR'
                    elif text.strip():
                        matched_years = []
                        for value_year in (year - 1, year):
                            amount = extract_amortization(text, value_year)
                            if amount is not None:
                                values.append({**report, 'year': value_year,
                                               'amortization_intangibles': amount})
                                matched_years.append(value_year)
                        status = ('Распознано: ' + ', '.join(map(str, matched_years))
                                  if matched_years else 'Текст прочитан, сумма не распознана')
                    else:
                        status = 'Документ без доступного текста'
                    evidence = excerpts(text)
                    if evidence:
                        archive.writestr(f'evidence/clarification_{detail_id}.txt', evidence)
                except Exception as exc:
                    # A malformed attachment must not prevent processing the remaining forms.
                    status = f'Ошибка чтения: {type(exc).__name__}: {str(exc)[:180]}'
            statuses.append({**report, 'status': status})

        columns = ['inn', 'form_year', 'detail_id', 'source']
        write_csv(args.data / 'amortization_candidates.csv',
                  [*columns, 'year', 'amortization_intangibles'], values)
        write_csv(args.data / 'amortization_status.csv', [*columns, 'status'], statuses)
        for name in [*filenames, 'amortization_candidates.csv', 'amortization_status.csv']:
            archive.write(args.data / name, name)
    temporary.replace(archive_path)
    print(f'Годовых форм: {len(forms)}. Кандидатов по амортизации НМА: {len(values)}.')
    for status, count in Counter(row['status'] for row in statuses).most_common():
        print(f'{count:3}  {status}')
    print(f'Файл для отправки: {archive_path.resolve()}')
    return 0 if forms else 1


if __name__ == '__main__':
    raise SystemExit(main())
