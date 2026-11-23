# Фото на документы

Подготовка фотографий на документы: формат, кадр, лист на печать.

Linux, Windows, macOS. Python 3.11+. [Лицензия](LICENSE): пользоваться можно, продавать программу нельзя.

## Запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/photofordocs
```

На Windows: `py -3 -m venv .venv` и `.venv\Scripts\photofordocs`.

## Проверка

```bash
.venv/bin/python -m unittest discover -s tests
```
