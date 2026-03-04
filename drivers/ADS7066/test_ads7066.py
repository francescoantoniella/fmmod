#!/usr/bin/env python3
"""
Programma di test per ADS7066 su Raspberry Pi
ADS7066 è un ADC a 16 bit con interfaccia SPI

Dipendenze:
    sudo apt-get install python3-pip
    pip3 install spidev numpy matplotlib

Configurazione SPI su Raspberry Pi:
    - Abilitare SPI: sudo raspi-config -> Interface Options -> SPI -> Enable
    - Verificare: lsmod | grep spi
    - Verificare dispositivi: ls -l /dev/spi*

Collegamenti tipici ADS7066:
    - VDD: 3.3V o 5V
    - GND: Ground
    - SCLK: GPIO 11 (SPI0 SCLK)
    - DIN: GPIO 10 (SPI0 MOSI) - opzionale per configurazione
    - DOUT: GPIO 9 (SPI0 MISO)
    - CS: GPIO 8 (SPI0 CE0) o GPIO 7 (SPI0 CE1)
    - CONVST: GPIO (qualsiasi GPIO per trigger manuale)
"""

import spidev
import time
import numpy as np
import sys
import argparse
from typing import Optional, List, Tuple

try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("Matplotlib non disponibile. La visualizzazione grafica sarà disabilitata.")


