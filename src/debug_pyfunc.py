import logging
from savant.deepstream.meta.frame import NvDsFrameMeta
from savant.deepstream.pyfunc import NvDsPyFuncPlugin

logger = logging.getLogger(__name__)

class DebugPyFunc(NvDsPyFuncPlugin):
    def process_frame(self, buffer: int, frame_meta: NvDsFrameMeta) -> None:
        objs = list(frame_meta.objects)
        if len(objs) > 1:
            info = [(o.label, o.element_name, o.bbox.left, o.bbox.top) for o in objs]
            logger.error(f"[DEBUG PYFUNC] Saw {len(objs)} objects! {info}")
        else:
            logger.error(f"[DEBUG PYFUNC] Saw only the frame object: {objs[0].label}")
