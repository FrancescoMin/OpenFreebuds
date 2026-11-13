from openfreebuds.driver.huawei.driver.generic import OfbDriverHuaweiGeneric
from openfreebuds.driver.huawei.handler import *


class OfbDriverHuaweiSe5Max(OfbDriverHuaweiGeneric):
    """
    HUAWEI FreeBuds SE 5 MAX
    """
    def __init__(self, address):
        super().__init__(address)
        self._spp_service_port = 1
        self.handlers = [
            OfbHuaweiLogsHandler(),
            OfbHuaweiInfoHandler(),
            OfbHuaweiStateInEarHandler(),
            OfbHuaweiBatteryHandler(),
            OfbHuaweiAncHandler(w_cancel_lvl=True, w_cancel_dynamic=True),
            OfbHuaweiActionDoubleTapHandler(w_in_call=True),
            OfbHuaweiActionTripleTapHandler(),
            OfbHuaweiActionLongTapSplitHandler(w_right=True, w_extra_options=True),
            OfbHuaweiConfigAutoPauseHandler(),
            OfnHuaweiSoundQualityPreferenceHandler(),
            OfbHuaweiLowLatencyPreferenceHandler(),
            OfbHuaweiEqualizerPresetHandler(
                w_presets={
                    1: "default",
                    2: "hardbass",
                    3: "treble",
                    9: "voices",
                },
                w_custom=True,
                w_fake_built_in=True,
            ),
            OfbHuaweiDualConnectHandler(w_auto_connect=False),
        ]
