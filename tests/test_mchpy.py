import os
import tempfile
import unittest
import urllib.error
from decimal import Decimal
from unittest.mock import MagicMock, patch

import mchpy


def make_app(money_units=None, conversion=None, temperature=None):
    """Создаёт экземпляр без вызова __init__ (без GUI и файлового I/O)."""
    app = mchpy.MCHPYApp.__new__(mchpy.MCHPYApp)
    app.conversion_factors = conversion or {}
    app.money_data = {"units": money_units} if money_units else {}
    app.temperature_data = temperature or {}
    app.history_lines = []
    app.save_history = MagicMock()
    app.history_text = MagicMock()
    return app


def set_widgets(app, category, value, from_u, to_u):
    app.category_combo = MagicMock()
    app.category_combo.get.return_value = category
    app.input_entry = MagicMock()
    app.input_entry.get.return_value = value
    app.from_unit_combo = MagicMock()
    app.from_unit_combo.get.return_value = from_u
    app.to_unit_combo = MagicMock()
    app.to_unit_combo.get.return_value = to_u


class TemperatureConversionTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

    def test_same_unit_returns_original(self):
        result = self.app.convert_temperature(
            Decimal("42.5"), "Цельсий (°C)", "Цельсий (°C)"
        )
        self.assertEqual(result, Decimal("42.5"))
        self.assertIsInstance(result, Decimal)

    def test_freezing_point(self):
        result = self.app.convert_temperature(
            Decimal(0), "Цельсий (°C)", "Фаренгейт (°F)"
        )
        self.assertEqual(result, Decimal(32))

    def test_boiling_point(self):
        result = self.app.convert_temperature(
            Decimal(100), "Цельсий (°C)", "Фаренгейт (°F)"
        )
        self.assertEqual(result, Decimal(212))

    def test_minus_40_equal_scales(self):
        result = self.app.convert_temperature(
            Decimal(-40), "Цельсий (°C)", "Фаренгейт (°F)"
        )
        self.assertEqual(result, Decimal(-40))

    def test_absolute_zero(self):
        result = self.app.convert_temperature(Decimal(0), "Кельвин (K)", "Цельсий (°C)")
        self.assertEqual(result, Decimal("-273.15"))

    def test_body_temperature_exact_decimal(self):
        # 37 °C = 98.6 °F точно в Decimal; float дал бы ~98.59999999999999
        result = self.app.convert_temperature(
            Decimal(37), "Цельсий (°C)", "Фаренгейт (°F)"
        )
        self.assertEqual(result, Decimal("98.6"))
        self.assertIsInstance(result, Decimal)


class MoneyConversionTests(unittest.TestCase):
    def _run(self, value, from_u, to_u, rates):
        app = make_app(money_units=rates)
        set_widgets(app, "Валюта", value, from_u, to_u)
        app.convert()
        self.assertEqual(len(app.history_lines), 1)
        return app.history_lines[0]

    def test_zero_amount(self):
        line = self._run("0", "USD", "EUR", {"USD": 1.0, "EUR": 0.9})
        self.assertIn("= 0", line)

    def test_negative_amount(self):
        line = self._run("-100", "USD", "EUR", {"USD": 1.0, "EUR": 0.9})
        self.assertIn("-90", line)

    def test_identity_rate(self):
        line = self._run("123.45", "USD", "USD", {"USD": 1.0})
        self.assertIn("123.45", line)

    def test_large_number(self):
        line = self._run("1000000", "USD", "RUB", {"USD": 1.0, "RUB": 90.0})
        self.assertIn("90000000", line)

    def test_decimal_precision_no_float_artifacts(self):
        # 0.1 * 0.1 = 0.01 точно в Decimal
        line = self._run("0.1", "USD", "EUR", {"USD": 1.0, "EUR": 0.1})
        self.assertIn("0.01", line)
        self.assertNotIn("0.010000000000000002", line)


class GeneralConversionTests(unittest.TestCase):
    def test_meter_to_kilometer(self):
        app = make_app(
            conversion={"Длина": {"units": {"метр": 1.0, "километр": 1000.0}}}
        )
        set_widgets(app, "Длина", "1000", "метр", "километр")
        app.convert()
        self.assertEqual(len(app.history_lines), 1)
        self.assertIn("= 1", app.history_lines[0])

    def test_zero_conversion(self):
        app = make_app(
            conversion={"Длина": {"units": {"метр": 1.0, "километр": 1000.0}}}
        )
        set_widgets(app, "Длина", "0", "метр", "километр")
        app.convert()
        self.assertIn("= 0", app.history_lines[0])

    def test_fractional_precision(self):
        app = make_app(
            conversion={"Длина": {"units": {"метр": 1.0, "километр": 1000.0}}}
        )
        set_widgets(app, "Длина", "100", "метр", "километр")
        app.convert()
        self.assertIn("0.1", app.history_lines[0])


