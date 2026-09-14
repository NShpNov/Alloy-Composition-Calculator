"""
GUI implementation.

One holy spaghetti of a code (including some AI slop). I'm not touching it.
It's barely holding together as it is.

Maybe I'll refactor it someday. Someday.
"""

import json
import queue
import re
import threading
from tkinter import filedialog
from pathlib import Path
import math
import customtkinter as ctk

from parcer import ScanCancelled, update_alloys as refresh_alloys_data
from logic.crucible import expand_batch_usage, pack_batches_into_crucibles
from logic.optimization import calculate_max_composition_amount
from PIL import Image, ImageTk
from pathlib import Path
import sys


def resource_path(filename):
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / filename

    return Path(__file__).resolve().parent / filename

BASE_DIR = Path(__file__).resolve().parent
ADD_ALLOY = "Add alloy"
DELETE_ALLOY = "Delete current alloy"
USER_DATA_PATH = BASE_DIR / "user_data.json"


def load_data():
	with (BASE_DIR / "alloys.json").open(encoding="utf-8") as file:
		alloys = json.load(file)["alloys"]
	with (BASE_DIR / "colors.json").open(encoding="utf-8") as file:
		colors = json.load(file)
	return alloys, colors


class AlloyApp(ctk.CTk):
	def __init__(self, alloys, colors):
		super().__init__()
		self.alloys = alloys
		self.colors = colors
		self.current_theme = "light"
		self.component_widgets = {}
		self.recursive_windows = []
		self.display_to_id = {}
		self.user_data = self.load_user_data()
		self.side_panel_open = False
		self.wm_iconbitmap()
		img_path = BASE_DIR / "icon.png"
		img = ImageTk.PhotoImage(Image.open(img_path), master=self)
		self.iconphoto(True, img)
		self._icon_ref = img
		self.title("Alloy Calculator")
		self.geometry("400x540")
		self.minsize(550, 650)
		self.maxsize(550, 800)
		self.compact_width = 700
		self.compact_height = 540
		self.protocol("WM_DELETE_WINDOW", self.destroy)

		self.build_layout()
		self.apply_theme()
		self.select_alloy(next(iter(self.display_to_id)))
		
	def build_layout(self):
		self.grid_columnconfigure(0, weight=1)
		self.grid_rowconfigure(3, weight=1)

		self.header = ctk.CTkFrame(self, fg_color="transparent")
		self.header.grid(row=0, column=0, padx=24, pady=(18, 8), sticky="ew")
		self.header.grid_columnconfigure(0, weight=1)

		self.title_label = ctk.CTkLabel(
			self.header, text="Alloy Calculator", font=ctk.CTkFont(size=24, weight="bold")
		)
		self.title_label.grid(row=0, column=0, sticky="w")
		self.theme_selector = ctk.CTkSegmentedButton(
			self.header,
			values=["Light", "Dark"],
			command=self.change_theme,
			width=120,
			height=30,
		)
		self.theme_selector.set("Light")
		self.theme_selector.grid(row=0, column=1, sticky="e")
		self.favorites_button = ctk.CTkButton(
			self.header, text="☆", width=34, height=30,
			command=lambda: self.toggle_side_panel("favorites"),
		)
		self.favorites_button.grid(row=0, column=2, padx=(8, 0), sticky="e")
		self.history_button = ctk.CTkButton(
			self.header, text="◷", width=34, height=30,
			command=lambda: self.toggle_side_panel("history"),
		)
		self.history_button.grid(row=0, column=3, padx=(6, 0), sticky="e")

		self.selector_frame = ctk.CTkFrame(self)
		self.selector_frame.grid(row=1, column=0, padx=24, pady=6, sticky="ew")
		self.selector_frame.grid_columnconfigure(1, weight=1)
		self.selector_frame.grid_columnconfigure(2, weight=0)
		self.alloy_label = ctk.CTkLabel(self.selector_frame, text="Alloy")
		self.alloy_label.grid(row=0, column=0, padx=(14, 10), pady=10)
		alloy_values = []
		for alloy_id, alloy in self.alloys.items():
			label = alloy.get("name", alloy_id)
			if label in self.display_to_id:
				label = f"{label} ({alloy_id})"
			self.display_to_id[label] = alloy_id
			alloy_values.append(label)
		self.current_display_name = alloy_values[0]
		self.alloy_selector = ctk.CTkButton(
			self.selector_frame,
			text=self.current_display_name,
			command=self.open_alloy_menu,
			anchor="w",
			width=340,
			height=34,
			font=ctk.CTkFont(size=13),
		)
		self.alloy_selector.grid(row=0, column=1, padx=(0, 8), pady=10, sticky="ew")
		self.update_alloys_button = ctk.CTkButton(
			self.selector_frame,
			text="Update alloys",
			command=self.update_alloys,
			width=112,
			height=34,
		)
		self.update_alloys_button.grid(row=0, column=2, padx=(0, 10), pady=10)

		self.side_panel = None
		self.side_title = None
		self.side_list = None

		self.content = ctk.CTkScrollableFrame(self, label_text="Component constraints")
		self.content.grid(row=3, column=0, padx=24, pady=0, sticky="nsew")
		self.content.grid_columnconfigure(0, weight=1)
		self.content.grid_columnconfigure(1, weight=0)
		self.content.grid_columnconfigure(2, weight=0)
		self.content.grid_columnconfigure(3, weight=0)

		self.action_bar = ctk.CTkFrame(self, fg_color="transparent")
		self.action_bar.grid(row=4, column=0, padx=24, pady=10, sticky="ew")
		self.action_bar.grid_columnconfigure(0, weight=1)
		self.action_bar.grid_columnconfigure(3, weight=1)
		self.amount_entry = ctk.CTkEntry(
			self.action_bar,
			placeholder_text="Target amount",
			width=220,
			height=38,
			font=ctk.CTkFont(size=14),
			justify="center",
		)
		self.amount_entry.grid(row=0, column=1, sticky="e")
		self.amount_unit_selector = ctk.CTkSegmentedButton(
			self.action_bar,
			values=["Units", "Ingots (144)"],
			command=self.change_amount_unit,
			width=176,
			height=26,
			font=ctk.CTkFont(size=11),
			corner_radius=7,
		)
		self.amount_unit_selector.set("Units")
		self.amount_unit = "Units"
		self.configure_numeric_entry(self.amount_entry)
		self.amount_unit_selector.grid(row=1, column=1, pady=(4, 0), sticky="e")
		self.crucible_var = ctk.BooleanVar(value=False)
		self.crucible_check = ctk.CTkCheckBox(
			self.action_bar,
			text="Split into crucible loads (4608)",
			variable=self.crucible_var,
			font=ctk.CTkFont(size=11),
			height=26,
		)
		self.crucible_check.grid(row=2, column=1, columnspan=1, pady=(4, 0), sticky="w")
		self.recursive_var = ctk.BooleanVar(value=False)
		self.recursive_check = ctk.CTkCheckBox(
			self.action_bar,
			text="Recursive alloying",
			variable=self.recursive_var,
			font=ctk.CTkFont(size=11),
			height=26,
		)
		self.recursive_check.grid(row=2, column=2, columnspan=1, pady=(4, 0), sticky="w")
		self.calculate_button = ctk.CTkButton(
			self.action_bar,
			text="Calculate",
			command=self.calculate,
			width=116,
			height=38,
			font=ctk.CTkFont(size=13, weight="bold"),
		)
		self.calculate_button.grid(row=0, column=2, padx=(14, 0), sticky="w")
		self.save_calculation_button = ctk.CTkButton(
			self.action_bar,
			text="☆",
			width=36,
			height=38,
			state="disabled",
			command=self.save_current_calculation,
		)
		self.save_calculation_button.grid(row=0, column=3, padx=(6, 0), sticky="w")
		self.save_calculation_button.grid_remove()

		self.result_panel = ctk.CTkFrame(self, corner_radius=10)
		self.result_panel.grid(row=5, column=0, padx=24, pady=(0, 14), sticky="ew")
		self.result_panel.grid_columnconfigure(0, weight=1)
		self.result_title = ctk.CTkLabel(
			self.result_panel,
			text="Result",
			anchor="w",
			font=ctk.CTkFont(size=15, weight="bold"),
		)
		self.result_title.grid(row=0, column=0, padx=16, pady=(12, 2), sticky="ew")
		self.result_scroll = ctk.CTkScrollableFrame(
			self.result_panel, height=220, corner_radius=6
		)
		self.result_scroll.grid(row=1, column=0, padx=10, pady=(2, 8), sticky="ew")
		self.result_scroll.grid_columnconfigure(0, weight=1)
		self.result_label = ctk.CTkLabel(
			self.result_scroll,
			text="",
			anchor="w",
			justify="left",
			wraplength=760,
			font=ctk.CTkFont(size=13),
		)
		self.result_label.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
		self.status_label = ctk.CTkLabel(
			self.result_panel, text="", anchor="w", justify="left",
			font=ctk.CTkFont(size=11),
		)
		self.status_label.grid(row=2, column=0, padx=16, pady=(0, 10), sticky="ew")
		self.result_panel.grid_remove()

	def apply_theme(self):
		palette = self.colors[self.current_theme]
		ctk.set_appearance_mode(self.current_theme)
		self.configure(fg_color=palette["FIRST_MAIN_COLOR"])
		self.selector_frame.configure(fg_color=palette["SECOND_MAIN_COLOR"])
		self.content.configure(fg_color=palette["SECOND_MAIN_COLOR"])
		self.result_panel.configure(fg_color=palette["SECOND_MAIN_COLOR"])
		self.result_scroll.configure(fg_color=palette["FOURTH_MAIN_COLOR"])
		if self.side_panel is not None and self.side_panel.winfo_exists():
			self.side_panel.configure(fg_color=palette["SECOND_MAIN_COLOR"])
			self.side_title.configure(text_color=palette["SECONDARY_COLOR"])
		self.action_bar.configure(fg_color="transparent")
		self.amount_entry.configure(
			fg_color=palette["FOURTH_MAIN_COLOR"],
			border_color=palette["BORDER_COLOR"],
			text_color=palette["TEXT_COLOR"],
			placeholder_text_color=palette["PLACEHOLDER_COLOR"],
		)
		self.amount_unit_selector.configure(
			fg_color=palette["THIRD_MAIN_COLOR"],
			selected_color=palette["SECONDARY_COLOR"],
			selected_hover_color=palette["HIGHLIGHT_COLOR"],
			unselected_color=palette["FOURTH_MAIN_COLOR"],
			unselected_hover_color=palette["HIGHLIGHT_COLOR"],
			text_color=palette["TEXT_COLOR"],
		)

		self.crucible_check.configure(text_color=palette["TEXT_COLOR"])
		self.recursive_check.configure(text_color=palette["TEXT_COLOR"])
		self.calculate_button.configure(
			fg_color=palette["SECONDARY_COLOR"],
			hover_color=palette["HIGHLIGHT_COLOR"],
			text_color=palette["FONT_COLOR"],
		)
		self.update_alloys_button.configure(
			fg_color=palette["THIRD_MAIN_COLOR"],
			hover_color=palette["HIGHLIGHT_COLOR"],
			text_color=palette["TEXT_COLOR"],
		)
		self.save_calculation_button.configure(
			fg_color=palette["THIRD_MAIN_COLOR"],
			hover_color=palette["HIGHLIGHT_COLOR"],
			text_color=palette["TEXT_COLOR"],
		)
		self.update_side_button_state(
			self.side_title.cget("text").lower()
			if self.side_panel_open else None
		)
		self.status_label.configure(text_color=palette["TEXT_COLOR"])
		self.result_label.configure(text_color=palette["TEXT_COLOR"])
		self.result_title.configure(text_color=palette["SECONDARY_COLOR"])

	def change_theme(self, value):
		self.current_theme = "dark" if value == "Dark" else "light"
		self.apply_theme()

	def change_amount_unit(self, unit):
		if unit == getattr(self, "amount_unit", unit):
			self.amount_unit = unit
			return
		value = self.amount_entry.get().strip().replace(",", ".")
		if value:
			amount = float(value)
			converted = math.ceil(amount * 144) if unit == "Units" else math.ceil(amount / 144)
			self.amount_entry.delete(0, "end")
			self.amount_entry.insert(0, f"{converted:g}")
		self.amount_unit = unit

	def configure_numeric_entry(self, entry, allow_negative=False):
		command = self.register(
			lambda value: bool(re.fullmatch(
				r"-?\d*([.,]\d*)?" if allow_negative else r"\d*([.,]\d*)?",
				value,
			))
		)
		entry.configure(validate="key", validatecommand=(command, "%P"))

	def load_user_data(self):
		default = {"favorites": [], "history": []}
		try:
			with USER_DATA_PATH.open(encoding="utf-8") as file:
				data = json.load(file)
			return {
				"favorites": [self.normalize_saved_record(item) for item in data.get("favorites", [])],
				"history": [self.normalize_saved_record(item) for item in data.get("history", [])[:8]],
			}
		except (OSError, json.JSONDecodeError, AttributeError):
			return default

	def normalize_saved_record(self, item):
		if isinstance(item, str):
			return {"recipe_id": item, "state": None, "result": ""}
		return item if isinstance(item, dict) else {}

	def save_user_data(self):
		with USER_DATA_PATH.open("w", encoding="utf-8") as file:
			json.dump(self.user_data, file, ensure_ascii=False, indent=2)
			file.write("\n")

	def toggle_side_panel(self, section):
		if self.side_panel_open and self.side_panel is not None and self.side_panel.winfo_exists() and self.side_title.cget("text").lower() == section:
			self.side_panel.destroy()
			self.side_panel = None
			self.side_title = None
			self.side_list = None
			self.side_panel_open = False
			self.update_side_button_state(None)
			return
		if self.side_panel_open and self.side_panel is not None and self.side_panel.winfo_exists():
			self.side_panel.destroy()

		self.side_panel = ctk.CTkToplevel(self)
		self.side_panel.title("Favorites" if section == "favorites" else "History")
		self.side_panel.geometry("420x620")
		self.side_panel.minsize(360, 360)
		self.side_panel.maxsize(520, self.winfo_screenheight() - 80)
		self.side_panel.protocol("WM_DELETE_WINDOW", self.close_side_panel)
		self.side_panel.grid_columnconfigure(0, weight=1)
		self.side_panel.grid_rowconfigure(1, weight=1)
		self.side_panel.transient(self)
		self.side_panel.lift()
		self.side_panel.update_idletasks()
		self.side_panel.geometry(
			f"420x620+{self.winfo_rootx() + self.winfo_width() + 12}+{self.winfo_rooty()}"
		)
		self.side_panel_open = True
		self.side_title = ctk.CTkLabel(
			self.side_panel, text="Favorites" if section == "favorites" else "History",
			anchor="w", font=ctk.CTkFont(size=17, weight="bold"),
		)
		self.side_title.grid(row=0, column=0, padx=14, pady=(14, 8), sticky="ew")
		self.side_list = ctk.CTkScrollableFrame(self.side_panel)
		self.side_list.grid(row=1, column=0, padx=8, pady=(0, 10), sticky="nsew")
		self.side_list.grid_columnconfigure(0, weight=1)
		self.side_title.configure(text="Favorites" if section == "favorites" else "History")
		self.populate_side_panel(section)
		self.update_side_button_state(section)

	def close_side_panel(self):
		if self.side_panel is not None and self.side_panel.winfo_exists():
			self.side_panel.destroy()
		self.side_panel = None
		self.side_title = None
		self.side_list = None
		self.side_panel_open = False
		self.update_side_button_state(None)

	def update_side_button_state(self, active):
		palette = self.colors[self.current_theme]
		for button, section in (
			(self.favorites_button, "favorites"),
			(self.history_button, "history"),
		):
			button.configure(
				fg_color=palette["SECONDARY_COLOR"] if active == section else palette["THIRD_MAIN_COLOR"],
				hover_color=palette["HIGHLIGHT_COLOR"],
				text_color=palette["FONT_COLOR"] if active == section else palette["TEXT_COLOR"],
			)

	def populate_side_panel(self, section):
		if self.side_list is None or not self.side_list.winfo_exists():
			return
		for widget in self.side_list.winfo_children():
			widget.destroy()
		records = self.user_data[section]
		if not records:
			ctk.CTkLabel(
				self.side_list,
				text="No saved recipes" if section == "favorites" else "No calculations yet",
				text_color=self.colors[self.current_theme]["PLACEHOLDER_COLOR"],
			).grid(row=0, column=0, padx=8, pady=14)
			return
		for row, record in enumerate(records):
			alloy_id = record.get("recipe_id")
			if alloy_id not in self.alloys:
				continue
			alloy = self.alloys[alloy_id]
			label = record.get("title") or record.get("label") or alloy.get("name", alloy_id)
			row_frame = ctk.CTkFrame(self.side_list, fg_color="transparent")
			row_frame.grid(row=row, column=0, padx=4, pady=3, sticky="ew")
			row_frame.grid_columnconfigure(0, weight=1)
			ctk.CTkButton(
				row_frame, text=label, anchor="w", fg_color="transparent", width=80,text_color=self.colors[self.current_theme]["TEXT_COLOR"], hover_color=self.colors[self.current_theme]["SECONDARY_COLOR"],
				command=lambda saved=record: self.load_saved_recipe(saved),
			).grid(row=0, column=0, sticky="ew")
			if section == "favorites":
				ctk.CTkButton(
					row_frame, text="★", width=30,
					command=lambda saved=record: self.remove_favorite(saved),
				).grid(row=0, column=1, padx=(4, 0))
			else:
				ctk.CTkButton(
					row_frame, text="-", width=30,
					fg_color="#c94c4c", hover_color="#a63d3d",
					command=lambda saved=record: self.remove_history(saved),
				).grid(row=0, column=1, padx=(4, 0))

	def load_saved_recipe(self, record):
		alloy_id = record.get("recipe_id")
		if alloy_id not in self.alloys:
			return
		self.select_alloy(alloy_id)
		if record.get("state"):
			self.apply_calculation_state(record["state"])
		if record.get("result"):
			self.last_calculation = record
			self.save_calculation_button.configure(state="normal", text="★")
			self.show_result(record["result"], error=False)
		self.alloy_selector.configure(text=self.current_display_name)
		if self.side_panel_open:
			self.close_side_panel()

	def toggle_favorite(self, alloy_id):
		favorites = self.user_data["favorites"]
		if any(item.get("recipe_id") == alloy_id for item in favorites):
			favorites[:] = [item for item in favorites if item.get("recipe_id") != alloy_id]
		else:
			favorites.append({"recipe_id": alloy_id, "state": None, "result": ""})
		self.save_user_data()
		if self.side_panel_open and self.side_title.cget("text") == "Favorites":
			self.populate_side_panel("favorites")

	def remove_favorite(self, record):
		self.user_data["favorites"] = [item for item in self.user_data["favorites"] if item is not record]
		self.save_user_data()
		self.populate_side_panel("favorites")

	def remove_history(self, record):
		self.user_data["history"] = [item for item in self.user_data["history"] if item is not record]
		self.save_user_data()
		self.populate_side_panel("history")

	def record_history(self, result_text, title=None):
		record = self.make_calculation_record(result_text, title)
		history = [
			item for item in self.user_data["history"]
			if item.get("state") != record.get("state")
		]
		history.insert(0, record)
		self.user_data["history"] = history[:8]
		self.save_user_data()
		if self.side_panel_open and self.side_title.cget("text") == "History":
			self.populate_side_panel("history")

	def make_calculation_record(self, result_text, title=None):
		return {
			"recipe_id": self.selected_alloy,
			"label": self.alloys[self.selected_alloy].get("name", self.selected_alloy),
			"title": title or self.alloys[self.selected_alloy].get("name", self.selected_alloy),
			"state": self.capture_calculation_state(),
			"result": result_text,
		}

	def save_current_calculation(self):
		if not getattr(self, "last_calculation", None):
			return
		record = dict(self.last_calculation)
		record["state"] = self.capture_calculation_state()
		favorites = self.user_data["favorites"]
		favorites[:] = [item for item in favorites if item.get("state") != record.get("state")]
		favorites.insert(0, record)
		self.save_user_data()
		self.save_calculation_button.configure(text="★")
		if self.side_panel_open and self.side_title.cget("text") == "Favorites":
			self.populate_side_panel("favorites")

	def capture_calculation_state(self):
		state = {
			"mode": "Composition",
			"amount": self.amount_entry.get(),
			"unit": self.amount_unit_selector.get(),
			"crucible": bool(self.crucible_var.get()),
			"recursive": bool(self.recursive_var.get()),
			"placeholders": {
				"amount": self.amount_entry.cget("placeholder_text"),
				"components": {},
			},
			"components": {},
		}
		for name, fields in self.component_widgets.items():
			state["placeholders"]["components"][name] = {
				"min": fields["min"].cget("placeholder_text"),
				"max": fields["max"].cget("placeholder_text"),
				"121": fields["batch_121"].cget("placeholder_text"),
				"144": fields["batch_144"].cget("placeholder_text"),
				"batches": [
					{
						"size": size.cget("placeholder_text"),
						"count": count.cget("placeholder_text"),
					}
					for _, size, count, _ in fields["batch_rows"]
				],
			}
			state["components"][name] = {
				"priority": round(self.normalize_priority(fields["priority"].get()), 2),
				"min": fields["min"].get(),
				"max": fields["max"].get(),
				"121": fields["batch_121"].get(),
				"144": fields["batch_144"].get(),
				"split": bool(fields["split_var"].get()),
				"batches": [
					{"size": size.get(), "count": count.get()}
					for _, size, count, _ in fields["batch_rows"]
				],
			}
		return state

	@staticmethod
	def restore_entry(entry, value, placeholder):
		entry.delete(0, "end")
		if isinstance(placeholder, bool):
			placeholder = entry.cget("placeholder_text")
		entry.configure(placeholder_text=placeholder or "")
		if value not in (None, ""):
			entry.insert(0, str(value))

	def apply_calculation_state(self, state):
		placeholders = state.get("placeholders", {})
		self.restore_entry(
			self.amount_entry,
			state.get("amount", ""),
			placeholders.get("amount", self.amount_entry.cget("placeholder_text")),
		)
		self.amount_unit_selector.set(state.get("unit", "Units"))
		self.amount_unit = state.get("unit", "Units")
		self.crucible_var.set(bool(state.get("crucible", False)))
		self.recursive_var.set(bool(state.get("recursive", False)))
		for name, values in state.get("components", {}).items():
			if name not in self.component_widgets:
				continue
			fields = self.component_widgets[name]
			component_placeholders = placeholders.get("components", {}).get(name, {})
			for key in ("priority", "min", "max", "121", "144"):
				fields_key = {"121": "batch_121", "144": "batch_144"}.get(key, key)
				if fields_key == "priority":
					fields[fields_key].set(self.normalize_priority(values.get(key, 0)))
				else:
					self.restore_entry(
						fields[fields_key],
						values.get(key, ""),
						component_placeholders.get(
							key, fields[fields_key].cget("placeholder_text")
						),
					)
			fields["split_var"].set(bool(values.get("split", False)))
			while len(fields["batch_rows"]) > 1:
				self.remove_batch_row(name, fields["batch_rows"][-1][0])
			for batch_index, batch in enumerate(values.get("batches", [])):
				if batch_index:
					self.add_batch_row(name)
				row = fields["batch_rows"][batch_index]
				batch_placeholders = component_placeholders.get("batches", [])
				row_placeholders = (
					batch_placeholders[batch_index]
					if batch_index < len(batch_placeholders)
					else {}
				)
				self.restore_entry(
					row[1], batch.get("size", ""),
					row_placeholders.get("size", row[1].cget("placeholder_text")),
				)
				self.restore_entry(
					row[2], batch.get("count", ""),
					row_placeholders.get("count", row[2].cget("placeholder_text")),
				)
			self.refresh_headers()

	@staticmethod
	def normalize_priority(value):
		try:
			priority = float(value or 0)
		except (TypeError, ValueError):
			priority = 0.0
		return max(-1.0, min(1.0, priority))

	def select_alloy(self, alloy_name):
		if alloy_name == ADD_ALLOY:
			self.open_add_alloy_window()
			self.alloy_selector.configure(text=self.current_display_name)
			return
		if alloy_name == DELETE_ALLOY:
			self.open_delete_alloy_window()
			self.alloy_selector.configure(text=self.current_display_name)
			return
		if hasattr(self, "result_panel"):
			self.hide_result()
			self.save_calculation_button.grid_remove()
		alloy_name = self.display_to_id.get(alloy_name, alloy_name)
		self.selected_alloy = alloy_name
		self.current_display_name = next(
			(label for label, identifier in self.display_to_id.items()
			 if identifier == alloy_name), alloy_name
		)
		self.alloy_selector.configure(text=self.current_display_name)
		for widget in self.content.winfo_children():
			widget.destroy()
		self.component_widgets = {}

		components = self.alloys[alloy_name]["components"]
		names = list(components)
		for component_index, name in enumerate(names):
			row = component_index * 2
			component_name = components[name].get("name", name)
			ctk.CTkLabel(self.content, text=component_name, anchor="w").grid(
				row=row, rowspan=2, column=0, padx=(8, 12), pady=5, sticky="w"
			)
			batch_frame = ctk.CTkFrame(self.content, fg_color="transparent")
			composition_batches = ctk.CTkFrame(self.content, fg_color="transparent")
			batch_121 = ctk.CTkEntry(composition_batches, placeholder_text="121 (Amount)", width=110, height=30)
			batch_144 = ctk.CTkEntry(composition_batches, placeholder_text="144 (Amount)", width=110, height=30)
			self.configure_numeric_entry(batch_121)
			self.configure_numeric_entry(batch_144)
			split_var = ctk.BooleanVar(value=False)
			split_check = ctk.CTkCheckBox(
				composition_batches, text="Split", variable=split_var,
				width=62, height=30,
			)
			batch_121.grid(row=0, column=0, padx=(0, 3))
			batch_144.grid(row=0, column=1, padx=(0, 3))
			split_check.grid(row=0, column=2, padx=(0, 3))
			priority_frame = ctk.CTkFrame(self.content)
			priority_frame.grid(row=row, column=1, padx=3, pady=5, sticky="ew")
			priority_frame.grid_columnconfigure(0, weight=1)
			priority = ctk.CTkSlider(
				priority_frame, from_=-1, to=1, number_of_steps=4,
				width=80, height=14,
			)
			priority.set(0)
			ctk.CTkLabel(priority_frame, text="priority", font=ctk.CTkFont(size=12)).grid(
				row=0, column=0, sticky="n"
			)
			priority.grid(row=0, column=1, sticky="ew")

			minimum = ctk.CTkEntry(
				self.content, placeholder_text="Min", width=44, height=30
			)
			self.configure_numeric_entry(minimum)
			maximum = ctk.CTkEntry(
				self.content, placeholder_text="Max", width=44, height=30
			)
			self.configure_numeric_entry(maximum)
			batch_frame.grid(row=row, column=2, columnspan=3, padx=3, pady=5, sticky="w")
			composition_batches.grid(
				row=row + 1, column=1, columnspan=4, padx=3, pady=(0, 5), sticky="w"
			)
			minimum.grid(row=row, column=2, padx=(1, 0), pady=5, sticky="w")
			maximum.grid(row=row, column=3, padx=(1, 0), pady=5, sticky="w")
			self.component_widgets[name] = {
				"batch_frame": batch_frame,
				"batch_rows": [],
				"composition_batches": composition_batches,
				"batch_121": batch_121,
				"batch_144": batch_144,
				"split_var": split_var,
				"split_check": split_check,
				"priority": priority,
				"min": minimum,
				"max": maximum,
			}
			self.add_batch_row(name)
		self.refresh_headers()

	def open_alloy_menu(self):
		menu = ctk.CTkToplevel(self)
		menu.title("Alloys")
		menu.geometry("500x420")
		menu.minsize(420, 260)
		menu.transient(self)
		menu.grab_set()
		menu.grid_columnconfigure(0, weight=1)
		menu.grid_rowconfigure(1, weight=1)
		actions = ctk.CTkFrame(menu, fg_color="transparent")
		actions.grid(row=0, column=0, padx=14, pady=(14, 0), sticky="ew")
		actions.grid_columnconfigure(0, weight=1)
		actions.grid_columnconfigure(1, weight=1)
		delete_buttons = []
		delete_mode_enabled = False

		ctk.CTkButton(
			actions,
			text="+ Create new alloy",
			command=lambda: self.add_from_menu(menu),
		).grid(row=0, column=0, padx=(0, 4), sticky="ew")
		delete_mode_button = ctk.CTkButton(
			actions,
			text="Delete an alloy",
			width=150,
		)
		delete_mode_button.grid(row=0, column=1, padx=(4, 0), sticky="ew")

		def toggle_delete_mode():
			nonlocal delete_mode_enabled
			delete_mode_enabled = not delete_mode_enabled
			for button in delete_buttons:
				if delete_mode_enabled:
					button.grid()
				else:
					button.grid_remove()
			delete_mode_button.configure(
				text="Done" if delete_mode_enabled else "Delete an alloy"
			)

		list_frame = ctk.CTkScrollableFrame(menu, label_text="Available alloys")
		list_frame.grid(row=1, column=0, padx=14, pady=14, sticky="nsew")
		list_frame.grid_columnconfigure(0, weight=1)
		delete_mode_button.configure(command=toggle_delete_mode)

		for row, (label, alloy_id) in enumerate(self.display_to_id.items()):
			row_frame = ctk.CTkFrame(list_frame, fg_color="transparent")
			row_frame.grid(row=row, column=0, pady=3, sticky="ew")
			row_frame.grid_columnconfigure(0, weight=1)
			ctk.CTkButton(
				row_frame,
				text=label,
				anchor="w",
				fg_color="transparent",
				text_color=self.colors[self.current_theme]["TEXT_COLOR"],
				command=lambda value=label: self.choose_alloy_from_menu(menu, value),
			).grid(row=0, column=0, sticky="ew")
			delete_button = ctk.CTkButton(
				row_frame,
				text="-",
				width=30,
				fg_color="#c94c4c",
				hover_color="#a63d3d",
				command=lambda identifier=alloy_id: self.delete_from_menu(menu, identifier),
			)
			delete_button.grid(row=0, column=1, padx=(6, 0))
			delete_button.grid_remove()
			delete_buttons.append(delete_button)

	def choose_alloy_from_menu(self, menu, label):
		menu.destroy()
		self.select_alloy(label)

	def add_from_menu(self, menu):
		menu.destroy()
		self.open_add_alloy_window()

	def delete_from_menu(self, menu, alloy_id):
		menu.destroy()
		self.open_delete_alloy_window(alloy_id)

	def open_delete_alloy_window(self, alloy_id=None):
		alloy_id = alloy_id or self.selected_alloy
		if len(self.alloys) <= 1:
			self.show_result(
				"At least one alloy recipe must remain.",
				error=True,
			)
			return

		window = ctk.CTkToplevel(self)
		window.title("Delete alloy")
		window.geometry("420x190")
		window.resizable(False, False)
		window.transient(self)
		window.grab_set()
		window.grid_columnconfigure(0, weight=1)
		current_name = self.alloys[alloy_id].get(
			"name", alloy_id
		)
		ctk.CTkLabel(
			window,
			text=f"Delete '{current_name}'?",
			font=ctk.CTkFont(size=16, weight="bold"),
			wraplength=370,
		).grid(row=0, column=0, padx=20, pady=(24, 10))
		ctk.CTkLabel(
			window,
			text="This recipe will be removed from alloys.json.",
			wraplength=370,
		).grid(row=1, column=0, padx=20, pady=4)
		buttons = ctk.CTkFrame(window, fg_color="transparent")
		buttons.grid(row=2, column=0, pady=18)
		ctk.CTkButton(
			buttons, text="Cancel", command=window.destroy, width=110
		).pack(side="left", padx=5)
		ctk.CTkButton(
			buttons,
			text="Delete",
			fg_color="#c94c4c",
			hover_color="#a63d3d",
			command=lambda: self.delete_selected_alloy(window, alloy_id),
			width=110,
		).pack(side="left", padx=5)

	def delete_selected_alloy(self, window, alloy_id=None):
		if len(self.alloys) <= 1:
			window.destroy()
			self.show_result(
				"At least one alloy recipe must remain.",
				error=True,
			)
			return

		alloy_id = alloy_id or self.selected_alloy
		del self.alloys[alloy_id]
		self.user_data["favorites"] = [
			record for record in self.user_data["favorites"]
			if record.get("recipe_id") != alloy_id
		]
		self.user_data["history"] = [
			record for record in self.user_data["history"]
			if record.get("recipe_id") != alloy_id
		]
		self.save_user_data()
		self.save_alloys_json()
		self.refresh_alloy_selector()
		first_label = next(label for label in self.display_to_id)
		self.alloy_selector.configure(text=first_label)
		self.select_alloy(first_label)
		if self.side_panel_open and self.side_title is not None:
			self.populate_side_panel(self.side_title.cget("text").lower())
		window.destroy()

	def open_add_alloy_window(self):
		window = ctk.CTkToplevel(self)
		window.title("Add alloy")
		window.geometry("560x520")
		window.minsize(520, 420)
		window.transient(self)
		window.grab_set()
		window.grid_columnconfigure(0, weight=1)
		window.grid_rowconfigure(3, weight=1)

		ctk.CTkLabel(
			window, text="New alloy", font=ctk.CTkFont(size=20, weight="bold")
		).grid(row=0, column=0, padx=22, pady=(18, 8), sticky="w")
		name_entry = ctk.CTkEntry(window, placeholder_text="Alloy name")
		name_entry.grid(row=1, column=0, padx=22, pady=6, sticky="ew")

		component_frame = ctk.CTkScrollableFrame(window, label_text="Components")
		component_frame.grid(row=3, column=0, padx=22, pady=8, sticky="nsew")
		component_frame.grid_columnconfigure(0, weight=1)
		rows = []

		def add_component_row():
			row_frame = ctk.CTkFrame(component_frame, fg_color="transparent")
			row_frame.grid(row=len(rows), column=0, pady=4, sticky="ew")
			row_frame.grid_columnconfigure(0, weight=1)
			component_entry = ctk.CTkEntry(row_frame, placeholder_text="Component name")
			minimum_entry = ctk.CTkEntry(row_frame, placeholder_text="Min %", width=82)
			maximum_entry = ctk.CTkEntry(row_frame, placeholder_text="Max %", width=82)
			self.configure_numeric_entry(minimum_entry)
			self.configure_numeric_entry(maximum_entry)
			remove_button = ctk.CTkButton(
				row_frame, text="-", width=30,
				command=lambda: remove_component_row(row_frame),
			)
			component_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")
			minimum_entry.grid(row=0, column=1, padx=3)
			maximum_entry.grid(row=0, column=2, padx=3)
			remove_button.grid(row=0, column=3, padx=(3, 0))
			rows.append((row_frame, component_entry, minimum_entry, maximum_entry, remove_button))
			update_component_buttons()

		def remove_component_row(row_frame):
			if len(rows) <= 2:
				return
			row = next(row for row in rows if row[0] is row_frame)
			rows.remove(row)
			row_frame.destroy()
			for index, item in enumerate(rows):
				item[0].grid(row=index, column=0, pady=4, sticky="ew")
			update_component_buttons()

		def update_component_buttons():
			for row in rows:
				row[4].configure(state="normal" if len(rows) > 2 else "disabled")

		for _ in range(2):
			add_component_row()

		add_button = ctk.CTkButton(
			window, text="+ Add component", command=add_component_row,
			width=150,
		)
		add_button.grid(row=4, column=0, padx=22, pady=(0, 8), sticky="w")
		error_label = ctk.CTkLabel(window, text="", anchor="w", text_color="#d9534f")
		error_label.grid(row=5, column=0, padx=22, sticky="ew")

		button_frame = ctk.CTkFrame(window, fg_color="transparent")
		button_frame.grid(row=6, column=0, padx=22, pady=14, sticky="e")
		ctk.CTkButton(
			button_frame, text="Cancel", command=window.destroy, width=110
		).pack(side="left", padx=5)
		ctk.CTkButton(
			button_frame, text="Save", command=lambda: self.save_custom_alloy(
				window, name_entry, rows, error_label
			), width=110
		).pack(side="left", padx=5)

		name_entry.focus_set()

	def save_custom_alloy(self, window, name_entry, rows, error_label):
		try:
			name = name_entry.get().strip()
			if not name:
				raise ValueError("Enter an alloy name")
			slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
			if not slug:
				raise ValueError("Alloy name must contain Latin letters or digits")
			alloy_id = f"tfc:alloy/{slug}"
			if alloy_id in self.alloys:
				raise ValueError("An alloy with this ID already exists")

			components = {}
			for _, component_entry, minimum_entry, maximum_entry, _ in rows:
				component_name = component_entry.get().strip()
				component_slug = re.sub(
					"[^a-z0-9]+", "_", component_name.lower()
				).strip("_")
				minimum = float(minimum_entry.get().strip().replace(",", "."))
				maximum = float(maximum_entry.get().strip().replace(",", "."))
				if not component_slug:
					raise ValueError("Each component needs a Latin name")
				if not 0 <= minimum <= maximum <= 100:
					raise ValueError("Component percentages must satisfy 0 <= min <= max <= 100")
				component_id = f"tfc:{component_slug}"
				if component_id in components:
					raise ValueError("Component names must be unique")
				components[component_id] = {
					"min": minimum,
					"max": maximum,
					"name": component_name,
				}
			if len(components) < 2:
				raise ValueError("Add at least two components")
			minimum_sum = sum(item["min"] for item in components.values())
			maximum_sum = sum(item["max"] for item in components.values())
			if minimum_sum > 100 + 1e-6:
				raise ValueError("Sum of minimum percentages cannot exceed 100%")
			if maximum_sum < 100 - 1e-6:
				raise ValueError("Sum of maximum percentages must be at least 100%")

			self.alloys[alloy_id] = {
				"result": f"tfc:{slug}",
				"name": name,
				"components": components,
				"source": "custom",
				"source_type": "custom",
			}
			self.save_alloys_json()
			self.refresh_alloy_selector()
			self.alloy_selector.configure(text=name)
			self.select_alloy(name)
			window.destroy()
		except (ValueError, TypeError) as error:
			error_label.configure(text=str(error))

	def save_alloys_json(self):
		path = BASE_DIR / "alloys.json"
		with path.open("r", encoding="utf-8") as file:
			data = json.load(file)
		data["alloys"] = self.alloys
		with path.open("w", encoding="utf-8") as file:
			json.dump(data, file, ensure_ascii=False, indent=2)
			file.write("\n")

	def refresh_alloy_selector(self):
		self.display_to_id.clear()
		for alloy_id, alloy in self.alloys.items():
			label = alloy.get("name", alloy_id)
			if label in self.display_to_id:
				label = f"{label} ({alloy_id})"
			self.display_to_id[label] = alloy_id

	def update_alloys(self):
		last_location = self.read_last_scan_location()
		dialog = ctk.CTkToplevel(self)
		dialog.title("Update alloys")
		dialog.geometry("440x190")
		dialog.resizable(False, False)
		dialog.transient(self)
		dialog.grab_set()
		ctk.CTkLabel(
			dialog,
			text="Choose the Minecraft instance to scan",
			font=ctk.CTkFont(size=16, weight="bold"),
		).pack(padx=20, pady=(24, 8))
		location_text = last_location or "No previous scan location"
		ctk.CTkLabel(
			dialog, text=location_text, wraplength=390,
		).pack(padx=20, pady=(0, 16))
		buttons = ctk.CTkFrame(dialog, fg_color="transparent")
		buttons.pack()
		ctk.CTkButton(
			buttons,
			text="Use previous location",
			command=lambda: self.run_alloy_update(dialog, Path(last_location)),
			state="normal" if last_location and Path(last_location).is_dir() else "disabled",
			width=170,
		).pack(side="left", padx=5)
		ctk.CTkButton(
			buttons,
			text="Choose new location",
			command=lambda: self.choose_scan_location(dialog),
			width=170,
		).pack(side="left", padx=5)

	def read_last_scan_location(self):
		try:
			with (BASE_DIR / "alloys.json").open(encoding="utf-8") as file:
				location = json.load(file).get("last_scan_location")
			return location if isinstance(location, str) and location else None
		except (OSError, json.JSONDecodeError, AttributeError):
			return None

	def choose_scan_location(self, dialog):
		location = filedialog.askdirectory(
			parent=dialog,
			title="Choose Minecraft instance",
		)
		if location:
			self.run_alloy_update(dialog, Path(location))

	def run_alloy_update(self, dialog, instance):
		dialog.destroy()
		self.scan_queue = queue.Queue()
		self.scan_stop_event = threading.Event()
		self.scan_dialog = ctk.CTkToplevel(self)
		self.scan_dialog.title("Updating alloys")
		self.scan_dialog.geometry("430x170")
		self.scan_dialog.resizable(False, False)
		self.scan_dialog.transient(self)
		self.scan_dialog.grab_set()
		self.scan_status = ctk.CTkLabel(self.scan_dialog, text="Starting scan...")
		self.scan_status.pack(padx=20, pady=(24, 10))
		self.scan_progress = ctk.CTkProgressBar(self.scan_dialog, width=370)
		self.scan_progress.pack(padx=20, pady=6)
		self.scan_progress.set(0)
		self.scan_cancel_button = ctk.CTkButton(
			self.scan_dialog, text="Stop", command=self.stop_alloy_scan, width=120,
		)
		self.scan_cancel_button.pack(pady=(10, 0))
		self.scan_dialog.protocol("WM_DELETE_WINDOW", self.stop_alloy_scan)
		worker = threading.Thread(
			target=self.scan_alloys_worker,
			args=(instance,),
			daemon=True,
		)
		worker.start()
		self.after(100, self.poll_alloy_scan)

	def scan_alloys_worker(self, instance):
		try:
			output = refresh_alloys_data(
				instance,
				BASE_DIR / "alloys.json",
				progress=lambda stage, current, total: self.scan_queue.put(
					("progress", stage, current, total)
				),
				stop_event=self.scan_stop_event,
			)
			self.scan_queue.put(("done", output))
		except ScanCancelled:
			self.scan_queue.put(("cancelled",))
		except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
			self.scan_queue.put(("error", str(error)))

	def poll_alloy_scan(self):
		try:
			while True:
				message = self.scan_queue.get_nowait()
				if message[0] == "progress":
					_, stage, current, total = message
					self.scan_status.configure(
						text=f"{stage}: {current}/{total}" if total else stage
					)
					if total:
						self.scan_progress.set(current / total)
					else:
						self.scan_progress.configure(mode="indeterminate")
						self.scan_progress.start()
					continue
				self.finish_alloy_scan(message)
				return
		except queue.Empty:
			self.after(100, self.poll_alloy_scan)

	def stop_alloy_scan(self):
		if not getattr(self, "scan_stop_event", None):
			return
		self.scan_stop_event.set()
		self.scan_cancel_button.configure(state="disabled", text="Stopping...")
		self.scan_status.configure(text="Stopping after the current file...")

	def finish_alloy_scan(self, message):
		if self.scan_progress.cget("mode") == "indeterminate":
			self.scan_progress.stop()
		self.scan_dialog.grab_release()
		self.scan_dialog.destroy()
		if message[0] == "cancelled":
			self.show_result("Alloy scan stopped.", error=False)
			return
		if message[0] == "error":
			self.show_result(f"Could not update alloys: {message[1]}", error=True)
			return
		output = message[1]
		self.alloys = output["alloys"]
		self.refresh_alloy_selector()
		selected = self.selected_alloy if self.selected_alloy in self.alloys else next(iter(self.alloys))
		self.select_alloy(selected)
		self.show_result(
			f"Alloys updated: {len(self.alloys)} recipes found.",
			error=False,
		)

	def refresh_headers(self):
		self.amount_entry.grid()
		self.amount_unit_selector.grid()
		self.crucible_check.grid()
		self.content.configure(label_text="Composition constraints")
		for fields in self.component_widgets.values():
			visible = (
				"priority",
				"min",
				"max",
				"composition_batches",
				"split_check",
			)
			for key, widget in fields.items():
				if key in ("batch_rows", "batch_add", "batch_121", "batch_144", "split_var"):
					continue
				if key in visible:
					widget.grid()
				else:
					widget.grid_remove()

	def display_name(self, component_id):
		return self.alloys[self.selected_alloy]["components"].get(
			component_id, {}
		).get("name", component_id)

	@staticmethod
	def number(entry, field_name, default=None):
		value = entry.get()
		if isinstance(value, (int, float)):
			return value
		value = value.strip()
		if not value:
			if default is not None:
				return default
			raise ValueError(f"Enter {field_name}")
		return float(value.replace(",", "."))

	def add_batch_row(self, component_name):
		fields = self.component_widgets[component_name]
		row_index = len(fields["batch_rows"])
		row_frame = ctk.CTkFrame(fields["batch_frame"], fg_color="transparent")
		row_frame.grid(row=row_index, column=0, sticky="w")
		size_entry = ctk.CTkEntry(
			row_frame, placeholder_text="Material Density", width=110, height=30,
			font=ctk.CTkFont(size=12),
		)
		self.configure_numeric_entry(size_entry)
		count_entry = ctk.CTkEntry(
			row_frame, placeholder_text="Amount", width=68, height=30,
			font=ctk.CTkFont(size=12),
		)
		self.configure_numeric_entry(count_entry)
		size_entry.grid(row=0, column=0, padx=(0, 3))
		count_entry.grid(row=0, column=1, padx=(0, 3))
		remove_button = ctk.CTkButton(
			row_frame, text="-", width=28, height=30,
			command=lambda: self.remove_batch_row(component_name, row_frame),
		)
		remove_button.grid(row=0, column=2, padx=(0, 3))
		fields["batch_rows"].append((row_frame, size_entry, count_entry, remove_button))
		if "batch_add" in fields:
			fields["batch_add"].grid_remove()
		add_button = ctk.CTkButton(
			fields["batch_frame"], text="+", width=28, height=30,
			command=lambda: self.add_batch_row(component_name),
		)
		add_button.grid(row=row_index, column=1, padx=(0, 3))
		fields["batch_add"] = add_button
		self.update_batch_buttons(component_name)

	def remove_batch_row(self, component_name, row_frame):
		fields = self.component_widgets[component_name]
		if len(fields["batch_rows"]) <= 1:
			return

		row = next(
			row for row in fields["batch_rows"]
			if row[0] is row_frame
		)
		fields["batch_rows"].remove(row)
		row_frame.destroy()
		self.relayout_batch_rows(component_name)

	def relayout_batch_rows(self, component_name):
		fields = self.component_widgets[component_name]
		for row_index, row in enumerate(fields["batch_rows"]):
			row[0].grid(row=row_index, column=0, sticky="w")

		if "batch_add" in fields:
			fields["batch_add"].grid_remove()
		add_button = ctk.CTkButton(
			fields["batch_frame"], text="+", width=28, height=30,
			command=lambda: self.add_batch_row(component_name),
		)
		add_button.grid(row=len(fields["batch_rows"]) - 1, column=1, padx=(0, 3))
		fields["batch_add"] = add_button
		self.update_batch_buttons(component_name)

	def update_batch_buttons(self, component_name):
		fields = self.component_widgets[component_name]
		for row_index, row in enumerate(fields["batch_rows"]):
			row[3].configure(
				state="normal" if len(fields["batch_rows"]) > 1 else "disabled"
			)

		if "batch_add" in fields:
			fields["batch_add"].grid(
				row=len(fields["batch_rows"]) - 1,
				column=1,
				padx=(0, 3),
			)

	def calculate(self):
		try:
			amount_text = self.amount_entry.get().strip()
			amount = float(amount_text.replace(",", ".")) if amount_text else None
			if amount is not None and self.amount_unit_selector.get() == "Ingots (144)":
				amount *= 144
			priorities = {
				name: self.number(fields["priority"], f"priority for {name}", 0.0)
				for name, fields in self.component_widgets.items()
			}
			limits = {}
			batch_limits = {}
			for name, fields in self.component_widgets.items():
				limits[name] = {
					"min": self.number(fields["min"], f"absolute min for {name}", 0.0),
				}
				max_text = fields["max"].get().strip()
				if max_text:
					limits[name]["max"] = float(max_text.replace(",", "."))
				batch_121_text = fields["batch_121"].get().strip()
				batch_144_text = fields["batch_144"].get().strip()
				batch_limits[name] = {
					"121": self.number(fields["batch_121"], f"121 batch count for {name}", 0),
					"144": (
						None
						if not batch_121_text and not batch_144_text
						else self.number(fields["batch_144"], f"144 batch count for {name}", 0)
					),
					"split": fields["split_var"].get(),
				}
			calculation_args = {
				"alloys": self.alloys,
				"alloy_name": self.selected_alloy,
				"absolute_limits": limits,
				"batch_limits": batch_limits,
				"priorities": priorities,
				"return_usage": True,
			}
			if amount is not None:
				calculation_args["minimum_amount"] = amount
			amount, contributions, usage = calculate_max_composition_amount(**calculation_args)
			if amount <= 0:
				raise ValueError("No positive alloy amount can be produced")
			for component_limits in limits.values():
				component_limits.setdefault("max", amount)
			lines = self.alloy_info_lines()
			lines.extend([f"{amount:g} units of alloy or {amount / 144:g} ingots", ""])
			lines.extend(
				self.component_output_line(name, value, amount, usage.get(name, {}))
				for name, value in contributions.items()
			)
			if self.crucible_var.get():
				self.append_crucible_output(lines, usage)
			result_text = "\n".join(lines)
			calculation_title = (
				f"{self.alloys[self.selected_alloy].get('name', self.selected_alloy)} | "
				f"Composition | {amount:g} units"
			)
			self.last_calculation = self.make_calculation_record(result_text, calculation_title)
			self.record_history(result_text, calculation_title)
			self.save_calculation_button.configure(state="normal", text="☆")
			self.save_calculation_button.grid()
			self.show_result(result_text, error=False)
			if self.recursive_var.get():
				self.open_recursive_windows(contributions)
		except (ValueError, TypeError) as error:
			error_text = str(error)
			mode_title = "Composition"
			error_title = (
				f"{self.alloys[self.selected_alloy].get('name', self.selected_alloy)} | "
				f"{mode_title} | Error: {error_text}"
			)
			self.record_history(error_text, error_title)
			self.last_calculation = None
			self.save_calculation_button.configure(state="disabled", text="☆")
			self.save_calculation_button.grid_remove()
			self.show_result(error_text, error=True)

	def alloy_info_lines(self):
		alloy = self.alloys[self.selected_alloy]
		lines = [
			f"Alloy: {alloy.get('name', self.selected_alloy)}",
			"Allowed composition:",
		]
		lines.extend(
			f"  {self.display_name(name)}: {float(data['min']):g}-{float(data['max']):g}%"
			for name, data in alloy["components"].items()
		)
		lines.append("")
		return lines

	def open_recursive_windows(self, contributions):
		for window in self.recursive_windows:
			if window.winfo_exists():
				window.destroy()
		self.recursive_windows = []
		for component, amount in contributions.items():
			recipe_id = next(
			(recipe_id for recipe_id, alloy in self.alloys.items()
			 if alloy.get("result") == component),
			None,
		)
			if recipe_id is None:
				continue
			self.recursive_windows.append(
				RecursiveAlloyWindow(self, self.alloys, self.colors, recipe_id, amount)
			)

	def component_output_line(self, name, amount, total, usage):
		batch_inventory = expand_batch_usage({name: usage}).get(name, [])
		batch_counts = {}
		for size in batch_inventory:
			batch_counts[size] = batch_counts.get(size, 0) + 1
		independent = usage.get("Independent", 0)
		if independent:
			batch_counts[independent] = batch_counts.get(independent, 0) + 1
		return self.component_line(name, amount, total, batch_counts)

	def component_line(self, name, amount, total, batch_counts):
		terms = " + ".join(
			f"{size:g}*{count}" for size, count in batch_counts.items()
		)
		if not terms:
			terms = "0"
		ingots = f"{amount / 144:.2f}".replace(".", ",")
		percentage = f"{amount / total * 100:.2f}".replace(".", ",")
		return (
			f"{self.display_name(name)}: {terms} = {amount:g} units/ "
			f"{ingots} ingots ({percentage}%)"
		)

	def append_crucible_output(self, lines, usage):
		batch_inventory = expand_batch_usage(usage)
		percentages = {
			name: (
				float(data["min"]) / 100,
				float(data["max"]) / 100,
			)
			for name, data in self.alloys[self.selected_alloy]["components"].items()
		}
		loads = pack_batches_into_crucibles(batch_inventory, percentages)
		lines.extend(("", "Crucible loads (maximum 4608 units):"))
		for index, load in enumerate(loads, start=1):
			load_total = load["total"]
			lines.append(f"Load {index} ({load_total:g} units):")
			for name, batches in load["metals"].items():
				component_total = sum(size * count for size, count in batches.items())
				lines.append(
					"  " + self.component_line(
						name, component_total, load_total, batches
					)
				)

	def show_result(self, text, error):
		self.result_panel.grid()
		self.result_title.configure(text="Could not calculate" if error else "Calculation result")
		self.result_label.configure(text=text)
		self.status_label.configure(text="Check the highlighted values and try again." if error else "Ready")
		self.update_idletasks()

		window_width = max(self.winfo_width(), self.compact_width)
		result_width = max(420, window_width - 80)
		self.result_label.configure(wraplength=result_width)
		self.update_idletasks()

		required_height = (
			self.compact_height
			+ self.result_panel.winfo_reqheight()
			+ 8
		)
		max_height = self.winfo_screenheight() - 50
		target_height = min(required_height, max_height)
		self.geometry(f"{window_width}x{target_height}")

	def hide_result(self):
		self.result_panel.grid_remove()
		self.result_label.configure(text="")
		self.status_label.configure(text="")
		self.geometry(f"{self.compact_width}x{self.compact_height}")


