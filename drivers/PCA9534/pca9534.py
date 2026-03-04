import smbus2
import time

# Costanti dei Registri TCA9534
TCA9534_DEFAULT_I2C_ADDR = 0x20
TCA9534_REGISTER_INPUT_PORT = 0x00
TCA9534_REGISTER_OUTPUT_PORT = 0x01
TCA9534_REGISTER_INVERSION = 0x02
TCA9534_REGISTER_CONFIGURATION = 0x03

class TCA9534:
    """Classe per gestire l'espansore I/O TCA9534 su Raspberry Pi"""

    def __init__(self, bus_number=1, address=TCA9534_DEFAULT_I2C_ADDR):
        self.bus = smbus2.SMBus(bus_number)
        self.address = address

    def read_register(self, reg_address):
        return self.bus.read_byte_data(self.address, reg_address)

    def write_register(self, reg_address, value):
        self.bus.write_byte_data(self.address, reg_address, value & 0xFF)

    def set_gpio_mode(self, gpio_position, mode):
        """1 per INPUT, 0 per OUTPUT"""
        current = self.read_register(TCA9534_REGISTER_CONFIGURATION)
        if mode:
            new_val = current | (1 << gpio_position)
        else:
            new_val = current & ~(1 << gpio_position)
        self.write_register(TCA9534_REGISTER_CONFIGURATION, new_val)

    def set_gpio(self, gpio_position, state):
        """Imposta lo stato (High/Low) se il pin è in modalità OUTPUT"""
        current = self.read_register(TCA9534_REGISTER_OUTPUT_PORT)
        if state:
            new_val = current | (1 << gpio_position)
        else:
            new_val = current & ~(1 << gpio_position)
        self.write_register(TCA9534_REGISTER_OUTPUT_PORT, new_val)

    def get_gpio(self, gpio_position):
        """Legge lo stato del pin"""
        reg_val = self.read_register(TCA9534_REGISTER_INPUT_PORT)
        return bool(reg_val & (1 << gpio_position))

    def set_gpio_invert(self, gpio_position, invert):
        """Inverte la polarità logica del pin di input"""
        current = self.read_register(TCA9534_REGISTER_INVERSION)
        if invert:
            new_val = current | (1 << gpio_position)
        else:
            new_val = current & ~(1 << gpio_position)
        self.write_register(TCA9534_REGISTER_INVERSION, new_val)

    def close(self):
        self.bus.close()

# ==========================================
# SEZIONE DI TEST
# ==========================================
if __name__ == "__main__":
    try:
        print("Inizializzazione TCA9534...")
        tca = TCA9534(bus_number=1, address=0x20)
        
        # Test 1: Configurazione Pin 0 come Output
        print("Configuro il Pin 0 come OUTPUT e inizio il blink...")
        tca.set_gpio_mode(0, 0) # 0 = Output
        
        for i in range(5):
            print(f"Blink {i+1}: ON")
            tca.set_gpio(0, True)
            time.sleep(0.5)
            print(f"Blink {i+1}: OFF")
            tca.set_gpio(0, False)
            time.sleep(0.5)

        # Test 2: Lettura Pin 1
        print("\nConfiguro il Pin 1 come INPUT.")
        tca.set_gpio_mode(1, 1) # 1 = Input
        print("Leggi lo stato del Pin 1 (premi Ctrl+C per fermare):")
        
        while True:
            stato = tca.get_gpio(1)
            print(f"Stato Pin 1: {'ALTO' if stato else 'BASSO'}", end="\r")
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nTest interrotto dall'utente.")
    except Exception as e:
        print(f"\nErrore durante il test: {e}")
        print("Suggerimento: controlla che l'indirizzo I2C (0x27) sia corretto con 'i2cdetect -y 1'")
    finally:
        print("Chiusura bus.")