class InvalidInputTests(unittest.TestCase):
    def test_non_numeric(self):
        app = make_app(money_units={"USD": 1.0, "EUR": 0.9})
        set_widgets(app, "Валюта", "abc", "USD", "EUR")
        with patch("mchpy.messagebox") as mb:
            app.convert()
        self.assertEqual(app.history_lines, [])
        mb.showerror.assert_called_once()

    def test_empty_string(self):
        app = make_app(money_units={"USD": 1.0, "EUR": 0.9})
        set_widgets(app, "Валюта", "   ", "USD", "EUR")
        with patch("mchpy.messagebox") as mb:
            app.convert()
        self.assertEqual(app.history_lines, [])
        mb.showerror.assert_called_once()

    def test_unknown_unit(self):
        app = make_app(money_units={"USD": 1.0})
        set_widgets(app, "Валюта", "1", "USD", "XYZ")
        with patch("mchpy.messagebox") as mb:
            app.convert()
        self.assertEqual(app.history_lines, [])
        mb.showerror.assert_called_once()


class RefreshRatesTests(unittest.TestCase):
    def test_network_error_does_not_crash(self):
        app = make_app()
        app.root = MagicMock()

        with patch(
            "mchpy.urllib.request.urlopen",
            side_effect=urllib.error.URLError("Нет соединения"),
        ):
            app._refresh_rates()

        app.root.after.assert_not_called()


class CustomUnitTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app(
            conversion={"Длина": {"units": {"метр": 1.0, "Тестовая единица": 2.0}}}
        )

    def _run(self, value, from_u, to_u):
        set_widgets(self.app, "Длина", value, from_u, to_u)
        self.app.convert()
        self.assertEqual(len(self.app.history_lines), 1)
        return self.app.history_lines[0]

    def test_meter_to_custom_unit(self):
        line = self._run("-1", "метр", "Тестовая единица")
        self.assertIn("-0.5", line)

    def test_custom_unit_to_meter(self):
        line = self._run("2", "Тестовая единица", "метр")
        self.assertIn("4", line)

    def test_custom_unit_zero_conversion(self):
        line = self._run("0", "метр", "Тестовая единица")
        self.assertIn("= 0", line)

    def test_custom_unit_fractional_conversion(self):
        line = self._run("1", "метр", "Тестовая единица")
        self.assertIn("0.5", line)


class CustomUnitValidationTests(unittest.TestCase):
    def test_valid_custom_unit(self):
        name, coef = mchpy.MCHPYApp._validate_custom_unit(" Тестовая единица ", "2")
        self.assertEqual(name, "Тестовая единица")
        self.assertEqual(coef, Decimal(2))

    def test_empty_name(self):
        with self.assertRaisesRegex(ValueError, "Введите название"):
            mchpy.MCHPYApp._validate_custom_unit("", "2")

    def test_reserved_category_name(self):
        with self.assertRaisesRegex(ValueError, "Недопустимое название"):
            mchpy.MCHPYApp._validate_custom_unit("Валюта", "2")

    def test_non_numeric_coefficient(self):
        with self.assertRaisesRegex(ValueError, "должен быть числом"):
            mchpy.MCHPYApp._validate_custom_unit("Тестовая", "abc")

    def test_zero_coefficient(self):
        with self.assertRaisesRegex(ValueError, "больше 0"):
            mchpy.MCHPYApp._validate_custom_unit("Тестовая", "0")

    def test_negative_coefficient(self):
        with self.assertRaisesRegex(ValueError, "больше 0"):
            mchpy.MCHPYApp._validate_custom_unit("Тестовая", "-2")

    def test_infinite_coefficient(self):
        with self.assertRaisesRegex(ValueError, "больше 0"):
            mchpy.MCHPYApp._validate_custom_unit("Тестовая", "Infinity")


class CustomUnitManagementTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app(
            conversion={
                "Длина": {
                    "units": {
                        "метр": 1.0,
                        "километр": 1000.0,
                        "Тестовая единица": 2.0,
                    }
                }
            }
        )

    def test_create_custom_unit(self):
        self.app._save_conversion_factors = MagicMock()

        self.app._save_custom_unit("Длина", "Новая единица", Decimal(5))

        units = self.app.conversion_factors["Длина"]["units"]

        self.assertEqual(units["Новая единица"], 5.0)
        self.app._save_conversion_factors.assert_called_once()

    def test_update_existing_custom_unit(self):
        self.app._save_conversion_factors = MagicMock()

        self.app._save_custom_unit("Длина", "Тестовая единица", Decimal(3))

        units = self.app.conversion_factors["Длина"]["units"]

        self.assertEqual(units["Тестовая единица"], 3.0)
        self.assertEqual(len(units), 3)
        self.app._save_conversion_factors.assert_called_once()

    def test_delete_custom_unit(self):
        self.app._save_conversion_factors = MagicMock()

        result = self.app._delete_custom_unit("Длина", "Тестовая единица")

        units = self.app.conversion_factors["Длина"]["units"]

        self.assertTrue(result)
        self.assertNotIn("Тестовая единица", units)
        self.assertEqual(len(units), 2)
        self.app._save_conversion_factors.assert_called_once()

    def test_delete_unknown_unit(self):
        self.app._save_conversion_factors = MagicMock()

        result = self.app._delete_custom_unit("Длина", "Несуществующая единица")

        self.assertFalse(result)
        self.app._save_conversion_factors.assert_not_called()

    def test_cannot_delete_last_unit(self):
        self.app.conversion_factors["Длина"]["units"] = {"метр": 1.0}
        self.app._save_conversion_factors = MagicMock()

        result = self.app._delete_custom_unit("Длина", "метр")

        self.assertFalse(result)
        self.assertIn("метр", self.app.conversion_factors["Длина"]["units"])
        self.app._save_conversion_factors.assert_not_called()


class CustomUnitPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.base_dir = self.temp_dir.name

        initial_data = {
            "Длина": {
                "units": {
                    "метр": 1.0,
                    "километр": 1000.0,
                }
            }
        }

        mchpy._write_json(
            os.path.join(self.base_dir, "conversion.json"),
            initial_data,
        )
        mchpy._write_json(
            os.path.join(self.base_dir, "temperature.json"),
            {"units": {}},
        )
        mchpy._write_json(
            os.path.join(self.base_dir, "money.json"),
            {"units": {}},
        )

        self.app = make_app(conversion=initial_data)
        self.app.base_dir = self.base_dir

    def _reload_app(self):
        """Создаёт экземпляр приложения и загружает данные из файлов."""
        app = mchpy.MCHPYApp.__new__(mchpy.MCHPYApp)
        app.base_dir = self.base_dir
        app.load_all_data()
        return app

    def test_created_unit_is_saved_and_loaded(self):
        self.app._save_custom_unit("Длина", "Тестовая единица", Decimal(2))

        reloaded_app = self._reload_app()

        units = reloaded_app.conversion_factors["Длина"]["units"]

        self.assertEqual(units["Тестовая единица"], 2.0)

    def test_updated_coefficient_is_saved_and_loaded(self):
        self.app._save_custom_unit("Длина", "километр", Decimal(1500))

        reloaded_app = self._reload_app()

        units = reloaded_app.conversion_factors["Длина"]["units"]

        self.assertEqual(units["километр"], 1500.0)

    def test_deleted_unit_is_removed_from_file(self):
        self.app._save_custom_unit("Длина", "Тестовая единица", Decimal(2))

        result = self.app._delete_custom_unit("Длина", "Тестовая единица")

        self.assertTrue(result)

        reloaded_app = self._reload_app()

        units = reloaded_app.conversion_factors["Длина"]["units"]

        self.assertNotIn("Тестовая единица", units)
        self.assertIn("метр", units)
        self.assertIn("километр", units)


