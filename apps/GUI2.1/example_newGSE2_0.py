import socket
import struct
import binascii
import threading
import sys
import math

TARGET_IP = "10.0.0.217"
TARGET_PORT = 10001

# --- ANSI ESCAPE CODES ---
CLEAR_SCREEN = "\033[2J\033[H"
SAVE_CURSOR = "\033[s"
RESTORE_CURSOR = "\033[u"
HOME = "\033[H"
CLEAR_LINE = "\033[K"

# --- PACKET DEFINITIONS ---
MAGIC_HEADER_BYTES = b'\xef\xbe\xad\xde'
GSE_DATA_FORMAT = '<I I 18? 14f 4I'
GSE_DATA_SIZE = struct.calcsize(GSE_DATA_FORMAT)

GSE_COMMAND_FORMAT = '<I 15? I'
GSE_COMMAND_MAGIC = 0xDEADD00D
GSE_COMMAND_SIZE = struct.calcsize(GSE_COMMAND_FORMAT)

def calculate_crc32(data):
    return binascii.crc32(data) & 0xFFFFFFFF

def decode_gse_data(raw_bytes):
    try:
        unpacked = struct.unpack(GSE_DATA_FORMAT, raw_bytes)
        # Bools start at index 2
        # Igniter Armed: 2
        # Igniter 0 Cont: 3
        # Igniter 1 Cont: 4
        # Igniter 0 Fire State: 5
        # Igniter 1 Fire State: 6
        # Alarm State: 7
        # Solenoid Internal States: 8-19
        
        # Floats start at index 20
        # Supply Voltages: 20, 21
        # Solenoid Currents: 22-33
        
        # Temps at 34, 35, 36
        # CRC at 37
        
        solenoid_states = unpacked[8:20]
        solenoid_currents = unpacked[22:34]
        
        # 1. Save cursor and move to top-left
        sys.stdout.write(SAVE_CURSOR + HOME)
        
        # 2. Build Dashboard String
        out = []
        out.append(f"================ GSE LIVE DASHBOARD [{unpacked[1]}] ================{CLEAR_LINE}")
        out.append(f"--- IGNITERS & ALARM ---{CLEAR_LINE}")
        out.append(f"  Armed: {'ARMED' if unpacked[2] else 'DISARMED':<8} | Ign0: {'GOOD' if unpacked[3] else 'FAIL':<5} | Ign1: {'GOOD' if unpacked[4] else 'FAIL':<5}{CLEAR_LINE}")
        out.append(f"  Fire0: {'HIGH' if unpacked[5] else 'LOW':<8} | Fire1: {'HIGH' if unpacked[6] else 'LOW':<5} | Alarm: {'ACTIVE' if unpacked[7] else 'OFF':<5}{CLEAR_LINE}")
        
        out.append(f"\n--- SOLENOIDS ---{CLEAR_LINE}")
        out.append(f"  ID  | State | Current    | ID  | State | Current{CLEAR_LINE}")
        out.append(f"  -------------------------------------------------{CLEAR_LINE}")
        for i in range(0, 12, 2):
            s1, s2 = i, i+1
            c1 = solenoid_currents[s1]
            c2 = solenoid_currents[s2]
            c1_str = f"{c1:.3f} A" if not math.isnan(c1) else "NaN"
            c2_str = f"{c2:.3f} A" if not math.isnan(c2) else "NaN"
            out.append(f"  #{s1:<2} | {'ON' if solenoid_states[s1] else 'OFF':<5} | {c1_str:<10} | #{s2:<2} | {'ON' if solenoid_states[s2] else 'OFF':<5} | {c2_str:<10}{CLEAR_LINE}")
            
        out.append(f"\n--- TELEMETRY ---{CLEAR_LINE}")
        out.append(f"  Supply: {unpacked[20]:.2f}V, {unpacked[21]:.2f}V | Temps: T0:{unpacked[34]} T1:{unpacked[35]} T2:{unpacked[36]}{CLEAR_LINE}")
        out.append(f"  CRC: {hex(unpacked[37])}{CLEAR_LINE}")
        out.append(f"========================================================{CLEAR_LINE}")
        
        sys.stdout.write("\n".join(out) + "\n")
        
        # 3. Restore cursor and flush
        sys.stdout.write(RESTORE_CURSOR)
        sys.stdout.flush()
    except Exception:
        pass

def send_gse_command(sock, igniter0=False, igniter1=False, alarm=False, solenoids=None):
    if solenoids is None:
        solenoids = [False] * 12
    payload_format = '<I 15?'
    payload = struct.pack(payload_format, GSE_COMMAND_MAGIC, igniter0, igniter1, alarm, *solenoids)
    crc = calculate_crc32(payload)
    full_packet = payload + struct.pack('<I', crc)
    sock.sendall(full_packet)

def receive_thread(sock):
    buffer = b""
    while True:
        try:
            chunk = sock.recv(1024)
            if not chunk: break
            buffer += chunk
            while True:
                magic_idx = buffer.find(MAGIC_HEADER_BYTES)
                if magic_idx == -1:
                    buffer = buffer[-3:] if len(buffer) >= 3 else buffer
                    break
                buffer = buffer[magic_idx:]
                if len(buffer) >= GSE_DATA_SIZE:
                    packet_bytes = buffer[:GSE_DATA_SIZE]
                    decode_gse_data(packet_bytes)
                    buffer = buffer[GSE_DATA_SIZE:]
                else: break
        except Exception: break

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((TARGET_IP, TARGET_PORT))
        print(CLEAR_SCREEN) # Initial clear
        
        t = threading.Thread(target=receive_thread, args=(s,), daemon=True)
        t.start()
        
        current_solenoids = [False] * 12
        current_ign0 = False
        current_ign1 = False
        current_alarm = False

        # Move cursor to bottom (approx line 30) for input
        sys.stdout.write("\033[30;1H")
        
        while True:
            # Reprint the command prompt line to ensure visibility
            sys.stdout.write(f"\r{CLEAR_LINE}Command (s<num>, i0, i1, a, off, q): ")
            sys.stdout.flush()
            
            cmd = input().strip().lower()
            if cmd == 'q': break
            elif cmd == 'off':
                current_solenoids = [False] * 12
                current_ign0 = current_ign1 = current_alarm = False
            elif cmd == 'a': current_alarm = not current_alarm
            elif cmd == 'i0': current_ign0 = not current_ign0
            elif cmd == 'i1': current_ign1 = not current_ign1
            elif cmd.startswith('s'):
                try:
                    idx = int(cmd[1:])
                    if 0 <= idx < 12: current_solenoids[idx] = not current_solenoids[idx]
                except ValueError: pass
            
            send_gse_command(s, current_ign0, current_ign1, current_alarm, current_solenoids)

    except KeyboardInterrupt: pass
    finally:
        s.close()

if __name__ == "__main__":
    main()