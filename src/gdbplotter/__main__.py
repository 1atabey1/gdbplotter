import argparse

from gdbplotter.plotter_ui import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GDB Plotter - Real-time data visualization for embedded systems")
    parser.add_argument(
        "--arm-trace",
        action="store_true",
        default=False,
        help="Use ARM Cortex-M trace capabilities for high-performance data acquisition (default: False)",
    )
    args = parser.parse_args()
    main(use_trace=args.arm_trace)
