
#!/usr/bin/env python3
# coding: utf-8

import time
import TLV320ADC




#Define a dummy ADC for testing without the TLV320ADC
# Just returns a stubbed total gain
class DUMMYADC:
    def __init__(self,i2c_address=0x4c, input_dbm_per_dbv=[0,0,0,0]): # default i2c address
        self.power_status = {"ADC":0, 1:0, 2:0, 3:0, 4:0}
        self.samplerate_status = 0
        self.pre_input_gain_db = input_dbm_per_dbv
        self.a_gain_db = [1.0,1.0,0.0,0.0]
        self.d_gain_db = [1.0,1.0,0.0,0.0]
        


        
        self.adc_i2c_address = i2c_address 


    
    def total_gain(self):
        
        total_db = []
        for i in range(len(self.a_gain_db)):
            total_db.append(self.pre_input_gain_db[i] + self.a_gain_db[i] + self.d_gain_db[i])
            
        return total_db




def setup_adc(adc, again,dgain):



    # Startup and wakeup sequence
#    adc.shutdown()
#    time.sleep(2)

#    adc.startup()
    adc.set_wake()
    adc.set_power_config()

    time.sleep(0.5)

    #Set communication
    adc.set_communication(samplerate=384)
    adc.set_output_type(protocol="I2S", word_length=16, compatibility=True)
    adc.set_output_slot(channel=2, slot_side="LEFT", slot_num=0)
    adc.set_output_slot(channel=1, slot_side="RIGHT", slot_num=0)

    #Set analog gains before ADC powerup
    adc.set_analog_gain(1, analog_gain_db=again)
    adc.set_analog_gain(2, analog_gain_db=again)

    #Set any coefficients and mixer settings here
    adc.set_summer(sum_type = "NONE")
    adc.set_dynamic_range_enhancer( trigger_threshold_db = -54, max_gain_db=+6, enable_dre=False )

    # Configure inputs

    adc.set_input(channel=1, in_type="LINE", config="SINGLE", coupling="DC", impedance=20, dynamic_range_processing="OFF")
    adc.set_input(channel=2, in_type="LINE", config="SINGLE", coupling="DC", impedance=20, dynamic_range_processing="OFF")

    #Turn on ADC
    adc.set_input_power([1,2], power="ON", enable = True)
    adc.set_output_enable(channel_list=[1,2],enable=True)
    adc.set_adc_power( mic_bias="OFF", vref_volt=2.5, change_input_pwr_while_recording=False)

    # Below items can be changed while running

    adc.set_digital_gain_calibration(1, calibration_db = 0.0)
    adc.set_digital_gain_calibration(2, calibration_db = 0.0)

    adc.set_phase_calibration(1, calibration_cycles = 0.0)
    adc.set_phase_calibration(2, calibration_cycles = 0.0)

    adc.set_digital_gain(channel=1, digital_gain_db = dgain, muted=False, soft_step=True, ganged=False)
    adc.set_digital_gain(channel=2, digital_gain_db = dgain, muted=False, soft_step=True, ganged=False)

    print("ADC is ready")


    return



def cputemp():
    with open("/sys/class/thermal/thermal_zone0/temp", 'r') as f:
        return float(f.read().strip()) / 1000


#Main code

    #adc1 = DUMMYADC()
adc1 = TLV320ADC.TLV320ADC()
adc1.debug = True
#debug
setup_adc(adc1,again=0,dgain=0)


import RPi.GPIO as GPIO
import time

# 1. Configurazione della modalità di numerazione dei pin
# GPIO.BCM: usa la numerazione Broadcom (GPIOXX)
# GPIO.BOARD: usa la numerazione fisica del pin (Pin 1, 2, 3...)
GPIO.setmode(GPIO.BCM) 

# Definisci il pin GPIO che vuoi usare (ad esempio GPIO 17)
PIN_LED = 4

try:
    # 2. Imposta il pin come OUTPUT
    GPIO.setup(PIN_LED, GPIO.OUT)

    print(f"Imposto GPIO {PIN_LED} su LOW")
    # 3. Scrivi un valore HIGH (3.3V) sul pin
    GPIO.output(PIN_LED, GPIO.LOW)
    time.sleep(2) # Attendi 2 secondi

finally:
    # 5. Pulizia: Rilascia le risorse GPIO e le resetta a input
    print("Pulizia completata.")
