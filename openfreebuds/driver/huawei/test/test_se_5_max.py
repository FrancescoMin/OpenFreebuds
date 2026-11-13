from openfreebuds.driver.huawei.driver.per_model.buds_se_5_max import OfbDriverHuaweiSe5Max
from openfreebuds.driver.constants import DEVICE_TO_DRIVER_MAP


def test_se_5_max_registered():
    assert "HUAWEI FreeBuds SE 5 MAX" in DEVICE_TO_DRIVER_MAP
    assert "HUAWEI FreeBuds SE 5 Max" in DEVICE_TO_DRIVER_MAP
    assert DEVICE_TO_DRIVER_MAP["HUAWEI FreeBuds SE 5 MAX"] is OfbDriverHuaweiSe5Max
    assert DEVICE_TO_DRIVER_MAP["HUAWEI FreeBuds SE 5 Max"] is OfbDriverHuaweiSe5Max


def test_se_5_max_handlers():
    driver = OfbDriverHuaweiSe5Max("64:79:24:20:dc:6c")
    assert driver.device_address == "64:79:24:20:dc:6c"
    assert driver._spp_service_port == 1
    handler_names = [h.__class__.__name__ for h in driver.handlers]
    
    # Core handlers
    assert "OfbHuaweiBatteryHandler" in handler_names
    assert "OfbHuaweiInfoHandler" in handler_names
    assert "OfbHuaweiLogsHandler" in handler_names
    
    # ANC and gestures
    assert "OfbHuaweiAncHandler" in handler_names
    assert "OfbHuaweiActionDoubleTapHandler" in handler_names
    assert "OfbHuaweiActionTripleTapHandler" in handler_names
    assert "OfbHuaweiActionLongTapSplitHandler" in handler_names
    
    # EQ and Preferences
    assert "OfbHuaweiEqualizerPresetHandler" in handler_names
    assert "OfbHuaweiLowLatencyPreferenceHandler" in handler_names
    assert "OfbHuaweiDualConnectHandler" in handler_names
    assert "OfnHuaweiSoundQualityPreferenceHandler" in handler_names


if __name__ == "__main__":
    test_se_5_max_registered()
    test_se_5_max_handlers()
    print("All FreeBuds SE 5 MAX tests passed successfully!")
