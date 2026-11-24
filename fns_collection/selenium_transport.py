"""Read public GIR BO responses through a Selenium-controlled Chrome session."""
from base64 import b64decode
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

from requests import Response, exceptions
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.service import Service


_START_FETCH = """
const [url, accept, timeout, stream, done] = arguments;
(async () => {
    const controller = new AbortController();
    window.__fnsRequest = controller;
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
        const response = await fetch(url, {
            headers: {Accept: accept},
            mode: 'same-origin',
            signal: controller.signal,
        });
        const meta = {status: response.status, url: response.url};
        if (stream && response.ok && response.body) {
            window.__fnsReader = response.body.getReader();
            return {...meta, stream: true};
        }
        return {...meta, text: await response.text()};
    } finally {
        clearTimeout(timer);
    }
})().then(done, error => done({error: String(error)}));
"""

_NEXT_CHUNK = """
const [timeout, done] = arguments;
(async () => {
    const timer = setTimeout(() => window.__fnsRequest.abort(), timeout);
    try {
        const next = await window.__fnsReader.read();
        if (next.done) return {done: true};
        const bytes = next.value;
        let binary = '';
        for (let offset = 0; offset < bytes.length; offset += 8192) {
            binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
        }
        return {chunk: btoa(binary)};
    } finally {
        clearTimeout(timer);
    }
})().then(done, error => done({error: String(error)}));
"""

_CLOSE_FETCH = """
if (window.__fnsRequest) window.__fnsRequest.abort();
delete window.__fnsRequest;
delete window.__fnsReader;
"""


class BrowserResponse(Response):
    """Expose status, JSON and streamed attachment bytes to the shared collector."""

    def __init__(self, browser, response, timeout_ms):
        super().__init__()
        self.browser = browser
        self.status_code = response['status']
        self.url = response['url']
        self.encoding = 'utf-8'
        self._content = response.get('text', '').encode('utf-8')
        self._content_consumed = True
        self.streaming = response.get('stream', False)
        self.timeout_ms = timeout_ms

    def iter_content(self, chunk_size=1, decode_unicode=False):
        if not self.streaming:
            yield from super().iter_content(chunk_size, decode_unicode)
            return
        while True:
            result = self.browser.execute(_NEXT_CHUNK, self.timeout_ms)
            if result.get('done'):
                return
            yield b64decode(result['chunk'])

    def close(self):
        if self.streaming:
            self.streaming = False
            try:
                self.browser.driver.execute_script(_CLOSE_FETCH)
            except WebDriverException:
                pass


class SeleniumSession:
    def __init__(self, driver, base):
        self.driver = driver
        self.base = base.rstrip('/')
        self.ready = False

    def execute(self, script, *args):
        try:
            result = self.driver.execute_async_script(script, *args)
        except WebDriverException as exc:
            raise exceptions.ConnectionError(exc.msg) from exc
        if 'error' in result:
            raise exceptions.ConnectionError(result['error'])
        return result

    def get(self, url, *, params=None, headers=None, timeout=(8, 25), stream=False):
        # Keep every request within the public portal origin.
        if urlsplit(url)[:2] != urlsplit(self.base)[:2]:
            raise ValueError('Запрос вне домена ГИР БО')
        if not self.ready:
            try:
                self.driver.get(self.base + '/')
                origin = self.driver.execute_script('return window.location.origin')
            except WebDriverException as exc:
                raise exceptions.ConnectionError(f'Chrome не открыл ГИР БО: {exc.msg}') from exc
            if origin != self.base:
                raise exceptions.ConnectionError(f'Chrome не открыл ГИР БО. Домен страницы: {origin}')
            self.ready = True
        if params:
            url += ('&' if '?' in url else '?') + urlencode(params)
        accept = (headers or {}).get('Accept', 'application/json')
        timeout_ms = int(timeout[1] * 1000)
        response = self.execute(_START_FETCH, url, accept, timeout_ms, stream)
        return BrowserResponse(self, response, timeout_ms)


@contextmanager
def selenium_session(base, headed=False, chrome_binary=None, chromedriver=None):
    options = webdriver.ChromeOptions()
    if not headed:
        options.add_argument('--headless')
    options.page_load_strategy = 'eager'
    if chrome_binary:
        options.binary_location = chrome_binary
    try:
        driver = webdriver.Chrome(options=options, service=Service(executable_path=chromedriver))
    except WebDriverException as exc:
        message = exc.msg.splitlines()[0] if exc.msg else type(exc).__name__
        raise SystemExit(f'Не удалось запустить Chrome через Selenium: {message}') from exc
    try:
        driver.set_page_load_timeout(25)
        driver.set_script_timeout(55)
        yield SeleniumSession(driver, base)
    finally:
        driver.quit()