class RecursiveAlloyWindow(ctk.CTkToplevel):
	def __init__(self, parent, alloys, colors, alloy_id, target_amount):
		super().__init__(parent)
		self.parent = parent
		self.alloys = alloys
		self.colors = colors
		self.current_theme = parent.current_theme
		self.alloy_id = alloy_id
		self.target_amount = target_amount
		self.fields = {}
		self.recursive_windows = []
		self.title(f"Recursive alloying: {self.alloy_name}")
		self.geometry("550x700")
		self.minsize(550, 600)
		self.transient(parent)
		self.protocol("WM_DELETE_WINDOW", self.close)
		self.build_layout()
		self.apply_theme()

	@property
	def alloy(self):
		return self.alloys[self.alloy_id]

	@property
	def alloy_name(self):
		return self.alloy.get("name", self.alloy_id)

	def build_layout(self):
		self.grid_columnconfigure(0, weight=1)
		self.grid_rowconfigure(1, weight=1)
		ctk.CTkLabel(
			self, text=f"Recursive alloying: {self.alloy_name}",
			font=ctk.CTkFont(size=20, weight="bold"),
		).grid(row=0, column=0, padx=20, pady=(18, 8), sticky="w")
		self.content = ctk.CTkScrollableFrame(self, label_text="Composition constraints")
		self.content.grid(row=1, column=0, padx=20, pady=8, sticky="nsew")
		self.content.grid_columnconfigure(0, weight=1)
		for index, (name, data) in enumerate(self.alloy["components"].items()):
			row = index * 2
			ctk.CTkLabel(
				self.content, text=data.get("name", name), anchor="w"
			).grid(row=row, column=0, padx=8, pady=5, sticky="w")
			priority_frame = ctk.CTkFrame(self.content)
			priority_frame.grid(row=row, column=1, padx=3, pady=5, sticky="ew")
			priority_frame.grid_columnconfigure(0, weight=1)
			priority = ctk.CTkSlider(
				priority_frame, from_=-1, to=1, number_of_steps=4,
				width=80, height=14,
			)
			priority.set(0)
			ctk.CTkLabel(priority_frame, text="priority", font=ctk.CTkFont(size=12)).grid(
				row=0, column=0, sticky="n"
			)
			priority.grid(row=0, column=1, sticky="ew")

			minimum = ctk.CTkEntry(self.content, placeholder_text="Min", width=60)
			self.configure_numeric_entry(minimum)
			maximum = ctk.CTkEntry(self.content, placeholder_text="Max", width=60)
			self.configure_numeric_entry(maximum)
			minimum.grid(row=row, column=2, padx=3, pady=5)
			maximum.grid(row=row, column=3, padx=3, pady=5)
			batch_121 = ctk.CTkEntry(self.content, placeholder_text="121", width=80)
			batch_144 = ctk.CTkEntry(self.content, placeholder_text="144", width=80)
			self.configure_numeric_entry(batch_121)
			self.configure_numeric_entry(batch_144)
			split_var = ctk.BooleanVar(value=False)
			split_check = ctk.CTkCheckBox(self.content, text="Split", variable=split_var)
			batch_121.grid(row=row + 1, column=1, padx=3, pady=(0, 5), sticky="w")
			batch_144.grid(row=row + 1, column=2, padx=3, pady=(0, 5), sticky="w")
			split_check.grid(row=row + 1, column=3, padx=3, pady=(0, 5), sticky="w")
			self.fields[name] = {
				"priority": priority, "min": minimum, "max": maximum,
				"121": batch_121, "144": batch_144, "split": split_var,
			}
		self.action_bar = ctk.CTkFrame(self, fg_color="transparent")
		self.action_bar.grid(row=2, column=0, padx=20, pady=8, sticky="ew")
		self.amount_entry = ctk.CTkEntry(self.action_bar, width=190, justify="center")
		self.configure_numeric_entry(self.amount_entry)
		self.amount_entry.insert(0, f"{self.target_amount:g}")
		self.amount_entry.grid(row=0, column=0, padx=(0, 8))
		self.amount_unit_selector = ctk.CTkSegmentedButton(
			self.action_bar, values=["Units", "Ingots (144)"], width=150,
			command=self.change_amount_unit,
		)
		self.amount_unit_selector.set("Units")
		self.amount_unit = "Units"
		self.amount_unit_selector.grid(row=0, column=2, padx=8)
		self.crucible_var = ctk.BooleanVar(value=False)
		ctk.CTkCheckBox(
			self.action_bar, text="Split into crucible loads (4608)",
			variable=self.crucible_var,
		).grid(row=1, column=0, columnspan=2, pady=5, sticky="w")
		self.recursive_var = ctk.BooleanVar(value=True)
		ctk.CTkCheckBox(
			self.action_bar, text="Recursive alloying", variable=self.recursive_var,
		).grid(row=2, column=0, columnspan=2, pady=5, sticky="w")
		ctk.CTkButton(
			self.action_bar, text="Calculate", command=self.calculate, width=120,
		).grid(row=0, column=1, padx=8)
		self.result = ctk.CTkTextbox(self, height=190)
		self.result.grid(row=3, column=0, padx=20, pady=(0, 12), sticky="ew")

	def apply_theme(self):
		palette = self.colors[self.current_theme]
		self.configure(fg_color=palette["FIRST_MAIN_COLOR"])
		self.content.configure(fg_color=palette["SECOND_MAIN_COLOR"])
		self.result.configure(
			fg_color=palette["FOURTH_MAIN_COLOR"],
			text_color=palette["TEXT_COLOR"],
		)
		self.amount_unit_selector.configure(
			fg_color=palette["THIRD_MAIN_COLOR"],
			selected_color=palette["SECONDARY_COLOR"],
			selected_hover_color=palette["HIGHLIGHT_COLOR"],
			unselected_color=palette["FOURTH_MAIN_COLOR"],
			unselected_hover_color=palette["HIGHLIGHT_COLOR"],
			text_color=palette["TEXT_COLOR"],
		)

	def configure_numeric_entry(self, entry, allow_negative=False):
		command = self.register(
			lambda value: bool(re.fullmatch(
				r"-?\d*([.,]\d*)?" if allow_negative else r"\d*([.,]\d*)?",
				value,
			))
		)
		entry.configure(validate="key", validatecommand=(command, "%P"))

	def number(self, entry, name, default=0.0):
		value = entry.get()
		if isinstance(value, (int, float)):
			return value
		value = value.strip()
		return default if not value else float(value.replace(",", "."))

	def display_name(self, component_id):
		return self.alloy["components"].get(component_id, {}).get("name", component_id)

	def alloy_info_lines(self):
		lines = [
			f"Alloy: {self.alloy_name}",
			"Allowed composition:",
		]
		lines.extend(
			f"  {self.display_name(name)}: {float(data['min']):g}-{float(data['max']):g}%"
			for name, data in self.alloy["components"].items()
		)
		lines.append("")
		return lines

	def component_output_line(self, name, amount, total, usage):
		batch_inventory = expand_batch_usage({name: usage}).get(name, [])
		batch_counts = {}
		for size in batch_inventory:
			batch_counts[size] = batch_counts.get(size, 0) + 1
		independent = usage.get("Independent", 0)
		if independent:
			batch_counts[independent] = batch_counts.get(independent, 0) + 1
		return self.component_line(name, amount, total, batch_counts)

	def component_line(self, name, amount, total, batch_counts):
		terms = " + ".join(
			f"{size:g}*{count}" for size, count in batch_counts.items()
		) or "0"
		ingots = f"{amount / 144:.2f}".replace(".", ",")
		percentage = f"{amount / total * 100:.2f}".replace(".", ",")
		return (
			f"{self.display_name(name)}: {terms} = {amount:g} units/ "
			f"{ingots} ingots ({percentage}%)"
		)

	def append_crucible_output(self, lines, usage):
		inventory = expand_batch_usage(usage)
		percentages = {
			name: (float(data["min"]) / 100, float(data["max"]) / 100)
			for name, data in self.alloy["components"].items()
		}
		loads = pack_batches_into_crucibles(inventory, percentages)
		lines.extend(("", "Crucible loads (maximum 4608 units):"))
		for index, load in enumerate(loads, start=1):
			load_total = load["total"]
			lines.append(f"Load {index} ({load_total:g} units):")
			for name, batches in load["metals"].items():
				component_total = sum(size * count for size, count in batches.items())
				lines.append("  " + self.component_line(
					name, component_total, load_total, batches
				))

	def change_amount_unit(self, unit):
		if unit == getattr(self, "amount_unit", unit):
			self.amount_unit = unit
			return
		value = self.amount_entry.get().strip().replace(",", ".")
		if value:
			amount = float(value)
			converted = math.ceil(amount * 144) if unit == "Units" else math.ceil(amount / 144)
			self.amount_entry.delete(0, "end")
			self.amount_entry.insert(0, f"{converted:g}")
		self.amount_unit = unit

	def calculate(self):
		try:
			amount = float(self.amount_entry.get().replace(",", "."))
			if self.amount_unit_selector.get() == "Ingots (144)":
				amount *= 144
			priorities = {}
			limits = {}
			batch_limits = {}
			for name, fields in self.fields.items():
				priorities[name] = self.number(fields["priority"], f"priority for {name}")
				limits[name] = {"min": self.number(fields["min"], f"minimum for {name}")}
				maximum = fields["max"].get().strip()
				if maximum:
					limits[name]["max"] = float(maximum.replace(",", "."))
				batch_limits[name] = {
					"121": self.number(fields["121"], "121 batches"),
					"144": (
						None
						if not fields["121"].get().strip() and not fields["144"].get().strip()
						else self.number(fields["144"], "144 batches")
					),
					"split": fields["split"].get(),
				}
			result_amount, contributions, usage = calculate_max_composition_amount(
				self.alloys, self.alloy_id, limits, batch_limits,
				minimum_amount=amount, priorities=priorities, return_usage=True,
			)
			lines = self.alloy_info_lines()
			lines.extend([
				f"{result_amount:g} units of alloy or {result_amount / 144:g} ingots",
				"",
			])
			lines.extend(
				self.component_output_line(name, value, result_amount, usage.get(name, {}))
				for name, value in contributions.items()
			)
			if self.crucible_var.get():
				self.append_crucible_output(lines, usage)
			self.result.delete("1.0", "end")
			self.result.insert("1.0", "\n".join(lines))
			if self.recursive_var.get():
				for window in self.recursive_windows:
					if window.winfo_exists():
						window.destroy()
				self.recursive_windows = []
				for name, value in contributions.items():
					nested_id = next(
						(recipe_id for recipe_id, alloy in self.alloys.items()
						 if alloy.get("result") == name), None
					)
					if nested_id:
						self.recursive_windows.append(
							RecursiveAlloyWindow(self, self.alloys, self.colors, nested_id, value)
						)
		except (ValueError, TypeError) as error:
			self.result.delete("1.0", "end")
			self.result.insert("1.0", str(error))

	def close(self):
		for window in self.recursive_windows:
			if window.winfo_exists():
				window.destroy()
		self.destroy()
