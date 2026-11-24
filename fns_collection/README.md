# Сбор данных ФНС

В `companies.csv` лежат ИНН 149 компаний из выборки. Для сбора нужны Python 3.10 или новее и Google Chrome. Selenium подбирает ChromeDriver при первом запуске.

Запустите в PowerShell из этой папки:

```powershell
python -m pip install -r requirements.txt
python collect_fns.py --headed
python parse_disclosures.py --data fns_selenium_data
```

`--headed` показывает окно Chrome. Для пробы можно добавить к команде сбора `--limit 3 --output fns_selenium_test`. Полный сбор запрашивает годовые формы с 2019 по 2025 год. Файлы сохраняются в `fns_selenium_data`, откуда их можно повторно прочитать. Итоговый архив называется `fns_for_analysis.zip`.

Если пояснения сохранены как сканы, можно включить распознавание текста. Нужен Tesseract с русской моделью.

```powershell
python parse_disclosures.py --data fns_selenium_data --ocr --tesseract "C:\Program Files\Tesseract-OCR\tesseract.exe" --tessdata ".\tessdata"
```