class JsonFileTests(unittest.TestCase):
    """Проверка чтения и записи JSON-файлов."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base_dir = self.temp_dir.name

    def test_read_json_success(self):
        path = os.path.join(self.base_dir, "test.json")
        expected = {"name": "Тест", "value": 10}

        mchpy._write_json(path, expected)

        result = mchpy._read_json(path)

        self.assertEqual(result, expected)

    def test_read_json_missing_file(self):
        path = os.path.join(self.base_dir, "missing.json")

        result = mchpy._read_json(path)

        self.assertIsNone(result)

    def test_read_json_invalid_json(self):
        path = os.path.join(self.base_dir, "invalid.json")

        with open(path, "w", encoding="utf-8") as file:
            file.write("{invalid json}")

        result = mchpy._read_json(path)

        self.assertIsNone(result)

    def test_read_json_os_error(self):
        path = os.path.join(self.base_dir, "test.json")

        with open(path, "w", encoding="utf-8") as file:
            file.write("{}")

        with patch("builtins.open", side_effect=OSError):
            result = mchpy._read_json(path)

        self.assertIsNone(result)

    def test_write_json_success(self):
        path = os.path.join(self.base_dir, "test.json")
        data = {"units": {"метр": 1.0}}

        result = mchpy._write_json(path, data)

        self.assertTrue(result)
        self.assertEqual(mchpy._read_json(path), data)

    def test_write_json_invalid_data(self):
        path = os.path.join(self.base_dir, "test.json")

        result = mchpy._write_json(path, {"value": object()})

        self.assertFalse(result)

    def test_write_json_os_error(self):
        path = os.path.join(self.base_dir, "test.json")

        with patch("builtins.open", side_effect=OSError):
            result = mchpy._write_json(path, {})

        self.assertFalse(result)


class LoadAllDataTests(unittest.TestCase):
    """Проверка загрузки данных приложения."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base_dir = self.temp_dir.name

        self.app = make_app()
        self.app.base_dir = self.base_dir

    def _write_data_file(self, filename, data):
        path = os.path.join(self.base_dir, filename)
        mchpy._write_json(path, data)

    def test_load_all_data_success(self):
        conversion = {
            "Длина": {
                "units": {
                    "метр": 1.0,
                    "километр": 1000.0,
                }
            }
        }
        temperature = {
            "units": {
                "celsius": "Градусы Цельсия (C)",
                "fahrenheit": "Градусы Фаренгейта (F)",
            }
        }
        money = {
            "units": {
                "USD": 1.0,
                "EUR": 0.9,
            }
        }

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_currency_cache(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        cached_money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": cached_money},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.money_data, cached_money)
        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        showerror.assert_not_called()

    def test_load_all_data_reports_missing_files(self):
        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, {})
        self.assertEqual(self.app.temperature_data, {})
        self.assertEqual(self.app.money_data, {})
        self.assertEqual(showerror.call_count, 3)

    def test_load_all_data_reports_invalid_json(self):
        self._write_data_file("temperature.json", {"units": {}})
        self._write_data_file("money.json", {"units": {}})

        conversion_path = os.path.join(self.base_dir, "conversion.json")
        with open(conversion_path, "w", encoding="utf-8") as file:
            file.write("{invalid json}")

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        showerror.assert_called_once()
        self.assertIn("conversion.json", showerror.call_args.args[1])


class CurrencyRateUpdateTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app.root = MagicMock()
        self.app.base_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.app.base_dir))

    def _mock_response(self, payload):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = mchpy.json.dumps(
            payload
        ).encode("utf-8")
        return response

    def test_refresh_rates_success(self):
        rates = {"USD": 1, "EUR": 0.9}
        response = self._mock_response({"rates": rates})

        with patch("mchpy.urllib.request.urlopen", return_value=response):
            self.app._refresh_rates()

        self.app.root.after.assert_called_once_with(
            0, self.app._apply_remote_rates, rates
        )

    def test_refresh_rates_without_rates(self):
        response = self._mock_response({"message": "No rates"})

        with patch("mchpy.urllib.request.urlopen", return_value=response):
            self.app._refresh_rates()

        self.app.root.after.assert_not_called()

    def test_refresh_rates_with_empty_rates(self):
        response = self._mock_response({"rates": {}})

        with patch("mchpy.urllib.request.urlopen", return_value=response):
            self.app._refresh_rates()

        self.app.root.after.assert_not_called()

    def test_refresh_rates_with_invalid_json(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"{invalid json}"

        with patch("mchpy.urllib.request.urlopen", return_value=response):
            self.app._refresh_rates()

        self.app.root.after.assert_not_called()

    def test_refresh_rates_network_error(self):
        with patch(
            "mchpy.urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection failed"),
        ):
            self.app._refresh_rates()

        self.app.root.after.assert_not_called()

    def test_apply_remote_rates_saves_cache(self):
        rates = {"USD": 1, "EUR": 0.9}
        self.app.category_combo = MagicMock()
        self.app.category_combo.__getitem__.return_value = ["Длина"]

        with patch("mchpy.time.time", return_value=123456):
            self.app._apply_remote_rates(rates)

        self.assertEqual(self.app.money_data, {"units": rates})

        cache_path = os.path.join(
            self.app.base_dir,
            self.app.RATES_CACHE_FILENAME,
        )
        cache = mchpy._read_json(cache_path)

        self.assertEqual(
            cache,
            {
                "timestamp": 123456,
                "data": {"units": rates},
            },
        )

    def test_apply_remote_rates_adds_currency_category(self):
        self.app.category_combo = MagicMock()
        self.app.category_combo.__getitem__.return_value = ["Длина"]

        self.app._apply_remote_rates({"USD": 1, "EUR": 0.9})

        self.app.category_combo.__setitem__.assert_called_once_with(
            "values",
            ["Длина", self.app.MONEY_CAT],
        )

    def test_apply_remote_rates_updates_selected_currency(self):
        self.app.category_combo = MagicMock()
        self.app.category_combo.__getitem__.return_value = [
            self.app.MONEY_CAT,
            "Длина",
        ]
        self.app.category_combo.get.return_value = self.app.MONEY_CAT
        self.app.update_units = MagicMock()

        self.app._apply_remote_rates({"USD": 1, "EUR": 0.9})

        self.app.update_units.assert_called_once()


class HistoryFileTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.app = make_app()
        self.app.history_file = os.path.join(
            self.temp_dir.name,
            "history.txt",
        )

    def test_ensure_history_file_creates_file(self):
        self.app.ensure_history_file()

        self.assertTrue(os.path.exists(self.app.history_file))

    def test_ensure_history_file_keeps_existing_file(self):
        with open(self.app.history_file, "w", encoding="utf-8") as file:
            file.write("Существующая запись")

        self.app.ensure_history_file()

        with open(self.app.history_file, encoding="utf-8") as file:
            self.assertEqual(file.read(), "Существующая запись")

    def test_load_history_reads_records(self):
        records = ["Запись 1", "Запись 2", "Запись 3"]

        with open(self.app.history_file, "w", encoding="utf-8") as file:
            file.write("\n".join(records))

        self.app.load_history()

        self.assertEqual(self.app.history_lines, records)

    def test_load_history_ignores_empty_lines(self):
        with open(self.app.history_file, "w", encoding="utf-8") as file:
            file.write("Запись 1\n\n   \nЗапись 2\n")

        self.app.load_history()

        self.assertEqual(
            self.app.history_lines,
            ["Запись 1", "Запись 2"],
        )

    def test_load_history_keeps_only_last_500_records(self):
        records = [f"Запись {i}" for i in range(600)]

        with open(self.app.history_file, "w", encoding="utf-8") as file:
            file.write("\n".join(records))

        self.app.load_history()

        self.assertEqual(len(self.app.history_lines), 500)
        self.assertEqual(self.app.history_lines[0], "Запись 100")
        self.assertEqual(self.app.history_lines[-1], "Запись 599")

    def test_load_history_missing_file(self):
        self.app.history_lines = ["Старая запись"]

        self.app.load_history()

        self.assertEqual(self.app.history_lines, [])

    def test_save_history_writes_records(self):
        self.app.history_lines = ["Запись 1", "Запись 2"]
        self.app.save_history = mchpy.MCHPYApp.save_history.__get__(
            self.app,
            mchpy.MCHPYApp,
        )

        self.app.save_history()

        with open(self.app.history_file, encoding="utf-8") as file:
            self.assertEqual(file.read(), "Запись 1\nЗапись 2\n")

    def test_add_to_history_adds_record_and_updates_interface(self):
        self.app._redraw_history = MagicMock()

        self.app.add_to_history("1 м = 100 см")

        self.assertEqual(
            self.app.history_lines,
            ["• 1 м = 100 см"],
        )
        self.app.save_history.assert_called_once()
        self.app._redraw_history.assert_called_once()

    def test_add_to_history_respects_limit(self):
        self.app.history_lines = [f"Запись {i}" for i in range(self.app.MAX_HISTORY)]

        self.app.add_to_history("Новая запись")

        self.assertEqual(len(self.app.history_lines), 500)
        self.assertEqual(self.app.history_lines[-1], "• Новая запись")
        self.assertEqual(self.app.history_lines[0], "Запись 1")

    def test_clear_history_when_empty(self):
        with patch.object(mchpy.messagebox, "showinfo") as showinfo:
            self.app.clear_history()

        showinfo.assert_called_once()
        self.app.save_history.assert_not_called()

    def test_clear_history_when_user_cancels(self):
        self.app.history_lines = ["Запись 1"]

        with patch.object(
            mchpy.messagebox,
            "askyesno",
            return_value=False,
        ):
            self.app.clear_history()

        self.assertEqual(self.app.history_lines, ["Запись 1"])
        self.app.save_history.assert_not_called()

    def test_clear_history_when_user_confirms(self):
        self.app.history_lines = ["Запись 1", "Запись 2"]
        self.app._redraw_history = MagicMock()

        with patch.object(
            mchpy.messagebox,
            "askyesno",
            return_value=True,
        ):
            self.app.clear_history()

        self.assertEqual(self.app.history_lines, [])
        self.app.save_history.assert_called_once()
        self.app._redraw_history.assert_called_once()


if __name__ == "__main__":
    unittest.main()
