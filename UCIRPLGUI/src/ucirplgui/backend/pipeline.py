from __future__ import annotations

from pythusa import Pipeline

from viviian import 
from ucirplgui import config


def run_pipeline() -> None:
    # TODO: Implement the pipeline here,
    # pipeline is called using viviian and it is used to process peform fft and store all data
    # comming from device interface 

    with VIVIIan as VIVII: 
        # TODO: Add the streams here
        # TODO: Add the tasks here
        # TODO: Add the events here
        # TODO: Add the connectors here
        #should have connectors comming in from the device interface and going to the frontend 
        # should have storage here as well for the data  

        VIVII.run() # run the pipeline 