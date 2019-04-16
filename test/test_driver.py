import logging
import time
import os

import numpy as np
import pytest
from timeflux_amti.nodes.driver import ForceDriver


@pytest.fixture(scope='module')
def driver():
    """Configured driver fixture"""
    # It's important to note that this fixture is on the module level because
    # it is not supported to have several ForceDriver instances at the same
    # time.
    dll_dir = os.environ.get('AMTI_DLL_DIR', None)
    amti = ForceDriver(rate=1000, dll_dir=dll_dir, device_index=0)
    yield amti
    amti.terminate()


def test_driver(driver):
    """Test driver general use-case"""
    # Call a first time to clear old data
    driver.update()
    # Wait, then call again to collect data
    time.sleep(1)
    driver.update()

    assert hasattr(driver, 'o'), 'Driver did not generate output'
    assert not driver.o.data.empty, 'Driver generated empty data'


def test_buffer(driver):
    """The underlying buffer of the DLL should support at least 10k samples"""
    # Call a first time to clear old data
    driver.update()
    # Wait, then call again to collect data
    # Here, we sleep 9.9 seconds to read a bit less than 10000 samples
    time.sleep(9.900)
    driver.update()

    # Obtain data
    df = driver.o.data

    # If the counter does not increment of exactly one, there might have been
    # some circular buffer overflow
    assert np.all(df.counter.diff().fillna(1) == 1), 'DLL buffer overflow'


def test_overflow_detection(driver, caplog):
    """When the underlying buffer is overflowed, there is a warning"""
    # Call a first time to clear old data
    driver.update()
    # Wait, then call again to collect data
    time.sleep(11)

    logger_name = 'timeflux.core.node.ForceDriver'  # NOTE: this name is due to the Node metaclass
    msg = 'Discontinuity on sample count. Check your sampling rate and graph rate!'
    with caplog.at_level(logging.WARNING, logger=logger_name):
        driver.update()
        assert (logger_name, logging.WARNING, msg) in caplog.record_tuples, \
            'Counter overflow was undetected'
