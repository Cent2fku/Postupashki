"""Recognize Russian text in local FNS PDF attachments."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

import pypdfium2 as pdfium


def recognize(source, tesseract, language_options, dpi):
    document = pdfium.PdfDocument(source)
    try:
        with tempfile.TemporaryDirectory() as temp:
            image_path = Path(temp) / 'page.png'
            for index in range(len(document)):
                page = document[index]
                try:
                    bitmap = page.render(scale=dpi / 72, grayscale=True)
                    try:
                        bitmap.to_pil().save(image_path)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                result = subprocess.run(
                    [tesseract, str(image_path), 'stdout', *language_options,
                     '-l', 'rus', '--psm', '3'],
                    capture_output=True, encoding='utf-8', errors='replace',
                    check=True, timeout=180,
                )
                yield f'--- Страница {index + 1} ---\n{result.stdout.rstrip()}\n'
    finally:
        document.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Файл .bin/.pdf или папка с пояснениями')
    parser.add_argument('destination', type=Path, help='Папка для текстовых файлов UTF-8')
    parser.add_argument('--tesseract', default='tesseract', help='Путь к tesseract.exe при необходимости')
    parser.add_argument('--tessdata', type=Path, help='Папка с rus.traineddata')
    parser.add_argument('--dpi', type=int, default=250)
    parser.add_argument('--force', action='store_true', help='Заменить результаты предыдущего запуска')
    args = parser.parse_args()

    tesseract = shutil.which(args.tesseract)
    if tesseract is None:
        parser.error('Установите Tesseract OCR или укажите путь через --tesseract.')
    if not 72 <= args.dpi <= 600:
        parser.error('--dpi должен быть от 72 до 600.')
    language_options = ['--tessdata-dir', str(args.tessdata)] if args.tessdata else []
    check = subprocess.run([tesseract, *language_options, '--list-langs'],
                           capture_output=True, text=True, errors='replace')
    if check.returncode or 'rus' not in check.stdout.splitlines():
        parser.error('Tesseract не видит русскую модель rus.traineddata. Проверьте --tessdata.')
    if args.source.is_file():
        files = [args.source]
    elif args.source.is_dir():
        files = sorted(p for p in args.source.iterdir() if p.is_file()
                       and p.suffix.lower() in {'.bin', '.pdf'})
    else:
        parser.error(f'Путь не найден: {args.source}')
    if not files:
        parser.error('В папке нет файлов .bin или .pdf.')

    args.destination.mkdir(parents=True, exist_ok=True)
    failed = False
    for source in files:
        target = args.destination / f'{source.name}.txt'
        if target.exists() and not args.force:
            print(f'Уже обработан: {source.name}', flush=True)
            continue
        partial = target.with_suffix('.tmp')
        try:
            with source.open('rb') as file:
                if not file.read(1024).lstrip().startswith(b'%PDF-'):
                    raise ValueError('Файл не содержит PDF.')
            print(f'Распознаю: {source.name}', flush=True)
            with partial.open('w', encoding='utf-8') as output:
                for page in recognize(source, tesseract, language_options, args.dpi):
                    output.write(page)
                    output.write('\n')
            partial.replace(target)
        except (OSError, ValueError, pdfium.PdfiumError, subprocess.SubprocessError) as exc:
            partial.unlink(missing_ok=True)
            detail = getattr(exc, 'stderr', None) or str(exc)
            print(f'Ошибка {source.name}: {detail.strip()}', flush=True)
            failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
