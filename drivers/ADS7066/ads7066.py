import spidev
import time

class ADS7066:
    # Mappa Registri fornita
    REG_SYSTEM_STATUS    = 0x00
    REG_GENERAL_CFG      = 0x01
    REG_DATA_CFG         = 0x02
    REG_OSR_CFG          = 0x03
    REG_OPMODE_CFG       = 0x04
    REG_PIN_CFG          = 0x05
    REG_GPIO_CFG         = 0x07
    REG_GPO_DRIVE_CFG    = 0x09
    REG_GPO_OUTPUT_VALUE = 0x0B
    REG_GPI_VALUE        = 0x0D
    REG_SEQUENCE_CFG     = 0x10
    REG_CHANNEL_SEL      = 0x11
    REG_AUTO_SEQ_CH_SEL  = 0x12

    # Opcodes
    OP_READ  = 0x10
    OP_WRITE = 0x08

    def __init__(self, bus=0, device=0, vref=5.0):
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = 400000
        self.spi.mode = 0b00 # Fondamentale: Mode 1
        self.vref = vref
        
        self.init_adc()

    def write_reg(self, reg, value):
        """Scrive un registro usando l'opcode 0x08"""
        payload = [self.OP_WRITE, reg, value]
        self.spi.xfer2(payload)

    def read_reg(self, reg):
        """Legge un registro usando l'opcode 0x10"""
        payload = [self.OP_READ, reg, 0x00]
        response = self.spi.xfer2(payload)
        response = self.spi.xfer2([0x00,0x00,0x00])
        return response[0]

    def init_adc(self):
        """Configurazione iniziale basata sui tuoi registri"""
        print("Inizializzazione ADS7066...")
        
        # 1. Reset Software (tramite GENERAL_CFG bit 0 o SYSTEM_STATUS?)
        # Di solito si scrive in GENERAL_CFG per il reset
        self.write_reg(self.REG_GENERAL_CFG, 0x88)
        time.sleep(0.1)       
        res = self.read_reg(self.REG_GENERAL_CFG)
        print(f"GEN CONFIG {hex(res)}")

        # 2. PIN_CFG (0x05): Imposta i pin come Analog Inputs
        # Reset = 0x00 (Tutti Analogici), quindi scriviamo 0x00 per sicurezza
        self.write_reg(self.REG_PIN_CFG, 0x00)

        # 3. OPMODE_CFG (0x04): Imposta la modalità operativa
        # Bit 2-0: 000 = Manual Mode, 001 = Auto-Sequence, ecc.
        # Il valore di reset è 0x04. Per Manual Mode proviamo 0x00.
        self.write_reg(self.REG_OPMODE_CFG, 0x00)

        # 4. DATA_CFG (0x02): Configura il formato dati
        # Assicuriamoci che non ci siano bit di stato extra che sporcano i 16 bit
        self.write_reg(self.REG_DATA_CFG, 0x10)

        self.write_reg(self.REG_SEQUENCE_CFG, 0x11)
        self.write_reg(self.REG_AUTO_SEQ_CH_SEL, 0x0F)

    def read_channel(self, channel):
        """Legge il canale in modalità Manuale"""
        # Seleziona il canale (0x11)
        self.write_reg(self.REG_CHANNEL_SEL, channel & 0x07)
        time.sleep(0.01) 
        # L'ADS7066 richiede un frame SPI per campionare e uno per trasmettere.
        # Inviamo 16 bit di clock per estrarre il dato.
        # Usiamo xfer2 per mantenere il CS basso durante i 2 byte.
        raw = self.spi.xfer2([0x00, 0x00,0x00])
        print(raw)
        #raw1 = self.read_reg(0xC1)
        #print(raw1)
        #raw2 = self.read_reg(0xC2)
        #print(raw2)
        # Unione dei due byte (MSB first)
        value = (raw[0] << 8) | raw[1]
#        print(value,value/2**16,value*5.0/2**16)
        return value

    def read_data(self):
        raw = self.spi.xfer2([0x00, 0x00,0x00])
        print(raw)
        #raw1 = self.read_reg(0xC1)
        #print(raw1)
        #raw2 = self.read_reg(0xC2)
        #print(raw2)
        # Unione dei due byte (MSB first)
        value = (raw[0] << 8) | raw[1]
        ch = raw[2]>>4 & 0x07
#        print(value,value/2**16,value*5.0/2**16)
        return value,ch

    def get_voltage(self, channel):
        digital_val = self.read_channel(channel)
#        print(f"DV :{self.vref} {digital_val} {(digital_val / 65535.0)} {(digital_val / 65535.0) * self.vref}")
        return (digital_val / 65535.0) * self.vref

    def get_voltage_auto(self):
        digital_val,ch = self.read_data()
#        print(f"DV :{self.vref} {digital_val} {(digital_val / 65535.0)} {(digital_val / 65535.0) * self.vref}")
        return (digital_val / 65535.0) * self.vref,ch
def test_adc():
    try:
        # Inizializza (VREF 5V o 3.3V a seconda di come alimenti l'AVDD del chip)
        adc = ADS7066(bus=0, device=0, vref=5.0)
        
        # Verifica comunicazione leggendo lo stato del sistema (Reset dovrebbe essere 0x81)
        status = adc.read_reg(0x00)
        print(f"--- Diagnostica SPI ---")
        print(f"Registro SYSTEM_STATUS: {hex(status)}")
        
        if status == 0x00 or status == 0xFF:
            print("ERRORE: Comunicazione SPI non funzionante. Controlla i cavi.")
            return

        print(f"\n--- Lettura Canali ---")
        v=[0, 0, 0, 0, 0, 0, 0, 0]
        while True:            
            vv,i = adc.get_voltage_auto()
            v[i]=vv
            for i in range(8):     
                print(f"CH{i} : {v[i]:.3f}V  ", end="")
            print(f"", end="\n")
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nTest terminato.")

if __name__ == "__main__":
    test_adc()
