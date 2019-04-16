import time
import os

import pytest
from timeflux_amti.nodes.driver import ForceDriver


@pytest.fixture(scope='function')
def driver():
    dll_dir = os.environ.get('AMTI_DLL_DIR', None)
    amti = ForceDriver(rate=1000, dll_dir=dll_dir, device_index=0)
    yield amti
    amti.terminate()


def test_driver(driver):
    # Call a first time to clear old data
    driver.update()
    # Wait, then call again to collect data
    time.sleep(1)
    driver.update()

    assert hasattr(driver, 'o'), 'Driver did not generate output'
    assert not driver.o.data.empty, 'Driver generated empty data'
