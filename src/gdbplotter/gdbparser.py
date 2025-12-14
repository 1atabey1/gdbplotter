import socket
import struct
import threading
import time
from collections import deque
from typing import Iterable


class MemoryRegion:
    """Represents a memory region with address, format string, and name"""

    def __init__(self, address: int, format_str: str, name: str = ""):
        self.address = address
        self.format_str = format_str
        self.name = name or f"Region_0x{address:X}"

    def get_byte_count(self):
        return struct.calcsize(self.format_str)

    def get_field_count(self):
        return len(struct.unpack(self.format_str, b"\x00" * self.get_byte_count()))

    def decode(self, payload: bytes):
        return struct.unpack(self.format_str, payload)

    def encode(self, data: Iterable):
        return struct.pack(self.format_str, *data)

    def to_dict(self):
        return {
            "address": self.address,
            "format_str": self.format_str,
            "name": self.name,
        }

    @staticmethod
    def from_dict(data):
        return MemoryRegion(
            address=data["address"],
            format_str=data["format_str"],
            name=data.get("name", ""),
        )


class DebugDataPacket:
    def __init__(self, region: MemoryRegion, payload: bytes):
        self.region = region
        self.raw = payload

    def decode(self):
        return self.region.decode(self.raw)


class GdbParser:
    def __init__(self, regions: list[MemoryRegion] = None, host: str = "localhost", port: int = 50000):
        # Initialize parent without serial port
        self.s = None  # No serial connection
        self.rxq: dict[str, deque[DebugDataPacket]] = {}  # Separate queue for each region
        self.is_running = False
        self.rx_t: threading.Thread = None

        # GDB-specific attributes
        self.regions = regions or []
        self.host = host
        self.port = port
        self.gdb_socket = None

        # Initialize queues for each region
        for region in self.regions:
            self.rxq[region.name] = deque(maxlen=100)

    def _connect_gdb(self):
        """Connect to GDB server"""
        try:
            self.gdb_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.gdb_socket.settimeout(10.0)  # Increased timeout
            print(f"Attempting to connect to GDB server on {self.host}:{self.port}")
            self.gdb_socket.connect((self.host, self.port))

            # Reset to blocking mode
            self.gdb_socket.settimeout(None)

            # Test connection with a simple command
            test_response = self._send_gdb_command("?")  # Query target status
            print(f"Test command response: {test_response}")

            return True
        except socket.timeout:
            print(f"Connection timeout - ensure GDB server is running on port {self.port}")
            return False
        except ConnectionRefusedError:
            print(f"Connection refused - GDB server not listening on port {self.port}")
            return False
        except Exception as e:
            print(f"Failed to connect to GDB server: {e}")
            return False

    def _send_gdb_command(self, command: str) -> str:
        """Send a command to GDB and return the response"""
        if not self.gdb_socket:
            return ""

        try:
            # Calculate checksum
            checksum = sum(ord(c) for c in command) & 0xFF
            clen = 3  # including "#"

            # Format: $command#checksum
            full_command = f"${command}#{checksum:02x}"

            self.gdb_socket.send(full_command.encode())

            # Read response
            response = b""
            packet_started = False

            while True:
                data = self.gdb_socket.recv(1024)
                if not data:
                    break
                response += data

                for i, char in enumerate(data):
                    char = bytes([char])
                    if char == b"$":
                        packet_started = True
                        continue
                    elif char == b"+" or char == b"-":
                        # Acknowledgment - continue reading
                        continue
                    elif packet_started and char == b"#":
                        if len(data) >= i + clen:
                            # Nothing to do, we already have all the data
                            pass
                        else:
                            response += self.gdb_socket.recv((i + clen) - len(data))
                        # Send acknowledgment
                        self.gdb_socket.send(b"+")
                        break
                else:
                    # only continue reading if we didnt break out of inner loop
                    continue
                break

            result = response.decode("ascii", errors="ignore")
            return result

        except socket.timeout:
            print("GDB command timeout")
            return ""
        except Exception as e:
            print(f"GDB command failed: {e}")
            return ""

    def _read_memory(self, address: int, length: int) -> bytes:
        """Read memory from GDB server"""
        # GDB memory read command: m<addr>,<length> (note: no space after 'm')
        command = f"m{address:x},{length:x}"  # Use hex for length too
        response = self._send_gdb_command(command)

        # Parse response (should start with $ and contain hex data)
        if response.startswith("+$") and "#" in response:
            hex_data = response[2 : response.index("#")]

            # Check for error response
            if hex_data.startswith("E"):
                error_code = hex_data[1:]
                print(f"GDB memory read error: {error_code}")
                return b""

            try:
                # Convert hex string to bytes
                return bytes.fromhex(hex_data.split("#")[0].lstrip("+$"))
            except ValueError:
                print(f"Invalid hex data in GDB response: {hex_data}")
                return b""

        print(f"Invalid GDB response: {response}")
        return b""

    def start(self):
        """Start the GDB parser thread"""
        if not self._connect_gdb():
            raise ConnectionError("Failed to connect to GDB server")

        def rx():
            while self.is_running:
                self.receive()
                time.sleep(0.001)  # Small delay to prevent excessive polling

        self.rx_t = threading.Thread(target=rx, name="gdb parser rx thread", daemon=True)
        self.is_running = True
        self.rx_t.start()

    def receive(self):
        """Override receive to read from GDB instead of serial"""
        try:
            # Read data from all configured memory regions
            for region in self.regions:
                payload = self._read_memory(region.address, region.get_byte_count())
                if payload and len(payload) == region.get_byte_count():
                    self.rxq[region.name].append(DebugDataPacket(region, payload))

        except Exception as e:
            print(f"Error reading from GDB: {e}")

    def stop(self):
        """Stop the parser and close GDB connection"""
        if self.is_running:
            self.is_running = False
            if self.rx_t:
                self.rx_t.join()

        if self.gdb_socket:
            try:
                self.gdb_socket.close()
            except Exception as e:
                print(f"Error reading from GDB: {e}")
            self.gdb_socket = None

    def get_last(self, region_name: str = None):
        """Get the last packet from a specific region or all regions"""
        if region_name:
            if region_name in self.rxq and len(self.rxq[region_name]) > 0:
                return self.rxq[region_name].pop()
            return None
        else:
            # Return dict with last packet from each region
            result = {}
            for name, queue in self.rxq.items():
                if len(queue) > 0:
                    result[name] = queue.pop()
            return result if result else None

    def add_region(self, region: MemoryRegion):
        """Add a new memory region to monitor"""
        self.regions.append(region)
        self.rxq[region.name] = deque(maxlen=100)

    def remove_region(self, region_name: str):
        """Remove a memory region"""
        self.regions = [r for r in self.regions if r.name != region_name]
        if region_name in self.rxq:
            del self.rxq[region_name]

    def __del__(self):
        self.stop()
