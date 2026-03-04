import smbus2 
import time
class DAC121:
    """Classe per gestire il DAC DAC121 (12-bit) su Raspberry Pi"""
    
    # Maschere e Costanti
    DATA_MASK = 0x0FFF       # 12 bit di dati (0-4095)
    PDM_MASK  = 0x3000       # Bit 12 e 13 per il Power Down Mode
    
    # Power Down Modes
    MODE_NORMAL    = 0b00
    MODE_2_5K_GND  = 0b01
    MODE_100K_GND  = 0b10
    MODE_HIGH_IMP  = 0b11

    def __init__(self, bus_number=1, address=0x4C, vref=5.0):
        self.bus = smbus2.SMBus(bus_number)
        self.address = address
        self.vref = vref  # Tensione di riferimento (default 5V)

    # ... (mantieni le funzioni precedenti read_dac_register e write_dac_register) ...

    def set_voltage(self, voltage):
        """
        Imposta l'uscita in Volt.
        Esempio: dac.set_voltage(2.5)
        """
        # Limita il valore tra 0 e Vref per evitare errori
        if voltage < 0: voltage = 0
        if voltage > self.vref: voltage = self.vref
        
        # Converte Volt in valore digitale (0-4095)
        digital_value = int((voltage / self.vref) * 4095)
        self.set_data(digital_value)

    def get_voltage(self):
        """
        Legge il valore attuale dal DAC e lo restituisce in Volt.
        """
        digital_value = self.get_data()
        return (digital_value / 4095.0) * self.vref

    # --- Funzioni di supporto già viste ---
    def read_dac_register(self):
        data = self.bus.read_i2c_block_data(self.address, 0x00, 2)
        return (data[0] << 8) | data[1]

    def write_dac_register(self, value):
        msb = (value >> 8) & 0xFF
        lsb = value & 0xFF
        self.bus.write_i2c_block_data(self.address, msb, [lsb])

    def set_data(self, data):
        current_reg = self.read_dac_register()
        pdm_bits = current_reg & 0x3000 
        masked_data = data & 0x0FFF
        self.write_dac_register(masked_data | pdm_bits)

    def get_data(self):
        return self.read_dac_register() & 0x0FFF

    def set_power_down_mode(self, mode):
        """Imposta la modalità di risparmio energetico"""
        current_data = self.get_data()
        masked_mode = (mode << 12) & self.PDM_MASK
        self.write_dac_register(current_data | masked_mode)

    def get_power_down_mode(self):
        """Ritorna la modalità PDM attuale"""
        reg = self.read_dac_register()
        return (reg & self.PDM_MASK) >> 12

    def close(self):
        self.bus.close()

# ==========================================
# SEZIONE DI TEST
# ==========================================
if __name__ == "__main__":
    try:
        # L'indirizzo 0x4C è comune per i moduli DAC121 di Adafruit/altri
        dac = DAC121(bus_number=1, address=0x0D)
        
        print("Configurazione DAC121...")
        dac.set_power_down_mode(DAC121.MODE_NORMAL)
        for i in range(0, 11): # Aumenta il valore
            dac.set_voltage(i/2.0)
            time.sleep(0.2)
        print("Ciclo completato.")

        print("Generazione onda a dente di sega (0V -> VCC)...")
        while True:
            for i in range(0, 4096): # Aumenta il valore
                dac.set_data(i)
                #time.sleep(0.05)
            print("Ciclo completato.")
            
    except KeyboardInterrupt:
        print("\nTest interrotto.")
    except Exception as e:
        print(f"Errore: {e}")
    finally:
        print("Chiusura.")