class ADS7066:
    """
    Classe per interfacciare l'ADC ADS7066 via SPI
    """
    
    # Registri ADS7066 (se configurabili via SPI)
    # Nota: ADS7066 può essere configurato via pin hardware o SPI
    # Questo codice assume configurazione base via SPI
    
    def __init__(self, spi_bus=0, spi_device=0, max_speed_hz=10000000):
        """
        Inizializza l'interfaccia SPI per ADS7066
        
        Args:
            spi_bus: Bus SPI (0 o 1 su Raspberry Pi)
            spi_device: Dispositivo SPI (0 per CE0, 1 per CE1)
            max_speed_hz: Velocità massima SPI in Hz (default 10MHz)
        """
        self.spi = spidev.SpiDev()
        self.spi.open(spi_bus, spi_device)
        self.spi.max_speed_hz = max_speed_hz
        self.spi.mode = 0b00  # CPOL=0, CPHA=0
        self.spi.bits_per_word = 8
        
        # Parametri ADS7066
        self.resolution = 16  # 16 bit
        self.vref = 2.5  # Tensione di riferimento (può essere 2.5V o 4.096V)
        
        print(f"ADS7066 inizializzato su SPI bus {spi_bus}, device {spi_device}")
        print(f"Velocità SPI: {max_speed_hz/1000000:.1f} MHz")
    
    def read_adc(self) -> int:
        """
        Legge un valore dall'ADC
        
        Returns:
            Valore ADC a 16 bit (0-65535)
        """
        # ADS7066 richiede 2 byte per lettura a 16 bit
        # La lettura avviene durante il trasferimento SPI
        response = self.spi.xfer2([0x00, 0x00])
        
        # Combina i due byte in un valore a 16 bit
        # ADS7066 usa formato MSB first
        value = (response[0] << 8) | response[1]
        
        return value
    
    def read_adc_voltage(self, vref: Optional[float] = None) -> float:
        """
        Legge un valore dall'ADC e lo converte in volt
        
        Args:
            vref: Tensione di riferimento (default: self.vref)
        
        Returns:
            Tensione in volt
        """
        if vref is None:
            vref = self.vref
        
        adc_value = self.read_adc()
        voltage = (adc_value / (2**self.resolution - 1)) * vref
        
        return voltage
    
    def read_multiple_samples(self, num_samples: int, sample_rate: float = 1000.0) -> Tuple[np.ndarray, np.ndarray]:
        """
        Legge multipli campioni dall'ADC
        
        Args:
            num_samples: Numero di campioni da leggere
            sample_rate: Frequenza di campionamento in Hz
        
        Returns:
            Tuple (tempi, valori_adc) come array numpy
        """
        values = []
        times = []
        
        sample_period = 1.0 / sample_rate
        
        print(f"Acquisizione di {num_samples} campioni a {sample_rate} Hz...")
        start_time = time.time()
        
        for i in range(num_samples):
            sample_time = time.time()
            value = self.read_adc()
            values.append(value)
            times.append(sample_time - start_time)
            
            # Mantieni il rate di campionamento
            if i < num_samples - 1:
                time.sleep(sample_period)
        
        elapsed = time.time() - start_time
        actual_rate = num_samples / elapsed
        
        print(f"Acquisizione completata in {elapsed:.2f} secondi")
        print(f"Frequenza effettiva: {actual_rate:.1f} Hz")
        
        return np.array(times), np.array(values)
    
    def read_multiple_samples_voltage(self, num_samples: int, sample_rate: float = 1000.0, 
                                     vref: Optional[float] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Legge multipli campioni e li converte in volt
        
        Args:
            num_samples: Numero di campioni da leggere
            sample_rate: Frequenza di campionamento in Hz
            vref: Tensione di riferimento
        
        Returns:
            Tuple (tempi, tensioni) come array numpy
        """
        times, values = self.read_multiple_samples(num_samples, sample_rate)
        
        if vref is None:
            vref = self.vref
        
        voltages = (values / (2**self.resolution - 1)) * vref
        
        return times, voltages
    
    def test_continuity(self, num_samples: int = 100) -> dict:
        """
        Test di continuità: verifica che l'ADC risponda correttamente
        
        Args:
            num_samples: Numero di campioni per il test
        
        Returns:
            Dizionario con statistiche del test
        """
        print(f"\n=== Test di continuità ===")
        print(f"Lettura di {num_samples} campioni...")
        
        values = []
        for i in range(num_samples):
            value = self.read_adc()
            values.append(value)
            if (i + 1) % 10 == 0:
                print(f"  Campione {i+1}/{num_samples}: {value}")
        
        values = np.array(values)
        
        stats = {
            'min': int(np.min(values)),
            'max': int(np.max(values)),
            'mean': float(np.mean(values)),
            'std': float(np.std(values)),
            'median': int(np.median(values))
        }
        
        print(f"\nStatistiche:")
        print(f"  Minimo: {stats['min']}")
        print(f"  Massimo: {stats['max']}")
        print(f"  Media: {stats['mean']:.2f}")
        print(f"  Deviazione standard: {stats['std']:.2f}")
        print(f"  Mediana: {stats['median']}")
        
        # Verifica che i valori siano nel range valido
        if stats['min'] >= 0 and stats['max'] < 2**self.resolution:
            print("✓ Range valori: OK")
        else:
            print("✗ Range valori: ERRORE")
        
        return stats
    
    def close(self):
        """Chiude la connessione SPI"""
        self.spi.close()
        print("Connessione SPI chiusa")


def plot_data(times: np.ndarray, values: np.ndarray, title: str = "Dati ADS7066", 
              ylabel: str = "Valore ADC", save_file: Optional[str] = None):
    """
    Visualizza i dati acquisiti
    
    Args:
        times: Array di tempi
        values: Array di valori
        title: Titolo del grafico
        ylabel: Etichetta asse Y
        save_file: Nome file per salvare (opzionale)
    """
    if not HAS_MATPLOTLIB:
        print("Matplotlib non disponibile. Impossibile visualizzare il grafico.")
        return
    
    plt.figure(figsize=(12, 6))
    plt.plot(times, values, 'b-', linewidth=0.5)
    plt.xlabel('Tempo (s)')
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    
    if save_file:
        plt.savefig(save_file, dpi=150)
        print(f"Grafico salvato in: {save_file}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(description='Test ADS7066 su Raspberry Pi')
    parser.add_argument('--bus', type=int, default=0, help='Bus SPI (0 o 1)')
    parser.add_argument('--device', type=int, default=0, help='Dispositivo SPI (0=CE0, 1=CE1)')
    parser.add_argument('--speed', type=int, default=10000000, help='Velocità SPI in Hz (default: 10MHz)')
    parser.add_argument('--samples', type=int, default=1000, help='Numero di campioni da acquisire')
    parser.add_argument('--rate', type=float, default=1000.0, help='Frequenza di campionamento in Hz')
    parser.add_argument('--vref', type=float, default=2.5, help='Tensione di riferimento in V (default: 2.5V)')
    parser.add_argument('--test', choices=['single', 'multiple', 'continuity', 'all'], 
                       default='all', help='Tipo di test da eseguire')
    parser.add_argument('--plot', action='store_true', help='Mostra grafico dei dati')
    parser.add_argument('--save', type=str, help='Salva i dati in un file CSV')
    parser.add_argument('--save-plot', type=str, help='Salva il grafico in un file')
    
    args = parser.parse_args()
    
    # Crea istanza ADS7066
    try:
        adc = ADS7066(spi_bus=args.bus, spi_device=args.device, max_speed_hz=args.speed)
        adc.vref = args.vref
    except Exception as e:
        print(f"ERRORE: Impossibile inizializzare ADS7066: {e}")
        print("\nVerifica che:")
        print("  1. SPI sia abilitato: sudo raspi-config")
        print("  2. Il dispositivo SPI esista: ls -l /dev/spi*")
        print("  3. I collegamenti hardware siano corretti")
        sys.exit(1)
    
    try:
        if args.test in ['single', 'all']:
            print("\n=== Test lettura singola ===")
            value = adc.read_adc()
            voltage = adc.read_adc_voltage()
            print(f"Valore ADC: {value} (0x{value:04X})")
            print(f"Tensione: {voltage:.4f} V")
        
        if args.test in ['continuity', 'all']:
            stats = adc.test_continuity(num_samples=100)
        
        if args.test in ['multiple', 'all']:
            print(f"\n=== Test acquisizione multipla ===")
            times, values = adc.read_multiple_samples(args.samples, args.rate)
            
            # Calcola statistiche
            voltages = (values / (2**adc.resolution - 1)) * adc.vref
            print(f"\nStatistiche valori ADC:")
            print(f"  Min: {np.min(values)}, Max: {np.max(values)}")
            print(f"  Media: {np.mean(values):.2f}, Std: {np.std(values):.2f}")
            print(f"\nStatistiche tensioni:")
            print(f"  Min: {np.min(voltages):.4f} V, Max: {np.max(voltages):.4f} V")
            print(f"  Media: {np.mean(voltages):.4f} V, Std: {np.std(voltages):.4f} V")
            
            # Salva dati se richiesto
            if args.save:
                data = np.column_stack((times, values, voltages))
                header = "Tempo(s),Valore_ADC,Tensione(V)"
                np.savetxt(args.save, data, delimiter=',', header=header, fmt='%.6f,%d,%.6f')
                print(f"\nDati salvati in: {args.save}")
            
            # Visualizza grafico se richiesto
            if args.plot or args.save_plot:
                plot_data(times, voltages, 
                         title=f"ADS7066 - {args.samples} campioni @ {args.rate} Hz",
                         ylabel="Tensione (V)",
                         save_file=args.save_plot)
    
    except KeyboardInterrupt:
        print("\n\nInterrotto dall'utente")
    except Exception as e:
        print(f"\nERRORE durante l'esecuzione: {e}")
        import traceback
        traceback.print_exc()
    finally:
        adc.close()


if __name__ == "__main__":
    main()

