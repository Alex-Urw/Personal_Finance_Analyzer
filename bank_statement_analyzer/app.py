"""Graphical interface for the Bank Statement Analyzer."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from bank_statement_analyzer.bank_analyzer import (
    CATEGORIES,
    ESSENTIAL_CATEGORIES,
    EXPENSE_CATEGORIES,
    AnalysisResult,
    MerchantMemory,
    Transaction,
    analyze_transactions,
    build_text_report,
    format_money,
    normalize_merchant,
    read_statement,
)


# Friendly labels in the interface map to short names used by the importer.
SIGN_MODES = {
    "Auto-detect": "auto",
    "Bank: negative spending / positive income": "bank",
    "Credit card: positive spending / negative payment": "credit_card",
}


class ReviewDialog(tk.Toplevel):
    """Ask the user to categorize unclear transactions one at a time."""

    def __init__(
        self,
        parent: tk.Tk,
        transactions: list[Transaction],
        memory: MerchantMemory,
        on_change: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.title("Review unclear transactions")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.transactions = transactions
        self.memory = memory
        self.on_change = on_change
        self.index = 0

        self.category_var = tk.StringVar(value="Miscellaneous")
        self.remember_var = tk.BooleanVar(value=True)

        container = ttk.Frame(self, padding=24)
        container.grid(row=0, column=0, sticky="nsew")

        self.progress_label = ttk.Label(container, font=("Segoe UI", 10, "bold"))
        self.progress_label.grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(container, text="Merchant").grid(row=1, column=0, sticky="nw", pady=(18, 4))
        self.merchant_label = ttk.Label(
            container,
            width=50,
            wraplength=420,
            font=("Segoe UI", 12, "bold"),
        )
        self.merchant_label.grid(row=1, column=1, sticky="w", pady=(18, 4))

        ttk.Label(container, text="Date").grid(row=2, column=0, sticky="w", pady=4)
        self.date_label = ttk.Label(container)
        self.date_label.grid(row=2, column=1, sticky="w", pady=4)

        ttk.Label(container, text="Amount").grid(row=3, column=0, sticky="w", pady=4)
        self.amount_label = ttk.Label(container, font=("Segoe UI", 11, "bold"))
        self.amount_label.grid(row=3, column=1, sticky="w", pady=4)

        ttk.Label(container, text="Category").grid(row=4, column=0, sticky="w", pady=(14, 4))
        category_box = ttk.Combobox(
            container,
            textvariable=self.category_var,
            values=CATEGORIES,
            state="readonly",
            width=34,
        )
        category_box.grid(row=4, column=1, sticky="w", pady=(14, 4))

        ttk.Checkbutton(
            container,
            text="Remember this merchant for future statements",
            variable=self.remember_var,
        ).grid(row=5, column=1, sticky="w", pady=(8, 18))

        button_row = ttk.Frame(container)
        button_row.grid(row=6, column=0, columnspan=2, sticky="e")
        ttk.Button(button_row, text="Finish later", command=self.destroy).pack(side="left", padx=5)
        self.save_button = ttk.Button(button_row, text="Save & next", command=self.save_and_next)
        self.save_button.pack(side="left", padx=5)

        self.bind("<Return>", lambda _event: self.save_and_next())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.show_current()

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def show_current(self) -> None:
        """Show the current unclear transaction."""

        # Skip duplicate merchants categorized by a saved choice.
        while (
            self.index < len(self.transactions)
            and not self.transactions[self.index].needs_review
        ):
            self.index += 1

        if self.index >= len(self.transactions):
            self.on_change()
            self.destroy()
            messagebox.showinfo("Review complete", "Every unclear transaction now has a category.")
            return

        transaction = self.transactions[self.index]
        self.progress_label.configure(
            text=f"Transaction {self.index + 1} of {len(self.transactions)}"
        )
        self.merchant_label.configure(text=transaction.merchant)
        self.date_label.configure(text=transaction.date or "Not provided")
        self.amount_label.configure(text=format_money(transaction.amount))
        self.category_var.set("Miscellaneous")

        if self.index == len(self.transactions) - 1:
            self.save_button.configure(text="Save & finish")

    def save_and_next(self) -> None:
        """Save the selected category and move forward."""

        transaction = self.transactions[self.index]
        category = self.category_var.get()
        transaction.category = category

        if self.remember_var.get():
            try:
                self.memory.remember(transaction.merchant, category)
                merchant_key = normalize_merchant(transaction.merchant)
                for later_transaction in self.transactions[self.index + 1 :]:
                    if (
                        later_transaction.needs_review
                        and normalize_merchant(later_transaction.merchant) == merchant_key
                    ):
                        later_transaction.category = category
            except OSError as exc:
                messagebox.showwarning(
                    "Could not save merchant memory",
                    f"The category was applied, but it could not be remembered.\n\n{exc}",
                    parent=self,
                )

        self.index += 1
        self.on_change()
        self.show_current()


class AnalyzerApp:
    """Main application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Bank Statement Analyzer")
        self.root.geometry("1180x760")
        self.root.minsize(980, 650)

        self.memory = MerchantMemory()
        self.transactions: list[Transaction] = []
        self.current_file: Optional[Path] = None
        self.result: Optional[AnalysisResult] = None

        self.sign_mode_var = tk.StringVar(value="Auto-detect")
        self.selected_category_var = tk.StringVar(value="Miscellaneous")
        self.remember_selected_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Choose a CSV or PDF statement to begin.")

        self._set_style()
        self._build_interface()

    def _set_style(self) -> None:
        """Set simple colors and spacing for a readable interface."""

        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground="#52606d")
        style.configure("CardTitle.TLabel", font=("Segoe UI", 9), foreground="#52606d")
        style.configure("CardValue.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def _build_interface(self) -> None:
        """Create all widgets in the main window."""

        outer = ttk.Frame(self.root, padding=(22, 18))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Bank Statement Analyzer", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Import locally, review every transaction, and see where your money went.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 14))

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x", pady=(0, 12))

        ttk.Button(
            toolbar,
            text="Import statement",
            style="Accent.TButton",
            command=self.import_statement,
        ).pack(side="left", padx=(0, 8))

        ttk.Label(toolbar, text="Amount format:").pack(side="left", padx=(8, 5))
        ttk.Combobox(
            toolbar,
            textvariable=self.sign_mode_var,
            values=tuple(SIGN_MODES),
            state="readonly",
            width=43,
        ).pack(side="left", padx=(0, 8))

        ttk.Button(toolbar, text="Review unclear", command=self.review_unclear).pack(
            side="left", padx=4
        )
        ttk.Button(toolbar, text="Analyze statement", command=self.analyze).pack(
            side="left", padx=4
        )
        ttk.Button(toolbar, text="Clear", command=self.clear_statement).pack(side="right")

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)

        self.transactions_tab = ttk.Frame(self.notebook, padding=12)
        self.summary_tab = ttk.Frame(self.notebook, padding=12)
        self.savings_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.transactions_tab, text="1. Transactions")
        self.notebook.add(self.summary_tab, text="2. Summary")
        self.notebook.add(self.savings_tab, text="3. Savings ideas")

        self._build_transactions_tab()
        self._build_summary_tab()
        self._build_savings_tab()

        status = ttk.Label(outer, textvariable=self.status_var, style="Subtitle.TLabel")
        status.pack(fill="x", pady=(10, 0))

    def _build_transactions_tab(self) -> None:
        """Create the transaction table and editing controls."""

        table_frame = ttk.Frame(self.transactions_tab)
        table_frame.pack(fill="both", expand=True)

        columns = ("date", "merchant", "amount", "category", "type")
        self.transaction_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.transaction_tree.heading("date", text="Date")
        self.transaction_tree.heading("merchant", text="Merchant / Description")
        self.transaction_tree.heading("amount", text="Amount")
        self.transaction_tree.heading("category", text="Category")
        self.transaction_tree.heading("type", text="Type")
        self.transaction_tree.column("date", width=105, anchor="w")
        self.transaction_tree.column("merchant", width=420, anchor="w")
        self.transaction_tree.column("amount", width=110, anchor="e")
        self.transaction_tree.column("category", width=170, anchor="w")
        self.transaction_tree.column("type", width=110, anchor="w")
        self.transaction_tree.tag_configure("review", background="#fff3cd")
        self.transaction_tree.tag_configure("income", background="#e7f6ec")
        self.transaction_tree.tag_configure("ignored", foreground="#7b8794")
        self.transaction_tree.bind("<<TreeviewSelect>>", self._load_selected_category)
        self.transaction_tree.bind("<Double-1>", lambda _event: self.selected_category_box.focus_set())

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.transaction_tree.yview)
        self.transaction_tree.configure(yscrollcommand=scrollbar.set)
        self.transaction_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        editor = ttk.LabelFrame(self.transactions_tab, text="Edit selected transaction", padding=10)
        editor.pack(fill="x", pady=(12, 0))

        ttk.Label(editor, text="Category:").pack(side="left")
        self.selected_category_box = ttk.Combobox(
            editor,
            textvariable=self.selected_category_var,
            values=CATEGORIES,
            state="readonly",
            width=25,
        )
        self.selected_category_box.pack(side="left", padx=(6, 10))
        ttk.Checkbutton(
            editor,
            text="Remember merchant",
            variable=self.remember_selected_var,
        ).pack(side="left", padx=6)
        ttk.Button(editor, text="Apply category", command=self.apply_selected_category).pack(
            side="left", padx=6
        )
        ttk.Button(editor, text="Clear merchant memory", command=self.clear_merchant_memory).pack(
            side="right"
        )

    def _build_summary_tab(self) -> None:
        """Create total cards and the category breakdown table."""

        cards = ttk.Frame(self.summary_tab)
        cards.pack(fill="x", pady=(0, 12))
        for column in range(5):
            cards.columnconfigure(column, weight=1)

        card_info = (
            ("Income", "income"),
            ("Total spending", "spending"),
            ("Essential", "essential"),
            ("Nonessential", "nonessential"),
            ("Spending / income", "ratio"),
        )
        self.card_values: dict[str, ttk.Label] = {}

        for column, (title, key) in enumerate(card_info):
            card = ttk.LabelFrame(cards, padding=12)
            card.grid(row=0, column=column, sticky="nsew", padx=4)
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
            value = ttk.Label(card, text="—", style="CardValue.TLabel")
            value.pack(anchor="w", pady=(4, 0))
            self.card_values[key] = value

        columns = ("category", "type", "amount", "income_percent", "spending_percent")
        self.summary_tree = ttk.Treeview(self.summary_tab, columns=columns, show="headings")
        headings = {
            "category": "Category",
            "type": "Essential?",
            "amount": "Amount",
            "income_percent": "% of income",
            "spending_percent": "% of spending",
        }
        widths = {
            "category": 230,
            "type": 120,
            "amount": 140,
            "income_percent": 140,
            "spending_percent": 150,
        }
        for column in columns:
            self.summary_tree.heading(column, text=headings[column])
            anchor = "e" if column in {"amount", "income_percent", "spending_percent"} else "w"
            self.summary_tree.column(column, width=widths[column], anchor=anchor)
        self.summary_tree.pack(fill="both", expand=True)

        summary_buttons = ttk.Frame(self.summary_tab)
        summary_buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(summary_buttons, text="Save text report", command=self.save_report).pack(
            side="right"
        )

    def _build_savings_tab(self) -> None:
        """Create the area for the top-three savings estimates."""

        ttk.Label(
            self.savings_tab,
            text="Savings estimates use your three highest nonessential categories.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 12))

        self.savings_container = ttk.Frame(self.savings_tab)
        self.savings_container.pack(fill="both", expand=True)
        self.empty_savings_label = ttk.Label(
            self.savings_container,
            text="Analyze a statement to see possible monthly and yearly savings.",
            font=("Segoe UI", 12),
        )
        self.empty_savings_label.pack(anchor="center", pady=80)

    def import_statement(self) -> None:
        """Open a file picker and import the selected statement."""

        path_text = filedialog.askopenfilename(
            title="Choose a bank statement",
            filetypes=(
                ("Bank statements", "*.csv *.pdf"),
                ("CSV files", "*.csv"),
                ("PDF files", "*.pdf"),
            ),
        )
        if not path_text:
            return

        path = Path(path_text)
        sign_mode = SIGN_MODES[self.sign_mode_var.get()]
        self.root.configure(cursor="watch")
        self.root.update_idletasks()

        try:
            transactions = read_statement(path, self.memory, sign_mode)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror("Could not import statement", str(exc))
            return
        finally:
            self.root.configure(cursor="")

        self.transactions = transactions
        self.current_file = path
        self.result = None
        self._clear_results()
        self.refresh_transaction_tree()
        self.notebook.select(self.transactions_tab)

        unclear_count = self._unclear_count()
        self.status_var.set(
            f"Imported {len(transactions)} transactions from {path.name}. "
            f"{unclear_count} need review."
        )

        if unclear_count and messagebox.askyesno(
            "Review unclear transactions?",
            f"{unclear_count} transaction(s) were not recognized. Review them now?",
        ):
            self.review_unclear()

    def refresh_transaction_tree(self) -> None:
        """Refresh every row in the transaction table."""

        for item in self.transaction_tree.get_children():
            self.transaction_tree.delete(item)

        for index, transaction in enumerate(self.transactions):
            category = transaction.category or "Needs review"
            if transaction.category in ESSENTIAL_CATEGORIES:
                type_text = "Essential"
            elif transaction.category in EXPENSE_CATEGORIES:
                type_text = "Nonessential"
            elif transaction.category == "Income":
                type_text = "Income"
            elif transaction.category == "Transfer / Ignore":
                type_text = "Excluded"
            else:
                type_text = "Unclear"

            tags: tuple[str, ...] = ()
            if transaction.needs_review:
                tags = ("review",)
            elif transaction.category == "Income":
                tags = ("income",)
            elif transaction.category == "Transfer / Ignore":
                tags = ("ignored",)

            self.transaction_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    transaction.date or "—",
                    transaction.merchant,
                    format_money(transaction.amount),
                    category,
                    type_text,
                ),
                tags=tags,
            )

    def _load_selected_category(self, _event: object = None) -> None:
        """Load the selected row's category into the editor."""

        selected = self.transaction_tree.selection()
        if not selected:
            return
        transaction = self.transactions[int(selected[0])]
        self.selected_category_var.set(transaction.category or "Miscellaneous")

    def apply_selected_category(self) -> None:
        """Apply an edited category to one transaction."""

        selected = self.transaction_tree.selection()
        if not selected:
            messagebox.showinfo("Select a transaction", "Click a transaction row first.")
            return

        index = int(selected[0])
        transaction = self.transactions[index]
        category = self.selected_category_var.get()
        transaction.category = category

        if self.remember_selected_var.get():
            try:
                self.memory.remember(transaction.merchant, category)
            except OSError as exc:
                messagebox.showwarning(
                    "Could not save merchant memory",
                    f"The category was applied, but it could not be remembered.\n\n{exc}",
                )

        self.result = None
        self._clear_results()
        self.refresh_transaction_tree()
        self.transaction_tree.selection_set(str(index))
        self.transaction_tree.focus(str(index))
        self.status_var.set(
            f"Category updated. {self._unclear_count()} transaction(s) still need review."
        )

    def review_unclear(self) -> None:
        """Open the guided review for transactions without a category."""

        unclear = [transaction for transaction in self.transactions if transaction.needs_review]
        if not self.transactions:
            messagebox.showinfo("No statement", "Import a statement first.")
            return
        if not unclear:
            messagebox.showinfo("Nothing to review", "Every transaction already has a category.")
            return

        ReviewDialog(self.root, unclear, self.memory, self._after_review_change)

    def _after_review_change(self) -> None:
        """Update the table while the guided review is running."""

        self.result = None
        self._clear_results()
        self.refresh_transaction_tree()
        self.status_var.set(f"{self._unclear_count()} transaction(s) still need review.")

    def analyze(self) -> None:
        """Calculate and display the complete analysis."""

        if not self.transactions:
            messagebox.showinfo("No statement", "Import a statement first.")
            return

        if self._unclear_count():
            if messagebox.askyesno(
                "Review required",
                "Every transaction needs a category before analysis. Review them now?",
            ):
                self.review_unclear()
            return

        try:
            self.result = analyze_transactions(self.transactions)
        except ValueError as exc:
            messagebox.showerror("Could not analyze statement", str(exc))
            return

        self._show_summary(self.result)
        self._show_savings(self.result)
        self.notebook.select(self.summary_tab)
        self.status_var.set("Analysis complete. You can edit any transaction and analyze again.")

    def _show_summary(self, result: AnalysisResult) -> None:
        """Fill the total cards and category table."""

        self.card_values["income"].configure(text=format_money(result.income_total))
        self.card_values["spending"].configure(text=format_money(result.spending_total))
        self.card_values["essential"].configure(text=format_money(result.essential_total))
        self.card_values["nonessential"].configure(text=format_money(result.nonessential_total))

        ratio_text = f"{result.spending_to_income_ratio:.2f}%" if result.income_total > 0 else "N/A"
        self.card_values["ratio"].configure(text=ratio_text)

        for item in self.summary_tree.get_children():
            self.summary_tree.delete(item)

        for item in result.categories:
            self.summary_tree.insert(
                "",
                "end",
                values=(
                    item.category,
                    "Yes" if item.essential else "No",
                    format_money(item.total),
                    f"{item.percent_of_income:.2f}%" if result.income_total > 0 else "N/A",
                    f"{item.percent_of_spending:.2f}%" if result.spending_total > 0 else "N/A",
                ),
            )

    def _show_savings(self, result: AnalysisResult) -> None:
        """Create one readable card for each savings prediction."""

        for child in self.savings_container.winfo_children():
            child.destroy()

        if not result.predictions:
            ttk.Label(
                self.savings_container,
                text="No nonessential spending was available for a savings estimate.",
                font=("Segoe UI", 12),
            ).pack(anchor="center", pady=80)
            return

        for rank, prediction in enumerate(result.predictions, start=1):
            card = ttk.LabelFrame(
                self.savings_container,
                text=f"#{rank}  {prediction.category}",
                padding=18,
            )
            card.pack(fill="x", pady=7)

            ttk.Label(
                card,
                text=f"Current monthly spending: {format_money(prediction.current_monthly)}",
                font=("Segoe UI", 11, "bold"),
            ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

            ttk.Label(card, text="Reduce by 25%").grid(row=1, column=0, sticky="w", padx=(0, 60))
            ttk.Label(
                card,
                text=(
                    f"Save {format_money(prediction.save_25_monthly)} per month  •  "
                    f"{format_money(prediction.save_25_yearly)} per year"
                ),
            ).grid(row=2, column=0, sticky="w", padx=(0, 60))

            ttk.Label(card, text="Reduce by 50%").grid(row=1, column=1, sticky="w")
            ttk.Label(
                card,
                text=(
                    f"Save {format_money(prediction.save_50_monthly)} per month  •  "
                    f"{format_money(prediction.save_50_yearly)} per year"
                ),
            ).grid(row=2, column=1, sticky="w")

    def save_report(self) -> None:
        """Save the current analysis as a plain-text file."""

        if not self.result:
            messagebox.showinfo("No analysis", "Analyze a statement before saving a report.")
            return

        default_name = "bank_statement_analysis.txt"
        if self.current_file:
            default_name = f"{self.current_file.stem}_analysis.txt"

        path_text = filedialog.asksaveasfilename(
            title="Save analysis report",
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=(("Text files", "*.txt"),),
        )
        if not path_text:
            return

        try:
            Path(path_text).write_text(build_text_report(self.result), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Could not save report", str(exc))
            return

        self.status_var.set(f"Report saved as {Path(path_text).name}.")

    def clear_statement(self) -> None:
        """Remove the current statement from the screen."""

        if self.transactions and not messagebox.askyesno(
            "Clear statement?",
            "Remove the imported statement and its current analysis from the app?",
        ):
            return

        self.transactions = []
        self.current_file = None
        self.result = None
        self.refresh_transaction_tree()
        self._clear_results()
        self.notebook.select(self.transactions_tab)
        self.status_var.set("Choose a CSV or PDF statement to begin.")

    def clear_merchant_memory(self) -> None:
        """Delete saved merchant choices after confirmation."""

        if not messagebox.askyesno(
            "Clear merchant memory?",
            "This removes every merchant category the app learned from you.",
        ):
            return

        try:
            self.memory.clear()
        except OSError as exc:
            messagebox.showerror("Could not clear memory", str(exc))
            return
        self.status_var.set("Merchant memory was cleared. Built-in rules are unchanged.")

    def _clear_results(self) -> None:
        """Reset summary and savings output."""

        for label in self.card_values.values():
            label.configure(text="—")
        for item in self.summary_tree.get_children():
            self.summary_tree.delete(item)
        for child in self.savings_container.winfo_children():
            child.destroy()
        ttk.Label(
            self.savings_container,
            text="Analyze a statement to see possible monthly and yearly savings.",
            font=("Segoe UI", 12),
        ).pack(anchor="center", pady=80)

    def _unclear_count(self) -> int:
        """Count transactions that still need the user."""

        return sum(transaction.needs_review for transaction in self.transactions)


def main() -> None:
    """Start the desktop application."""

    root = tk.Tk()
    AnalyzerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
