# garmin-mcp

Особистий MCP-сервер для Garmin Connect. Працює локально на твоєму комп'ютері, без посередників.
Побудований на [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) 0.3.x
(неофіційне API, той самий логін, що й у мобільного застосунку Garmin).

## Що вміє

**Читання**

| Tool | Що повертає |
|---|---|
| `list_activities` | активності за період, компактно (темп, пульс, TE, load) |
| `get_activity` | одна активність: підсумок, кола/спліти, час у пульсових зонах |
| `get_activity_timeseries` | посекундні дані (HR, швидкість, каденс, потужність, висота), з даунсемплом |
| `get_strength_sets` | підходи силової, як їх записав годинник, + альтернативні вправи |
| `get_daily_summary` | день: кроки, RHR, стрес, Body Battery, сон, HRV, Training Readiness |
| `get_sleep`, `get_wellness_range` | сон за ніч, щоденні RHR/HRV/сон/BB за період (до 60 днів) |
| `get_training_status` | Training Status, acute/chronic load, ACWR, load focus, VO2max |
| `get_performance` | прогнози на 5K–марафон, лактатний поріг, Endurance Score |
| `list_workouts`, `get_workout`, `get_calendar` | бібліотека тренувань і календар |
| `search_exercises` | пошук у каталозі вправ Garmin (1527 шт.) |
| `garmin_get` | доступ до будь-якого `get_*`-методу бібліотеки на випадок, коли інших тулів забракне |

**Запис.** Кожен такий тул спершу показує preview, а застосовує зміни лише з `confirm=true`.

| Tool | Що робить |
|---|---|
| `edit_strength_sets` | виправляє вправи, повторення й вагу в силовій (перед записом робить бекап оригіналу) |
| `update_activity_info` | назва й опис активності |
| `create_run_workout` | структуроване бігове тренування (пейс / пульсова зона / пульс, повтори), за потреби одразу в календар |
| `create_strength_workout` | силове тренування з вправами, підходами й вагою |
| `schedule_workout`, `unschedule_workout`, `delete_workout` | керування календарем і бібліотекою |

## Встановлення

Потрібен [uv](https://docs.astral.sh/uv/) (він сам поставить Python 3.12+).

```bash
cd garmin-mcp
uv sync
uv run garmin-mcp-login        # email, пароль, код MFA, якщо ввімкнений
```

Пароль використовується один раз і ніде не зберігається. Логін записує токени в
`~/.garminconnect/garmin_tokens.json` (права 0600), і сервер працює лише з ними.
Ставися до цієї папки як до пароля. Відкликати доступ можна в налаштуваннях безпеки акаунта Garmin.

### Claude Desktop

`claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/garmin-mcp", "run", "garmin-mcp"]
    }
  }
}
```

Після цього перезапусти Claude Desktop. Коли застосунок відкритий, сервер доступний і з хмарних чатів, прив'язаних до цього комп'ютера.

### Claude Code

```bash
claude mcp add garmin -- uv --directory /ABSOLUTE/PATH/TO/garmin-mcp run garmin-mcp
```

## Приклади

- «Що в мене було за останні 2 тижні, і як змінювався пульс на Base-пробіжках?»
- «Розбери вчорашні 2×15 TH: темп і пульс по відрізках, чи був дрейф»
- «Сьогоднішня силова: фронтальний присід 4×4 по 90 кг, виправ у Garmin»
- «Зроби на п'ятницю тренування: 15 хв розминка Z2, 2×15 хв 4:10–4:20 через 3 хв, 10 хв заминка»

## Розробка

```bash
uv sync --extra dev
uv run pytest
```

Логіка без мережі (правка підходів, збирання тренувань) винесена в `strength.py` і `workouts.py` і покрита тестами.

## Застереження

- API неофіційне: Garmin періодично змінює авторизацію, і тоді потрібно оновити `garminconnect` (`uv lock --upgrade-package garminconnect`).
- Garmin обмежує частоту запитів. `get_wellness_range` робить ~3 запити на день, тому довгі періоди краще не тягнути.
- `edit_strength_sets` замінює весь масив підходів. Оригінал зберігається в `~/.garmin-mcp/backups/`.
  Вагу Garmin зберігає в грамах: сервер це враховує і після запису перечитує активність, щоб перевірити результат.
