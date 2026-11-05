import os
import tempfile
from pathlib import Path
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

    def test_unknown_temperature_unit(self):
        app = make_app()
        set_widgets(
            app,
            "Температура",
            "25",
            "Неизвестная единица",
            "Кельвин (K)",
        )

        with patch("mchpy.messagebox") as mb:
            app.convert()

        self.assertEqual(app.history_lines, [])
        mb.showerror.assert_called_once_with(
            "Ошибка",
            "Неизвестная единица измерения температуры",
        )

    def test_division_by_zero(self):
        app = make_app(
            conversion={
                "Длина": {
                    "units": {
                        "метр": 1.0,
                        "нулевая единица": 0.0,
                    }
                }
            }
        )
        set_widgets(app, "Длина", "10", "метр", "нулевая единица")

        with patch("mchpy.messagebox") as mb:
            app.convert()

        self.assertEqual(app.history_lines, [])
        mb.showerror.assert_called_once()
        self.assertIn("Деление на ноль", mb.showerror.call_args.args[1])


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

    def test_load_all_data_ignores_invalid_currency_cache(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)

        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": "invalid"},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_handles_invalid_conversion_structure(self):
        self._write_data_file("conversion.json", "invalid")
        self._write_data_file("temperature.json", {"units": {}})
        self._write_data_file("money.json", {"units": {"USD": 1.0}})

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, {})
        showerror.assert_called_once_with(
            "Ошибка",
            "Некорректная структура файла conversion.json",
        )

    def test_load_all_data_handles_invalid_temperature_structure(self):
        self._write_data_file("conversion.json", {})
        self._write_data_file("temperature.json", ["invalid"])
        self._write_data_file("money.json", {"units": {"USD": 1.0}})

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.temperature_data, {})
        showerror.assert_called_once_with(
            "Ошибка",
            "Некорректная структура файла temperature.json",
        )

    def test_load_all_data_ignores_invalid_money_structure(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", "invalid")

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, {})
        showerror.assert_called_once_with(
            "Ошибка",
            "Некорректная структура файла money.json",
        )

    def test_load_all_data_uses_money_file_when_cache_is_empty(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)

        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"units": {}}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cache_has_no_data(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_data_is_empty(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_data_is_null(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": None},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cache_has_no_units(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"timestamp": 123456}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_units_are_empty(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"units": {}}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_units_are_invalid(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"units": "invalid"}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_units_are_list(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"units": ["USD", "EUR"]}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_units_are_number(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"units": 123}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_units_are_boolean(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"units": True}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_units_are_empty_string(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"units": ""}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_uses_money_file_when_cached_units_is_empty_dict(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}
        temperature = {"units": {}}
        money = {"units": {"USD": 1.0, "EUR": 0.9}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", temperature)
        self._write_data_file("money.json", money)
        self._write_data_file(
            mchpy.MCHPYApp.RATES_CACHE_FILENAME,
            {"timestamp": 123456, "data": {"units": {}}},
        )

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, temperature)
        self.assertEqual(self.app.money_data, money)
        showerror.assert_not_called()

    def test_load_all_data_reports_invalid_conversion_file_structure(self):
        self._write_data_file("conversion.json", "invalid")
        self._write_data_file("temperature.json", {"units": {}})
        self._write_data_file("money.json", {"units": {"USD": 1.0}})

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, {})
        showerror.assert_called_once_with(
            "Ошибка",
            "Некорректная структура файла conversion.json",
        )

    def test_load_all_data_reports_missing_conversion_units(self):
        self._write_data_file("conversion.json", {"Длина": {"name": "Длина"}})
        self._write_data_file("temperature.json", {"units": {}})
        self._write_data_file("money.json", {"units": {"USD": 1.0}})

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, {})
        showerror.assert_called_once_with(
            "Ошибка",
            "Некорректная структура файла conversion.json",
        )

    def test_load_all_data_reports_invalid_temperature_units(self):
        conversion = {"Длина": {"units": {"метр": 1.0}}}

        self._write_data_file("conversion.json", conversion)
        self._write_data_file("temperature.json", {"units": "invalid"})
        self._write_data_file("money.json", {"units": {"USD": 1.0}})

        with patch.object(mchpy.messagebox, "showerror") as showerror:
            self.app.load_all_data()

        self.assertEqual(self.app.conversion_factors, conversion)
        self.assertEqual(self.app.temperature_data, {})
        showerror.assert_any_call(
            "Ошибка",
            "Некорректная структура файла temperature.json",
        )


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

    def test_save_conversion_factors_handles_write_error(self):
        self.app.conversion_factors = {"Длина": {"units": {"метр": 1.0}}}

        with (
            patch("mchpy._write_json", return_value=False),
            patch("mchpy.messagebox.showerror") as showerror,
        ):
            self.app._save_conversion_factors()

        showerror.assert_called_once_with(
            "Ошибка",
            "Не удалось сохранить conversion.json",
        )

    def test_refresh_rates_without_usd(self):
        response = self._mock_response({"rates": {"EUR": 0.9, "RUB": 90.0}})

        with patch("mchpy.urllib.request.urlopen", return_value=response):
            self.app._refresh_rates()

        self.app.root.after.assert_not_called()

    def test_apply_remote_rates_does_not_change_other_selected_category(self):
        self.app.category_combo = MagicMock()
        self.app.category_combo.__getitem__.return_value = [
            "Длина",
            self.app.MONEY_CAT,
        ]
        self.app.category_combo.get.return_value = "Длина"
        self.app.update_units = MagicMock()

        self.app._apply_remote_rates({"USD": 1, "EUR": 0.9})

        self.app.category_combo.__setitem__.assert_not_called()
        self.app.update_units.assert_not_called()


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

    def test_load_history_handles_os_error(self):
        with (
            patch("mchpy.os.path.exists", return_value=True),
            patch("builtins.open", side_effect=OSError("Ошибка чтения")),
            patch("mchpy.messagebox.showerror") as showerror,
        ):
            self.app.load_history()

        self.assertEqual(self.app.history_lines, [])
        showerror.assert_called_once()

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

    def test_ensure_history_file_handles_os_error(self):
        with (
            patch("mchpy.os.path.exists", return_value=False),
            patch("builtins.open", side_effect=OSError("Ошибка создания")),
            patch("mchpy.messagebox.showerror") as showerror,
        ):
            self.app.ensure_history_file()

        showerror.assert_called_once()


class HelperFunctionsTests(unittest.TestCase):
    """Проверка вспомогательных функций модуля."""

    def test_system_user_returns_username(self):
        with patch("mchpy.getpass.getuser", return_value="vera"):
            self.assertEqual(mchpy._system_user(), "vera")

    def test_system_user_returns_fallback_for_empty_username(self):
        with patch("mchpy.getpass.getuser", return_value=""):
            self.assertEqual(mchpy._system_user(), "user")

    def test_system_user_returns_fallback_on_os_error(self):
        with patch("mchpy.getpass.getuser", side_effect=OSError):
            self.assertEqual(mchpy._system_user(), "user")

    def test_system_user_returns_fallback_on_import_error(self):
        with patch("mchpy.getpass.getuser", side_effect=ImportError):
            self.assertEqual(mchpy._system_user(), "user")

    def test_safe_part_keeps_valid_characters(self):
        self.assertEqual(mchpy._safe_part("user_123-test"), "user_123-test")

    def test_safe_part_removes_special_characters(self):
        self.assertEqual(mchpy._safe_part("user name!@#"), "username")

    def test_safe_part_returns_fallback_for_empty_result(self):
        self.assertEqual(mchpy._safe_part("!@#$%^&*"), "user")

    def test_unit_label_extracts_text_from_parentheses(self):
        self.assertEqual(mchpy._unit_label("Метр (m)"), "m")

    def test_unit_label_uses_last_parentheses(self):
        self.assertEqual(mchpy._unit_label("Единица (описание) (unit)"), "unit")

    def test_unit_label_returns_original_text_without_parentheses(self):
        self.assertEqual(mchpy._unit_label("Метр"), "Метр")


class BaseUnitNameTests(unittest.TestCase):
    """Проверка определения базовой единицы."""

    def setUp(self):
        self.app = make_app()

    def test_returns_first_unit_with_coefficient_one(self):
        units = {
            "километр": 1000.0,
            "метр": 1.0,
            "сантиметр": 0.01,
        }

        self.assertEqual(self.app._base_unit_name(units), "метр")

    def test_returns_first_matching_unit(self):
        units = {
            "метр": 1.0,
            "условная единица": 1.0,
        }

        self.assertEqual(self.app._base_unit_name(units), "метр")

    def test_returns_fallback_when_no_base_unit_exists(self):
        units = {
            "километр": 1000.0,
            "сантиметр": 0.01,
        }

        self.assertEqual(
            self.app._base_unit_name(units),
            "базовая единица",
        )

    def test_ignores_invalid_coefficient(self):
        units = {
            "ошибочная единица": "invalid",
            "метр": 1.0,
        }

        self.assertEqual(self.app._base_unit_name(units), "метр")


class UpdateUnitsTests(unittest.TestCase):
    """Проверка обновления списков единиц измерения."""

    def setUp(self):
        self.app = make_app(
            conversion={
                "Длина": {
                    "units": {
                        "метр": 1.0,
                        "километр": 1000.0,
                    }
                }
            },
            temperature={
                "units": {
                    "celsius": "Цельсий (°C)",
                    "fahrenheit": "Фаренгейт (°F)",
                }
            },
            money_units={
                "USD": 1.0,
                "EUR": 0.9,
            },
        )

        self.app.category_combo = MagicMock()
        self.app.from_unit_combo = MagicMock()
        self.app.to_unit_combo = MagicMock()

    def test_update_length_units(self):
        self.app.category_combo.get.return_value = "Длина"

        self.app.update_units()

        self.app.from_unit_combo.__setitem__.assert_called_once_with(
            "values",
            ["метр", "километр"],
        )
        self.app.to_unit_combo.__setitem__.assert_called_once_with(
            "values",
            ["метр", "километр"],
        )
        self.app.from_unit_combo.current.assert_called_once_with(0)
        self.app.to_unit_combo.current.assert_called_once_with(1)

    def test_update_temperature_units(self):
        self.app.category_combo.get.return_value = self.app.TEMP_CAT

        self.app.update_units()

        expected = ["Цельсий (°C)", "Фаренгейт (°F)"]

        self.app.from_unit_combo.__setitem__.assert_called_once_with(
            "values",
            expected,
        )
        self.app.to_unit_combo.__setitem__.assert_called_once_with(
            "values",
            expected,
        )

    def test_update_currency_units(self):
        self.app.category_combo.get.return_value = self.app.MONEY_CAT

        self.app.update_units()

        expected = ["USD", "EUR"]

        self.app.from_unit_combo.__setitem__.assert_called_once_with(
            "values",
            expected,
        )
        self.app.to_unit_combo.__setitem__.assert_called_once_with(
            "values",
            expected,
        )

    def test_update_units_with_one_unit(self):
        self.app.conversion_factors = {"Длина": {"units": {"метр": 1.0}}}
        self.app.category_combo.get.return_value = "Длина"

        self.app.update_units()

        self.app.from_unit_combo.current.assert_called_once_with(0)
        self.app.to_unit_combo.current.assert_called_once_with(0)

    def test_update_units_with_empty_category(self):
        self.app.conversion_factors = {"Длина": {"units": {}}}
        self.app.category_combo.get.return_value = "Длина"

        self.app.update_units()

        self.app.from_unit_combo.__setitem__.assert_called_once_with(
            "values",
            [],
        )
        self.app.to_unit_combo.__setitem__.assert_called_once_with(
            "values",
            [],
        )
        self.app.from_unit_combo.current.assert_not_called()
        self.app.to_unit_combo.current.assert_not_called()


class RefreshUnitsAfterTests(unittest.TestCase):
    """Проверка обновления списков после изменения пользовательских единиц."""

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

        self.app.category_combo = MagicMock()
        self.app.from_unit_combo = MagicMock()
        self.app.to_unit_combo = MagicMock()

    def test_refresh_units_after_selects_requested_unit(self):
        self.app.category_combo.get.return_value = "Длина"

        self.app._refresh_units_after("Длина", "Тестовая единица")

        self.app.to_unit_combo.current.assert_called_once_with(2)
        self.app.from_unit_combo.current.assert_not_called()

    def test_refresh_units_after_uses_default_selection(self):
        self.app.category_combo.get.return_value = "Длина"

        self.app._refresh_units_after("Длина")

        self.app.from_unit_combo.current.assert_called_once_with(0)
        self.app.to_unit_combo.current.assert_called_once_with(1)

    def test_refresh_units_after_ignores_another_category(self):
        self.app.category_combo.get.return_value = "Масса"

        self.app._refresh_units_after("Длина")

        self.app.from_unit_combo.__setitem__.assert_not_called()
        self.app.to_unit_combo.__setitem__.assert_not_called()
        self.app.from_unit_combo.current.assert_not_called()
        self.app.to_unit_combo.current.assert_not_called()

    def test_refresh_units_after_ignores_unknown_selected_unit(self):
        self.app.category_combo.get.return_value = "Длина"

        self.app._refresh_units_after("Длина", "Несуществующая единица")

        self.app.from_unit_combo.current.assert_called_once_with(0)
        self.app.to_unit_combo.current.assert_called_once_with(1)

    def test_refresh_units_after_handles_empty_unit_list(self):
        self.app.conversion_factors["Длина"]["units"] = {}
        self.app.category_combo.get.return_value = "Длина"

        self.app._refresh_units_after("Длина")

        self.app.from_unit_combo.__setitem__.assert_called_once_with(
            "values",
            [],
        )
        self.app.to_unit_combo.__setitem__.assert_called_once_with(
            "values",
            [],
        )
        self.app.from_unit_combo.current.assert_not_called()
        self.app.to_unit_combo.current.assert_not_called()

    def test_network_response_with_non_dict_json(self):
        app = make_app()
        app.root = MagicMock()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'["invalid"]'

        with patch("mchpy.urllib.request.urlopen", return_value=response):
            app._refresh_rates()

        app.root.after.assert_not_called()


class DataDirTests(unittest.TestCase):
    @patch("mchpy.platform.system", return_value="Windows")
    @patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"}, clear=True)
    def test_windows_uses_appdata(self, _):
        result = mchpy._data_dir()

        self.assertEqual(
            result,
            os.path.join(r"C:\Users\Test\AppData\Roaming", "MCHPY"),
        )

    @patch("mchpy.platform.system", return_value="Windows")
    @patch.dict(os.environ, {}, clear=True)
    @patch("mchpy.os.path.expanduser", return_value=r"C:\Users\Test")
    def test_windows_without_appdata_uses_home(self, _, __):
        result = mchpy._data_dir()

        self.assertEqual(
            result,
            os.path.join(r"C:\Users\Test", "MCHPY"),
        )

    @patch("mchpy.platform.system", return_value="Linux")
    @patch.dict(os.environ, {"XDG_DATA_HOME": "/home/test/data"}, clear=True)
    def test_linux_uses_xdg_data_home(self, _):
        result = mchpy._data_dir()

        self.assertEqual(
            result,
            os.path.join("/home/test/data", "MCHPY"),
        )

    @patch("mchpy.platform.system", return_value="Linux")
    @patch.dict(os.environ, {}, clear=True)
    @patch("mchpy.os.path.expanduser", return_value="/home/test")
    def test_linux_without_xdg_uses_default_directory(self, _, __):
        result = mchpy._data_dir()

        self.assertEqual(
            result,
            os.path.join("/home/test", ".local", "share", "MCHPY"),
        )


class AdditionalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app.history_file = "test_history.txt"

    def test_save_history_handles_os_error(self):
        del self.app.save_history
        self.app.history_lines = ["Первая запись"]

        with (
            patch(
                "builtins.open",
                side_effect=OSError("Ошибка записи"),
            ) as mock_open,
            patch("mchpy.messagebox.showerror") as mock_error,
        ):
            self.app.save_history()

        mock_open.assert_called_once_with("test_history.txt", "w", encoding="utf-8")
        mock_error.assert_called_once()


class HistoryRedrawTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app.history_text = MagicMock()

    def test_redraw_history_with_entries(self):
        self.app.history_lines = ["1 m = 0.001 km", "0 C = 32 F"]

        self.app._redraw_history()

        self.app.history_text.config.assert_any_call(state=mchpy.tk.NORMAL)
        self.app.history_text.delete.assert_called_once_with("1.0", mchpy.tk.END)
        self.app.history_text.insert.assert_any_call(mchpy.tk.END, "1 m = 0.001 km\n")
        self.app.history_text.insert.assert_any_call(mchpy.tk.END, "0 C = 32 F\n")
        self.app.history_text.see.assert_called_once_with(mchpy.tk.END)
        self.app.history_text.config.assert_any_call(state=mchpy.tk.DISABLED)

    def test_redraw_empty_history(self):
        self.app.history_lines = []

        self.app._redraw_history()

        self.app.history_text.delete.assert_called_once_with("1.0", mchpy.tk.END)
        self.app.history_text.insert.assert_not_called()


class UpdateUnitsCallbackTests(unittest.TestCase):
    def test_callback_calls_update_units(self):
        app = make_app()
        app.update_units = MagicMock()
        event = object()

        app.update_units_callback(event)

        app.update_units.assert_called_once_with()


class CenterOnParentTests(unittest.TestCase):
    def test_dialog_is_centered_on_parent(self):
        app = make_app()
        dialog = MagicMock()
        parent = MagicMock()

        parent.winfo_rootx.return_value = 100
        parent.winfo_rooty.return_value = 200
        parent.winfo_width.return_value = 500
        parent.winfo_height.return_value = 400

        dialog.winfo_reqwidth.return_value = 200
        dialog.winfo_reqheight.return_value = 100

        app._center_on_parent(dialog, parent)

        dialog.update_idletasks.assert_called_once_with()
        dialog.geometry.assert_called_once_with("+250+350")

    def test_dialog_coordinates_do_not_go_below_zero(self):
        app = make_app()
        dialog = MagicMock()
        parent = MagicMock()

        parent.winfo_rootx.return_value = 0
        parent.winfo_rooty.return_value = 0
        parent.winfo_width.return_value = 100
        parent.winfo_height.return_value = 80

        dialog.winfo_reqwidth.return_value = 300
        dialog.winfo_reqheight.return_value = 200

        app._center_on_parent(dialog, parent)

        dialog.geometry.assert_called_once_with("+0+0")


class UtilityFunctionsTests(unittest.TestCase):
    def test_system_user_returns_user(self):
        with patch.object(mchpy.getpass, "getuser", return_value="Vera"):
            self.assertEqual(mchpy._system_user(), "Vera")

    def test_system_user_returns_fallback_on_error(self):
        with patch.object(
            mchpy.getpass,
            "getuser",
            side_effect=OSError,
        ):
            self.assertEqual(mchpy._system_user(), "user")

    def test_safe_part_removes_invalid_characters(self):
        self.assertEqual(mchpy._safe_part("Vera Smith!"), "VeraSmith")
        self.assertEqual(mchpy._safe_part(""), "user")

    def test_unit_label_extracts_short_label(self):
        self.assertEqual(mchpy._unit_label("метр (м)"), "м")
        self.assertEqual(mchpy._unit_label("килограмм"), "килограмм")

    def test_read_json_returns_none_for_missing_file(self):
        path = os.path.join(self.temp_dir.name, "missing.json")
        self.assertIsNone(mchpy._read_json(path))

    def test_write_json_returns_false_for_invalid_data(self):
        path = os.path.join(self.temp_dir.name, "test.json")
        self.assertFalse(mchpy._write_json(path, {1, 2, 3}))

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()


class AdditionalCoverageTests(unittest.TestCase):
    def test_data_dir_windows_uses_appdata(self):
        with (
            patch.object(mchpy.platform, "system", return_value="Windows"),
            patch.dict(mchpy.os.environ, {"APPDATA": "C:/Users/Vera/AppData/Roaming"}),
        ):
            self.assertEqual(
                mchpy._data_dir(),
                os.path.join("C:/Users/Vera/AppData/Roaming", "MCHPY"),
            )

    def test_data_dir_linux_uses_xdg(self):
        with (
            patch.object(mchpy.platform, "system", return_value="Linux"),
            patch.dict(mchpy.os.environ, {"XDG_DATA_HOME": "/home/test/.data"}),
        ):
            self.assertEqual(
                mchpy._data_dir(),
                os.path.join("/home/test/.data", "MCHPY"),
            )

    def test_base_unit_name_finds_coefficient_one(self):
        app = object.__new__(mchpy.MCHPYApp)
        self.assertEqual(
            app._base_unit_name({"сантиметр": 0.01, "метр": 1.0}),
            "метр",
        )

    def test_base_unit_name_skips_invalid_coefficients(self):
        app = object.__new__(mchpy.MCHPYApp)
        self.assertEqual(
            app._base_unit_name({"ошибка": "abc", "ноль": 0.5}),
            "базовая единица",
        )


class TemperatureCoverageTests(unittest.TestCase):
    def test_convert_temperature_celsius_to_celsius(self):
        app = object.__new__(mchpy.MCHPYApp)
        result = app.convert_temperature(Decimal("25"), "°C", "°C")
        self.assertEqual(result, Decimal("25"))

    def test_convert_temperature_celsius_to_fahrenheit(self):
        app = object.__new__(mchpy.MCHPYApp)
        result = app.convert_temperature(Decimal("0"), "°C", "°F")
        self.assertEqual(result, Decimal("32"))


class InterfaceBuilderTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app.root = MagicMock()

    def test_build_top_bar(self):
        with (
            patch("mchpy.ttk.Frame") as frame,
            patch("mchpy.ttk.Button") as button,
        ):
            self.app.clear_history = MagicMock()

            self.app._build_top_bar()

        frame.assert_called_once()
        button.assert_called_once()
        button.return_value.pack.assert_called_once_with(fill=mchpy.tk.X)

    def test_build_history_frame(self):
        with (
            patch("mchpy.ttk.LabelFrame") as label_frame,
            patch("mchpy.tk.Text") as text_widget,
            patch("mchpy.ttk.Scrollbar") as scrollbar,
        ):
            self.app._build_history_frame()

        label_frame.assert_called_once()
        text_widget.assert_called_once()
        scrollbar.assert_called_once()
        text_widget.return_value.configure.assert_called_once()
        text_widget.return_value.pack.assert_called_once()
        scrollbar.return_value.pack.assert_called_once()

    def test_build_controls_frame(self):
        self.app.conversion_factors = {"Длина": {"units": {"метр": 1.0}}}
        self.app.money_data = {"units": {"USD": 1.0}}
        self.app.temperature_data = {"units": {"celsius": "Цельсий"}}

        with (
            patch("mchpy.ttk.Frame"),
            patch("mchpy.ttk.Label"),
            patch("mchpy.ttk.Combobox") as combo,
            patch("mchpy.ttk.Entry"),
            patch("mchpy.ttk.Button"),
        ):
            self.app._build_controls_frame()

        self.assertEqual(combo.call_count, 3)
        self.assertEqual(
            self.app.category_combo.__setitem__.call_args,
            unittest.mock.call(
                "values",
                ["Длина", "Валюта", "Температура"],
            ),
        )
        self.app.category_combo.current.assert_called_once_with(0)


class TemperatureMissingBranchesTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

    def test_celsius_to_fahrenheit(self):
        result = self.app.convert_temperature(
            Decimal("20"),
            "Цельсий",
            "Фаренгейт",
        )

        self.assertEqual(result, Decimal("68"))

    def test_fahrenheit_to_celsius(self):
        result = self.app.convert_temperature(
            Decimal("68"),
            "Фаренгейт",
            "Цельсий",
        )

        self.assertEqual(result, Decimal("20"))

    def test_celsius_to_kelvin(self):
        app = make_app()

        result = app.convert_temperature(
            Decimal("20"),
            "Цельсий",
            "Кельвин",
        )

        self.assertEqual(result, Decimal("293.15"))


class AppInitCoverageTests(unittest.TestCase):
    def _run_init(self, makedirs_side_effect=None):
        root = MagicMock()

        with (
            patch("mchpy._data_dir", return_value="test_history"),
            patch("mchpy._system_user", return_value="test_user"),
            patch("mchpy.os.makedirs", side_effect=makedirs_side_effect) as makedirs,
            patch("mchpy.messagebox.showerror") as showerror,
            patch.object(mchpy.MCHPYApp, "load_all_data"),
            patch.object(mchpy.MCHPYApp, "ensure_history_file"),
            patch.object(mchpy.MCHPYApp, "load_history"),
            patch.object(mchpy.MCHPYApp, "_build_top_bar"),
            patch.object(mchpy.MCHPYApp, "_build_history_frame"),
            patch.object(mchpy.MCHPYApp, "_build_controls_frame"),
            patch.object(mchpy.MCHPYApp, "_redraw_history"),
            patch.object(mchpy.MCHPYApp, "update_units"),
            patch("mchpy.threading.Thread") as thread,
        ):
            app = mchpy.MCHPYApp(root)

        return app, root, makedirs, showerror, thread

    def test_init_success(self):
        app, root, makedirs, showerror, thread = self._run_init()

        root.title.assert_called_once()
        root.geometry.assert_called_once_with("450x600")
        root.minsize.assert_called_once_with(450, 600)

        makedirs.assert_called_once_with("test_history", exist_ok=True)
        showerror.assert_not_called()

        self.assertEqual(app.history_dir, "test_history")
        self.assertEqual(
            app.history_file, os.path.join("test_history", "history_test_user.txt")
        )
        self.assertEqual(app.history_lines, [])

        thread.assert_called_once_with(
            target=app._refresh_rates,
            daemon=True,
        )
        thread.return_value.start.assert_called_once()

    def test_init_directory_error(self):
        app, root, makedirs, showerror, thread = self._run_init(OSError("Нет доступа"))

        makedirs.assert_called_once()
        showerror.assert_called_once()

        self.assertEqual(app.history_dir, "test_history")
        self.assertEqual(app.history_lines, [])

        thread.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Тесты графического диалога пользовательских единиц измерения
# ---------------------------------------------------------------------------


class FakeDialogVar:
    """Минимальная имитация tkinter.StringVar с поддержкой trace_add."""

    def __init__(self):
        self.value = ""
        self.callbacks = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.callbacks:
            callback()

    def trace_add(self, mode, callback):
        self.callbacks.append(callback)


class FakeDialogWidget:
    """Минимальная имитация виджетов ttk для тестирования без GUI."""

    def __init__(self, master=None, **kwargs):
        self.master = master
        self.kwargs = kwargs
        self.command = kwargs.get("command")
        self.value = ""
        self.visible = True

    def pack(self, **kwargs):
        return None

    def grid(self, **kwargs):
        self.visible = True

    def grid_remove(self):
        self.visible = False

    def insert(self, index, value):
        self.value = value

    def get(self):
        return self.value

    def focus_set(self):
        return None

    def columnconfigure(self, *args, **kwargs):
        return None

    def grid_rowconfigure(self, *args, **kwargs):
        return None


class FakeCustomUnitDialog(FakeDialogWidget):
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.bindings = {}
        self.destroyed = False
        self.window_title = None
        self.window_geometry = None

    def title(self, value):
        self.window_title = value

    def transient(self, parent):
        return None

    def grab_set(self):
        return None

    def resizable(self, *args):
        return None

    def bind(self, event, callback):
        self.bindings[event] = callback

    def destroy(self):
        self.destroyed = True

    def update_idletasks(self):
        return None

    def winfo_reqwidth(self):
        return 200

    def winfo_reqheight(self):
        return 100

    def geometry(self, value):
        self.window_geometry = value


class FakeDialogRoot:
    def winfo_rootx(self):
        return 100

    def winfo_rooty(self):
        return 100

    def winfo_width(self):
        return 500

    def winfo_height(self):
        return 400


class AddCustomUnitDialogTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app(
            conversion={
                "Длина": {
                    "units": {
                        "метр": 1.0,
                        "километр": 1000.0,
                    }
                }
            }
        )
        self.app.root = FakeDialogRoot()
        self.app.category_combo = MagicMock()
        self.app.category_combo.get.return_value = "Длина"
        self.app._save_conversion_factors = MagicMock()
        self.app._refresh_units_after = MagicMock()

        self.dialog = FakeCustomUnitDialog()
        self.variables = []
        self.entries = []
        self.buttons = {}

        def make_var():
            variable = FakeDialogVar()
            self.variables.append(variable)
            return variable

        def make_entry(master=None, **kwargs):
            entry = FakeDialogWidget(master, **kwargs)
            self.entries.append(entry)
            return entry

        def make_button(master=None, **kwargs):
            button = FakeDialogWidget(master, **kwargs)
            self.buttons[kwargs["text"]] = button
            return button

        self.patches = [
            patch("mchpy.tk.Toplevel", return_value=self.dialog),
            patch("mchpy.tk.StringVar", side_effect=make_var),
            patch("mchpy.ttk.Frame", FakeDialogWidget),
            patch("mchpy.ttk.Label", FakeDialogWidget),
            patch("mchpy.ttk.Entry", side_effect=make_entry),
            patch("mchpy.ttk.Button", side_effect=make_button),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def open_dialog(self):
        self.app.add_custom_unit()
        return self.variables[0], self.variables[1], self.variables[2]

    def test_empty_category_returns_without_opening_dialog(self):
        self.app.category_combo.get.return_value = ""

        self.app.add_custom_unit()

        self.assertIsNone(self.dialog.window_title)

    def test_currency_category_is_rejected(self):
        self.app.category_combo.get.return_value = self.app.MONEY_CAT

        with patch("mchpy.messagebox.showinfo") as showinfo:
            self.app.add_custom_unit()

        showinfo.assert_called_once()
        self.assertIsNone(self.dialog.window_title)

    def test_temperature_category_is_rejected(self):
        self.app.category_combo.get.return_value = self.app.TEMP_CAT

        with patch("mchpy.messagebox.showinfo") as showinfo:
            self.app.add_custom_unit()

        showinfo.assert_called_once()
        self.assertIsNone(self.dialog.window_title)

    def test_dialog_is_created_with_controls_and_centered(self):
        self.open_dialog()

        self.assertEqual(self.dialog.window_title, "Единица — Длина")
        self.assertEqual(self.dialog.window_geometry, "+250+250")
        self.assertEqual(
            set(self.buttons),
            {"Удалить", "Отмена", "Сохранить"},
        )
        self.assertIn("<Return>", self.dialog.bindings)
        self.assertIn("<Escape>", self.dialog.bindings)
        self.assertEqual(self.entries[1].get(), "1")

    def test_existing_unit_name_displays_update_hint_and_delete_button(self):
        _, hint_var, _ = self.open_dialog()

        self.variables[0].set("метр")

        self.assertIn("уже существует", hint_var.get())
        self.assertTrue(self.buttons["Удалить"].visible)

    def test_new_unit_name_clears_hint_and_hides_delete_button(self):
        _, hint_var, _ = self.open_dialog()
        self.variables[0].set("метр")
        self.variables[0].set("сантиметр")

        self.assertEqual(hint_var.get(), "")
        self.assertFalse(self.buttons["Удалить"].visible)

    def test_invalid_name_is_shown_in_error_label(self):
        _, _, error_var = self.open_dialog()

        self.buttons["Сохранить"].command()

        self.assertEqual(error_var.get(), "Введите название.")
        self.assertFalse(self.dialog.destroyed)
        self.app._refresh_units_after.assert_not_called()

    def test_invalid_coefficient_is_shown_in_error_label(self):
        _, _, error_var = self.open_dialog()
        self.variables[0].set("сантиметр")
        self.entries[1].value = "не число"

        self.buttons["Сохранить"].command()

        self.assertEqual(error_var.get(), "Коэффициент должен быть числом.")
        self.assertFalse(self.dialog.destroyed)

    def test_valid_new_unit_is_saved_and_dialog_is_closed(self):
        self.open_dialog()
        self.variables[0].set("сантиметр")
        self.entries[1].value = "0.01"

        with patch.object(self.app, "_save_custom_unit") as save_unit:
            self.buttons["Сохранить"].command()

        save_unit.assert_called_once_with("Длина", "сантиметр", Decimal("0.01"))
        self.assertTrue(self.dialog.destroyed)
        self.app._refresh_units_after.assert_called_once_with("Длина", "сантиметр")

    def test_existing_unit_can_be_updated(self):
        self.open_dialog()
        self.variables[0].set("километр")
        self.entries[1].value = "1500"

        with patch.object(self.app, "_save_custom_unit") as save_unit:
            self.buttons["Сохранить"].command()

        save_unit.assert_called_once_with("Длина", "километр", Decimal("1500"))
        self.assertTrue(self.dialog.destroyed)

    def test_cancel_button_closes_dialog(self):
        self.open_dialog()

        self.buttons["Отмена"].command()

        self.assertTrue(self.dialog.destroyed)

    def test_escape_key_closes_dialog(self):
        self.open_dialog()

        self.dialog.bindings["<Escape>"](None)

        self.assertTrue(self.dialog.destroyed)

    def test_return_key_invokes_save_callback(self):
        self.open_dialog()
        self.variables[0].set("сантиметр")
        self.entries[1].value = "0.01"

        with patch.object(self.app, "_save_custom_unit") as save_unit:
            self.dialog.bindings["<Return>"](None)

        save_unit.assert_called_once_with("Длина", "сантиметр", Decimal("0.01"))

    def test_delete_callback_does_nothing_for_unknown_unit(self):
        self.open_dialog()
        self.variables[0].set("несуществующая")

        with patch("mchpy.messagebox.askyesno") as askyesno:
            self.buttons["Удалить"].command()

        askyesno.assert_not_called()
        self.assertFalse(self.dialog.destroyed)

    def test_delete_can_be_cancelled(self):
        self.open_dialog()
        self.variables[0].set("километр")

        with (
            patch("mchpy.messagebox.askyesno", return_value=False) as askyesno,
            patch.object(self.app, "_delete_custom_unit") as delete_unit,
        ):
            self.buttons["Удалить"].command()

        askyesno.assert_called_once()
        delete_unit.assert_not_called()
        self.assertFalse(self.dialog.destroyed)

    def test_failed_delete_displays_warning(self):
        self.open_dialog()
        self.variables[0].set("километр")

        with (
            patch("mchpy.messagebox.askyesno", return_value=True),
            patch.object(self.app, "_delete_custom_unit", return_value=False),
            patch("mchpy.messagebox.showwarning") as showwarning,
        ):
            self.buttons["Удалить"].command()

        showwarning.assert_called_once()
        self.assertFalse(self.dialog.destroyed)

    def test_successful_delete_closes_dialog_and_refreshes_units(self):
        self.open_dialog()
        self.variables[0].set("километр")

        with (
            patch("mchpy.messagebox.askyesno", return_value=True),
            patch.object(
                self.app, "_delete_custom_unit", return_value=True
            ) as delete_unit,
        ):
            self.buttons["Удалить"].command()

        delete_unit.assert_called_once_with("Длина", "километр")
        self.assertTrue(self.dialog.destroyed)
        self.app._refresh_units_after.assert_called_once_with("Длина")


class MainEntryPointCoverageTests(unittest.TestCase):
    def test_main_entry_point_creates_window_app_and_starts_loop(self):
        """Проверяет три строки точки входа без запуска настоящего GUI."""
        import ast

        source_path = mchpy.__file__
        source = Path(source_path).read_text(encoding="utf-8")
        syntax_tree = ast.parse(source)

        main_guard = next(
            node
            for node in syntax_tree.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "__main__"
        )

        main_module = ast.Module(body=[main_guard], type_ignores=[])
        code = compile(
            ast.fix_missing_locations(main_module),
            source_path,
            "exec",
        )

        fake_root = MagicMock()
        fake_tk = MagicMock()
        fake_tk.Tk.return_value = fake_root
        fake_app_class = MagicMock()

        exec(
            code,
            {
                "__name__": "__main__",
                "tk": fake_tk,
                "MCHPYApp": fake_app_class,
            },
        )

        fake_tk.Tk.assert_called_once_with()
        fake_app_class.assert_called_once_with(fake_root)
        fake_root.mainloop.assert_called_once_with()
