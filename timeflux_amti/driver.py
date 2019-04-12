# -*- coding: utf-8 -*-

"""Timeflux AMTI driver node

Use this node to acquire data from a AMTI force device.
"""

import ctypes
import pathlib
import sys
import time
import warnings

from timeflux.core.node import Node
import numpy as np

from timeflux_amti.exceptions import TimefluxAmtiException


class ForceDriver(Node):

    SAMPLING_RATES = (
        2000, 1800, 1500, 1200, 1000, 900, 800, 600, 500, 450, 400, 360, 300,
        250, 240, 225, 200, 180, 150, 125, 120, 100, 90, 80, 75, 60, 50, 45,
        40, 30, 25, 20, 15, 10
    )

    def __init__(self, rate, dll_dir, device_index=0):
        super().__init__()
        if rate not in ForceDriver.SAMPLING_RATES:
            raise ValueError('Invalid sampling rate')
        elif rate > 1000:
            warnings.warn(
                'Sampling frequencies over 1000Hz are accepted, but the SDK '
                'documentation discourages it.',
                UserWarning,
                stacklevel=2,
            )
        self._path = dll_dir
        self._rate = rate
        self._dev_index = device_index
        self._channel_names = ('counter', 'Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz', 'trigger')
        self._dll = None
        self._buffer = None
        self._start_timestamp = None
        self._reference_ts = None
        self._sample_count = None
        self._init_device()

    @property
    def driver(self):
        if self._dll is None:
            self.logger.info('Loading DLL AMTIUSBDevice')
            try:
                dll_filename = pathlib.Path(self._path) / 'AMTIUSBDevice.dll'
                self.logger.info('Attempting to load DLL %s', dll_filename)
                self._dll = ctypes.WinDLL(str(dll_filename.resolve()))
            except Exception as ex:
                self.logger.error('Could not load AMTIUSBDevice driver %s.',
                                  dll_filename, exc_info=True)
                raise TimefluxAmtiException('Failed to load AMTIUSBDevice') from ex
        return self._dll

    def update(self):
        # The first time, drop all samples that might have been captured
        # between the initialization and the first time this is called.
        # This step is crucial to get a correct estimation of the drift.
        if self._sample_count is None:
            n_read = None
            n_drop = 0
            while n_read != 0:
                n_read = self.driver.fmDLLGetTheFloatDataLBVStyle(self._buffer,
                                                                  ctypes.sizeof(self._buffer))
                n_drop += n_read

            self.logger.info('Dropped a total of %d samples of data between '
                             'driver initialization and first node update', n_drop)
            self._start_timestamp = np.datetime64(int(time.time() * 1e6), 'us')
            self._reference_ts = self._start_timestamp
            self._sample_count = 0

        data = []
        remaining_samples = None
        while remaining_samples != 0:
            remaining_samples = self.driver.fmDLLGetTheFloatDataLBVStyle(self._buffer,
                                                                         ctypes.sizeof(self._buffer))
            if remaining_samples:
                # numpy reshape to two dimensions: sample, channel
                # Since the dll gives always N values with N a multiple of 8,
                # the reshape sets the second dimension as 8 (channels) and will
                # add as many rows (samples) as needed
                data_samples = np.array(self._buffer).reshape(-1, 8)
                data.append(data_samples)

        if data:
            data = np.vstack(data)
            n_samples = data.shape[0]

            # sample counting to calculate drift
            self._sample_count += n_samples
            elapsed_seconds = (
                (np.datetime64(int(time.time() * 1e6), 'us') - self._reference_ts) /
                np.timedelta64(1, 's')
            )
            n_expected = int(np.round(elapsed_seconds * self._rate))
            self.logger.debug('Read samples=%d, elapsed_seconds=%f. '
                              'Expected=%d Real=%d Diff=%d (%.3f sec)',
                              n_samples, elapsed_seconds,
                              n_expected, self._sample_count, n_expected - self._sample_count,
                              (n_expected - self._sample_count) / self._rate)

            # Manage timestamps
            # For this node, we are trusting the device clock and setting the
            # timestamps from the sample number and sampling rate
            timestamps = (
                self._start_timestamp +
                (np.arange(n_samples + 1) * 1e6 / self._rate).astype('timedelta64[us]')
            )
            self._start_timestamp = timestamps[-1]

            # Write output to timeflux
            self.o.set(data, timestamps=timestamps[:-1], names=self._channel_names)

    def terminate(self):
        self._release_device()

    def _init_device(self):
        if sys.platform != 'win32':
            raise TimefluxAmtiException('This node is supported on Windows only')

        # DLL initialization as specified in SDK section 7.0
        self.logger.info('Initializing driver...')
        self.driver.fmDLLInit()
        retries = 3
        while True:
            time.sleep(0.250)  # Sleep 250ms as specified in SDK section 20.0
            res = self.driver.fmDLLIsDeviceInitComplete()
            if res in (1, 2):
                self.logger.info('DLL initialized')
                break
            self.logger.info('DLL still not initialized, retrying...')
            retries -= 1
            if retries <= 0:
                self.logger.warning('DLL initialization failed. '
                                    'Suggestion: check the contents / existence '
                                    'of C:/AMTI/AMTIUsbSetup.cfg')
                raise TimefluxAmtiException('Could not initialize DLL')

        self.logger.info('Setup check')
        res = self.driver.fmDLLSetupCheck()
        if res not in (0, 1):
            # 0: no signal conditioners found (ok)
            # 1: current setup is the same as the last saved configuration (ok)
            raise TimefluxAmtiException(f'Setup check failed with code {res}')

        self.logger.info('Selecting device %d', self._dev_index)
        n_devices = self.driver.fmDLLGetDeviceCount()
        if n_devices <= 0:
            raise TimefluxAmtiException('No devices found')
        self.driver.fmDLLSelectDeviceIndex(self._dev_index)

        self.logger.info('Selecting sampling rate')
        self.driver.fmBroadcastAcquisitionRate(self._rate)
        self.driver.fmBroadcastRunMode(1)  # metric, fully conditioned
        self.driver.fmDLLSetDataFormat(1)  # 8 values: counter, 3 force, 3 momentum, trigger
        self._buffer = (ctypes.c_float * (8 * 16))()  # 8 values per sample, and AMTI gives 16 samples every time

        # Start DLL acquisition
        self.driver.fmBroadcastStart()
        self.driver.fmBroadcastZero()
        time.sleep(1)

    def _release_device(self):
        self.logger.info('Releasing AMTIUSBDevice')
        self.driver.fmBroadcastStop()
        self.driver.fmDLLShutDown()
        time.sleep(0.500)  # Sleep 500ms as specified in SDK section 7.0
        self.logger.info('Device released')
