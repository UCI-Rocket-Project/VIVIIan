import nidaqmx
from nidaqmx.system import System

system = System.local()

for dev in system.devices: 
    print(f"Name: {dev.name}")
    
    print(f"Device Info:  {dev}  ")