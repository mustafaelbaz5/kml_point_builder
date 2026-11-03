from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .builder import build_project
from .models import BuildOptions


class Application(ttk.Frame):
    def __init__(self, root: tk.Tk) -> None:
        super().__init__(root, padding=20)
        self.root = root
        self.source = tk.StringVar()
        self.output = tk.StringVar()
        self.status = tk.StringVar(value="جاهز لاختيار ملف KML أو KMZ.")
        self.grid(sticky="nsew")
        self._build()

    def _build(self) -> None:
        self.root.title("KML Point Builder")
        self.root.minsize(690, 330)
        ttk.Label(self, text="KML Point Builder", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            self, text="تحويل KML/KMZ إلى طبقات قطع ونقاط مرجعية جاهزة لـ ArcGIS Pro."
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 18))
        self._file_row(2, "ملف الإدخال", self.source, self._select_source, "KML أو KMZ")
        self._file_row(
            3, "مجلد الإخراج", self.output, self._select_output, "اختيار مجلد"
        )
        ttk.Separator(self).grid(row=4, column=0, columnspan=3, sticky="ew", pady=15)
        ttk.Label(
            self,
            text="الناتج الثابت: ملف GeoPackage وملف KML، وفي كل منهما القطع والنقاط المرجعية.",
            wraplength=620,
        ).grid(row=5, column=0, columnspan=3, sticky="w")
        self.run_button = ttk.Button(self, text="إنشاء المشروع", command=self._run)
        self.run_button.grid(row=6, column=0, sticky="w", pady=22)
        ttk.Label(self, textvariable=self.status, wraplength=620).grid(
            row=7, column=0, columnspan=3, sticky="w"
        )
        self.columnconfigure(1, weight=1)

    def _file_row(
        self, row: int, label: str, variable: tk.StringVar, command, button: str
    ) -> None:
        ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(self, textvariable=variable, width=62).grid(
            row=row, column=1, sticky="ew", pady=5
        )
        ttk.Button(self, text=button, command=command).grid(
            row=row, column=2, padx=(8, 0)
        )

    def _select_source(self) -> None:
        value = filedialog.askopenfilename(filetypes=[("KML/KMZ", "*.kml *.kmz")])
        if value:
            self.source.set(value)
            if not self.output.get():
                self.output.set(str(Path(value).parent))

    def _select_output(self) -> None:
        value = filedialog.askdirectory()
        if value:
            self.output.set(value)

    def _run(self) -> None:
        if not self.source.get() or not self.output.get():
            messagebox.showerror(
                "بيانات ناقصة", "اختر ملف الإدخال ومجلد الإخراج أولًا."
            )
            return
        self.run_button.state(["disabled"])
        self.status.set("جارٍ قراءة الملف وإنشاء القطع والنقاط المرجعية…")
        options = BuildOptions(
            Path(self.source.get()),
            Path(self.output.get()),
            Path(self.source.get()).stem,
        )
        threading.Thread(target=self._work, args=(options,), daemon=True).start()

    def _work(self, options: BuildOptions) -> None:
        try:
            result = build_project(options)
            message = (
                f"تم إنشاء {result.parcel_count} قطعة و{result.point_count} نقطة.\n"
                f"GeoPackage: {result.geopackage}\n"
                f"KML: {result.kml}"
            )
            if result.warnings:
                message += "\n\nتنبيهات:\n" + "\n".join(result.warnings)
            self.root.after(0, lambda: self._complete(message, False))
        except Exception as error:  # surfaced clearly to the operator
            self.root.after(0, lambda: self._complete(str(error), True))

    def _complete(self, message: str, failed: bool) -> None:
        self.run_button.state(["!disabled"])
        self.status.set(message)
        (messagebox.showerror if failed else messagebox.showinfo)(
            "تعذر إنشاء المشروع" if failed else "اكتمل", message
        )


def main() -> None:
    root = tk.Tk()
    Application(root)
    root.mainloop()
