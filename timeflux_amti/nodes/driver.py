# -*- coding: utf-8 -*-

"""Timeflux AMTI driver node

Use this node to acquire data from a AMTI force device.
"""

import ctypes
import json
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

    Notes:

    .. attention::

        Using a sampling frequency higher than 1000 Hz have been observed to
        drift significantly. Presumably, these higher frequencies would need
        the usage of an external trigger (the genlock feature).

    .. hint::

        Make sure to use an appropriate rate on the graph that contains this
        node. The graph rate should be short enough so that the underlying
        AMTI DLL buffer does not overflow, which will give repeated samples
        (this will be shown as a warning on the logs). The AMTI DLL buffer
        can hold about 10000 complete samples. For example, using
        ``ForceDriver(rate=1000)`` and a graph with rate of 0.1 (i.e. one
        update every 10 seconds), you would be dangerously close to overwriting
        the AMTI DLL buffer.

    .. warning::

        Since this class opens a library (DLL) and the release code is not
        guaranteed to free the library, using this class a second time on the
        same Python interpreter will fail with an OSError.

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
        self._diagnostics_dict = None
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
            if idx.size > 0 and np.any(data[idx, 0] != (2**24 - 1)):
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

            # Send diagnostic dictionary as metadata when it is set, but wait until there is data first
            # (otherwise hdf5.save will complain)
            if self._diagnostics_dict is not None:
                self.o.meta = self._diagnostics_dict
                self._diagnostics_dict = None

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

        # Setup some DLL functions that do not return int but something else
        self.driver.fmGetCableLength.restype = ctypes.c_float
        self.driver.fmGetPlatformRotation.restype = ctypes.c_float
        self.driver.fmGetADRef.restype = ctypes.c_float

        # DLL initialization as specified in SDK section 7.0
        self.logger.info('Initializing driver...')
        self.driver.fmDLLInit()
        retries = 3
        while True:  # TODO: change to self._retry
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
        if res not in (0, 1, 214):
            # 0: no signal conditioners found (ok)
            # 1: current setup is the same as the last saved configuration (ok)
            # 214: configuration has changed (ok?)
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

        # Log some diagnostics before starting
        self._diagnostics_dict = self._diagnostics()
        # Select back the device
        self.driver.fmDLLSelectDeviceIndex(self._dev_index)

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

    def _diagnostics(self):

        self.logger.info('Performing AMTI diagnostics')
        long_buffer = (ctypes.c_long * 64)()
        float_buffer = (ctypes.c_float * 64)()
        char_buffer = (ctypes.c_char * 64)()
        char_buffer2 = (ctypes.c_char * 64)()

        # DLL-level (general) diagnostics
        general = dict()

        general['init_complete'] = self.driver.fmDLLIsDeviceInitComplete()
        general['setup_check'] = self.driver.fmDLLSetupCheck()
        n_devices = self.driver.fmDLLGetDeviceCount()
        general['device_count'] = n_devices
        general['run_mode'] = self.driver.fmDLLGetRunMode()  # Note: exists also for device
        general['genlock'] = self.driver.fmDLLGetGenlock()
        general['acquisition_rate'] = self.driver.fmDLLGetAcquisitionRate()  # Note: exists also for device

        # Device-specific diagnostics
        devices = []
        for dev in range(n_devices):
            info = dict(index=dev)
            self.driver.fmDLLSelectDeviceIndex(dev)

            # general
            info['index'] = self.driver.fmDLLGetDeviceIndex()
            # run mode
            info['run_mode'] = self.driver.fmGetRunMode()
            # acquisition rate
            info['acquisition_rate'] = self.driver.fmGetAcquisitionRate()

            # signal conditioner configuration
            sc_config = dict()
            info['config'] = sc_config
            # gains
            self.driver.fmGetCurrentGains(long_buffer)
            sc_config['gains'] = long_buffer[:6]
            # excitations
            self.driver.fmGetCurrentExcitations(long_buffer)
            sc_config['excitations'] = long_buffer[:6]
            # channel offsets
            self.driver.fmGetChannelOffsetsTable(float_buffer)
            sc_config['channel_offsets'] = float_buffer[:6]
            # cable length
            sc_config['cable_length'] = self.driver.fmGetCableLength()
            # matrix mode
            sc_config['matrix_mode'] = self.driver.fmGetMatrixMode()
            # platform rotation
            sc_config['platform_rotation'] = self.driver.fmGetPlatformRotation()

            # signal conditioner mechanical limits
            sc_limits = dict()
            info['limits'] = sc_limits
            # mechanical max and min
            self._retry(lambda: self.driver.fmGetMechanicalMaxAndMin(float_buffer) != 1,
                        num_retries=3, wait=1, description='Obtaining mechanical max and min')
            sc_limits['mechanical_max_and_min'] = list(zip(float_buffer[0:6], float_buffer[6:12]))
            # analog max and min
            self._retry(lambda: self.driver.fmGetAnalogMaxAndMin(float_buffer) != 1,
                        num_retries=3, wait=1, description='Obtaining analog max and min')
            sc_limits['analog_max_and_min'] = list(zip(float_buffer[0:6], float_buffer[6:12]))

            # signal conditioner calibrations
            sc_calib = dict()
            sc_calib['amplifier'] = dict()
            info['signal_conditioner_calibration'] = sc_calib
            # product type
            sc_calib['product_type'] = self.driver.fmGetProductType()
            # amplifier model number
            self.driver.fmGetAmplifierModelNumber(char_buffer)
            sc_calib['amplifier']['model_number'] = ctypes.cast(char_buffer, ctypes.c_char_p).value.decode('ascii')
            # amplifier serial number
            self.driver.fmGetAmplifierSerialNumber(char_buffer)
            sc_calib['amplifier']['serial_number'] = ctypes.cast(char_buffer, ctypes.c_char_p).value.decode('ascii')
            # amplifier firmware version
            self.driver.fmGetAmplifierFirmwareVersion(char_buffer)
            sc_calib['amplifier']['firmware_version'] = ctypes.cast(char_buffer, ctypes.c_char_p).value.decode('ascii')
            # amplifier last calibration date
            self.driver.fmGetAmplifierDate(char_buffer)
            sc_calib['amplifier']['calibration_date'] = ctypes.cast(char_buffer, ctypes.c_char_p).value.decode('ascii')
            # gain table
            self.driver.fmGetGainTable(float_buffer)
            sc_calib['gain_table'] = float_buffer[:24]
            # excitation table
            self.driver.fmGetExcitationTable(float_buffer)
            sc_calib['excitation_table'] = float_buffer[:18]
            # DAC gains table
            self.driver.fmGetDACGainsTable(float_buffer)
            sc_calib['DAC_gains_table'] = float_buffer[:6]
            # DAC offset table
            self.driver.fmGetDACOffsetTable(float_buffer)
            sc_calib['DAC_offset_table'] = float_buffer[:6]
            # DAC sensitivities
            self.driver.fmGetDACSensitivities(float_buffer)
            sc_calib['DAC_sensitivities'] = float_buffer[:6]
            # ADRef
            sc_calib['AD_ref'] = self.driver.fmGetADRef()

            # Platform calibrations
            pc_calib = dict()
            info['platform_calibration'] = pc_calib

            # platform last calibration date
            self.driver.fmGetPlatformDate(char_buffer)
            pc_calib['calibration_date'] = ctypes.cast(char_buffer, ctypes.c_char_p).value.decode('ascii')
            # platform model number
            self.driver.fmGetPlatformModelNumber(char_buffer)
            pc_calib['model_number'] = ctypes.cast(char_buffer, ctypes.c_char_p).value.decode('ascii')
            # platform serial number
            self.driver.fmGetPlatformSerialNumber(char_buffer)
            pc_calib['serial_number'] = ctypes.cast(char_buffer, ctypes.c_char_p).value.decode('ascii')
            # platform length and width
            self.driver.fmGetPlatformLengthAndWidth(char_buffer, char_buffer2)
            pc_calib['length'] = ctypes.cast(char_buffer, ctypes.c_char_p).value.decode('ascii')
            pc_calib['width'] = ctypes.cast(char_buffer2, ctypes.c_char_p).value.decode('ascii')
            # platform xyz offsets
            self.driver.fmGetPlatformXYZOffsets(float_buffer)
            pc_calib['xyz_offset'] = float_buffer[:3]
            # platform xyz extensions
            self.driver.fmGetPlatformXYZExtensions(float_buffer)
            pc_calib['xyz_extensions'] = float_buffer[:3]
            # platform capacity
            self.driver.fmGetPlatformCapacity(float_buffer)
            pc_calib['capacity'] = float_buffer[:6]
            # platform bridge resistance
            self.driver.fmGetPlatformBridgeResistance(float_buffer)
            pc_calib['bridge_resistance'] = float_buffer[:6]
            # platform sensitivity matrix
            self.driver.fmGetInvertedSensitivityMatrix(float_buffer)
            pc_calib['inverted_sensitivity_matrix'] = float_buffer[:36]

            # That is all we can get from the platform!
            devices.append(info)

        diagnostics = dict(
            general=general,
            devices=devices,
        )
        self.logger.info('AMTI diagnostics results:\n%s',
                         json.dumps(diagnostics, indent=2))
        return diagnostics

    def _retry(self, predicate, num_retries=3, wait=1, description=None, exception=None):
        result = predicate()
        if wait < 0:
            wait = 1
        while not result and num_retries > 0:
            if description:
                self.logger.debug('%s failed, retyring in %f seconds...', description, wait)
            time.sleep(wait)
            result = predicate()
            num_retries -= 1
        exception = exception or TimefluxAmtiException
        if not result:
            desc = 'Failed to perform retryable operation'
            if description:
                desc += str(description)
            raise exception(description)

