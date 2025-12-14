# gdbplotter

Lightweight Python tool that connects to a running gdb server and visualises data from given memory addresses. Be aware that this only really makes sense on target architectures that allow reading the memory while the target is running (e.g. "Cortex Live Watch"). Tested with various STM32 processors and regular STLinks.

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

## Configuration

Upon first run of the GUI, the tool will create a file called `gdbplotter_config.json` in the working directory — edit this to change default behavior (input paths, plotting options, etc.).

## Tests

There is a small test helper in `test/gdbservermock.py` for development. Run tests or examples manually as needed.
