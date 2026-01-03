import struct
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
