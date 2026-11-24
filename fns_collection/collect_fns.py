"""Скачать годовые формы ГИР БО и приложенные пояснения."""
import argparse
import csv
from contextlib import contextmanager
from html import unescape
import json
from pathlib import Path
import re
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = 'https://bo.nalog.gov.ru'
FIELDS = {
    'revenue': '2110',
    'operating_profit': '2200',
    'pretax_profit': '2300',
    'interest_received': '2320',
    'interest_paid': '2330',
    'income_tax': '2410',
    'net_profit': '2400',
}
FORM_COLUMNS = ['inn', 'year', 'org_id', 'detail_id', 'form_url',
                'clarification_url', 'clarification_status', 'clarification_name',
                'book_value_intangibles', *FIELDS]


def write_csv(path, columns, rows):
    partial = path.with_suffix('.tmp')
    with partial.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    partial.replace(path)


def fetch_json(session, cache_file, path, expected, params=None):
    if cache_file.exists():
        try:
            payload = json.loads(cache_file.read_text(encoding='utf-8-sig'))
            if isinstance(payload, expected):
                return payload
        except ValueError:
            pass
    time.sleep(0.35)
    response = session.get(BASE + path, params=params, timeout=(8, 25))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, expected):
        raise ValueError(f'Неожиданный формат JSON: {path}')
    if payload and (expected is not dict or payload.get('content')):
        partial = cache_file.with_suffix('.tmp')
        partial.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        partial.replace(cache_file)
    return payload


def collect_company(session, cache, inn, first_year, last_year):
    search = fetch_json(session, cache / f'search_{inn}.json',
                        '/advanced-search/organizations/search', dict,
                        {'query': inn, 'page': 0, 'size': 20})
    items = search['content']
    if not isinstance(items, list):
        raise ValueError('Поле content в выдаче должно быть списком.')
    ids = {
        int(item['id']) for item in items
        if re.sub(r'<[^>]+>', '', unescape(str(item.get('inn', '')))).strip() == inn
    }
    if len(ids) != 1:
        return [], ('Нет точного совпадения ИНН' if not ids else 'Найдено несколько карточек')

    org_id = ids.pop()
    reports = fetch_json(session, cache / f'bfo_{inn}.json',
                         f'/nbo/organizations/{org_id}/bfo/', list)
    latest = {}
    for report in reports:
        period = str(report.get('period', ''))
        if not period.isdigit() or not first_year <= int(period) <= last_year:
            continue
        year = int(period)
        for entry in report.get('typeCorrections') or []:
            correction = entry.get('correction') or {}
            if str(entry.get('type', correction.get('periodType'))) != '12' or not correction.get('id'):
                continue
            form = correction.get('financialResult') or {}
            if not form:
                continue
            detail_id = int(correction['id'])
            version = (str(correction.get('datePresent') or report.get('publishedCorrectionDate') or ''),
                       int(correction.get('correctionVersion') or 0), detail_id)
            if year in latest and version <= latest[year][0]:
                continue
            attachment = (correction.get('clarification') or {}).get('fileMetadata') or {}
            balance = correction.get('balance') or {}
            latest[year] = (version, {
                'inn': inn, 'year': year, 'org_id': org_id, 'detail_id': detail_id,
                'form_url': f'{BASE}/download/bfo/pdf/{org_id}?period={year}&detailId={detail_id}',
                'clarification_url': f'{BASE}/download/clarification/{detail_id}' if attachment else '',
                'clarification_status': 'Нет файла в метаданных',
                'clarification_name': attachment.get('originalName') or '',
                'book_value_intangibles': balance.get('current1110'),
                **{field: form.get(f'current{code}') for field, code in FIELDS.items()},
            })
    return [record for _, record in sorted(latest.values(), key=lambda item: item[1]['year'])], (
        'Получено' if latest else 'Годовых форм за выбранный период нет'
    )


def download_clarification(session, cache, record):
    target = cache / f"clarification_{record['detail_id']}.bin"
    if target.exists():
        with target.open('rb') as stream:
            if stream.read(5).startswith((b'%PDF-', b'PK')):
                return 'Получено из кэша'
    partial = target.with_suffix('.tmp')
    try:
        time.sleep(0.35)
        with session.get(record['clarification_url'], headers={'Accept': '*/*'},
                         timeout=(8, 45), stream=True) as response:
            response.raise_for_status()
            with partial.open('wb') as stream:
                for chunk in response.iter_content(chunk_size=1024 * 128):
                    stream.write(chunk)
        with partial.open('rb') as stream:
            if not stream.read(5).startswith((b'%PDF-', b'PK')):
                return 'Формат ответа отличен от PDF или ZIP'
        partial.replace(target)
        return 'Скачано'
    finally:
        partial.unlink(missing_ok=True)


