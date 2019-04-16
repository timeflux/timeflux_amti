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

import timeflux_amti
from timeflux_amti.exceptions import TimefluxAmtiException


_default_dll_dir = (
    pathlib.Path(timeflux_amti.__file__).parent / 'dll' / 'windows' /
    ('64bit' if sys.maxsize > 2**32 else '32bit')
).resolve()


class ForceDriver(Node):
    """ Acquisition driver for the AMTI force platform.

    This node uses the AMTI USB Device SDK version 1.3.00 to communicate with
    an AMTI AccuGait Optimized (AGO) force platform. All operations are
    performed through the `AMTIUSBDevice.dll` provided by AMTI and following
    the SDK documentation.

    Please refer to the SDK documentation for more details on how the force
    platform is configured and used. This class implements a single use-case
    (but can be extended if needed), which corresponds to:

    * 6+2 channels (three force, three moments, sample count and trigger).
    * Fully conditioned mode (see section 21 of SDK).
    * No genlock feature used (when an input port is used to synchronise and
      trigger a sample of the signal).

    The output of this node is a dataframe with 8 columns, representing the
    following channels: sample counter, three force values in x, y and z axis,
    three momentum values in x, y and z axis, and a trigger channel.
    Force and momentum are in SI units (newton and newton-meters, respectively).
    The output dataframe index are timestamps, calculated from the sample
    number. In other words, this node trusts the time management of the
    underlying AMTI DLL.


    Args:
        rate (int): Sampling rate in Hz. It must be one of the supported
            frequencies as listed in :py:attr:`SAMPLING_RATES`. Defaults to
            500 Hz.
        dll_dir (str): Directory where the DLL file `AMTIUSBDevice.dll` will
            be searched and loaded. By default, it uses the DLL directory
            included in the timeflux_amti package.
        device_index (int): Device number to read. AMTI supports several
            chained devices, but this has not been tested in timeflux_amti.
            Use the default, 0.

    Attributes:
        o (Port): Default output, provides a pandas.DataFrame with 8 columns.

    Notes:
        Using a sampling frequency higher than 1000 Hz have been observed to
        drift significantly. Presumably, these higher frequencies would need
        the usage of an external trigger (the genlock feature).

        Make sure to use an appropriate rate on the graph that contains this
        node. The graph rate should be short enough so that the underlying
        AMTI DLL buffer does not overflow, which will give repeated samples
        (this will be shown as a warning on the logs). The AMTI DLL buffer
        can hold about 10000 complete samples. For example, using
        ``ForceDriver(rate=1000)`` and a graph with rate of 0.1 (i.e. one
        update every 10 seconds), you would be dangerously close to overwriting
        the AMTI DLL buffer.

    Examples:

        The following YAML pipeline can be used to acquire from the AMTI force
        platform and print each sample:

        .. code-block:: yaml

           graphs:
              - nodes:
                - id: driver
                  module: timeflux_amti.nodes.driver
                  class: ForceDriver
                  params:
                    rate: 100

                - id: display
                  module: timeflux.nodes.debug
                  class: Display

                rate: 20

                edges:
                  - source: driver
                    target: display

    """

    SAMPLING_RATES = (
        2000, 1800, 1500, 1200, 1000, 900, 800, 600, 500, 450, 400, 360, 300,
        250, 240, 225, 200, 180, 150, 125, 120, 100, 90, 80, 75, 60, 50, 45,
        40, 30, 25, 20, 15, 10
    )
    """Supported sampling rates (in Hz) for the AMTI force platform."""

    def __init__(self, rate=500, dll_dir=None, device_index=0):
        super().__init__()
        if rate not in ForceDriver.SAMPLING_RATES:
            raise ValueError('Invalid sampling rate')
        elif rate > 1000:
            warnings.warn(
                'Sampling frequencies over 1000Hz are accepted, but the SDK '
                'documentation discourages it. There may be considerable drift.',
                UserWarning,
                stacklevel=2,
            )
        self._path = pathlib.Path(dll_dir or _default_dll_dir)
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
        """Property for the ctypes.WinDLL interface driver object"""
        if self._dll is None:
            self.logger.info('Loading DLL AMTIUSBDevice')
            dll_filename = self._path / 'AMTIUSBDevice.dll'
            self.logger.info('Attempting to load DLL %s', dll_filename)
            try:
                self._dll = ctypes.WinDLL(str(dll_filename.resolve()))
            except Exception as ex:
                self.logger.error('Could not load AMTIUSBDevice driver %s.',
                                  dll_filename, exc_info=True)
                raise TimefluxAmtiException('Failed to load AMTIUSBDevice') from ex
        return self._dll

    def update(self):
        """Read samples from the AMTI force platform"""
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

            # verify that there is no buffer overflow, but ignore the case when
            # the counter rolls over (which is at 2^24 - 1, according to SDK on
            # the fmDLLSetDataFormat function documentation)
            idx = np.where(np.diff(data[:, 0]) != 1)[0]
            if idx and np.any(data[idx, 0] != (2**24 - 1)):
                self.logger.warning('Discontinuity on sample count. Check '
                                    'your sampling rate and graph rate!')

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
        """Release the DLL and internal variables."""
        self._release_device()

    def _init_device(self):
        """Perform the device initialization procedure.

        This method follows the SDK documentation to initialize a device and
        start acquiring data from it.

        """
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
        """Perform the device release procedure.

        This function follows the SDK documentation to stop acquiring from a
        device.

        """
        self.logger.info('Releasing AMTIUSBDevice')
        self.driver.fmBroadcastStop()
        self.driver.fmDLLShutDown()
        time.sleep(0.500)  # Sleep 500ms as specified in SDK section 7.0
        self.logger.info('Device released')
