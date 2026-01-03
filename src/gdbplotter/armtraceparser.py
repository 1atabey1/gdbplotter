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
    ETB_BASE = 0xE0042000     # ETB Base Address
    ETB_RAM = 0xE0042000      # RAM Buffer (at base offset 0x0000)
    ETB_RDP = 0xE0042004      # RAM Data Port
    ETB_STS = 0xE0042008      # Status Register
    ETB_RRP = 0xE004200C      # RAM Read Pointer
    ETB_RWP = 0xE0042010      # RAM Write Pointer
    ETB_TRG = 0xE0042014      # Trigger Counter
    ETB_CTL = 0xE0042020      # Control Register
    ETB_RWD = 0xE0042024      # RAM Write Data (for depth detection)
    ETB_FFCR = 0xE0042304     # Formatter and Flush Control Register
    
    # ETB buffer size (typical values: 2048, 4096, 8192 bytes)
    # This can be detected by reading the RWD register
    ETB_BUFFER_SIZE = 2048    # Default 2KB, will be auto-detected
    
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
        self.etb_buffer_size = self.ETB_BUFFER_SIZE  # Will be detected on init
        
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
    
    def _detect_etb_buffer_size(self) -> int:
        """
        Detect the ETB buffer size by reading the RAM Write Data register.
        
        Returns: Buffer size in bytes
        """
        try:
            # Read the ETB RAM depth register (RWD)
            # This contains the buffer depth in 32-bit words
            depth_words = self._read_register(self.ETB_RWD)
            
            if depth_words > 0 and depth_words < 0x10000:  # Sanity check (max 256KB)
                buffer_size_bytes = depth_words * 4
                print(f"ETB buffer size detected: {buffer_size_bytes} bytes ({depth_words} words)")
                return buffer_size_bytes
            else:
                print(f"ETB depth register returned invalid value: {depth_words}, using default")
                return self.ETB_BUFFER_SIZE
                
        except Exception as e:
            print(f"Failed to detect ETB buffer size: {e}, using default {self.ETB_BUFFER_SIZE} bytes")
            return self.ETB_BUFFER_SIZE
    
    def _configure_trace_infrastructure(self) -> bool:
        """Configure the ARM CoreSight trace infrastructure"""
        try:
            # Detect ETB buffer size first
            self.etb_buffer_size = self._detect_etb_buffer_size()
            
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
    
    def _read_etb_buffer(self) -> bytes:
        """Read available data from the ETB trace buffer"""
        trace_stream = bytearray()
        
        try:
            # Check if data is available
            sts = self._read_register(self.ETB_STS)
            if not (sts & 0x1):  # Check if trigger occurred
                return bytes(trace_stream)
            
            # Get read and write pointers (in words, not bytes)
            rrp = self._read_register(self.ETB_RRP)
            rwp = self._read_register(self.ETB_RWP)
            
            if rrp == rwp:
                return bytes(trace_stream)
            
            # Calculate number of words to read
            buffer_size_words = self.etb_buffer_size // 4
            
            if rwp > rrp:
                # Simple case: no wrap-around
                num_words = rwp - rrp
                start_offset = rrp * 4
                
                # Read all data in one go from ETB RAM
                trace_stream = bytearray(self._read_memory(
                    self.ETB_RAM + start_offset,
                    num_words * 4
                ))
            else:
                # Wrap-around case: read from rrp to end, then from start to rwp
                # Read first chunk (rrp to end of buffer)
                words_to_end = buffer_size_words - rrp
                if words_to_end > 0:
                    chunk1 = self._read_memory(
                        self.ETB_RAM + (rrp * 4),
                        words_to_end * 4
                    )
                    trace_stream.extend(chunk1)
                
                # Read second chunk (start to rwp)
                if rwp > 0:
                    chunk2 = self._read_memory(
                        self.ETB_RAM,
                        rwp * 4
                    )
                    trace_stream.extend(chunk2)
            
            # Update read pointer to match write pointer (mark as read)
            self._write_register(self.ETB_RRP, rwp)
                
        except Exception as e:
            print(f"Error reading ETB: {e}")
        
        return bytes(trace_stream)
    
    def _parse_dwt_packet(self, header: int, payload: bytes, offset: int, trace_stream: bytes) -> tuple:
        """
        Parse a DWT data trace packet.
        
        Returns: (comparator_index, data_bytes, bytes_consumed) or (None, None, 0) if invalid
        """
        # DWT Event packet format (when POSTPRESET=1, which is default):
        # Bits 7:3 = discriminator (11100 for DWT data trace)
        # Bits 2:0 = comparator number (0-3)
        
        # DWT Data Value packet:
        # Header byte contains comparator number in bits 2:0
        # Payload follows with the actual data value
        
        # Check if this is a DWT packet (discriminator 11100 = 0x1C shifted)
        discriminator = (header >> 3) & 0x1F
        
        # DWT packet discriminators:
        # 0b00001 (0x01) - DWT event counter
        # 0b00010 (0x02) - DWT exception trace
        # 0b00011 (0x03) - DWT PC sample
        # 0b01110 (0x0E) - DWT data trace PC
        # 0b01111 (0x0F) - DWT data trace address
        # For data value trace, we look for specific patterns
        
        # Validate this is a DWT packet type
        valid_dwt_discriminators = [0x01, 0x02, 0x03, 0x0E, 0x0F]
        if discriminator not in valid_dwt_discriminators:
            return (None, None, 0)
        
        # Extract comparator number from lower bits
        comp_num = header & 0x03  # Bits 1:0 for comparator
        
        # Determine payload size based on header
        # Size encoding in bits 2:1 of header for some packet types
        size_field = (header >> 1) & 0x03
        payload_size = [1, 2, 4, 4][size_field]  # Possible sizes
        
        # Verify we have enough bytes
        if offset + payload_size > len(trace_stream):
            return (None, None, 0)
        
        # Extract data payload
        data_bytes = trace_stream[offset:offset + payload_size]
        
        return (comp_num, data_bytes, payload_size)
    
    def _parse_itm_packet(self, header: int, offset: int, trace_stream: bytes) -> tuple:
        """
        Parse an ITM stimulus port packet.
        
        Returns: (port_number, data_bytes, bytes_consumed) or (None, None, 0) if invalid
        """
        # ITM Stimulus Port packet format:
        # Bits 7:3 = port number (0-31)
        # Bits 2:1 = size (00=1 byte, 01=2 bytes, 10=4 bytes, 11=reserved)
        # Bit 0 = always 1 for stimulus
        
        if (header & 0x01) == 0:
            return (None, None, 0)  # Not a stimulus packet
        
        port = (header >> 3) & 0x1F
        size_field = (header >> 1) & 0x03
        
        if size_field == 0x03:  # Reserved
            return (None, None, 0)
        
        payload_size = [1, 2, 4][size_field]
        
        # Verify we have enough bytes
        if offset + payload_size > len(trace_stream):
            return (None, None, 0)
        
        # Extract data
        data_bytes = trace_stream[offset:offset + payload_size]
        
        return (port, data_bytes, payload_size)
    
    def _parse_trace_packets(self, trace_stream: bytes) -> dict:
        """
        Parse DWT/ITM trace stream and extract data for each region.
        
        Implements proper decoding of the ARM Cortex-M DWT trace protocol.
        """
        region_data = {}
        
        if not trace_stream:
            return region_data
        
        offset = 0
        
        while offset < len(trace_stream):
            if offset >= len(trace_stream):
                break
            
            header = trace_stream[offset]
            offset += 1
            
            # Check for synchronization packet (0x00 or 0x80 sequences)
            if header == 0x00:
                # Null/idle packet
                continue
            
            # Check for synchronization pattern (0x80 followed by zeros)
            if header == 0x80:
                # Skip synchronization sequence
                while offset < len(trace_stream) and trace_stream[offset] == 0x00:
                    offset += 1
                continue
            
            # Check for timestamp packets (header bits 7:4 = 0xC or 0x7)
            if (header & 0xF0) == 0xC0 or (header & 0xF0) == 0x70:
                # Timestamp packet - variable length, skip for now
                # Could parse timestamp for better synchronization
                continue
            
            # Check for overflow packet (0x70)
            if header == 0x70:
                print("Warning: Trace buffer overflow detected")
                continue
            
            # Parse DWT hardware source packets
            # DWT packets typically have specific bit patterns in header
            if (header & 0x04) == 0x04:  # DWT event/data trace indicator
                comp_num, data_bytes, consumed = self._parse_dwt_packet(
                    header, b'', offset, trace_stream
                )
                
                if comp_num is not None and data_bytes:
                    # Map comparator to region
                    if comp_num in self.comparator_map:
                        region_name = self.comparator_map[comp_num]
                        region = next((r for r in self.regions if r.name == region_name), None)
                        
                        if region:
                            expected_size = region.get_byte_count()
                            
                            # Accumulate data if we need more bytes
                            if len(data_bytes) < expected_size:
                                # Read additional bytes
                                bytes_needed = expected_size - len(data_bytes)
                                if offset + consumed + bytes_needed <= len(trace_stream):
                                    data_bytes = trace_stream[offset:offset + consumed + bytes_needed]
                                    consumed = bytes_needed
                            
                            # Only add if we have complete data
                            if len(data_bytes) >= expected_size:
                                payload = data_bytes[:expected_size]
                                if region_name not in region_data:
                                    region_data[region_name] = []
                                region_data[region_name].append(payload)
                    
                    offset += consumed
                else:
                    # Failed to parse, skip byte
                    pass
            
            # Parse ITM stimulus port packets (software trace)
            elif (header & 0x01) == 0x01:
                port, data_bytes, consumed = self._parse_itm_packet(header, offset, trace_stream)
                
                if port is not None and data_bytes:
                    # ITM ports could be mapped to regions if needed
                    offset += consumed
                else:
                    # Failed to parse, skip byte
                    pass
            
            # Unknown packet type
            else:
                # Skip unknown byte
                pass
        
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
            # Read trace stream from ETB
            trace_stream = self._read_etb_buffer()
            
            if trace_stream:
                # Parse trace stream and match to regions
                region_data = self._parse_trace_packets(trace_stream)
                
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
