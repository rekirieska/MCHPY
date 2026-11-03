
import getpass
import json
import os
import platform
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request

from decimal import Decimal, InvalidOperation
from tkinter import messagebox, ttk


def _system_user():
    """Имя пользователя ОС (Linux/Windows)"""
    try:
        return getpass.getuser() or "user"
    except (OSError, ImportError):
        return "user"


def _data_dir():
    """Каталог данных для текущей ОС"""
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
    return os.path.join(base, "MCHPY")


def _safe_part(name):
    """Фильтрует строку для использования в имени файла"""
    return "".join(c for c in name if c.isalnum() or c in "-_") or "user"


def _read_json(path):
    """Читает JSON-файл; None при ошибке или отсутствии"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path, data):
    """Пишет JSON-файл; True при успехе"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _unit_label(text):
    """«метр (м)» → «м»; без скобок возвращает исходное"""
    return text.split("(")[-1].strip(")") if "(" in text else text


class MCHPYApp:
    """GUI-приложение для конвертации единиц, валют и температуры.

    Attributes:
        MAX_HISTORY (int): Максимальное число хранимых записей истории.
        root (tk.Tk): Корневое окно приложения.
        base_dir (str): Директория со скриптом и файлами данных.
        history_file (str): Абсолютный путь к файлу истории.
        history_lines (list[str]): Текущее содержимое истории в памяти.
        conversion_factors (dict): Данные из conversion.json.
        money_data (dict): Данные из money.json / API.
        temperature_data (dict): Данные из temperature.json.
    """

    MAX_HISTORY = 500
    RATES_API_URL = "https://open.er-api.com/v6/latest/USD"
    RATES_API_TIMEOUT = 3
    RATES_CACHE_FILENAME = "rates_cache.json"
    TEMP_CAT = "Температура"
    MONEY_CAT = "Валюта"

    def __init__(self, root):
        """Инициализирует приложение и строит интерфейс"""
        self.root = root
        root.title("MCHPY — Конвертер единиц, валют и температуры")
        root.geometry("450x600")
        root.minsize(450, 600)
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.history_dir = _data_dir()
        try:
            os.makedirs(self.history_dir, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось создать каталог истории: {e}")

        self.history_file = os.path.join(
            self.history_dir, f"history_{_safe_part(_system_user())}.txt"
        )
        self.history_lines = []
        self.load_all_data()
        self.ensure_history_file()
        self.load_history()
        self._build_top_bar()
        self._build_history_frame()
        self._build_controls_frame()
        self._redraw_history()
        self.update_units()
        threading.Thread(target=self._refresh_rates, daemon=True).start()

    def _build_top_bar(self):
        """Создаёт верхнюю панель с кнопкой очистки истории"""
        top_bar = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        top_bar.pack(fill=tk.X)
        ttk.Button(top_bar, text="Очистить историю", command=self.clear_history).pack(
            fill=tk.X
        )

    def _build_history_frame(self):
        """Создаёт блок истории операций с полосой прокрутки"""
        history_frame = ttk.LabelFrame(self.root, text="История операций")
        history_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.history_text = tk.Text(
            history_frame, wrap=tk.WORD, height=12, state=tk.DISABLED
        )
        scrollbar = ttk.Scrollbar(
            history_frame, orient=tk.VERTICAL, command=self.history_text.yview
        )
        self.history_text.configure(yscrollcommand=scrollbar.set)
        self.history_text.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0), pady=5
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5, padx=(0, 5))

    def _build_controls_frame(self):
        """Создаёт панель управления: категория, значение, единицы, кнопка"""
        cf = ttk.Frame(self.root, padding=10)
        cf.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(cf, text="Категория:").grid(row=0, column=0, sticky=tk.W, pady=5)

        self.category_combo = ttk.Combobox(cf, state="readonly", width=28)
        categories = list(self.conversion_factors.keys())
        if self.money_data:
            categories.append(self.MONEY_CAT)
        if self.temperature_data:
            categories.append(self.TEMP_CAT)
        self.category_combo["values"] = categories
        if categories:
            self.category_combo.current(0)
        self.category_combo.grid(row=0, column=1, sticky=tk.EW, pady=5)
        self.category_combo.bind("<<ComboboxSelected>>", self.update_units_callback)

        ttk.Label(cf, text="Значение:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.input_entry = ttk.Entry(cf, width=30)
        self.input_entry.grid(row=1, column=1, sticky=tk.EW, pady=5)
        self.input_entry.insert(0, "1")

        ttk.Label(cf, text="Из единицы:").grid(row=2, column=0, sticky=tk.W, pady=5)

        self.from_unit_combo = ttk.Combobox(cf, state="readonly", width=28)
        self.from_unit_combo.grid(row=2, column=1, sticky=tk.EW, pady=5)

        ttk.Label(cf, text="В единицу:").grid(row=3, column=0, sticky=tk.W, pady=5)

        to_frame = ttk.Frame(cf)
        to_frame.grid(row=3, column=1, sticky=tk.EW, pady=5)
        self.to_unit_combo = ttk.Combobox(to_frame, state="readonly", width=24)
        self.to_unit_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(to_frame, text="+", width=3, command=self.add_custom_unit).pack(
            side=tk.LEFT, padx=(4, 0)
        )

        ttk.Button(cf, text="Конвертировать", command=self.convert).grid(
            row=4, column=0, columnspan=2, sticky=tk.EW, pady=(12, 4)
        )

    def load_all_data(self):
        """Загружает локальные файлы коэффициентов"""
        self.conversion_factors = {}
        self.money_data = {}
        self.temperature_data = {}

        cache = _read_json(os.path.join(self.base_dir, self.RATES_CACHE_FILENAME))

        if cache and cache.get("data", {}).get("units"):
            self.money_data = cache["data"]

        targets = [
            ("conversion.json", "conversion_factors"),
            ("temperature.json", "temperature_data"),
        ]
        if not self.money_data.get("units"):
            targets.append(("money.json", "money_data"))

        for filename, attr in targets:
            path = os.path.join(self.base_dir, filename)
            data = _read_json(path)
            if data is not None:
                setattr(self, attr, data)
            elif os.path.exists(path):
                messagebox.showerror("Ошибка", f"Не удалось прочитать файл {filename}")
            else:
                messagebox.showerror(
                    "Критическая ошибка",
                    f"Файл '{filename}' не найден!\nОжидаемый путь:\n{path}",
                )

    def _refresh_rates(self):
        """Обновление курсов валют"""
        try:
            with urllib.request.urlopen(
                self.RATES_API_URL, timeout=self.RATES_API_TIMEOUT
            ) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

                if not isinstance(payload, dict):
                    return

                rates = payload.get("rates")
            if isinstance(rates, dict) and rates.get("USD"):
                self.root.after(0, self._apply_remote_rates, rates)

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            AttributeError,
        ):
            return

    def _apply_remote_rates(self, rates):
        """Применяет курсы из API"""
        self.money_data = {"units": rates}
        _write_json(
            os.path.join(self.base_dir, self.RATES_CACHE_FILENAME),
            {"timestamp": time.time(), "data": self.money_data},
        )
        values = list(self.category_combo["values"])
        if self.MONEY_CAT not in values:
            self.category_combo["values"] = values + [self.MONEY_CAT]
        elif self.category_combo.get() == self.MONEY_CAT:
            self.update_units()

    def _save_conversion_factors(self):
        """Сохраняет в conversion.json"""
        if not _write_json(
            os.path.join(self.base_dir, "conversion.json"),
            self.conversion_factors,
        ):
            messagebox.showerror("Ошибка", "Не удалось сохранить conversion.json")

    def ensure_history_file(self):
        """Создаёт пустой файл истории, если его ещё нет"""
        if os.path.exists(self.history_file):
            return
        try:
            open(self.history_file, "w", encoding="utf-8").close()
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось создать файл истории: {e}")

    def load_history(self):
        """Читает из файла последние MAX_HISTORY строк истории"""
        self.history_lines = []
        if not os.path.exists(self.history_file):
            return
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                lines = [line.rstrip("\n") for line in f if line.strip()]
            self.history_lines = lines[-self.MAX_HISTORY :]
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось прочитать историю: {e}")

    def save_history(self):
        """Перезаписывает файл истории текущим содержимым"""
        try:
            text = "\n".join(self.history_lines)
            if text:
                text += "\n"
            with open(self.history_file, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить историю: {e}")

    def _redraw_history(self):
        """Перерисовывает виджет истории из self.history_lines"""
        self.history_text.config(state=tk.NORMAL)
        self.history_text.delete("1.0", tk.END)
        for line in self.history_lines:
            self.history_text.insert(tk.END, line + "\n")
        self.history_text.see(tk.END)
        self.history_text.config(state=tk.DISABLED)

    def update_units_callback(self, event):
        self.update_units()

    def update_units(self):
        """Обновляет списки единиц для выбранной категории"""
        cat = self.category_combo.get()
        if self.TEMP_CAT in cat:
            units = list(self.temperature_data.get("units", {}).values())
        elif self.MONEY_CAT in cat:
            units = list(self.money_data.get("units", {}).keys())
        else:
            units = list(self.conversion_factors.get(cat, {}).get("units", {}).keys())

        self.from_unit_combo["values"] = units
        self.to_unit_combo["values"] = units
        if units:
            self.from_unit_combo.current(0)
            self.to_unit_combo.current(1 if len(units) > 1 else 0)

    def _refresh_units_after(self, cat, select=None):
        """Обновляет после изменения списка единиц категории"""
        if self.category_combo.get() != cat:
            return
        units = list(self.conversion_factors.get(cat, {}).get("units", {}).keys())

        self.from_unit_combo["values"] = units
        self.to_unit_combo["values"] = units
        if not units:
            return
        if select and select in units:
            self.to_unit_combo.current(units.index(select))
        else:
            self.from_unit_combo.current(0)
            self.to_unit_combo.current(0 if len(units) == 1 else 1)

    def _base_unit_name(self, units):
        """Возвращает имя базовой единицы"""
        for name, coef in units.items():
            try:
                if abs(float(coef) - 1.0) < 1e-12:
                    return name
            except (TypeError, ValueError):
                continue
        return "базовая единица"

    def _center_on_parent(self, dialog, parent):
        dialog.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = (
            parent.winfo_rooty()
            + (parent.winfo_height() - dialog.winfo_reqheight()) // 2
        )
        dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def add_custom_unit(self):
        """Диалог добавления/обновления/удаления единицы в текущей категории"""
        cat = self.category_combo.get()
        if not cat:
            return
        if cat in (self.TEMP_CAT, self.MONEY_CAT):
            messagebox.showinfo(
                "Недоступно",
                "Свои единицы можно добавлять только в категории единиц "
                "измерения (не «Валюта» и не «Температура»).",
            )
            return

        data = self.conversion_factors.setdefault(cat, {})
        units = data.setdefault("units", {})
        base_name = self._base_unit_name(units)
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Единица — {cat}")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.grid_rowconfigure(1, minsize=48)
        frame.grid_rowconfigure(3, minsize=20)
        ttk.Label(frame, text="Название:").grid(row=0, column=0, sticky=tk.W, pady=4)

        name_var = tk.StringVar()
        name_entry = ttk.Entry(frame, width=32, textvariable=name_var)
        name_entry.grid(row=0, column=1, sticky=tk.EW, pady=4)
        hint_var = tk.StringVar()
        ttk.Label(
            frame,
            textvariable=hint_var,
            foreground="#555555",
            wraplength=260,
            justify=tk.LEFT,
        ).grid(row=1, column=1, sticky=tk.NW)

        ttk.Label(frame, text=f"Коэффициент от «{base_name}»:").grid(
            row=2, column=0, sticky=tk.W, pady=4
        )
        coef_entry = ttk.Entry(frame, width=32)
        coef_entry.grid(row=2, column=1, sticky=tk.EW, pady=4)
        coef_entry.insert(0, "1")
        err_var = tk.StringVar()
        ttk.Label(
            frame,
            textvariable=err_var,
            foreground="red",
            wraplength=260,
            justify=tk.LEFT,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W)
        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, sticky=tk.EW, pady=(10, 0))

        btn_frame.columnconfigure(1, weight=1)

        def on_name_change(*_):
            name = name_var.get().strip()
            if name in units:
                hint_var.set(
                    f"«{name}» уже существует (коэффициент {units[name]}). "
                    f"«Сохранить» обновит её."
                )
                delete_btn.grid()
            else:
                hint_var.set("")
                delete_btn.grid_remove()

        def on_ok():
            name = name_var.get().strip()
            if not name:
                err_var.set("Введите название.")
                return
            if name in (self.TEMP_CAT, self.MONEY_CAT):
                err_var.set("Недопустимое название.")
                return
            try:
                coef = Decimal(coef_entry.get().strip())
            except (InvalidOperation, ValueError):
                err_var.set("Коэффициент должен быть числом.")
                return
            if coef <= 0:
                err_var.set("Коэффициент должен быть больше 0.")
                return
            units[name] = float(coef)
            data["units"] = units
            self.conversion_factors[cat] = data
            self._save_conversion_factors()
            dialog.destroy()
            self._refresh_units_after(cat, name)

        def on_delete():
            name = name_var.get().strip()
            if name not in units:
                return
            if len(units) <= 1:
                messagebox.showwarning(
                    "Нельзя удалить",
                    "В категории должна остаться хотя бы одна единица.",
                    parent=dialog,
                )
                return
            if not messagebox.askyesno(
                "Удаление",
                f"Удалить единицу «{name}» из категории «{cat}»?",
                parent=dialog,
            ):
                return
            del units[name]
            data["units"] = units
            self.conversion_factors[cat] = data
            self._save_conversion_factors()
            dialog.destroy()
            self._refresh_units_after(cat)

        def on_cancel():
            dialog.destroy()

        delete_btn = ttk.Button(btn_frame, text="Удалить", command=on_delete)
        delete_btn.grid(row=0, column=0, sticky=tk.W)
        delete_btn.grid_remove()
        ttk.Button(btn_frame, text="Отмена", command=on_cancel).grid(
            row=0, column=2, padx=(6, 0)
        )
        ttk.Button(btn_frame, text="Сохранить", command=on_ok).grid(row=0, column=3)

        name_var.trace_add("write", on_name_change)
        dialog.bind("<Return>", lambda e: on_ok())
        dialog.bind("<Escape>", lambda e: on_cancel())
        name_entry.focus_set()
        self._center_on_parent(dialog, self.root)

    def convert(self):
        """Выполняет конвертацию и добавляет результат в историю"""
        cat = self.category_combo.get()
        try:
            val = Decimal(self.input_entry.get().strip())
        except (InvalidOperation, ValueError):
            messagebox.showerror(
                "Ошибка ввода",
                "Пожалуйста, введите корректное числовое значение!",
            )
            return
        from_u = self.from_unit_combo.get()
        to_u = self.to_unit_combo.get()
        try:
            if self.TEMP_CAT in cat:
                result = self.convert_temperature(val, from_u, to_u)
            elif self.MONEY_CAT in cat:
                rates = self.money_data.get("units", {})
                result = val / Decimal(str(rates[from_u])) * Decimal(str(rates[to_u]))
            else:
                units = self.conversion_factors.get(cat, {}).get("units", {})
                result = val * Decimal(str(units[from_u])) / Decimal(str(units[to_u]))
            res_str = f"{val} {_unit_label(from_u)} = {result:.10g} {_unit_label(to_u)}"

            self.add_to_history(res_str)
        except KeyError as e:
            messagebox.showerror("Ошибка", f"Неизвестная единица: {e}")
        except ZeroDivisionError:
            messagebox.showerror("Ошибка", "Деление на ноль")
        except ValueError as e:
            messagebox.showerror("Ошибка", str(e))

    def convert_temperature(self, val, from_u, to_u):
        """Конвертация температур"""
        unit_codes = {
            "C": "C",
            "°C": "C",
            "ЦЕЛЬСИЙ": "C",
            "ЦЕЛЬСИЯ": "C",
            "ГРАДУСЫ ЦЕЛЬСИЯ": "C",
            "F": "F",
            "°F": "F",
            "ФАРЕНГЕЙТ": "F",
            "ФАРЕНГЕЙТА": "F",
            "ГРАДУСЫ ФАРЕНГЕЙТА": "F",
            "K": "K",
            "К": "K",
            "КЕЛЬВИН": "K",
            "КЕЛЬВИНЫ": "K",
        }

        from_label = _unit_label(from_u).strip().upper()
        to_label = _unit_label(to_u).strip().upper()
        f = unit_codes.get(from_label)
        t = unit_codes.get(to_label)

        if f is None or t is None:
            raise ValueError("Неизвестная единица измерения температуры")

        if f == t:
            return val

        if f == "C":
            c = val
        elif f == "F":
            c = (val - 32) * 5 / 9
        else:
            c = val - Decimal("273.15")

        if t == "C":
            return c
        if t == "F":
            return c * 9 / 5 + 32
        return c + Decimal("273.15")

    def add_to_history(self, record):
        """Добавляет запись в память, файл и виджет с учётом лимита"""
        self.history_lines.append("• " + record)
        if len(self.history_lines) > self.MAX_HISTORY:
            self.history_lines = self.history_lines[-self.MAX_HISTORY :]
        self.save_history()
        self._redraw_history()

    def clear_history(self):
        """Очищает историю"""
        if not self.history_lines:
            messagebox.showinfo("История", "История уже пуста.")
            return
        if not messagebox.askyesno("Подтверждение", "Очистить всю историю операций?"):
            return
        self.history_lines = []
        self.save_history()
        self._redraw_history()


if __name__ == "__main__":
    root = tk.Tk()
    app = MCHPYApp(root)
    root.mainloop()
