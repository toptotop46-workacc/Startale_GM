# StartaleGM

Автоматический мониторинг GM для Startale:

- запускает браузер через **AdsPower** (локальный API)
- импортирует кошелёк в расширение **Rabby** по приватному ключу
- проходит сценарий подключения на `portal.soneium.org` / `app.startale.com`
- при доступности GM нажимает **Send GM back**
- сохраняет состояние по каждому кошельку (когда следующий GM доступен, создан ли smart-account) в `startalegm.json`
- крутится в цикле и сам запускает аккаунты, когда наступает время GM

## Требования

- Python 3.11+
- [AdsPower](https://www.adspower.net/) (с запущенным локальным API)
- API ключ AdsPower

## Установка

1. Установите зависимости:

```bash
pip install -r requirements.txt
```

2. Установите браузер для Playwright:

```bash
playwright install chromium
```

## Настройка

### 1) Приватные ключи (НЕ сохраняются в JSON)

В файле `keys.txt` в корне проекта — по одному ключу на строку (hex 64 символа, с `0x` или без).

### 2) API ключ AdsPower

В файле `adspower_api_key.txt` — ваш API ключ AdsPower.

### 3) Прокси для API profile/mapping (опционально)

В файле `proxy.txt` можно указать прокси, которые используются для запроса:

Формат строк:

- `host:port`
- `host:port:user:pass`

## Запуск

```bash
python main.py
```

Скрипт работает в режиме **мониторинга**:

- каждые ~10 секунд проверяет, для каких кошельков пора GM (по `startalegm.json`)
- если пора — запускает браузер AdsPower, выполняет сценарий и обновляет `startalegm.json`
- остановка: **Ctrl+C** (браузер останавливается, профиль удаляется, мониторинг завершается)

## Файл состояния `startalegm.json`

Создаётся автоматически в корне проекта. Пример структуры:

```json
{
  "accounts": {
    "0xYourEoaAddress": {
      "next_gm_available_at": "2026-02-24T23:59:59.000000+00:00",
      "smart_account_created": true,
      "updated_at": "2026-02-24T17:13:59.226996+00:00"
    }
  }
}
```

Пояснения:

- `next_gm_available_at`: момент (UTC), когда следующий GM должен стать доступен
- `smart_account_created`: известен ли smart-account для кошелька
- `updated_at`: когда запись обновлялась последний раз

Если `next_gm_available_at = null`, аккаунт будет считаться “должным” и мониторинг попробует обработать его снова.

## Структура

```
StartaleGM/
├── main.py              # Точка входа
├── requirements.txt
├── keys.txt             # Приватные ключи
├── adspower_api_key.txt # API ключ AdsPower
├── proxy.txt            # (опционально) прокси для profile/mapping
├── startalegm.json      # состояние/расписание по кошелькам
└── modules/
    ├── __init__.py
    ├── db.py            # JSON-хранилище (startalegm.json)
    └── startalegm.py    # Вся логика сценария + мониторинг
```

## Частые проблемы

### `Timeout ... "I already have an address"`
Rabby мог быть уже инициализирован/в другом состоянии или страница расширения не успела прогрузиться.
Обычно помогает повторная попытка (мониторинг сам повторит).

### JSON пустой/битый → `JSONDecodeError`
Скрипт автоматически лечит пустой/битый `startalegm.json`, считая его пустым состоянием.
