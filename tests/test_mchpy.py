import unittest
import urllib.error
from decimal import Decimal
from unittest.mock import MagicMock, patch
import tempfile
import mchpy
import os


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
        self.assertEqual(coef, Decimal("2"))

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

        self.app._save_custom_unit("Длина", "Новая единица", Decimal("5"))

        units = self.app.conversion_factors["Длина"]["units"]

        self.assertEqual(units["Новая единица"], 5.0)
        self.app._save_conversion_factors.assert_called_once()

    def test_update_existing_custom_unit(self):
        self.app._save_conversion_factors = MagicMock()

        self.app._save_custom_unit("Длина", "Тестовая единица", Decimal("3"))

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
        self.app._save_custom_unit("Длина", "Тестовая единица", Decimal("2"))

        reloaded_app = self._reload_app()

        units = reloaded_app.conversion_factors["Длина"]["units"]

        self.assertEqual(units["Тестовая единица"], 2.0)

    def test_updated_coefficient_is_saved_and_loaded(self):
        self.app._save_custom_unit("Длина", "километр", Decimal("1500"))

        reloaded_app = self._reload_app()

        units = reloaded_app.conversion_factors["Длина"]["units"]

        self.assertEqual(units["километр"], 1500.0)

    def test_deleted_unit_is_removed_from_file(self):
        self.app._save_custom_unit("Длина", "Тестовая единица", Decimal("2"))

        result = self.app._delete_custom_unit("Длина", "Тестовая единица")

        self.assertTrue(result)

        reloaded_app = self._reload_app()

        units = reloaded_app.conversion_factors["Длина"]["units"]

        self.assertNotIn("Тестовая единица", units)
        self.assertIn("метр", units)
        self.assertIn("километр", units)


if __name__ == "__main__":
    unittest.main()
