import itertools
import os
import time

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Thread

import cv2
import numpy as np

from ir import IRThread
from rgb import RGBThread
from ui import make_ir_view, make_rgb_view, make_combined_view
from utils.transforms import transform_boxes

from config import (
    HZ_CAP,
    LOG_DIR,
    SHOW_DISPLAY,
    SAVE_FRAMES,
    MAX_FILE_QUEUE,
    FRAME_SIZE,  # TODO: make everything size-independent
    IR_WIN_NAME,
    IR_WIN_SIZE,
    VIS_WIN_NAME,
    VIS_WIN_SIZE,
    X_DISPLAY_ADDR,
    VIS_BBOX_COLOR,
    IR_BBOX_COLOR,
    FACE_DET_MODEL,
)


def exit_handler():
    print("exit handler called")
    rgb_thread.stop()
    ir_thread.stop()

    rgb_thread.join()
    ir_thread.join()

    cv2.destroyAllWindows()


def setup_display(display_addr):
    if os.environ.get("DISPLAY") is None:
        os.environ["DISPLAY"] = display_addr
    elif X_DISPLAY_ADDR:
        print("WARN: Using $DISPLAY from environment, not from config")

    cv2.namedWindow(VIS_WIN_NAME)
    cv2.namedWindow(IR_WIN_NAME)
    cv2.moveWindow(IR_WIN_NAME, VIS_WIN_SIZE[0], 1) # horizontal, side by side


def read_temps(arr, bboxes):
    """
    arr - NumPy array of temperatures in deg C
    """
    temps = []
    (H, W) = arr.shape

    for box in bboxes:

        box_px = np.array([H, W, H, W]) * box
        box_px = np.rint(box_px).astype(np.int)
        x1, y1, x2, y2 = box_px

        roi = arr[y1:y2, x1:x2]
        roi = roi[roi > 30]  # reject regions below 30 degrees as background

        if roi.size != 0:
            Tavg = np.mean(roi)
            Tmax = np.max(roi)
            T90th = np.percentile(roi, 90)
        else:
            Tavg, Tmax, T90th = [float("nan")] * 3

        temps.append((Tavg, Tmax, T90th))

    return temps


def mainloop():

    for i in itertools.count(start=0, step=1):

        time_start = time.monotonic()

        ir_raw = ir_thread.raw
        ir_arr = ir_thread.frame
        temp_arr = ir_thread.temperatures

        rgb_arr = rgb_thread.frame
        scores, boxes, landms = rgb_thread.get_detections()

        # only keep detections with confidence above 50%
        scores = np.array(scores)
        boxes = np.array(boxes)
        landms = np.array(landms)

        keep = scores > 0.5

        scores = scores[keep]
        boxes = boxes[keep]
        landms = landms[keep]

        boxes_ir = transform_boxes(boxes, 1.05, 1.05, 0, 0)
        temps = read_temps(temp_arr, boxes_ir)

        # Render UI views
        ir_view = make_ir_view(temp_arr, scores, boxes_ir, landms, temps, IR_WIN_SIZE)
        rgb_view = make_rgb_view(rgb_arr, scores, boxes, landms, VIS_WIN_SIZE)
        combo_view = rgb_arr  # make_combined_view(rgb_arr, ir_arr)

        # Show rendered UI
        if SHOW_DISPLAY:
            cv2.imshow(VIS_WIN_NAME, rgb_view)
            cv2.imshow(IR_WIN_NAME, ir_view)
            cv2.imshow("Combined", combo_view)
            key = cv2.waitKey(1) & 0xFF

            # if the `q` key was pressed in the cv2 window, we break from the loop and exit the program
            if key == ord("q"):
                break

        # Save images to filesystem
        if SAVE_FRAMES:
            if executor._work_queue.qsize() > MAX_FILE_QUEUE:
                print(
                    "Error: Too many files in file queue. Not saving frames from this iteration."
                )
            else:
                # TODO: catch writing errors due to full SD card
                # For examlple: libpng error: Write Error
                # The ret value of imwrite can be obtained from:
                # future = executor.submit(...)
                # future.result()

                # OPTIMIZE: we're saving frames with main loop frequency (up to 20Hz)
                # It would be more efficient to check if the frames changed between
                # iterations
                executor.submit(
                    cv2.imwrite, f"{LOG_DIR}/frames/{i:05d}-rgb.jpg", rgb_view
                )
                executor.submit(
                    cv2.imwrite, f"{LOG_DIR}/frames/{i:05d}-ir.png", ir_view
                )

        main_latency = time.monotonic() - time_start

        # Quick f-string format specifiers reference:
        # f'{value:{width}.{precision}}'
        print(
            f"RGB thread latency={rgb_thread._delay:6.2f}ms   "
            f"IR thread latency={ir_thread.latency:6.2f}ms    "
            f"Main thread latency={1000*main_latency:6.2f}ms"
        )

        time.sleep(max(0, 1 / HZ_CAP - main_latency))


if __name__ == "__main__":

    rgb_thread = RGBThread(model=FACE_DET_MODEL)
    rgb_thread.start()

    ir_thread = IRThread(resize_to=FRAME_SIZE)
    ir_thread.start()

    if SAVE_FRAMES:
        executor = ThreadPoolExecutor(max_workers=4)

    if SHOW_DISPLAY:
        setup_display(X_DISPLAY_ADDR)

    while rgb_thread.frame is None:
        print("Waiting for RGB frames")
        time.sleep(1)

    while ir_thread.frame is None:
        print("Waiting for IR frames")
        time.sleep(1)

    try:
        mainloop()

    finally:
        exit_handler()
