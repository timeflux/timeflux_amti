# -*- coding: utf-8 -*-

""""""

import ctypes
import datetime
import logging
import sys
import time

from timeflux.core.node import Node
import numpy as np

from timeflux_amti.exceptions import TimefluxAmtiException
from timeflux_amti.utils import path_context

logger = logging.getLogger(__name__)


class ForceDriver(Node):

    SAMPLING_RATES = (
        2000, 1800, 1500, 1200, 1000, 900, 800, 600, 500, 450, 400, 360, 300,
        250, 240, 225, 200, 180, 150, 125, 120, 100, 90, 80, 75, 60, 50, 45,
        40, 30, 25, 20, 15, 10
    )

    def __init__(self, rate, path=None):
        super().__init__()
        if rate not in ForceDriver.SAMPLING_RATES:
            raise ValueError('Invalid sampling rate')
        self._path = path
        self._rate = rate
        self._channel_names = ('counter', 'Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz', 'trigger')
        self._dll = None
        self._buffer = None
        self._start_timestamp = None
        self._reference_ts = None
        self._init_device()
        self.n_samples = None
        # print('Created AMTI', logger.getEffectiveLevel(), logger.handlers)
        # root = logging.getLogger()
        # print('root', root.getEffectiveLevel(), root.handlers)

    @property
    def driver(self):
        if self._dll is None:
            logger.info('Loading DLL AMTIUSBDevice')
            try:
                with path_context(self._path):
                    #self._dll = ctypes.cdll.AMTIUSBDevice
                    self._dll = ctypes.WinDLL(self._path + '\\AMTIUSBDevice.dll')
            except Exception as ex:
                self.logger.error('Could not load AMTIUSBDevice driver', exc_info=True)
                raise TimefluxAmtiException('Failed to load AMTIUSBDevice') from ex
        return self._dll

    def update(self):
        # The first time, drop all samples that might have been captured
        # between the initialization and the first time this is called
        if self.n_samples is None:
            n_read = None
            n_drop = 0
            while n_read != 0:
                n_read = self.driver.fmDLLGetTheFloatDataLBVStyle(self._buffer, ctypes.sizeof(self._buffer))
                self.logger.info('Pre-read %d samples', n_read)
                n_drop += n_read

            self.logger.info('Dropped %d samples', n_drop)
            self._start_timestamp = np.datetime64(int(time.time() * 1e6), 'us')
            self._reference_ts = self._start_timestamp
            self.n_samples = 0

        #logger.info('Update: expect %d samples', int(date_diff * self._rate))
        data = []

        remaining_samples = None
        #date_diff = (datetime.datetime.now() - self._last_update).total_seconds()
        while remaining_samples != 0:
            remaining_samples = self.driver.fmDLLGetTheFloatDataLBVStyle(self._buffer, ctypes.sizeof(self._buffer))
            if remaining_samples:
                # TODO: transform to numpy: np.array(buffer).reshape((-1, 8))
                data_samples = np.array(self._buffer).reshape(-1, 8)
                data.append(data_samples)
                # for a in range(0, 8 * 16, 8):
                #    data.append(self._buffer[a:(a + 8)])

        if data:
            data = np.vstack(data)
            n_samples = data.shape[0]

            ################################
            self.n_samples += n_samples
            elapsed_seconds = (np.datetime64(int(time.time() * 1e6), 'us') - self._reference_ts) / np.timedelta64(1, 's')
            # logger.info('%s %s', np.datetime64(int(time.time() * 1e6), 'us'), self._reference_ts)
            n_expected = int(np.round(elapsed_seconds * self._rate))
            logger.info('n_samples, elapsed_seconds=%f. Expected=%d Real=%d Diff=%d (%.3f sec)',
                        elapsed_seconds, n_expected, self.n_samples, n_expected - self.n_samples,
                        (n_expected - self.n_samples) / self._rate)
            #################################

            #logger.info('Read %d samples, expected %f', data.shape[0], date_diff * self._rate)
            logger.info('Read  %d samples', n_samples)

            timestamps = self._start_timestamp + (np.arange(n_samples + 1) * 1e6 / self._rate).astype('timedelta64[us]')
            self._start_timestamp = timestamps[-1]
            self.o.set(np.vstack(data),
                       timestamps=timestamps[:-1],  # TODO: determine/decide who manages timestamps
                       names=self._channel_names)
            #self.o.data['manual_time'] = timestamps[:-1]
            #self._last_update = datetime.datetime.now()

    def terminate(self):
        self._release_device()

    def _init_device(self):
        if sys.platform != 'win32':
            raise TimefluxAmtiException('This node only works on Windows')

        # DLL initialization as specified in SDK section 7.0
        self.logger.info('Initializing driver...')
        self.driver.fmDLLInit()
        retries = 3
        while retries > 0:
            time.sleep(0.250)  # Sleep 250ms as specified in SDK section 20.0
            # TODO: check or message on contents of AMTIUSBSetup.cfg?
            res = self.driver.fmDLLIsDeviceInitComplete()
            if res == 2:
                self.logger.info('DLL initialized')
                break
            self.logger.info('DLL still not initialized, retrying...')
            retries -= 1

        self.logger.info('Setup check')
        res = self.driver.fmDLLSetupCheck()
        if res not in (0, 1):
            raise TimefluxAmtiException(f'Setup check failed with code {res}')

        self.logger.info('Selecting device 0')
        n_devices = self.driver.fmDLLGetDeviceCount()
        if n_devices <= 0:
            raise TimefluxAmtiException('No devices found')
        self.driver.fmDLLSelectDeviceIndex(0)  # TODO: parametrize

        self.logger.info('Selecting sampling rate')
        self.driver.fmBroadcastAcquisitionRate(self._rate)  # TODO: check correct value
        self.driver.fmBroadcastRunMode(1)  # metric, fully conditioned TODO: parametrize
        self.driver.fmDLLSetDataFormat(1)  # 8 values: counter, 3 force, 3 momentum, trigger
        self._buffer = (ctypes.c_float * (8 * 16))()  # 8 values per sample, and AMTI gives 16 samples every time

        # Start DLL acquisition
        #self.logger.info('Start timestamp is %s', self._start_timestamp)
        self.driver.fmBroadcastStart()
        self.driver.fmBroadcastZero()
        time.sleep(1)

    def _release_device(self):
        self.logger.info('Releasing AMTIUSBDevice')
        self.driver.fmBroadcastStop()
        self.driver.fmDLLShutDown()
        time.sleep(0.500)  # Sleep 500ms as specified in SDK section 7.0
        #del self._dll
        #self._dll = None
        self.logger.info('Device released')
