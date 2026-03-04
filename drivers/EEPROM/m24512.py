from smbus2 import SMBus, i2c_msg
import time

class N24512:
    def __init__(self, bus_number=1, address=0x50):
        self.bus_num = bus_number
        self.address = address
        self.pagesize = 128  # La 24C512 ha pagine da 128 byte

    def write_byte(self, mem_addr, data):
        """Scrive un singolo byte a un indirizzo a 16 bit."""
        with SMBus(self.bus_num) as bus:
            # Protocollo: [Indirizzo I2C] + [Addr MSB] + [Addr LSB] + [Data]
            msb = (mem_addr >> 8) & 0xFF
            lsb = mem_addr & 0xFF
            bus.write_i2c_block_data(self.address, msb, [lsb, data])
            # Tempo di scrittura interno (tipico 5ms per EEPROM)
            time.sleep(0.005)

    def read_byte(self, mem_addr):
        """Legge un singolo byte da un indirizzo a 16 bit."""
        with SMBus(self.bus_num) as bus:
            msb = (mem_addr >> 8) & 0xFF
            lsb = mem_addr & 0xFF
            # Scrive l'indirizzo da leggere, poi legge 1 byte
            bus.write_i2c_block_data(self.address, msb, [lsb])
            return bus.read_byte(self.address)

    def write_page(self, start_addr, data_list):
        """
        Scrive una pagina o parte di essa. 
        ATTENZIONE: Non superare il limite della pagina (128 byte) o si torna all'inizio!
        Scrive in blocchi di max 31 byte per rispettare il limite I2C (32 byte totali con LSB).
        """
        if len(data_list) > self.pagesize:
            raise ValueError(f"Dati troppo lunghi. Max {self.pagesize} byte.")
        
        with SMBus(self.bus_num) as bus:
            msb = (start_addr >> 8) & 0xFF
            current_addr = start_addr
            data_index = 0
            max_block_size = 31  # 32 byte totali (1 LSB + 31 dati)
            
            # Scrivi in blocchi di max 31 byte
            while data_index < len(data_list):
                block_size = min(max_block_size, len(data_list) - data_index)
                block_data = data_list[data_index:data_index + block_size]
                
                lsb = current_addr & 0xFF
                # Combina LSB e i dati in un'unica lista
                payload = [lsb] + block_data
                bus.write_i2c_block_data(self.address, msb, payload)
                time.sleep(0.005)  # Tempo di scrittura interno
                
                data_index += block_size
                current_addr += block_size
                
                # Aggiorna MSB se necessario (ogni 256 byte)
                new_msb = (current_addr >> 8) & 0xFF
                if new_msb != msb:
                    msb = new_msb

    def read_block(self, start_addr, length):
        """Legge un blocco di byte consecutivi."""
        with SMBus(self.bus_num) as bus:
            msb = (start_addr >> 8) & 0xFF
            lsb = start_addr & 0xFF
            # Imposta il puntatore di lettura
            bus.write_i2c_block_data(self.address, msb, [lsb])
            # Legge 'length' byte
            msg = i2c_msg.read(self.address, length)
            bus.i2c_rdwr(msg)
            return list(msg)

# --- ESEMPIO DI UTILIZZO ---
if __name__ == "__main__":
    import random
    eeprom = N24512(address=0x50)

    print("Scrittura byte 0xAA all'indirizzo 0x0100...")
    eeprom.write_byte(0x0100, 0xAA)
    
    val = eeprom.read_byte(0x0100)
    print(f"Letto: {hex(val)}")

    block = eeprom.read_block(0x0200, 10)
    print(f"Blocco letto: {block}")
    print("\nScrittura pagina (10 byte)...")
    test_data = [random.randint(0,255) for i in range(10)]
    eeprom.write_page(0x0200, test_data)
    
    block = eeprom.read_block(0x0200, 10)
    print(f"Blocco letto: {block}")
