import struct
from gdbplotter.gdbparser import GdbParser
from gdbplotter.datastructures import DebugDataPacket, MemoryRegion


class TraceParser(GdbParser):
    """
    High-performance data acquisition using ARM Cortex-M trace capabilities.
    
    Instead of polling memory regions via GDB commands, this parser configures
    the DWT (Data Watchpoint and Trace) and ETB (Embedded Trace Buffer) to 
    automatically capture data when memory locations are accessed, providing
    significantly higher performance for rapidly changing data.
    """
    
    # CoreSight ETB (Embedded Trace Buffer) registers
    ETB_RDP = 0xE0042004      # RAM Data Port
    ETB_STS = 0xE0042008      # Status Register
    ETB_RRP = 0xE004200C      # RAM Read Pointer
    ETB_RWP = 0xE0042010      # RAM Write Pointer
    ETB_TRG = 0xE0042014      # Trigger Counter
    ETB_CTL = 0xE0042020      # Control Register
    ETB_FFCR = 0xE0042304     # Formatter and Flush Control Register
    
    # DWT (Data Watchpoint and Trace) registers
    DWT_CTRL = 0xE0001000     # Control Register
    DWT_COMP0 = 0xE0001020    # Comparator 0
    DWT_MASK0 = 0xE0001024    # Mask 0
    DWT_FUNCTION0 = 0xE0001028  # Function 0
    
    # TPIU (Trace Port Interface Unit) registers
    TPIU_CSPSR = 0xE0040004   # Current Parallel Port Size
    TPIU_ACPR = 0xE0040010    # Async Clock Prescaler
    TPIU_SPPR = 0xE00400F0    # Selected Pin Protocol
    TPIU_FFCR = 0xE0040304    # Formatter and Flush Control
    
    # ITM (Instrumentation Trace Macrocell) registers  
    ITM_TCR = 0xE0000E80      # Trace Control Register
    ITM_TER = 0xE0000E00      # Trace Enable Register
    
    # DWT Function values
    FUNC_DISABLED = 0x0
    FUNC_DATA_ADDR_COMPARE = 0x4    # Generate trace on address match
    FUNC_DATA_VALUE_RD = 0x5        # Trace data value on read
    FUNC_DATA_VALUE_WR = 0x6        # Trace data value on write  
    FUNC_DATA_VALUE_RW = 0x7        # Trace data value on read or write
    
    def __init__(self, regions: list[MemoryRegion] = None, host: str = "localhost", port: int = 50000):
        super().__init__(regions, host, port)
        self.trace_configured = False
        self.comparator_map = {}  # Maps comparator index to region name
        
    def _write_memory(self, address: int, data: bytes) -> bool:
        """Write memory via GDB"""
        hex_data = data.hex()
        length = len(data)
        command = f"M{address:x},{length:x}:{hex_data}"
        response = self._send_gdb_command(command)
        return "OK" in response
    
    def _write_register(self, address: int, value: int) -> bool:
        """Write a 32-bit register"""
        data = struct.pack('<I', value)
        return self._write_memory(address, data)
    
    def _read_register(self, address: int) -> int:
        """Read a 32-bit register"""
        data = self._read_memory(address, 4)
        if data and len(data) == 4:
            return struct.unpack('<I', data)[0]
        return 0
    
    def _configure_trace_infrastructure(self) -> bool:
        """Configure the ARM CoreSight trace infrastructure"""
        try:
            # Enable DWT
            ctrl = self._read_register(self.DWT_CTRL)
            ctrl |= 0x1  # Enable cycle counter
            self._write_register(self.DWT_CTRL, ctrl)
            
            # Configure ITM for trace output
            self._write_register(self.ITM_TCR, 0x00010005)  # Enable ITM
            self._write_register(self.ITM_TER, 0xFFFFFFFF)  # Enable all stimulus ports
            
            # Configure TPIU for SWO output
            self._write_register(self.TPIU_SPPR, 0x00000002)  # Use NRZ/UART encoding
            self._write_register(self.TPIU_ACPR, 0)            # Prescaler
            self._write_register(self.TPIU_FFCR, 0x00000100)  # Continuous formatting
            
            # Configure ETB as trace sink
            self._write_register(self.ETB_CTL, 0x00000000)   # Disable ETB
            self._write_register(self.ETB_FFCR, 0x00000000)  # Clear formatter
            self._write_register(self.ETB_RWP, 0)             # Reset write pointer
            self._write_register(self.ETB_RRP, 0)             # Reset read pointer
            self._write_register(self.ETB_CTL, 0x00000001)   # Enable ETB
            
            print("Trace infrastructure configured")
            return True
        except Exception as e:
            print(f"Failed to configure trace: {e}")
            return False
    
    def _configure_dwt_comparator(self, comp_idx: int, region: MemoryRegion) -> bool:
        """Configure a DWT comparator for a memory region"""
        comp_base = self.DWT_COMP0 + (comp_idx * 0x10)
        mask_base = self.DWT_MASK0 + (comp_idx * 0x10)
        func_base = self.DWT_FUNCTION0 + (comp_idx * 0x10)
        
        # Set address to watch
        self._write_register(comp_base, region.address)
        
        # Set mask (0 = exact match, higher values create address ranges)
        # For multi-byte regions, calculate appropriate mask
        byte_count = region.get_byte_count()
        mask = 0
        if byte_count > 1:
            # Calculate mask to cover the region size
            import math
            mask = max(0, int(math.log2(byte_count)) - 1) if byte_count > 1 else 0
        
        self._write_register(mask_base, mask)
        
        # Set function - trace on read/write with data value
        self._write_register(func_base, self.FUNC_DATA_VALUE_RW)
        
        self.comparator_map[comp_idx] = region.name
        print(f"DWT comparator {comp_idx} configured for {region.name} at 0x{region.address:08X}")
        return True
    
    def _read_etb_buffer(self) -> list[bytes]:
        """Read available data from the ETB trace buffer"""
        packets = []
        
        try:
            # Check if data is available
            sts = self._read_register(self.ETB_STS)
            if not (sts & 0x1):  # Check if trigger occurred
                return packets
            
            # Get read and write pointers
            rrp = self._read_register(self.ETB_RRP)
            rwp = self._read_register(self.ETB_RWP)
            
            # Read data from buffer
            while rrp != rwp:
                word = self._read_register(self.ETB_RDP)
                packets.append(struct.pack('<I', word))
                rrp = self._read_register(self.ETB_RRP)  # Auto-increments
                
        except Exception as e:
            print(f"Error reading ETB: {e}")
        
        return packets
    
    def _parse_trace_packets(self, raw_packets: list[bytes]) -> dict:
        """Parse trace packets and extract data for each region"""
        region_data = {}
        
        # Simple parsing - in real implementation would decode ITM/DWT protocol
        # For now, we attempt to match packet data to configured regions
        for packet in raw_packets:
            if len(packet) < 4:
                continue
                
            # Try to match packet to regions based on size
            for region in self.regions:
                expected_size = region.get_byte_count()
                if len(packet) >= expected_size:
                    # Extract data matching region size
                    payload = packet[:expected_size]
                    if region.name not in region_data:
                        region_data[region.name] = []
                    region_data[region.name].append(payload)
        
        return region_data
    
    def start(self):
        """Start the trace parser with hardware trace configuration"""
        if not self._connect_gdb():
            raise ConnectionError("Failed to connect to GDB server")
        
        # Configure trace hardware
        if not self._configure_trace_infrastructure():
            print("Warning: Trace infrastructure configuration failed")
            print("Falling back to polling mode")
        else:
            # Configure DWT comparators for each region
            for idx, region in enumerate(self.regions):
                if idx >= 4:  # Most Cortex-M have 4 comparators
                    print(f"Warning: Only 4 DWT comparators available, region {region.name} will be polled")
                    break
                self._configure_dwt_comparator(idx, region)
            
            self.trace_configured = True
        
        # Start the receive thread
        import threading
        import time
        
        def rx():
            while self.is_running:
                self.receive()
                time.sleep(0.001)
        
        self.rx_t = threading.Thread(target=rx, name="trace parser rx thread", daemon=True)
        self.is_running = True
        self.rx_t.start()
    
    def receive(self):
        """
        Override receive to use trace-based acquisition.
        
        Falls back to standard polling if trace is not configured.
        """
        if not self.trace_configured:
            # Fallback to standard GDB memory polling
            super().receive()
            return
        
        try:
            # Read trace packets from ETB
            raw_packets = self._read_etb_buffer()
            
            if raw_packets:
                # Parse packets and match to regions
                region_data = self._parse_trace_packets(raw_packets)
                
                # Add parsed data to queues
                for region_name, payloads in region_data.items():
                    for payload in payloads:
                        region = next((r for r in self.regions if r.name == region_name), None)
                        if region and len(payload) == region.get_byte_count():
                            self.rxq[region_name].append(DebugDataPacket(region, payload))
            else:
                # If no trace data, occasionally poll to ensure we have data
                # This handles cases where trace might not capture every change
                import random
                if random.random() < 0.1:  # Poll 10% of the time as backup
                    super().receive()
                    
        except Exception as e:
            print(f"Error in trace receive: {e}")
            # Fallback to polling on error
            super().receive()
    
    def stop(self):
        """Stop trace parser and disable comparators"""
        # Disable all DWT comparators
        for comp_idx in self.comparator_map.keys():
            func_base = self.DWT_FUNCTION0 + (comp_idx * 0x10)
            self._write_register(func_base, self.FUNC_DISABLED)
        
        # Disable ETB
        if self.trace_configured:
            self._write_register(self.ETB_CTL, 0x00000000)
        
        self.comparator_map.clear()
        self.trace_configured = False
        
        # Call parent stop
        super().stop()
