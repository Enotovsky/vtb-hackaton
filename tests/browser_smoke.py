"""Отдельный браузерный прогон на временной БД: python tests/browser_smoke.py.

Нужны requirements-test.txt и python -m playwright install chromium.
Сервер запускается и останавливается самим тестом. Пользовательские данные не затрагиваются.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


def action(page,name,id_=None):
    selector=f'[data-action="{name}"]'+(f'[data-id="{id_}"]' if id_ else '')
    return page.locator(selector).first


def close(page):
    if page.locator('#sheet').evaluate('(n)=>n.open'):
        page.keyboard.press('Escape')
    expect(page.locator('#sheet')).not_to_be_visible()


def pay(page,kind,amount,title=None):
    action(page,kind).click()
    form=page.locator('#payment-form');expect(form).to_be_visible()
    form.locator('[name="amount"]').fill(str(amount))
    if title:form.locator('[name="title"]').fill(title)
    form.get_by_role('button',name='Проверить перед оплатой').click()
    page.get_by_role('button',name='Подтвердить ·',exact=False).click()
    expect(page.locator('#sheet-title')).to_have_text('Операция выполнена')
    close(page)


def main():
    with tempfile.TemporaryDirectory(prefix='vtb-browser-') as tmp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env={**os.environ,'DATABASE_URL':f'sqlite:///{tmp}/browser.db','UPLOAD_DIR':tmp+'/uploads','VTB_DEMO_MODE':'1'}
        log=open(Path(tmp)/'server.log','w+')
        server=subprocess.Popen([sys.executable,'run.py','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
        try:
            with httpx.Client(trust_env=False) as client:
                for _ in range(100):
                    if server.poll() is not None:
                        log.seek(0);raise RuntimeError(log.read())
                    try:
                        if client.get(base+'/health').status_code==200:break
                    except httpx.ConnectError:pass
                    time.sleep(.1)
                else:raise RuntimeError('Сервер не запустился')
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
                errors=[]
                contexts=[browser.new_context(viewport={'width':390,'height':844}) for _ in range(3)]
                a,m,k=[context.new_page() for context in contexts]
                for page,name in [(a,'Александр'),(m,'Мария'),(k,'Макс')]:
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(base+'/?mobile=1')
                    page.get_by_role('button',name='Войти как '+name,exact=False).click()
                expect(a.get_by_test_id('shared-balance')).to_contain_text('8')
                expect(m.locator('main')).to_contain_text('Пока нет активного приглашения')
                print('PASS login: 3 independent sessions',flush=True)
                action(a,'invite').click();a.get_by_role('button',name='Создать приглашение').click()
                expect(a.locator('#sheet')).to_contain_text('Приглашение отправлено');close(a)
                m.get_by_role('button',name='Посмотреть приглашение').click()
                m.locator('[name="consent"]').check();m.get_by_role('button',name='Принять приглашение',exact=True).click()
                expect(m.get_by_test_id('shared-balance')).to_be_visible()
                print('PASS invitation and consent',flush=True)
                action(a,'allocate').click();a.locator('#monthly-budget-form [name="food"]').fill('20000')
                a.get_by_role('button',name='Сохранить бюджет').click();expect(a.locator('#sheet')).not_to_be_visible()
                expect(m.locator('.month-total')).to_contain_text('85')
                a.goto(base+'/?mobile=1#budget');action(a,'budget-tab','plan').click();action(a,'edit-plan').click()
                a.get_by_role('button',name='Сохранить черновик').click();expect(a.locator('#sheet')).not_to_be_visible()
                action(a,'submit-plan').click();expect(a.locator('main')).to_contain_text('На согласовании')
                m.goto(base+'/?mobile=1#budget');action(m,'budget-tab','plan').click();action(m,'approve-plan').click()
                expect(m.locator('main')).to_contain_text('Согласовали:');expect(a.locator('main')).to_contain_text('Согласовали:')
                print('PASS monthly budget and child plan approval',flush=True)
                a.goto(base+'/?mobile=1#home')
                action(a,'reminders').click()
                a.locator('#sheet [data-action="reminder"]').filter(has_text='Секции').first.click()
                action(a,'pay-reminder').click();a.get_by_role('button',name='Проверить перед оплатой').click()
                a.get_by_role('button',name='Подтвердить ·',exact=False).click()
                expect(a.locator('#sheet-title')).to_have_text('Операция выполнена');close(a)
                expect(a.get_by_test_id('shared-balance')).to_contain_text('2')
                pay(a,'allowance',2000)
                expect(k.locator('.child-bankcard')).to_contain_text('2\u00a0000')
                pay(k,'purchase',300,'Обед в школе')
                expect(k.locator('.child-bankcard')).to_contain_text('1\u00a0700')
                a.goto(base+'/?mobile=1#budget');action(a,'budget-tab','stats').click()
                expect(a.locator('.stats-circle')).to_contain_text('6\u00a0300')
                print('PASS payment, allowance, child purchase and expense total',flush=True)
                a.goto(base+'/?mobile=1#child/max');action(a,'child-settings','max').click()
                a.locator('[name="frozen"]').check();a.get_by_role('button',name='Сохранить настройки').click();close(a)
                action(k,'purchase').click();k.locator('[name="amount"]').fill('50');k.locator('[name="title"]').fill('Булочка')
                k.get_by_role('button',name='Проверить перед оплатой').click();k.get_by_role('button',name='Подтвердить ·',exact=False).click()
                expect(k.locator('#sheet-title')).to_have_text('Оплата не выполнена');expect(k.locator('#sheet')).to_contain_text('заморожена');close(k)
                a.goto(base+'/?mobile=1#notes');action(a,'edit-note').click()
                a.locator('#note-form [name="title"]').fill('Проверка интеграции');a.locator('#note-form [name="text"]').fill('Сохраняется в SQLite')
                a.get_by_role('button',name='Сохранить заметку').click();expect(a.locator('#sheet')).not_to_be_visible()
                m.goto(base+'/?mobile=1#notes');expect(m.locator('main')).to_contain_text('Проверка интеграции')
                m.reload();expect(m.locator('main')).to_contain_text('Проверка интеграции')
                print('PASS freeze, shared notes and reload persistence',flush=True)
                a.goto(base+'/?mobile=1#home');action(a,'members').click();action(a,'remove-member','maria').click();action(a,'confirm-remove','maria').click()
                expect(a.locator('[data-action="confirm-remove"]')).not_to_be_visible();close(a)
                expect(m.locator('main')).to_contain_text('Общий счёт, покупки и планы недоступны',timeout=10000)
                expect(m.locator('main')).not_to_contain_text('Проверка интеграции')
                print('PASS live access revocation',flush=True)
                out=ROOT/'.build'/'browser';out.mkdir(parents=True,exist_ok=True)
                a.goto(base+'/?mobile=1#home');a.screenshot(path=str(out/'mobile.png'),full_page=True)
                desktop=browser.new_page(viewport={'width':1440,'height':1000})
                desktop.goto(base+'/');desktop.get_by_role('button',name='Войти как Александр',exact=False).click()
                expect(desktop.get_by_test_id('shared-balance')).to_be_visible();desktop.screenshot(path=str(out/'desktop.png'),full_page=True)
                assert not errors,errors
                browser.close()
                print('PASS no JavaScript errors; mobile and desktop rendered',flush=True)
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
            log.close()


if __name__=='__main__':main()
