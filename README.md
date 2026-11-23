# Фото на документы

Программа для фотосалона: кадр под формат, готовая фотография, лист на печать. Снимок обрабатывается на этом компьютере.

Linux, Windows, macOS. Python 3.11 или новее. Лицензия [MIT](LICENSE). Сторонние библиотеки и модели перечислены в [NOTICE](NOTICE).

## Запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/photofordocs
```

На Windows те же команды через `py -3 -m venv .venv` и `.venv\Scripts\photofordocs`.

Настройки лежат в `~/.config/photofordocs` (на Windows — в каталоге профиля). Свои форматы добавляются в справочнике. Чужой справочник можно перенести через **Сервис → Перенос настроек**.

## Проверка

```bash
.venv/bin/python -m unittest discover -s tests
```
