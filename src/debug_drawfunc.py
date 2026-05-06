import logging

from savant.deepstream.drawfunc import NvDsDrawFunc
from savant.deepstream.meta.frame import NvDsFrameMeta
from savant.utils.artist import Artist
from savant_rs.primitives.geometry import BBox

logger = logging.getLogger(__name__)


class DebugDrawFunc(NvDsDrawFunc):
    """
    Debug draw function for SV-PRO LPR/FR pipeline.

    It draws bounding boxes for objects and overlays:
      - plate number attribute from element 'lpr' / name 'plate_number' when present.
    """

    def draw_on_frame(self, frame_meta: NvDsFrameMeta, artist: Artist):
        frame_w, frame_h = artist.frame_wh

        for obj_meta in frame_meta.objects:
            try:
                bbox = obj_meta.bbox
            except Exception:
                continue

            # bbox coords are already in pixels (converter scales to ROI/frame space)
            pixel_left = max(0.0, bbox.left)
            pixel_top = max(0.0, bbox.top)
            pixel_right = min(bbox.left + bbox.width, frame_w)
            pixel_bottom = min(bbox.top + bbox.height, frame_h)

            pixel_width = pixel_right - pixel_left
            pixel_height = pixel_bottom - pixel_top
            if pixel_width < 1 or pixel_height < 1:
                continue

            try:
                artist.add_bbox(
                    BBox(
                        pixel_left + pixel_width / 2,
                        pixel_top + pixel_height / 2,
                        pixel_width,
                        pixel_height,
                    ),
                    2,
                    (0, 255, 0, 255),
                    None,
                    (0, 0, 0, 0),
                )

                label = f"{obj_meta.label} {obj_meta.confidence:.0%}"

                plate_attr = obj_meta.get_attr_meta("lpr", "plate_number")
                if plate_attr is not None:
                    label = f"{plate_attr.value} | {label}"

                artist.add_text(
                    label,
                    (int(pixel_left), max(0, int(pixel_top) - 10)),
                    0.5,
                    1,
                    (255, 255, 255, 255),
                    1,
                    (0, 0, 0, 0),
                    (0, 0, 0, 200),
                    (2, 2, 2, 2),
                    0,
                )
            except Exception as e:
                logger.warning("Draw error: %s", e)