def describe_error(exc):
    response = getattr(exc, 'response', None)
    if response is None:
        return str(exc)[:220]
    detail = ' '.join(response.text.split())[:180]
    return f'HTTP {response.status_code}' + (f': {detail}' if detail and not detail.startswith('<') else '')


@contextmanager
def requests_session():
    with requests.Session() as session:
        session.headers.update({'Accept': 'application/json', 'User-Agent': 'FNS-Research/1.0'})
        session.mount('https://', HTTPAdapter(max_retries=Retry(
            total=1, backoff_factor=1, status_forcelist=(502, 503, 504), raise_on_status=False,
        )))
        yield session


def open_session(args):
    if args.transport == 'requests':
        return requests_session()
    from selenium_transport import selenium_session

    return selenium_session(BASE, args.headed, args.chrome_binary, args.chromedriver)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--companies', type=Path, default=Path(__file__).with_name('companies.csv'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--transport', choices=['selenium', 'requests'], default='selenium')
    parser.add_argument('--headed', action='store_true', help='Показать окно Chrome')
    parser.add_argument('--chrome-binary', help='Путь к Chrome, если он установлен нестандартно')
    parser.add_argument('--chromedriver', help='Путь к установленному ChromeDriver')
    parser.add_argument('--from-year', type=int, default=2019)
    parser.add_argument('--to-year', type=int, default=2025)
    parser.add_argument('--limit', type=int, help='Проверочный запуск на первых N компаниях')
    args = parser.parse_args()
    if args.output is None:
        args.output = Path('fns_selenium_data' if args.transport == 'selenium' else 'fns_data')
    if args.from_year > args.to_year or (args.limit is not None and args.limit < 1):
        parser.error('Проверьте диапазон лет и --limit.')
    if args.transport == 'requests' and (args.headed or args.chrome_binary or args.chromedriver):
        parser.error('Настройки Chrome применяются только с Selenium.')
    with args.companies.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        companies = list(reader)
    companies = companies[:args.limit]
    inns = [company['inn'].strip() for company in companies]
    if not inns or len(inns) != len(set(inns)) or not all(re.fullmatch(r'[0-9]{10}', inn) for inn in inns):
        parser.error('В companies.csv должны быть уникальные десятизначные ИНН организаций.')
    cache = args.output / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    print(f'Сбор через {args.transport}. Папка: {args.output.resolve()}', flush=True)
    write_csv(args.output / 'companies.csv', columns, companies)
    forms, statuses = [], []
    columns_status = ['inn', 'status', 'forms']
    failed = 0
    stop = False
    with open_session(args) as session:
        for index, inn in enumerate(inns, 1):
            if stop:
                statuses.append({'inn': inn, 'status': 'Запрос отложен', 'forms': 0})
                continue
            try:
                rows, status = collect_company(session, cache, inn, args.from_year, args.to_year)
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                failed += 1
                status = f'Ошибка: {describe_error(exc)}'
                rows = []
                response = getattr(exc, 'response', None)
                stop = failed >= 3 or (response is not None and response.status_code == 429)
            else:
                failed = 0
            for row in rows:
                if not row['clarification_url']:
                    continue
                if stop:
                    row['clarification_status'] = 'Скачивание отложено'
                    continue
                try:
                    row['clarification_status'] = download_clarification(session, cache, row)
                except requests.RequestException as exc:
                    row['clarification_status'] = f'Ошибка: {describe_error(exc)}'
                    stop = exc.response is not None and exc.response.status_code == 429
            forms.extend(rows)
            statuses.append({'inn': inn, 'status': status, 'forms': len(rows)})
            print(f'{index}/{len(inns)} {inn}: {status}, форм {len(rows)}', flush=True)
            write_csv(args.output / 'financials.csv', FORM_COLUMNS, forms)
            write_csv(args.output / 'collection_status.csv', columns_status, statuses)
    write_csv(args.output / 'collection_status.csv', columns_status, statuses)
    print(f'Сохранено годовых форм: {len(forms)} → {args.output.resolve()}')
    return 0 if forms else 1


if __name__ == '__main__':
    raise SystemExit(main())
