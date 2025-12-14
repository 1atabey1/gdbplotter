import socketserver
import socket
import struct

class GdbMockHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request: socket.socket
        while True:
            try:
                cmd = self.request.recv(64).decode(errors="ignore")
                print(f"CMD received: {cmd}")
                if cmd.startswith("$?"):
                    self.request.send("$hi#12".encode("ascii"))
                else:
                    self.request.send("+$".encode("ascii") + struct.pack("<I8f", *[32, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8]).hex().encode("ascii") + "#12".encode("ascii"))
            except:
                break
        return super().handle()




if __name__ == "__main__":
    s = socketserver.ThreadingTCPServer(("localhost", 50000), GdbMockHandler)
    s.serve_forever()
