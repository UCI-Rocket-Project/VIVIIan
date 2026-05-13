import numpy as np 
from scipy import signal






def moving_avg(data:np.array, kernel:np.array):
    return np.convolve(data, kernel, mode="same")

