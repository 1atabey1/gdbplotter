# gdbplotter

Lightweight Python tool that connects to a running gdb server and visualises data from given memory addresses.

![UI Overview](doc/ui_data_mon.png)

## Features

- Communicate with a GDB server and read out specific memory regions
- Specify decoding rules for each region
- Simple plotting UI for quick inspection of numeric traces

## Installation

Install from pypi

```shell
pip install gdbplotter
```

Install from source into a virtual environment:

```shell
uv sync
```

## Usage

- Run interactively (should be installed into *Scripts* or *bin* of the virtualenv by default):

```
gdbplotter
```

- Run as python module (if previous method doesnt work)

```
python -m gdbplotter
```

- Or import in your own scripts:

```py
from gdbplotter import gdbparser

# parse gdb output and plot
```

### Specifying your memory regions

![Memory regions](doc/ui_mem_regions.png)

gdbplotter parses its signals based on the base addresses and format strings that you define in the *Memory Regions* tab. You can't run the tool without specifying at least one memory region first.

You can find the base address of your variables by simply starting a gdb instance and with your debug symbols loaded:

```
gdb
>file main.elf
>target remote localhost:50000
>print &variable
```

The format string structure for decoding the fields of your data region can be looked up in the [Python documentation](https://docs.python.org/3/library/struct.html#format-characters)

## Configuration

Upon first run of the GUI, the tool will create a file called `gdbplotter_config.json` in the working directory — edit this to change default behavior (input paths, plotting options, etc.).

## Tests

There is a small test helper in `test/gdbservermock.py` for development. Run tests or examples manually as needed.

## Note

Be aware that this only really makes sense on target architectures that allow reading the memory while the target is running (e.g. "Cortex Live Watch"). Tested with various STM32 processors and regular STLinks.
