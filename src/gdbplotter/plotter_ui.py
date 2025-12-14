import csv
import json
import os
import struct
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from gdbplotter.gdbparser import GdbParser, MemoryRegion

PERIOD_S = 0.002  # max. 500Hz update rate
CONFIG_FILE = "gdbplotter_config.json"


class DebugDataUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Debug Data Monitor - Multi-Region")
        self.root.geometry("900x750")

        # Memory regions configuration
        self.regions = []  # List of MemoryRegion objects

        # Dynamic signal labels (populated based on all regions)
        self.signal_labels = []
        self.signal_names = {}  # Maps (region_name, signal_index) to custom name

        self.parser = None
        self.update_thread = None
        self.is_running = False

        # Plot data storage - keyed by (region_name, signal_index)
        self.max_plot_points = 1000
        self.plot_data = {}  # Will be populated dynamically
        self.time_data = deque(maxlen=self.max_plot_points)
        self.plot_start_time = time.time()

        # Plot selection variables
        self.plot_vars = []  # Will be populated dynamically
        self.plot_colors = None  # Will be set when regions are configured

        # CSV logging variables
        self.is_logging = False
        self.csv_file = None
        self.csv_writer = None
        self.log_start_time = None

        # UI elements that need dynamic updates
        self.value_labels = []
        self.measurement_labels = []
        self.data_frame = None
        self.scrollable_frame = None
        self.canvas = None

        self.packet_count = 0
        self.last_update_time = time.time()

        self.setup_ui()

        # Load configuration after UI is set up
        self.load_config()

    def load_config(self):
        """Load configuration from file if it exists"""
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)

                # Load memory regions
                if "regions" in config:
                    self.regions = [MemoryRegion.from_dict(r) for r in config["regions"]]
                    self.update_regions_list()  # Update the regions list UI
                    self.rebuild_signal_list()

                # Load signal names (now keyed by (region_name, signal_index))
                if "signal_names" in config:
                    # Convert from JSON format back to tuple keys
                    self.signal_names = {}
                    for key_str, name in config["signal_names"].items():
                        # Parse key like "Region_0x24000478:0"
                        if ":" in key_str:
                            region_name, idx_str = key_str.split(":", 1)
                            self.signal_names[(region_name, int(idx_str))] = name
                    self.update_all_displays()

                # Load GDB settings
                if "gdb_port" in config:
                    self.gdb_port_var.set(config["gdb_port"])
                if "gdb_host" in config:
                    self.gdb_host_var.set(config["gdb_host"])

                print(f"Configuration loaded from {CONFIG_FILE}")
        except Exception as e:
            print(f"Error loading configuration: {e}")

    def save_config(self):
        """Save current configuration to file"""
        try:
            # Convert signal names keys to strings for JSON
            signal_names_json = {}
            for (region_name, idx), name in self.signal_names.items():
                signal_names_json[f"{region_name}:{idx}"] = name

            config = {
                "regions": [r.to_dict() for r in self.regions],
                "signal_names": signal_names_json,
                "gdb_port": self.gdb_port_var.get(),
                "gdb_host": self.gdb_host_var.get(),
            }

            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)

            print(f"Configuration saved to {CONFIG_FILE}")
        except Exception as e:
            print(f"Error saving configuration: {e}")

    def setup_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=5)

        regions_tab = ttk.Frame(notebook)
        notebook.add(regions_tab, text="Memory Regions")

        data_tab = ttk.Frame(notebook)
        notebook.add(data_tab, text="Data Monitor")

        plot_tab = ttk.Frame(notebook)
        notebook.add(plot_tab, text="Plots")

        config_tab = ttk.Frame(notebook)
        notebook.add(config_tab, text="Signal Names")

        self.setup_regions_tab(regions_tab)
        self.setup_data_tab(data_tab)
        self.setup_plot_tab(plot_tab)
        self.setup_config_tab(config_tab)

    def setup_regions_tab(self, parent):
        """Setup the memory regions configuration tab"""
        # Instructions
        inst_frame = ttk.LabelFrame(parent, text="Instructions", padding=10)
        inst_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(
            inst_frame,
            text="Add memory regions to monitor. Each region has an address, format string, and name.",
            wraplength=800,
        ).pack()

        add_frame = ttk.LabelFrame(parent, text="Add New Region", padding=10)
        add_frame.pack(fill="x", padx=10, pady=5)

        name_frame = ttk.Frame(add_frame)
        name_frame.pack(fill="x", pady=2)
        ttk.Label(name_frame, text="Name:", width=15).pack(side="left")
        self.new_region_name_var = tk.StringVar()
        ttk.Entry(name_frame, textvariable=self.new_region_name_var, width=30).pack(side="left", padx=5)

        addr_frame = ttk.Frame(add_frame)
        addr_frame.pack(fill="x", pady=2)
        ttk.Label(addr_frame, text="Address (hex):", width=15).pack(side="left")
        self.new_region_addr_var = tk.StringVar(value="0x24000478")
        ttk.Entry(addr_frame, textvariable=self.new_region_addr_var, width=30).pack(side="left", padx=5)

        fmt_frame = ttk.Frame(add_frame)
        fmt_frame.pack(fill="x", pady=2)
        ttk.Label(fmt_frame, text="Format String:", width=15).pack(side="left")
        self.new_region_fmt_var = tk.StringVar(value="<I8f")
        ttk.Entry(fmt_frame, textvariable=self.new_region_fmt_var, width=30).pack(side="left", padx=5)
        ttk.Label(fmt_frame, text="e.g., <I8f (uint32 + 8 floats)", font=("Courier", 9)).pack(side="left", padx=5)

        btn_frame = ttk.Frame(add_frame)
        btn_frame.pack(fill="x", pady=10)
        ttk.Button(btn_frame, text="Add Region", command=self.add_new_region).pack(side="left")

        list_frame = ttk.LabelFrame(parent, text="Configured Regions", padding=10)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)

        canvas = tk.Canvas(list_frame)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self.regions_list_frame = ttk.Frame(canvas)

        self.regions_list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        canvas.create_window((0, 0), window=self.regions_list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.update_regions_list()

    def add_new_region(self):
        """Add a new memory region"""
        try:
            name = self.new_region_name_var.get().strip()
            addr_str = self.new_region_addr_var.get().strip()
            fmt_str = self.new_region_fmt_var.get().strip()

            if not name:
                name = addr_str

            # Parse address
            if addr_str.startswith("0x") or addr_str.startswith("0X"):
                address = int(addr_str, 16)
            else:
                address = int(addr_str)

            # Validate format string
            try:
                struct.calcsize(fmt_str)
            except struct.error as e:
                messagebox.showerror("Invalid Format", f"Invalid format string: {e}")
                return

            # Check for duplicate names
            if any(r.name == name for r in self.regions):
                messagebox.showerror("Duplicate Name", f"Region '{name}' already exists")
                return

            # Create and add region
            region = MemoryRegion(address, fmt_str, name)
            self.regions.append(region)

            # Update UI
            self.update_regions_list()
            self.rebuild_signal_list()
            self.save_config()

            # Clear inputs
            self.new_region_name_var.set("")
            self.new_region_addr_var.set("0x24000478")
            self.new_region_fmt_var.set("<I8f")

            messagebox.showinfo("Success", f"Region '{name}' added successfully")

        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Invalid address format: {e}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to add region: {e}")

    def remove_region(self, region_name: str):
        """Remove a memory region"""
        self.regions = [r for r in self.regions if r.name != region_name]

        # Remove associated signal names
        keys_to_remove = [k for k in self.signal_names.keys() if k[0] == region_name]
        for key in keys_to_remove:
            del self.signal_names[key]

        self.update_regions_list()
        self.rebuild_signal_list()
        self.save_config()

    def update_regions_list(self):
        """Update the regions list display"""
        # Clear existing widgets
        for widget in self.regions_list_frame.winfo_children():
            widget.destroy()

        if not self.regions:
            ttk.Label(self.regions_list_frame, text="No regions configured", foreground="gray").pack(pady=20)
            return

        # Display each region
        for i, region in enumerate(self.regions):
            region_frame = ttk.Frame(self.regions_list_frame, relief="ridge", borderwidth=2)
            region_frame.pack(fill="x", padx=5, pady=5)

            # Header with name and remove button
            header_frame = ttk.Frame(region_frame)
            header_frame.pack(fill="x", padx=5, pady=5)

            ttk.Label(
                header_frame,
                text=f"Region: {region.name}",
                font=("TkDefaultFont", 10, "bold"),
            ).pack(side="left")

            ttk.Button(
                header_frame,
                text="Remove",
                command=lambda n=region.name: self.remove_region(n),
                width=10,
            ).pack(side="right")

            # Details
            details_frame = ttk.Frame(region_frame)
            details_frame.pack(fill="x", padx=5, pady=5)

            ttk.Label(details_frame, text=f"Address: 0x{region.address:X}").pack(anchor="w")
            ttk.Label(details_frame, text=f"Format: {region.format_str}").pack(anchor="w")
            ttk.Label(
                details_frame,
                text=f"Size: {region.get_byte_count()} bytes, " f"{region.get_field_count()} fields",
            ).pack(anchor="w")

    def rebuild_signal_list(self):
        """Rebuild the complete signal list from all regions"""
        self.measurement_labels = []
        self.plot_data = {}
        self.plot_vars = []

        signal_count = 0
        for region in self.regions:
            for i in range(region.get_field_count()):
                key = (region.name, i)
                label = self.signal_names.get(key, f"{region.name}[{i}]")
                self.measurement_labels.append(label)
                self.plot_data[signal_count] = deque(maxlen=self.max_plot_points)
                self.plot_vars.append(tk.BooleanVar())
                signal_count += 1

        # Update plot colors
        if signal_count > 0:
            self.plot_colors = plt.cm.tab20(np.linspace(0, 1, max(signal_count, 20)))

        # Refresh UI
        self.recreate_measurement_display()
        self.recreate_plot_checkboxes()
        self.create_signal_config_entries()

    def setup_data_tab(self, parent):
        # Connection frame
        conn_frame = ttk.LabelFrame(parent, text="Connection", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)

        # GDB-specific controls frame
        self.gdb_frame = ttk.LabelFrame(conn_frame, text="GDB Settings", padding=10)
        self.gdb_frame.pack(fill="x", pady=(0, 10))

        gdb_control_frame = ttk.Frame(self.gdb_frame)
        gdb_control_frame.pack(fill="x")

        ttk.Label(gdb_control_frame, text="GDB Port:").grid(row=0, column=0, sticky="w")
        self.gdb_port_var = tk.StringVar(value="50000")
        self.gdb_port_var.trace_add("write", lambda *args: self.save_config())
        ttk.Entry(gdb_control_frame, textvariable=self.gdb_port_var, width=8).grid(row=0, column=1, padx=5)

        ttk.Label(gdb_control_frame, text="GDB Host:").grid(row=1, column=0, sticky="w")
        self.gdb_host_var = tk.StringVar(value="localhost")
        self.gdb_host_var.trace_add("write", lambda *args: self.save_config())
        ttk.Entry(gdb_control_frame, textvariable=self.gdb_host_var, width=15).grid(row=1, column=1, padx=5)

        # Connection buttons frame
        button_frame = ttk.Frame(conn_frame)
        button_frame.pack(fill="x", pady=(10, 0))

        self.connect_btn = ttk.Button(button_frame, text="Connect", command=self.connect)
        self.connect_btn.pack(side="left", padx=(0, 5))

        self.disconnect_btn = ttk.Button(button_frame, text="Disconnect", command=self.disconnect, state="disabled")
        self.disconnect_btn.pack(side="left", padx=5)

        # Status label
        self.status_label = ttk.Label(conn_frame, text="Status: Disconnected", foreground="red")
        self.status_label.pack(pady=(10, 0))

        # Data display frame
        self.data_frame = ttk.LabelFrame(parent, text="Measurements", padding=10)
        self.data_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Create scrollable frame
        self.canvas = tk.Canvas(self.data_frame)
        scrollbar = ttk.Scrollbar(self.data_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        # Bind mousewheel to canvas
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Stats frame
        stats_frame = ttk.LabelFrame(parent, text="Statistics", padding=10)
        stats_frame.pack(fill="x", padx=10, pady=5)

        # Left side - stats labels
        stats_left = ttk.Frame(stats_frame)
        stats_left.pack(side="left", fill="x", expand=True)

        self.packets_label = ttk.Label(stats_left, text="Packets received: 0")
        self.packets_label.pack(side="left")

        self.update_rate_label = ttk.Label(stats_left, text="Update rate: -- Hz")
        self.update_rate_label.pack(side="left", padx=(20, 0))

        # Right side - logging controls
        logging_frame = ttk.Frame(stats_frame)
        logging_frame.pack(side="right")

        self.log_btn = ttk.Button(logging_frame, text="Start Logging", command=self.toggle_logging)
        self.log_btn.pack(side="right", padx=(0, 10))

        self.log_status_label = ttk.Label(logging_frame, text="Not logging", foreground="red")
        self.log_status_label.pack(side="right")

        self.packet_count = 0
        self.last_update_time = time.time()

    def recreate_measurement_display(self):
        """Recreate the measurement display widgets in the data tab"""
        # Clear existing widgets
        if hasattr(self, "scrollable_frame"):
            for widget in self.scrollable_frame.winfo_children():
                widget.destroy()

        self.value_labels = []

        # Create measurement display widgets
        for i, label in enumerate(self.measurement_labels):
            row_frame = ttk.Frame(self.scrollable_frame)
            row_frame.pack(fill="x", pady=2)

            ttk.Label(row_frame, text=f"{i}: {label}", width=25, anchor="w").pack(side="left")
            value_label = ttk.Label(row_frame, text="--", width=50, anchor="e", font=("Courier", 10))
            value_label.pack(side="right")
            self.value_labels.append(value_label)

    def recreate_plot_checkboxes(self):
        """Recreate the plot checkboxes in the plot tab"""
        # Find the checkbox frame in the plot tab and clear it
        if hasattr(self, "checkbox_frame"):
            for widget in self.checkbox_frame.winfo_children():
                widget.destroy()

            cols = 3
            for i, label in enumerate(self.measurement_labels):
                row = i // cols
                col = i % cols

                cb = ttk.Checkbutton(
                    self.checkbox_frame,
                    text=f"{i}: {label}",
                    variable=self.plot_vars[i],
                    command=self.update_plot,
                )
                cb.grid(row=row, column=col, sticky="w", padx=10, pady=2)

    def get_signal_display_name(self, index: int) -> str:
        """Get the display name for a signal (custom name or default)"""
        if index < len(self.measurement_labels):
            return self.measurement_labels[index]
        return f"Signal {index}"

    def setup_plot_tab(self, parent):
        # Plot controls frame
        controls_frame = ttk.LabelFrame(parent, text="Plot Controls", padding=10)
        controls_frame.pack(fill="x", padx=10, pady=5)

        # Selection frame
        selection_frame = ttk.Frame(controls_frame)
        selection_frame.pack(fill="x")

        # Create checkboxes for plot selection in a grid
        self.checkbox_frame = ttk.Frame(selection_frame)
        self.checkbox_frame.pack(side="left", fill="both", expand=True)

        # Plot control buttons
        button_frame = ttk.Frame(selection_frame)
        button_frame.pack(side="right", padx=10)

        ttk.Button(button_frame, text="Select All", command=self.select_all_plots).pack(pady=2)
        ttk.Button(button_frame, text="Clear All", command=self.clear_all_plots).pack(pady=2)
        ttk.Button(button_frame, text="Clear Data", command=self.clear_plot_data).pack(pady=2)

        # Plot frame
        plot_frame = ttk.LabelFrame(parent, text="Live Plot", padding=10)
        plot_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Create matplotlib figure
        self.fig = Figure(figsize=(10, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Value")
        self.ax.grid(True, alpha=0.3)
        self.ax.set_title("Real-time Measurements")

        # Create canvas
        self.plot_canvas = FigureCanvasTkAgg(self.fig, plot_frame)
        self.plot_canvas.draw()
        self.plot_canvas.get_tk_widget().pack(fill="both", expand=True)

        # Initialize empty plot lines
        self.plot_lines = {}

    def setup_config_tab(self, parent):
        """Setup the signal configuration tab for renaming signals"""
        config_frame = ttk.LabelFrame(parent, text="Signal Names", padding=10)
        config_frame.pack(fill="both", expand=True, padx=10, pady=5)

        # Create scrollable frame for signal entries
        canvas = tk.Canvas(config_frame)
        scrollbar = ttk.Scrollbar(config_frame, orient="vertical", command=canvas.yview)
        self.config_scrollable_frame = ttk.Frame(canvas)

        self.config_scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        canvas.create_window((0, 0), window=self.config_scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Bind mousewheel
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.signal_entry_widgets = []
        self.create_signal_config_entries()

    def create_signal_config_entries(self):
        """Create entry widgets for signal renaming"""
        # Clear existing widgets
        for widget in self.config_scrollable_frame.winfo_children():
            widget.destroy()

        self.signal_entry_widgets = []

        if not self.regions:
            ttk.Label(
                self.config_scrollable_frame,
                text="No regions configured. Add regions in the Memory Regions tab.",
                foreground="gray",
            ).pack(pady=20)
            return

        signal_idx = 0
        for region in self.regions:
            # Region header
            region_header = ttk.LabelFrame(self.config_scrollable_frame, text=f"Region: {region.name}", padding=5)
            region_header.pack(fill="x", pady=10, padx=5)

            for i in range(region.get_field_count()):
                key = (region.name, i)
                row_frame = ttk.Frame(region_header)
                row_frame.pack(fill="x", pady=2, padx=5)

                # Signal index label
                ttk.Label(row_frame, text=f"[{i}]:", width=8, anchor="w").pack(side="left", padx=5)

                # Entry field
                default_name = f"{region.name}[{i}]"
                entry_var = tk.StringVar(value=self.signal_names.get(key, default_name))
                entry = ttk.Entry(row_frame, textvariable=entry_var, width=40)
                entry.pack(side="left", padx=5, fill="x", expand=True)

                # Reset button
                def make_reset_callback(k):
                    return lambda: self.reset_signal_name(k)

                ttk.Button(row_frame, text="Reset", width=8, command=make_reset_callback(key)).pack(side="left", padx=2)

                # Store reference with a callback to update on focus out
                def make_update_callback(k, var, default):
                    def update_signal():
                        new_name = var.get().strip()
                        if new_name and new_name != default:
                            self.signal_names[k] = new_name
                            self.rebuild_signal_list()
                            self.save_config()
                        elif not new_name:
                            var.set(self.signal_names.get(k, default))

                    return update_signal

                entry.bind(
                    "<FocusOut>",
                    lambda e, k=key, var=entry_var, d=default_name: make_update_callback(k, var, d)(),
                )
                entry.bind(
                    "<Return>",
                    lambda e, k=key, var=entry_var, d=default_name: make_update_callback(k, var, d)(),
                )

                self.signal_entry_widgets.append(entry_var)
                signal_idx += 1

    def reset_signal_name(self, key):
        """Reset a signal name to its default"""
        self.signal_names.pop(key, None)
        self.rebuild_signal_list()
        self.save_config()

    def update_all_displays(self):
        """Update all UI elements to reflect signal name changes"""
        self.rebuild_signal_list()

    def log_message(self, message):
        """Add a message to the command log"""
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)  # Auto-scroll to bottom

    def clear_log(self):
        """Clear the command log"""
        self.log_text.delete(1.0, tk.END)
        self.log_message("Log cleared")

    def toggle_logging(self):
        """Toggle CSV logging on/off"""
        if self.is_logging:
            self.stop_logging()
        else:
            self.start_logging()

    def start_logging(self):
        """Start CSV logging"""
        try:
            # Get filename from user
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            default_filename = f"measurement_log_{timestamp}.csv"

            filename = filedialog.asksaveasfilename(
                title="Save CSV Log File",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile=default_filename,
            )

            if not filename:
                return  # User cancelled

            # Open CSV file for writing
            self.csv_file = open(filename, "w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)

            # Write header row
            header = ["Timestamp", "Relative_Time_s"] + [
                f"{i}_{self.get_signal_display_name(i).replace(' ', '_')}" for i in range(len(self.measurement_labels))
            ]
            self.csv_writer.writerow(header)

            self.is_logging = True
            self.log_start_time = time.time()

            # Update UI
            self.log_btn.config(text="Stop Logging")
            self.log_status_label.config(text=f"Logging to: {filename.split('/')[-1]}", foreground="green")

            print(f"Started logging to: {filename}")

        except Exception as e:
            messagebox.showerror("Logging Error", f"Failed to start logging: {str(e)}")
            self.stop_logging()

    def stop_logging(self):
        """Stop CSV logging"""
        try:
            self.is_logging = False

            if self.csv_file:
                self.csv_file.close()
                self.csv_file = None
                self.csv_writer = None

            # Update UI
            self.log_btn.config(text="Start Logging")
            self.log_status_label.config(text="Not logging", foreground="red")

            print("Stopped logging")

        except Exception as e:
            print(f"Error stopping logging: {e}")

    def log_csv_data(self, values):
        """Log data to CSV file if logging is active"""
        if self.is_logging and self.csv_writer:
            try:
                current_time = time.time()
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # Include milliseconds
                relative_time = current_time - self.log_start_time

                # Create row with timestamp, relative time, and all measurement values
                row = [timestamp, f"{relative_time:.3f}"] + [f"{value:.6f}" for value in values]
                self.csv_writer.writerow(row)

                # Flush to ensure data is written immediately
                self.csv_file.flush()

            except Exception as e:
                print(f"Error writing to CSV: {e}")
                self.stop_logging()

    def select_all_plots(self):
        for var in self.plot_vars:
            var.set(True)
        self.update_plot()

    def clear_all_plots(self):
        for var in self.plot_vars:
            var.set(False)
        self.update_plot()

    def clear_plot_data(self):
        for i in range(len(self.measurement_labels)):
            self.plot_data[i].clear()
        self.time_data.clear()
        self.plot_start_time = time.time()
        self.update_plot()

    def update_plot(self):
        # Clear existing lines
        self.ax.clear()
        self.ax.set_xlabel("Time (s)")
        self.ax.set_ylabel("Value")
        self.ax.grid(True, alpha=0.3)
        self.ax.set_title("Real-time Measurements")

        # Plot selected measurements
        legend_labels = []
        if len(self.time_data) > 0:
            time_array = np.array(self.time_data)

            for i, var in enumerate(self.plot_vars):
                if var.get() and len(self.plot_data[i]) > 0:
                    data_array = np.array(self.plot_data[i])
                    display_name = self.get_signal_display_name(i)
                    _ = self.ax.plot(
                        time_array[-len(data_array) :],
                        data_array,
                        color=self.plot_colors[i],
                        linewidth=1.5,
                        label=f"{i}: {display_name}",
                    )[0]
                    legend_labels.append(f"{i}: {display_name}")

            if legend_labels:
                self.ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=8)

        self.fig.tight_layout()
        self.plot_canvas.draw()

    def connect(self):
        try:
            if not self.regions:
                messagebox.showerror(
                    "No Regions",
                    "Please configure at least one memory region before connecting.",
                )
                return

            # GDB connection
            gdb_host = self.gdb_host_var.get()
            gdb_port = int(self.gdb_port_var.get())

            self.parser = GdbParser(regions=self.regions, port=gdb_port, host=gdb_host)
            connection_info = f"GDB {gdb_host}:{gdb_port}"

            # Start the parser
            self.parser.start()

            self.is_running = True
            self.update_thread = threading.Thread(target=self.update_data, daemon=True)
            self.update_thread.start()

            # Reset plot timing
            self.plot_start_time = time.time()

            self.connect_btn.config(state="disabled")
            self.disconnect_btn.config(state="normal")

            self.status_label.config(text=f"Status: Connected to {connection_info}", foreground="green")

        except Exception as e:
            messagebox.showerror("Connection Error", f"Failed to connect: {str(e)}")

    def disconnect(self):
        self.is_running = False

        # Stop logging if active
        if self.is_logging:
            self.stop_logging()

        if self.parser:
            self.parser.stop()
            self.parser = None

        self.connect_btn.config(state="normal")
        self.disconnect_btn.config(state="disabled")

        self.status_label.config(text="Status: Disconnected", foreground="red")

        # Clear display
        for label in self.value_labels:
            label.config(text="--")

    def update_data(self):
        last_plot_update = time.time()
        plot_update_interval = 0.1  # Update plot every 100ms

        while self.is_running:
            try:
                if self.parser:
                    packets_dict = self.parser.get_last()
                    if packets_dict:
                        # Collect all values from all regions in order
                        all_values = []
                        for region in self.regions:
                            if region.name in packets_dict:
                                packet = packets_dict[region.name]
                                values = packet.decode()
                                all_values.extend(values)

                        if all_values:
                            self.packet_count += 1
                            current_time = time.time()

                            # Store data for plotting
                            relative_time = current_time - self.plot_start_time
                            self.time_data.append(relative_time)

                            for i, value in enumerate(all_values):
                                if i < len(self.plot_data):
                                    self.plot_data[i].append(value)

                            # Log to CSV if logging is active
                            self.log_csv_data(all_values)

                            # Update UI in main thread
                            self.root.after(0, self.update_display, all_values)

                            # Update plot periodically
                            if current_time - last_plot_update >= plot_update_interval:
                                self.root.after(0, self.update_plot)
                                last_plot_update = current_time

                            # Calculate update rate
                            if current_time - self.last_update_time >= 1.0:
                                rate = self.packet_count / (current_time - self.last_update_time)
                                self.root.after(0, self.update_stats, rate, self.packet_count)
                                self.last_update_time = current_time
                                self.packet_count = 0

                time.sleep(PERIOD_S)

            except Exception as e:
                print(f"Update error: {e}")
                continue

    def update_display(self, values):
        for i, value in enumerate(values):
            if i < len(self.value_labels):
                # Format different types of measurements appropriately
                if "Temp" in self.measurement_labels[i]:
                    formatted_value = f"{value:.1f} °C"
                elif "Voltage" in self.measurement_labels[i] or "Sens" in self.measurement_labels[i]:
                    formatted_value = f"{value:.5f} V"
                elif "Current" in self.measurement_labels[i]:
                    formatted_value = f"{value:.5f} A"
                else:
                    formatted_value = f"{value:.5f}"
            try:
                self.value_labels[i].config(text=formatted_value)
            except tk.TclError:
                pass

    def update_stats(self, rate, pack_count):
        self.packets_label.config(text=f"Packets received: {pack_count}")
        self.update_rate_label.config(text=f"Update rate: {rate:.1f} Hz")

    def run(self):
        try:
            self.root.mainloop()
        finally:
            try:
                # Save configuration before closing
                self.save_config()
                # Stop logging before disconnecting
                if self.is_logging:
                    self.stop_logging()
                self.disconnect()
            except tk.TclError:
                pass
            except Exception as e:
                print(f"Error while closing: {e}")


def main():
    app = DebugDataUI()
    app.run()

if __name__ == "__main__":
    main()
