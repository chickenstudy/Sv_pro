import re

with open("/home/admin-linux/svpro/module/module.yml", "r") as f:
    text = f.read()

text = text.replace("""    - element: nvinfer@complex_model
    - element: pyfunc
      module: src.debug_pyfunc
      class_name: DebugPyFunc
      name: debug_post_yolo


      name: yolov8_primary""", """    - element: nvinfer@complex_model
      name: yolov8_primary""")

with open("/home/admin-linux/svpro/module/module.yml", "w") as f:
    f.write(text)
