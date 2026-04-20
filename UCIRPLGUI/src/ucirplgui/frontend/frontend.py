from __future__ import annotations

from pythusa import Pipeline

from viviian import 
from ucirplgui import config


def run_frontend() -> None:
    # TODO: Implement the frontend here,
    # frontend is called using viviian and it is used to display the dashboard and the widgets
    # it is the connectors to the backend
    # it is the dashboard from components/dashboard.py 
    # it has connectors going back to the device interface 
    # it should use the tau-ceti theme 

    with VIVIIan as VIVII: 
        # TODO: Add the streams here
        # TODO: Add the tasks here one of the being the dashboard from components/dashboard.py
        # TODO: Add the events here
        # TODO: Add the connectors here comming from the backend and going to the dashboard
        # TODO: Add the connectors going back to the device interface  

        VIVII.run() # run the frontend 